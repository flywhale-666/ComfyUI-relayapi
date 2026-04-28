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


def _download_video_to_tempfile(url, timeout=120, headers=None):
    resp = requests.get(url, headers=headers, stream=True, timeout=timeout)
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


GROK_RATIOS = ["auto", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"]
GROK_SIZES = ["720P", "1080P"]
GROK_DURATIONS = ["6", "10", "15", "30"]

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
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
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

    def _headers_auth(self, api_key):
        return {
            "Authorization": "Bearer " + api_key,
        }

    def _response_json(self, resp, context):
        try:
            return resp.json()
        except ValueError:
            body = (resp.text or "").strip()
            if not body:
                body = "<empty response body>"
            self._err(
                context
                + " returned non-JSON response: HTTP "
                + str(resp.status_code)
                + " - "
                + body[:500]
            )

    def _sanitize_for_response(self, value):
        if isinstance(value, str):
            if value.startswith("data:image/") and ";base64," in value:
                prefix = value.split(",", 1)[0]
                return prefix + ",<base64 omitted; length=" + str(len(value)) + ">"
            return value
        if isinstance(value, list):
            return [self._sanitize_for_response(item) for item in value]
        if isinstance(value, dict):
            return {key: self._sanitize_for_response(item) for key, item in value.items()}
        return value

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
        return API_PATHS.get(key, API_PATHS["video_v1/video"])

    def _grok_create(self, base_url, api_key, model, prompt, ratio, size,
                     duration, images, pbar, api_format="v1/video"):
        ratio = "auto" if str(ratio).lower() == "auto" else ratio
        if size not in GROK_SIZES:
            self._err("Unsupported Grok size: " + str(size) + ". Use one of: " + ", ".join(GROK_SIZES))
        if str(duration) not in GROK_DURATIONS:
            self._err("Unsupported Grok duration: " + str(duration) + ". Use one of: " + ", ".join(GROK_DURATIONS))

        paths = self._get_paths(api_format)
        payload = {
            "model": model or "grok-video-3",
            "prompt": prompt,
            "resolution": size.lower(),
            "duration": int(duration),
        }

        if ratio and ratio != "auto":
            payload["aspect_ratio"] = ratio
            payload["ratio"] = ratio

        if api_format == "v1/videos":
            payload["size"] = ratio if ratio and ratio != "auto" else "16:9"

        if images:
            b64_list = []
            for i, img in enumerate(images):
                pbar.update_absolute(15 + i * 2)
                uri = self._image_to_base64_uri(img)
                if uri:
                    b64_list.append(uri)
                else:
                    return self._err("Failed to convert image " + str(i + 1) + ".")

            if api_format == "v2/videos":
                payload["image"] = b64_list[0]
                if len(b64_list) > 1:
                    payload["images"] = b64_list
            elif api_format == "v1/videos":
                payload["input_reference"] = b64_list[0]
            else:
                payload["images"] = b64_list
                if len(b64_list) == 1:
                    payload["image"] = {"url": b64_list[0]}
                else:
                    payload["reference_images"] = [{"url": u} for u in b64_list]

        pbar.update_absolute(30)
        url = base_url + paths['grok_create']
        print("[RelayAPI] POST " + url)
        resp = requests.post(url, headers=self._headers_auth(api_key),
                             json=payload, timeout=self.timeout)
        print("[RelayAPI] -> " + str(resp.status_code))
        if resp.status_code != 200:
            self._err("Grok create error: " + str(resp.status_code) + " - " + resp.text[:500])

        result = self._response_json(resp, "Grok create " + url)
        task_id = result.get("task_id") or result.get("id") or result.get("request_id")
        if not task_id:
            self._err("No task ID returned: " + json.dumps(result)[:300])

        print("[RelayAPI] Task: " + task_id)
        return task_id, payload

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

    def _unwrap_payload(self, raw):
        # 中转站常见返回格式是 {"code":0,"data":{...真实任务字段...}}
        # 这里把真实任务字段提取出来，供后续状态判定统一读取
        if not isinstance(raw, dict):
            return {}
        data_field = raw.get("data")
        if isinstance(data_field, dict) and any(
            k in data_field for k in ("status", "state", "progress", "fail_reason")
        ):
            merged = dict(data_field)
            # 顶层的部分字段也合并进来，避免漏取
            for k in ("fail_reason", "last_error", "error", "message", "video_url", "url"):
                if k not in merged and k in raw:
                    merged[k] = raw[k]
            return merged
        return raw

    def _extract_fail_reason(self, payload, raw):
        # 统一提取失败原因，兼容多种字段命名
        for src in (payload, raw):
            if not isinstance(src, dict):
                continue
            for key in ("fail_reason", "failReason", "last_error", "message"):
                val = src.get(key)
                if isinstance(val, str) and val.strip():
                    return val
            err = src.get("error")
            if isinstance(err, dict):
                msg = err.get("message") or err.get("msg")
                if isinstance(msg, str) and msg.strip():
                    return msg
            elif isinstance(err, str) and err.strip():
                return err
        return ""

    def _grok_query(self, base_url, api_key, task_id, api_format="v1/video"):
        paths = self._get_paths(api_format)
        url = base_url + paths['grok_query'].format(task_id=task_id)
        resp = requests.get(url, headers=self._headers_auth(api_key), timeout=30)
        if resp.status_code != 200:
            # 把 HTTP 错误信息抛出来，让轮询层决定是否终止
            raise RuntimeError("HTTP " + str(resp.status_code) + ": " + resp.text[:300])
        raw = self._response_json(resp, "Grok query " + url)
        payload = self._unwrap_payload(raw)
        status = payload.get("status") or payload.get("state") or "unknown"
        video_url = self._extract_video_url(payload) or self._extract_video_url(raw)
        if api_format == "v1/videos" and not video_url:
            status_lower = str(status or "").lower()
            if any(k in status_lower for k in ("success", "completed", "done", "succeed")):
                content_path = paths.get("grok_content", "/v1/videos/{task_id}/content")
                video_url = base_url + content_path.format(task_id=task_id)
        # payload 里带上原始响应，方便失败时提取 reason
        payload.setdefault("__raw__", raw)
        return status, video_url, payload

    def _veo_actual_size(self, size, ratio):
        vertical = ratio == "9:16"
        if size == "1080P":
            return "1080x1920" if vertical else "1920x1080"
        return "720x1280" if vertical else "1280x720"

    def _veo_create(self, base_url, api_key, model, prompt, ratio, size, duration,
                    enhance_prompt, enable_HD, images, pbar, api_format="v1/videos"):
        paths = self._get_paths(api_format)

        model_has_4k = model and "4k" in model.lower()

        if model_has_4k or size == "4K":
            actual_size = None
        else:
            actual_size = self._veo_actual_size(size, ratio)

        _to_bool = lambda s: s.lower() == "true" if isinstance(s, str) else bool(s)
        if not model:
            self._err("Veo model not found. Please set via Relay API Settings node.")

        payload = {
            "model": model,
            "prompt": prompt,
            "aspect_ratio": ratio,
            "enhance_prompt": _to_bool(enhance_prompt),
            "enable_upsample": _to_bool(enable_HD),
        }
        if api_format != "v1/video":
            payload["duration"] = int(duration)
            payload["ratio"] = ratio
            payload["watermark"] = False
        if api_format != "v1/video" and actual_size is not None:
            payload["size"] = actual_size

        if api_format == "v1/videos":
            if model_has_4k or size == "4K":
                cheap_size = None
            else:
                cheap_size = self._veo_actual_size(size, ratio)
            data = {
                "model": model,
                "prompt": prompt,
                "seconds": str(duration),
                "watermark": "false",
            }
            if cheap_size is not None:
                data["size"] = cheap_size

            files = {}
            if images:
                image_bytes = self._image_to_bytes(images[0])
                if not image_bytes:
                    return self._err("Failed to convert image 1.")
                files["input_reference"] = ("input_reference.png", image_bytes, "image/png")

            pbar.update_absolute(30)
            url = base_url + paths['veo_create']
            print("[RelayAPI] POST " + url + " (Veo OpenAI video cheap, size=" + str(cheap_size) + ")")
            resp = requests.post(
                url,
                headers=self._headers_auth(api_key),
                data=data,
                files=files or None,
                timeout=self.timeout,
            )
            print("[RelayAPI] -> " + str(resp.status_code))
            if resp.status_code != 200:
                self._err("Veo create error: " + str(resp.status_code) + " - " + resp.text[:500])

            result = self._response_json(resp, "Veo create " + url)
            task_id = result.get("task_id") or result.get("id") or result.get("request_id")
            if not task_id:
                self._err("No task ID returned: " + json.dumps(result)[:300])

            print("[RelayAPI] Veo task: " + task_id)
            return task_id, data

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
        resp = requests.post(url, headers=self._headers_auth(api_key),
                             json=payload, timeout=self.timeout)
        print("[RelayAPI] -> " + str(resp.status_code))
        if resp.status_code != 200:
            self._err("Veo create error: " + str(resp.status_code) + " - " + resp.text[:500])

        result = self._response_json(resp, "Veo create " + url)
        task_id = result.get("task_id") or result.get("id") or result.get("request_id")
        if not task_id:
            self._err("No task ID returned: " + json.dumps(result)[:300])

        print("[RelayAPI] Veo task: " + task_id)
        return task_id, payload

    def _veo_query(self, base_url, api_key, task_id, api_format="v1/videos"):
        paths = self._get_paths(api_format)
        url = base_url + paths['veo_query'].format(task_id=task_id)
        resp = requests.get(url,
                            headers={"Accept": "application/json", "Authorization": "Bearer " + api_key},
                            timeout=30)
        if resp.status_code != 200:
            raise RuntimeError("HTTP " + str(resp.status_code) + ": " + resp.text[:300])
        raw = self._response_json(resp, "Veo query " + url)
        payload = self._unwrap_payload(raw)
        status = payload.get("status") or payload.get("state") or "unknown"
        video_url = self._extract_video_url(payload) or self._extract_video_url(raw)
        if api_format == "v1/videos" and not video_url:
            status_lower = str(status or "").lower()
            if any(k in status_lower for k in ("success", "completed", "done", "succeed")):
                content_path = paths.get("veo_content", "/v1/videos/{task_id}/content")
                video_url = base_url + content_path.format(task_id=task_id)
        payload.setdefault("__raw__", raw)
        return status, video_url, payload

    def _poll(self, query_fn, base_url, api_key, task_id, pbar):
        max_wait = 600
        start = time.time()
        consecutive_errors = 0  # 连续 HTTP/解析错误计数，避免无声空转

        for attempt in range(200):
            elapsed = time.time() - start
            if elapsed > max_wait:
                return self._err("Timeout after " + str(round(elapsed, 1)) + "s")

            time.sleep(5)

            try:
                status, video_url, data = query_fn(base_url, api_key, task_id)
                consecutive_errors = 0  # 查询成功后重置
                if status is None:
                    continue

                status_lower = (status or "").lower()

                progress_raw = (data or {}).get("progress")
                if progress_raw is not None:
                    try:
                        if isinstance(progress_raw, str) and progress_raw.endswith('%'):
                            pn = int(progress_raw.rstrip('%'))
                        elif isinstance(progress_raw, (int, float)):
                            pn = int(progress_raw)
                        else:
                            pn = 0
                        pbar.update_absolute(min(90, 40 + pn * 50 // 100))
                    except (ValueError, TypeError):
                        pbar.update_absolute(min(80, 40 + attempt * 40 // 200))

                # 失败判定优先，避免某些平台同时带 success/fail 关键字时误判
                if any(k in status_lower for k in ("fail", "error", "cancel")):
                    raw = (data or {}).get("__raw__", {})
                    reason = self._extract_fail_reason(data or {}, raw)
                    self._err("Task failed: " + (reason or status))

                if any(k in status_lower for k in ("success", "completed", "done", "succeed")):
                    if video_url:
                        return video_url
                    print("[RelayAPI] Task done but no video URL found: " + json.dumps(data, ensure_ascii=False)[:500])
                    continue

            except requests.exceptions.Timeout:
                consecutive_errors += 1
            except RuntimeError as e:
                # 来自 _grok_query/_veo_query 的 HTTP 非 200，或 _err 的失败
                msg = str(e)
                if msg.startswith("[RelayAPI]"):
                    # _err 抛出的终止错误直接上抛
                    raise
                consecutive_errors += 1
                print("[RelayAPI] query error (" + str(consecutive_errors) + "): " + msg)
            except Exception as e:
                consecutive_errors += 1
                print("[RelayAPI] query exception (" + str(consecutive_errors) + "): " + str(e))

            # 连续 6 次（约 30 秒）查询都失败就停止，别再空等到 600 秒
            if consecutive_errors >= 6:
                return self._err("Query keeps failing, aborted after "
                                 + str(round(time.time() - start, 1)) + "s")

        return self._err("No result after " + str(round(time.time() - start, 1)) + "s")

    def generate_video(self, prompt, ratio, size, duration, seed,
                       info="",
                       enhance_prompt="true", enable_HD="false",
                       image1=None, image2=None, image3=None,
                       image4=None, image5=None, image6=None, image7=None):
        parsed = {}
        if info and info.strip():
            try:
                parsed = json.loads(info)
            except Exception:
                pass

        task_id = ""
        request_payload = {}
        try:
            api_key = self._get_api_key(parsed.get("apikey", ""))
            if not api_key:
                return self._err("API key not found. Please set via Relay API Settings node.")

            raw_base = parsed.get("api_base", "")
            base_url = raw_base.strip().rstrip('/') if raw_base.strip() else get_current_base_url()
            platform = (parsed.get("platform") or "Grok").strip()
            model = parsed.get("model", "")
            api_format = parsed.get("api_format", "v1/video")
            if api_format not in {"v1/video", "v1/videos", "v2/videos"}:
                self._err("Unsupported video api_format: " + api_format)
            print("[RelayAPI] " + platform + " | " + api_format + " | " + base_url + " | " + model)
            images = [img for img in [image1, image2, image3, image4, image5, image6, image7] if img is not None]

            pbar = comfy.utils.ProgressBar(100)
            pbar.update_absolute(10)

            if platform == "Veo":
                task_id, request_payload = self._veo_create(
                    base_url, api_key, model, prompt, ratio, size, duration,
                    enhance_prompt, enable_HD, images, pbar,
                    api_format=api_format,
                )
                query_fn = lambda bu, ak, tid: self._veo_query(bu, ak, tid, api_format=api_format)
            else:
                task_id, request_payload = self._grok_create(
                    base_url, api_key, model, prompt, ratio, size, duration,
                    images, pbar,
                    api_format=api_format,
                )
                query_fn = lambda bu, ak, tid: self._grok_query(bu, ak, tid, api_format=api_format)

            pbar.update_absolute(40)

            video_url = self._poll(query_fn, base_url, api_key, task_id, pbar)
            pbar.update_absolute(90)
            print("[RelayAPI] Downloading video: " + video_url)

            download_headers = self._headers_auth(api_key) if video_url.startswith(base_url) else None
            video_path = _download_video_to_tempfile(video_url, headers=download_headers)
            video_obj = VideoFromFile(video_path)
            pbar.update_absolute(100)
            print("[RelayAPI] Video ready")

            return (
                video_obj,
                task_id,
                json.dumps(
                    {
                        "code": "success",
                        "url": video_url,
                        "api_format": api_format,
                        "platform": platform,
                        "request_payload": self._sanitize_for_response(request_payload),
                    },
                    ensure_ascii=False,
                ),
                video_url,
            )

        except Exception as e:
            error_resp = json.dumps({"code": "error", "message": str(e)}, ensure_ascii=False)
            return (ExecutionBlocker(None), task_id, error_resp, "")
