import base64
import json
import time
from io import BytesIO

import comfy.utils  # type: ignore[reportMissingImports]
import requests

from .config import API_PATHS, get_config, get_current_base_url
from .utils import tensor2pil


TEXT_MAX_IMAGES = 8
TEXT_SUPPORTED_MODELS = {
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
}


class RelayTextGenerator:
    @classmethod
    def INPUT_TYPES(cls):
        optional = {
            "info": ("STRING", {"default": "", "forceInput": True}),
        }
        for i in range(1, TEXT_MAX_IMAGES + 1):
            optional[f"image{i}"] = ("IMAGE",)

        return {
            "required": {
                "prompt": ("STRING", {"default": "", "multiline": True}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
            },
            "optional": optional,
        }

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "response")
    FUNCTION = "generate_text"
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
        return get_config().get("api_key", "")

    def _image_to_base64(self, image_tensor):
        pil_image = tensor2pil(image_tensor)[0]
        buffered = BytesIO()
        pil_image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")

    def _extract_text(self, result):
        if not isinstance(result, dict):
            return ""

        direct_text = result.get("text") or result.get("output_text") or result.get("response")
        if isinstance(direct_text, str) and direct_text.strip():
            return direct_text.strip()

        candidates = result.get("candidates", [])
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                content = candidate.get("content", {})
                if not isinstance(content, dict):
                    continue
                parts = content.get("parts", [])
                texts = []
                if isinstance(parts, list):
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            texts.append(text.strip())
                if texts:
                    return "\n".join(texts)

        return ""

    def _gemini_text_generate(self, base_url, api_key, model, prompt, images, pbar):
        paths = API_PATHS.get("text_native_style", {})
        path_tpl = paths.get("generate", "/v1beta/models/{model}:generateContent")
        url = f"{base_url}{path_tpl.format(model=model)}"

        parts = [{"text": prompt}]
        for i, img in enumerate(images):
            pbar.update_absolute(min(15 + i * 3, 55))
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": self._image_to_base64(img),
                }
            })

        payload = {
            "contents": [{"role": "user", "parts": parts}],
        }

        pbar.update_absolute(60)
        print(f"[RelayAPI] POST {url} (Gemini text, {len(images)} images, timeout={self.timeout}s)")
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=self.timeout,
        )
        pbar.update_absolute(85)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"Gemini text error: {resp.status_code} - {resp.text[:500]}")
        return resp.json()

    def generate_text(self, prompt, seed, info="", **kwargs):
        parsed = {}
        if info and info.strip():
            try:
                parsed = json.loads(info)
            except Exception:
                pass

        try:
            _ = seed
            api_key = self._get_api_key(parsed.get("apikey", ""))
            if not api_key:
                self._err("API key not found. Please set via Relay API Settings node.")

            raw_base = parsed.get("api_base", "")
            base_url = raw_base.strip().rstrip("/") if raw_base.strip() else get_current_base_url()
            model = (parsed.get("model", "") or "").strip()
            api_format = (parsed.get("api_format", "native_style") or "").strip()
            platform = (parsed.get("platform", "GeminiText") or "").strip()
            task_type = (parsed.get("task_type", "text") or "").strip()

            if task_type != "text":
                self._err("Relay API Settings task_type must be text.")
            if platform != "GeminiText":
                self._err(f"Unsupported text platform: {platform}")
            if api_format != "native_style":
                self._err(f"Unsupported text api_format: {api_format}")
            if model not in TEXT_SUPPORTED_MODELS:
                self._err(f"Unsupported text model: {model or '(empty)'}")

            images = []
            for i in range(1, TEXT_MAX_IMAGES + 1):
                img = kwargs.get(f"image{i}")
                if img is not None:
                    images.append(img)

            pbar = comfy.utils.ProgressBar(100)
            pbar.update_absolute(10)
            t0 = time.time()
            print(f"[RelayAPI] text | {platform} | {api_format} | {base_url} | {model}")

            result = self._gemini_text_generate(base_url, api_key, model, prompt, images, pbar)
            text = self._extract_text(result)
            if not text:
                self._err("No text returned in Gemini response.")

            pbar.update_absolute(100)
            response = json.dumps(result, ensure_ascii=False)
            print(f"[RelayAPI] TIMING total={time.time()-t0:.1f}s")
            return (text, response)
        except Exception as e:
            error_resp = json.dumps({"code": "error", "message": str(e)}, ensure_ascii=False)
            return ("", error_resp)
