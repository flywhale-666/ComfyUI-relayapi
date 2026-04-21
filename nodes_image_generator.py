import json
import base64
import requests
import comfy.utils  # type: ignore[reportMissingImports]
from io import BytesIO
from PIL import Image

from comfy_execution.graph_utils import ExecutionBlocker  # type: ignore[reportMissingImports]
from .utils import tensor2pil, pil2tensor
from .config import get_config, get_current_base_url, API_PATHS


IMAGE_RATIOS = ["AUTO", "auto", "1:1", "16:9", "9:16", "4:3", "3:4", "21:9", "3:2", "2:3"]
IMAGE_SIZES = ["1K", "2K", "4K"]

PRO_MAX_IMAGES = 14
FLASH_MAX_IMAGES = 14
GPT_IMAGE2_MAX_IMAGES = 16
ALL_MAX_IMAGES = max(PRO_MAX_IMAGES, FLASH_MAX_IMAGES, GPT_IMAGE2_MAX_IMAGES)


class RelayImageGenerator:
    @classmethod
    def INPUT_TYPES(cls):
        optional = {
            "info": ("STRING", {"default": "", "forceInput": True}),
        }
        for i in range(1, ALL_MAX_IMAGES + 1):
            optional[f"image{i}"] = ("IMAGE",)

        return {
            "required": {
                "prompt": ("STRING", {"multiline": True}),
                "ratio": (IMAGE_RATIOS, {"default": "1:1"}),
                "size": (IMAGE_SIZES, {"default": "2K"}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff, "control_after_generate": True}),
            },
            "optional": optional,
        }

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "response", "image_url")
    FUNCTION = "generate_image"
    CATEGORY = "RelayAPI"

    def __init__(self):
        self.timeout = 120

    def _err(self, msg):
        full_msg = f"[RelayAPI] {msg}"
        print(full_msg)
        raise RuntimeError(full_msg)

    def _get_api_key(self, api_key):
        if api_key and api_key.strip():
            return api_key.strip()
        return get_config().get('api_key', '')

    def _image_to_base64(self, image_tensor):
        pil_image = tensor2pil(image_tensor)[0]
        buffered = BytesIO()
        pil_image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    def _image_to_bytes(self, image_tensor):
        pil_image = tensor2pil(image_tensor)[0]
        buffered = BytesIO()
        pil_image.save(buffered, format="PNG")
        return buffered.getvalue()

    # ══════════════════════════════════════
    #  native — Gemini 原生（多图 inline_data）
    # ══════════════════════════════════════
    def _gemini_generate(self, base_url, api_key, model, prompt, ratio, size,
                         images, seed, pbar):
        paths = API_PATHS.get("image_native_style", {})
        path_tpl = paths.get("generate", "/v1beta/models/{model}:generateContent")
        url = f"{base_url}{path_tpl.format(model=model)}"

        parts = [{"text": prompt}]

        for i, img in enumerate(images):
            pbar.update_absolute(15 + i * 2)
            b64 = self._image_to_base64(img)
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": b64,
                }
            })

        image_config = {}
        if ratio and ratio != "AUTO":
            image_config["aspectRatio"] = ratio
        if size and size != "AUTO":
            image_config["imageSize"] = size

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": image_config,
            },
        }

        pbar.update_absolute(40)
        print(f"[RelayAPI] POST {url} (Gemini native, {len(images)} images)")
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        pbar.update_absolute(75)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"Gemini error: {resp.status_code} - {resp.text[:500]}")
        return resp.json()

    # ══════════════════════════════════════
    #  openai — OpenAI Images 兼容
    # ══════════════════════════════════════
    def _gpt_image2_size(self, ratio, images):
        if ratio == "1:1":
            return "1024x1024"
        if ratio == "3:2":
            return "1536x1024"
        if ratio == "2:3":
            return "1024x1536"
        if ratio == "16:9":
            return "1755x896"
        if ratio == "9:16":
            return "896x1755"
        if ratio in ("AUTO", "auto") and images:
            img = tensor2pil(images[0])[0]
            width, height = img.size
            if width > height:
                return "1536x1024"
            if height > width:
                return "1024x1536"
            return "1024x1024"
        return "auto"

    def _gpt_image2_generate(self, base_url, api_key, model, prompt, ratio, images, pbar):
        paths = API_PATHS.get("image_native_style", {})
        image_size = self._gpt_image2_size(ratio, images)
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

        if images:
            url = f"{base_url}{paths.get('gpt_image2_edit', '/v1/images/edits')}"
            data_dict = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": "1",
            }
            files_list = []
            for i, img in enumerate(images[:GPT_IMAGE2_MAX_IMAGES]):
                pbar.update_absolute(15 + i * 2)
                img_bytes = self._image_to_bytes(img)
                files_list.append(
                    ("image", (f"image_{i+1}.png", BytesIO(img_bytes), "image/png"))
                )

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 edit, {len(files_list)} images, size={image_size})")
            resp = requests.post(url, headers=headers, data=data_dict,
                                 files=files_list, timeout=self.timeout)
        else:
            url = f"{base_url}{paths.get('gpt_image2_generate', '/v1/images/generations')}"
            payload = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": 1,
            }

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 create, size={image_size})")
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)

        pbar.update_absolute(75)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"gpt-image2 error: {resp.status_code} - {resp.text[:500]}")
        return resp.json()

    def _gpt_image2_openai_generate(self, base_url, api_key, model, prompt, ratio, images, pbar):
        paths = API_PATHS.get("image_openai_style", {})
        image_size = self._gpt_image2_size(ratio, images)
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

        if images:
            url = f"{base_url}{paths.get('edit', '/v1/images/edits')}"
            data_dict = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": "1",
            }
            files_list = []
            for i, img in enumerate(images[:GPT_IMAGE2_MAX_IMAGES]):
                pbar.update_absolute(15 + i * 2)
                img_bytes = self._image_to_bytes(img)
                files_list.append(
                    ("image", (f"image_{i+1}.png", BytesIO(img_bytes), "image/png"))
                )

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 openai edit, {len(files_list)} images, size={image_size})")
            resp = requests.post(url, headers=headers, data=data_dict,
                                 files=files_list, timeout=self.timeout)
        else:
            url = f"{base_url}{paths.get('generate', '/v1/images/generations')}"
            payload = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": 1,
            }

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 openai create, size={image_size})")
            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)

        pbar.update_absolute(75)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"gpt-image2 openai error: {resp.status_code} - {resp.text[:500]}")
        return resp.json()

    def _openai_text2img(self, base_url, api_key, model, prompt, ratio, size, seed, pbar):
        paths = API_PATHS.get("image_openai_style", {})
        url = f"{base_url}{paths.get('generate', '/v1/images/generations')}"

        payload = {
            "model": model,
            "prompt": prompt,
            "response_format": "url",
            "image_size": size,
            "n": 1,
        }
        if ratio and ratio != "AUTO":
            payload["aspect_ratio"] = ratio
        if seed > 0:
            payload["seed"] = seed

        pbar.update_absolute(40)
        print(f"[RelayAPI] POST {url} (OpenAI text2img)")
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout)
        pbar.update_absolute(75)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"Image create error: {resp.status_code} - {resp.text[:500]}")
        return resp.json()

    def _openai_edit(self, base_url, api_key, model, prompt, ratio, size,
                     images, seed, pbar):
        paths = API_PATHS.get("image_openai_style", {})
        url = f"{base_url}{paths.get('edit', '/v1/images/edits')}"

        data_dict = {
            "model": model,
            "prompt": prompt,
            "response_format": "url",
            "image_size": size,
            "n": "1",
        }
        if ratio and ratio != "AUTO":
            data_dict["aspect_ratio"] = ratio
        if seed > 0:
            data_dict["seed"] = str(seed)

        files_list = []
        for i, img in enumerate(images):
            pbar.update_absolute(15 + i * 2)
            img_bytes = self._image_to_bytes(img)
            files_list.append(
                ("image", (f"image_{i+1}.png", BytesIO(img_bytes), "image/png"))
            )

        pbar.update_absolute(40)
        print(f"[RelayAPI] POST {url} (OpenAI edit, {len(images)} images)")
        headers = {"Authorization": f"Bearer {api_key}"}
        resp = requests.post(url, headers=headers, data=data_dict,
                             files=files_list, timeout=self.timeout)
        pbar.update_absolute(75)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"Image edit error: {resp.status_code} - {resp.text[:500]}")
        return resp.json()

    # ══════════════════════════════════════
    #  提取结果
    # ══════════════════════════════════════
    def _extract_image(self, result):
        data_list = result.get("data", [])
        if data_list:
            item = data_list[0]
            img_url = (item.get("url")
                       or (item.get("image_url") or {}).get("url", "")
                       or item.get("output_url")
                       or item.get("download_url"))
            if img_url:
                return "url", img_url

            b64 = item.get("b64_json", "")
            if b64:
                return "base64", b64

        candidates = result.get("candidates", [])
        for c in candidates:
            parts = (c.get("content") or {}).get("parts", [])
            for p in parts:
                inline = p.get("inlineData") or p.get("inline_data") or {}
                if inline.get("data"):
                    return "base64", inline["data"]

        self._err(f"No image in response: {json.dumps(result)[:500]}")

    def _download_image(self, url):
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        return pil2tensor(img)

    def _base64_to_tensor(self, b64_data):
        img_bytes = base64.b64decode(b64_data)
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        return pil2tensor(img)

    # ══════════════════════════════════════
    #  主入口
    # ══════════════════════════════════════
    def generate_image(self, prompt, ratio, size, seed, info="", **kwargs):
        parsed = {}
        if info and info.strip():
            try:
                parsed = json.loads(info)
            except Exception:
                pass

        try:
            api_key = self._get_api_key(parsed.get("apikey", ""))
            if not api_key:
                self._err("API key not found. Please set via Relay API Settings node.")

            raw_base = parsed.get("api_base", "")
            base_url = raw_base.strip().rstrip('/') if raw_base.strip() else get_current_base_url()
            model = parsed.get("model", "")
            api_format = parsed.get("api_format", "openai_style")
            platform = parsed.get("platform", "banana-pro")
            print(f"[RelayAPI] image | {platform} | {api_format} | {base_url} | {model}")

            images = []
            for i in range(1, ALL_MAX_IMAGES + 1):
                img = kwargs.get(f"image{i}")
                if img is not None:
                    images.append(img)

            has_images = len(images) > 0

            pbar = comfy.utils.ProgressBar(100)
            pbar.update_absolute(10)

            if platform == "gpt-image2" and api_format == "openai_style":
                result = self._gpt_image2_openai_generate(
                    base_url, api_key, model, prompt, ratio, images, pbar,
                )
            elif platform == "gpt-image2":
                result = self._gpt_image2_generate(
                    base_url, api_key, model, prompt, ratio, images, pbar,
                )
            elif api_format == "native_style":
                result = self._gemini_generate(
                    base_url, api_key, model, prompt, ratio, size,
                    images, seed, pbar,
                )
            else:
                if has_images:
                    result = self._openai_edit(
                        base_url, api_key, model, prompt, ratio, size,
                        images, seed, pbar,
                    )
                else:
                    result = self._openai_text2img(
                        base_url, api_key, model, prompt, ratio, size, seed, pbar,
                    )

            pbar.update_absolute(80)
            img_type, img_data = self._extract_image(result)

            if img_type == "url":
                print(f"[RelayAPI] Downloading image: {img_data}")
                img_tensor = self._download_image(img_data)
                pbar.update_absolute(100)
                resp_json = json.dumps({"code": "success", "url": img_data})
                return (img_tensor, resp_json, img_data)
            else:
                img_tensor = self._base64_to_tensor(img_data)
                pbar.update_absolute(100)
                resp_json = json.dumps({"code": "success", "type": "base64"})
                return (img_tensor, resp_json, "")

        except Exception as e:
            error_resp = json.dumps({"code": "error", "message": str(e)}, ensure_ascii=False)
            return (ExecutionBlocker(None), error_resp, "")
