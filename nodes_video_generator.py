import json
import time
import requests
import comfy.utils
from io import BytesIO
from comfy.comfy_types import IO
from comfy_api.latest._input_impl.video_types import VideoFromFile

from comfy_execution.graph_utils import ExecutionBlocker
from .utils import tensor2pil
from .config import get_config, get_current_base_url, API_PATHS


def _download_video_as_bytesio(url, timeout=120):
    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    buf = BytesIO()
    for chunk in resp.iter_content(chunk_size=8192):
        buf.write(chunk)
    buf.seek(0)
    return buf


# ── Grok 平台参数 ──
GROK_RATIOS = ["AUTO", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]
GROK_SIZES = ["720P", "480P"]
GROK_DURATIONS = ["6", "10"]

# ── Veo 平台参数 ──
VEO_RATIOS = ["16:9", "9:16"]
VEO_SIZES = ["720P", "1080P"]
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
                "enable_upsample": (VEO_UPSAMPLE, {"default": "false"}),
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
        full_msg = f"[RelayAPI] {msg}"
        print(full_msg)
        raise RuntimeError(full_msg)

    def _get_api_key(self, api_key):
        if api_key and api_key.strip():
            return api_key.strip()
        return get_config().get('api_key', '')

    def _headers_json(self, api_key):
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def _headers_auth(self, api_key):
        return {
            "Authorization": f"Bearer {api_key}",
        }

    def _image_to_base64_uri(self, image_tensor):
        try:
            import base64
            pil_image = tensor2pil(image_tensor)[0]
            buffered = BytesIO()
            pil_image.save(buffered, format="PNG")
            b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            print(f"[RelayAPI] Error converting image to base64: {e}")
            return None

    # ── 图片转 bytes（Veo 用 multipart）──
    def _image_to_bytes(self, image_tensor):
        try:
            pil_image = tensor2pil(image_tensor)[0]
            buffered = BytesIO()
            pil_image.save(buffered, format="PNG")
            return buffered.getvalue()
        except Exception as e:
            print(f"[RelayAPI] Error converting image: {e}")
            return None

    def _get_paths(self, api_format):
        return API_PATHS.get(api_format, API_PATHS["video_v1"])

    # ══════════════════════════════════════
    #  Grok 流程
    # ══════════════════════════════════════
    def _grok_create(self, base_url, api_key, model, prompt, ratio, size,
                     duration, images, seed, pbar, api_format="video_v1"):
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
                    return self._err(f"Failed to convert image {i + 1}.")

            if api_format == "video_v2":
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
        url = f"{base_url}{paths['grok_create']}"
        print(f"[RelayAPI] POST {url}")
        resp = requests.post(url, headers=self._headers_json(api_key),
                             json=payload, timeout=self.timeout)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"Grok create error: {resp.status_code} - {resp.text[:500]}")

        result = resp.json()
        task_id = result.get("task_id") or result.get("id") or result.get("request_id")
        if not task_id:
            self._err(f"No task ID returned: {json.dumps(result)[:300]}")

        print(f"[RelayAPI] Task: {task_id}")
        return task_id

    def _grok_query(self, base_url, api_key, task_id, api_format="video_v1"):
        paths = self._get_paths(api_format)
        url = f"{base_url}{paths['grok_query'].format(task_id=task_id)}"
        resp = requests.get(url, headers=self._headers_json(api_key), timeout=30)
        if resp.status_code != 200:
            return None, None, None
        data = resp.json()
        status = data.get("status", "unknown")
        video_url = (data.get("video_url")
                     or data.get("url")
                     or (data.get("video") or {}).get("url"))
        return status, video_url, data

    # ══════════════════════════════════════
    #  Veo 流程
    # ══════════════════════════════════════
    def _veo_actual_size(self, size, ratio):
        vertical = ratio == "9:16"
        if size == "1080P":
            return "1080x1920" if vertical else "1920x1080"
        return "720x1280" if vertical else "1280x720"

    def _veo_create(self, base_url, api_key, model, prompt, ratio, size,
                    enhance_prompt, enable_upsample, images, pbar, api_format="video_v1"):
        import io as _io
        paths = self._get_paths(api_format)

        actual_size = self._veo_actual_size(size, ratio)
        fields = [
            ("model", model or "veo_3_1-fast"),
            ("prompt", prompt),
            ("size", actual_size),
            ("aspect_ratio", ratio),
            ("ratio", ratio),
            ("enhance_prompt", enhance_prompt),
            ("enable_upsample", enable_upsample),
        ]

        for i, img in enumerate(images):
            if img is not None:
                pbar.update_absolute(15 + i * 3)
                img_bytes = self._image_to_bytes(img)
                if img_bytes:
                    fields.append(("input_reference[]", (f"image_{i+1}.png", _io.BytesIO(img_bytes), "image/png")))
                else:
                    return self._err(f"Failed to convert image {i + 1}.")

        pbar.update_absolute(30)

        files_list = []
        data_dict = {}
        for key, val in fields:
            if isinstance(val, tuple):
                files_list.append((key, val))
            else:
                data_dict[key] = val

        url = f"{base_url}{paths['veo_create']}"
        print(f"[RelayAPI] POST {url} (Veo)")
        resp = requests.post(url, headers=self._headers_auth(api_key),
                             data=data_dict, files=files_list, timeout=self.timeout)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"Veo create error: {resp.status_code} - {resp.text[:500]}")

        result = resp.json()
        task_id = result.get("task_id") or result.get("id") or result.get("request_id")
        if not task_id:
            self._err(f"No task ID returned: {json.dumps(result)[:300]}")

        print(f"[RelayAPI] Veo task: {task_id}")
        return task_id

    def _veo_query(self, base_url, api_key, task_id, api_format="video_v1"):
        paths = self._get_paths(api_format)
        url = f"{base_url}{paths['veo_query'].format(task_id=task_id)}"
        resp = requests.get(url,
                            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
                            timeout=30)
        if resp.status_code != 200:
            return None, None, None
        data = resp.json()
        status = data.get("status", "unknown")
        video_url = (data.get("url")
                     or data.get("video_url")
                     or (data.get("video") or {}).get("url"))
        return status, video_url, data

    # ══════════════════════════════════════
    #  通用轮询
    # ══════════════════════════════════════
    def _poll(self, query_fn, base_url, api_key, task_id, pbar):
        max_wait = 600
        start = time.time()

        for attempt in range(200):
            elapsed = time.time() - start
            if elapsed > max_wait:
                return self._err(f"Timeout after {elapsed:.1f}s")

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
                    continue

                if any(k in status_lower for k in ("fail", "error")):
                    reason = ""
                    if data:
                        reason = (data.get("fail_reason")
                                  or data.get("last_error")
                                  or (data.get("error") or {}).get("message", "")
                                  or "")
                    self._err(f"Task failed: {reason or status}")

            except requests.exceptions.Timeout:
                continue
            except RuntimeError:
                raise
            except Exception:
                continue

        return self._err(f"No result after {time.time() - start:.1f}s")

    # ══════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════
    def generate_video(self, prompt, ratio, size, duration,
                       info="",
                       enhance_prompt="true", enable_upsample="false",
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
            api_format = parsed.get("api_format", "video_v1")
            print(f"[RelayAPI] {platform} | {api_format} | {base_url} | {model}")
            images = [img for img in [image1, image2, image3, image4, image5, image6, image7] if img is not None]

            pbar = comfy.utils.ProgressBar(100)
            pbar.update_absolute(10)

            if platform == "Veo":
                task_id = self._veo_create(
                    base_url, api_key, model, prompt, ratio, size,
                    enhance_prompt, enable_upsample, images, pbar,
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
            print(f"[RelayAPI] Downloading video: {video_url}")

            video_buf = _download_video_as_bytesio(video_url)
            video_obj = VideoFromFile(video_buf)
            pbar.update_absolute(100)
            print(f"[RelayAPI] Video ready")

            return (video_obj, task_id, json.dumps({"code": "success", "url": video_url}), video_url)

        except Exception as e:
            error_resp = json.dumps({"code": "error", "message": str(e)}, ensure_ascii=False)
            return (ExecutionBlocker(None), task_id, error_resp, "")
