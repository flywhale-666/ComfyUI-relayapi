import json
import os
import tempfile
import time
import requests
import comfy.utils  # type: ignore[reportMissingImports]
import folder_paths  # type: ignore[reportMissingImports]
from io import BytesIO
from comfy.comfy_types import IO  # type: ignore[reportMissingImports]
from comfy_api.latest._input_impl.video_types import VideoFromFile  # type: ignore[reportMissingImports]

from comfy_execution.graph_utils import ExecutionBlocker  # type: ignore[reportMissingImports]
from .utils import tensor2pil
from .config import get_config, get_current_base_url, API_PATHS


def _download_video_to_tempfile(url, timeout=120):
    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    ext = ".mp4"
    if "webm" in content_type:
        ext = ".webm"
    elif "quicktime" in content_type:
        ext = ".mov"
    temp_dir = folder_paths.get_temp_directory()
    os.makedirs(temp_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(suffix=ext, dir=temp_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception:
        os.unlink(temp_path)
        raise
    return temp_path


GROK_RATIOS = ["AUTO", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]
GROK_SIZES = ["720P", "480P"]
GROK_DURATIONS = ["6", "10"]

VEO_RATIOS = ["16:9", "9:16"]
VEO_SIZES = ["720P", "1080P", "4K"]
VEO_DURATIONS = ["4", "6", "8"]
VEO_ENHANCE = ["true", "false"]
VEO_UPSAMPLE = ["true", "false"]

ALL_RATIOS = list(dict.fromkeys(GROK_RATIOS + VEO_RATIOS))
ALL_SIZES = list(dict.fromkeys(GROK_SIZES + VEO_SIZES))
ALL_DURATIONS = list(dict.fromkeys(GROK_DURATIONS + VEO_DURATIONS))


class RelayVideoGenerator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True}),
                "ratio": (ALL_RATIOS, {"default": "16:9"}),
                "size": (ALL_SIZES, {"default": "720P"}),
                "duration": (ALL_DURATIONS, {"default": "10"}),
            },
            "optional": {
                "info": ("STRING", {"default": "", "forceInput": True}),
                "enhance_prompt": (VEO_ENHANCE, {"default": "true"}),
                "enable_HD": (VEO_UPSAMPLE, {"default": "false"}),
                "image1": ("IMAGE",),
                "image2": ("IMAGE",),
                "image3": ("IMAGE",),
                "image4": ("IMAGE",),
                "image5": ("IMAGE",),
                "image6": ("IMAGE",),
                "image7": ("IMAGE",),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
            }
        }

    RETURN_TYPES = (IO.VIDEO, "STRING", "STRING", "STRING")
    RETURN_NAMES = ("video", "task_id", "response", "video_url")
    FUNCTION = "generate_video"
    CATEGORY = "RelayAPI"

    def __init__(self):
        self.timeout = 300

    def _err(self, msg):
        full_msg = "[RelayAPI] " + msg
        print(full_msg)
        raise RuntimeError(full_msg)

    def _get_api_key(self, api_key):
        if api_key and api_key.strip():
            return api_key.strip()
        return get_config().get('api_key', '')

    def _headers_json(self, api_key):
        return {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        }

    def _headers_auth(self, api_key):
        return {
            "Authorization": "Bearer " + api_key,
        }

    def _image_to_base64_uri(self, image_tensor):
        try:
            import base64
            pil_image = tensor2pil(image_tensor)[0]
            buffered = BytesIO()
            pil_image.save(buffered, format="PNG")
            b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            return "data:image/png;base64," + b64
        except Exception as e:
            print("[RelayAPI] Error converting image to base64: " + str(e))
            return None

    def _image_to_bytes(self, image_tensor):
        try:
            pil_image = tensor2pil(image_tensor)[0]
            buffered = BytesIO()
            pil_image.save(buffered, format="PNG")
            return buffered.getvalue()
        except Exception as e:
            print("[RelayAPI] Error converting image: " + str(e))
            return None

    def _get_paths(self, api_format):
        key = "video_" + api_format
        return API_PATHS.get(key, API_PATHS["video_native_style"])

    def _grok_create(self, base_url, api_key, model, prompt, ratio, size,
                     duration, images, seed, pbar, api_format="native_style"):
        paths = self._get_paths(api_format)
        payload = {
            "model": model or "grok-video-3",
            "prompt": prompt,
            "resolution": size.lower(),
            "duration": int(duration),
        }

        if seed > 0:
            payload["seed"] = seed

        if ratio and ratio != "AUTO":
            payload["aspect_ratio"] = ratio
            payload["ratio"] = ratio

        if images:
            b64_list = []
            for i, img in enumerate(images):
                pbar.update_absolute(15 + i * 2)
                uri = self._image_to_base64_uri(img)
                if uri:
                    b64_list.append(uri)
                else:
                    return self._err("Failed to convert image " + str(i + 1) + ".")

            if api_format == "openai_style":
                payload["image"] = b64_list[0]
                if len(b64_list) > 1:
                    payload["images"] = b64_list
            else:
                payload["images"] = b64_list
                if len(b64_list) == 1:
                    payload["image"] = {"url": b64_list[0]}
                else:
                    payload["reference_images"] = [{"url": u} for u in b64_list]

        pbar.update_absolute(30)
        url = base_url + paths['grok_create']
        print("[RelayAPI] POST " + url)
        resp = requests.post(url, headers=self._headers_json(api_key),
                             json=payload, timeout=self.timeout)
        print("[RelayAPI] -> " + str(resp.status_code))
        if resp.status_code != 200:
            self._err("Grok create error: " + str(resp.status_code) + " - " + resp.text[:500])

        result = resp.json()
        task_id = result.get("task_id") or result.get("id") or result.get("request_id")
        if not task_id:
            self._err("No task ID returned: " + json.dumps(result)[:300])

        print("[RelayAPI] Task: " + task_id)
        return task_id

    def _extract_video_url(self, data):
        _is_url = lambda v: isinstance(v, str) and v.startswith("http")

        for key in ("video_url", "url", "download_url", "output_url", "output"):
            val = data.get(key)
            if _is_url(val):
                return val

        for nest_key in ("video", "output", "data"):
            nested = data.get(nest_key)
            if isinstance(nested, dict):
                for key in ("url", "video_url", "download_url", "output_url", "output"):
                    val = nested.get(key)
                    if _is_url(val):
                        return val

        results = data.get("results") or data.get("data") or []
        if isinstance(results, list) and results:
            item = results[0]
            if isinstance(item, dict):
                for key in ("url", "video_url", "download_url", "output"):
                    val = item.get(key)
                    if _is_url(val):
                        return val
            elif _is_url(item):
                return item

        return None

    def _grok_query(self, base_url, api_key, task_id, api_format="native_style"):
        paths = self._get_paths(api_format)
        url = base_url + paths['grok_query'].format(task_id=task_id)
        resp = requests.get(url, headers=self._headers_json(api_key), timeout=30)
        if resp.status_code != 200:
            return None, None, None
        data = resp.json()
        status = data.get("status", "unknown")
        video_url = self._extract_video_url(data)
        return status, video_url, data

    def _veo_actual_size(self, size, ratio):
        if size == "4K":
            return "4K"
        vertical = ratio == "9:16"
        if size == "1080P":
            return "1080x1920" if vertical else "1920x1080"
        return "720x1280" if vertical else "1280x720"

    def _veo_create(self, base_url, api_key, model, prompt, ratio, size,
                    enhance_prompt, enable_HD, images, pbar, api_format="native_style"):
        paths = self._get_paths(api_format)

        model_has_4k = model and "4k" in model.lower()

        if model_has_4k:
            actual_size = None
        elif size == "4K":
            actual_size = "4K"
        else:
            actual_size = self._veo_actual_size(size, ratio)

        _to_bool = lambda s: s.lower() == "true" if isinstance(s, str) else bool(s)

        payload = {
            "model": model or "veo_3_1-fast",
            "prompt": prompt,
            "aspect_ratio": ratio,
            "ratio": ratio,
            "enhance_prompt": _to_bool(enhance_prompt),
            "enable_upsample": _to_bool(enable_HD),
        }
        if actual_size is not None:
            payload["size"] = actual_size

        if images:
            b64_list = []
            for i, img in enumerate(images):
                pbar.update_absolute(15 + i * 3)
                uri = self._image_to_base64_uri(img)
                if uri:
                    b64_list.append(uri)
                else:
                    return self._err("Failed to convert image " + str(i + 1) + ".")
            payload["images"] = b64_list
            if len(b64_list) == 1:
                payload["image"] = {"url": b64_list[0]}
            else:
                payload["reference_images"] = [{"url": u} for u in b64_list]

        pbar.update_absolute(30)

        url = base_url + paths['veo_create']
        print("[RelayAPI] POST " + url + " (Veo, " + str(len(images)) + " images, size=" + str(actual_size) + ")")
        resp = requests.post(url, headers=self._headers_json(api_key),
                             json=payload, timeout=self.timeout)
        print("[RelayAPI] -> " + str(resp.status_code))
        if resp.status_code != 200:
            self._err("Veo create error: " + str(resp.status_code) + " - " + resp.text[:500])

        result = resp.json()
        task_id = result.get("task_id") or result.get("id") or result.get("request_id")
        if not task_id:
            self._err("No task ID returned: " + json.dumps(result)[:300])

        print("[RelayAPI] Veo task: " + task_id)
        return task_id

    def _veo_query(self, base_url, api_key, task_id, api_format="native_style"):
        paths = self._get_paths(api_format)
        url = base_url + paths['veo_query'].format(task_id=task_id)
        resp = requests.get(url,
                            headers={"Accept": "application/json", "Authorization": "Bearer " + api_key},
                            timeout=30)
        if resp.status_code != 200:
            return None, None, None
        data = resp.json()
        status = data.get("status", "unknown")
        video_url = self._extract_video_url(data)
        return status, video_url, data

    def _poll(self, query_fn, base_url, api_key, task_id, pbar):
        max_wait = 600
        start = time.time()

        for attempt in range(200):
            elapsed = time.time() - start
            if elapsed > max_wait:
                return self._err("Timeout after " + str(round(elapsed, 1)) + "s")

            time.sleep(5)

            try:
                status, video_url, data = query_fn(base_url, api_key, task_id)
                if status is None:
                    continue

                status_lower = (status or "").lower()

                if "progress" in (data or {}):
                    progress = data.get("progress", "0%")
                    try:
                        if isinstance(progress, str) and progress.endswith('%'):
                            pn = int(progress.rstrip('%'))
                        elif isinstance(progress, (int, float)):
                            pn = int(progress)
                        else:
                            pn = 0
                        pbar.update_absolute(min(90, 40 + pn * 50 // 100))
                    except (ValueError, TypeError):
                        pbar.update_absolute(min(80, 40 + attempt * 40 // 200))

                if any(k in status_lower for k in ("success", "completed", "done", "succeed")):
                    if video_url:
                        return video_url
                    print("[RelayAPI] Task done but no video URL found: " + json.dumps(data)[:500])
                    continue

                if any(k in status_lower for k in ("fail", "error")):
                    reason = ""
                    if data:
                        reason = (data.get("fail_reason")
                                  or data.get("last_error")
                                  or (data.get("error") or {}).get("message", "")
                                  or "")
                    self._err("Task failed: " + (reason or status))

            except requests.exceptions.Timeout:
                continue
            except RuntimeError:
                raise
            except Exception:
                continue

        return self._err("No result after " + str(round(time.time() - start, 1)) + "s")

    def generate_video(self, prompt, ratio, size, duration,
                       info="",
                       enhance_prompt="true", enable_HD="false",
                       image1=None, image2=None, image3=None,
                       image4=None, image5=None, image6=None, image7=None, seed=0):
        parsed = {}
        if info and info.strip():
            try:
                parsed = json.loads(info)
            except Exception:
                pass

        task_id = ""
        try:
            api_key = self._get_api_key(parsed.get("apikey", ""))
            if not api_key:
                return self._err("API key not found. Please set via Relay API Settings node.")

            raw_base = parsed.get("api_base", "")
            base_url = raw_base.strip().rstrip('/') if raw_base.strip() else get_current_base_url()
            platform = (parsed.get("platform") or "Grok").strip()
            model = parsed.get("model", "")
            api_format = parsed.get("api_format", "native_style")
            print("[RelayAPI] " + platform + " | " + api_format + " | " + base_url + " | " + model)
            images = [img for img in [image1, image2, image3, image4, image5, image6, image7] if img is not None]

            pbar = comfy.utils.ProgressBar(100)
            pbar.update_absolute(10)

            if platform == "Veo":
                task_id = self._veo_create(
                    base_url, api_key, model, prompt, ratio, size,
                    enhance_prompt, enable_HD, images, pbar,
                    api_format=api_format,
                )
                query_fn = lambda bu, ak, tid: self._veo_query(bu, ak, tid, api_format=api_format)
            else:
                task_id = self._grok_create(
                    base_url, api_key, model, prompt, ratio, size, duration,
                    images, seed, pbar,
                    api_format=api_format,
                )
                query_fn = lambda bu, ak, tid: self._grok_query(bu, ak, tid, api_format=api_format)

            pbar.update_absolute(40)

            video_url = self._poll(query_fn, base_url, api_key, task_id, pbar)
            pbar.update_absolute(90)
            print("[RelayAPI] Downloading video: " + video_url)

            video_path = _download_video_to_tempfile(video_url)
            video_obj = VideoFromFile(video_path)
            pbar.update_absolute(100)
            print("[RelayAPI] Video ready")

            return (video_obj, task_id, json.dumps({"code": "success", "url": video_url}), video_url)

        except Exception as e:
            error_resp = json.dumps({"code": "error", "message": str(e)}, ensure_ascii=False)
            return (ExecutionBlocker(None), task_id, error_resp, "")
