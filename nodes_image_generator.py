import json
import time
import base64
import requests
import comfy.utils  # type: ignore[reportMissingImports]
from io import BytesIO
from PIL import Image

from comfy_execution.graph_utils import ExecutionBlocker  # type: ignore[reportMissingImports]
from .utils import tensor2pil, pil2tensor
from .config import get_config, get_current_base_url, API_PATHS


def _post_with_timing(label, session_post_kwargs):
    """封装带细分耗时的 POST。
    返回 (resp, t_ttfb, t_body, first_chunk_dt, chunks_trace)
    - t_ttfb: 从发请求到收到响应头的时间（服务端"憋"的时间）
    - t_body: 从响应头到读完整个 body 的时间（带宽/CDN 传输的时间）
    - first_chunk_dt: 从收到响应头到第一个 body chunk 的时间（若 >0 明显 > 0，
      说明中转即使回了 200 头也还在流式憋 body）
    - chunks_trace: 关键 chunk 的采样时间戳（秒, 距离开始发请求的相对时间）
    """
    url = session_post_kwargs.pop("url")
    t0 = time.time()
    resp = requests.post(url, stream=True, **session_post_kwargs)
    t_ttfb = time.time() - t0

    # 流式读 body，记录第一个 chunk 到来的时间，以及每 1MB 的时间戳，
    # 方便肉眼看出中间有没有"憋一段、再出一段"的阶梯式拖延
    buf = bytearray()
    first_chunk_at = None
    chunks_trace = []
    next_mark = 1024 * 1024  # 每 1MB 打一次点
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        if not chunk:
            continue
        if first_chunk_at is None:
            first_chunk_at = time.time() - t0
        buf.extend(chunk)
        if len(buf) >= next_mark:
            chunks_trace.append((len(buf), time.time() - t0))
            next_mark += 1024 * 1024

    t_total = time.time() - t0
    t_body = t_total - t_ttfb
    first_chunk_dt = (first_chunk_at - t_ttfb) if first_chunk_at is not None else 0.0

    # 把流式读出来的内容塞回 resp，让下游 resp.content / resp.text / resp.json() 照常工作
    resp._content = bytes(buf)

    trace_txt = " | ".join(f"{sz/1024/1024:.1f}MB@{t:.1f}s" for sz, t in chunks_trace[:6])
    print(
        f"[RelayAPI][{label}] ttfb={t_ttfb:.1f}s body={t_body:.1f}s "
        f"firstChunkAfterHdr={first_chunk_dt:.1f}s size={len(buf)/1024:.1f}KB"
        + (f" | trace: {trace_txt}" if trace_txt else "")
    )

    return resp


# 统一的比例列表（小写 auto）。三个平台都用这一个列表：
#   - gpt-image2：通过 GPT_IMAGE2_RATIO_SIZES 映射成具体像素带入
#   - banana-pro / banana-2：直接把比例字符串当 aspect_ratio / aspectRatio 发
IMAGE_RATIOS = ["auto", "1:1", "2:3", "3:2", "4:3", "3:4", "9:16", "16:9", "9:21", "21:9"]

# gpt-image2（taikuaila 的 gpt-image-2-all）实测档位：传入=输出
# key 为节点下拉里的比例，value 为该比例对应的像素尺寸
GPT_IMAGE2_RATIO_SIZES = {
    "1:1":  "1254x1254",
    "3:2":  "1536x1024",
    "2:3":  "1024x1536",
    "4:3":  "1448x1086",
    "3:4":  "1086x1448",
    "16:9": "1755x896",
    "9:16": "896x1755",
    "21:9": "1915x821",
    "9:21": "821x1915",
}
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
        if ratio and ratio != "auto":
            image_config["aspectRatio"] = ratio
        if size and size != "auto":
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
        # 命中具体比例时直接查表
        if ratio in GPT_IMAGE2_RATIO_SIZES:
            return GPT_IMAGE2_RATIO_SIZES[ratio]

        # auto + 有参考图：按参考图的宽高比从所有档位里挑最接近的
        if ratio == "auto" and images:
            img = tensor2pil(images[0])[0]
            w, h = img.size
            if h <= 0:
                return "1254x1254"
            target = w / h
            best_key = min(
                GPT_IMAGE2_RATIO_SIZES.keys(),
                key=lambda k: abs(
                    (int(GPT_IMAGE2_RATIO_SIZES[k].split("x")[0])
                     / int(GPT_IMAGE2_RATIO_SIZES[k].split("x")[1])) - target
                ),
            )
            return GPT_IMAGE2_RATIO_SIZES[best_key]

        # AUTO 无参考图，或其它未识别值，交给 API 自行决定
        return "auto"

    def _gpt_image2_generate(self, base_url, api_key, model, prompt, ratio, images, pbar):
        paths = API_PATHS.get("image_native_style", {})
        image_size = self._gpt_image2_size(ratio, images)
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}

        if images:
            url = f"{base_url}{paths.get('gpt_image2_edit', '/v1/images/edits')}"
            # 显式要 b64_json：实测三家中转里
            #   - bltc / t8star：url 模式会走慢速通道（或不支持），120s 内跑不完；
            #                    b64 模式则走快速通道，bltc ~70s、t8star ~100s 稳定出图
            #   - taikuaila：b64 / url 都能通，b64 稍慢但稳定
            # 所以统一用 b64_json 保三家都稳
            data_dict = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": "1",
                "response_format": "b64_json",
            }
            files_list = []
            for i, img in enumerate(images[:GPT_IMAGE2_MAX_IMAGES]):
                pbar.update_absolute(15 + i * 2)
                img_bytes = self._image_to_bytes(img)
                # OpenAI 官方多图编辑字段名是 image[]；单图也兼容
                files_list.append(
                    ("image[]", (f"image_{i+1}.png", BytesIO(img_bytes), "image/png"))
                )

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 edit, {len(files_list)} images, size={image_size})")
            resp = _post_with_timing("gpt-image2 edit", {
                "url": url, "headers": headers, "data": data_dict,
                "files": files_list, "timeout": self.timeout,
            })
        else:
            url = f"{base_url}{paths.get('gpt_image2_generate', '/v1/images/generations')}"
            # 同 edit 分支的理由：显式要 b64_json，三家都稳
            payload = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": 1,
                "response_format": "b64_json",
            }

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 create, size={image_size})")
            resp = _post_with_timing("gpt-image2 create", {
                "url": url, "headers": headers, "json": payload,
                "timeout": self.timeout,
            })

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
            # 显式要 b64_json，理由见上方 _gpt_image2_generate 的注释
            data_dict = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": "1",
                "response_format": "b64_json",
            }
            files_list = []
            for i, img in enumerate(images[:GPT_IMAGE2_MAX_IMAGES]):
                pbar.update_absolute(15 + i * 2)
                img_bytes = self._image_to_bytes(img)
                # OpenAI 官方多图编辑字段名是 image[]；单图也兼容
                files_list.append(
                    ("image[]", (f"image_{i+1}.png", BytesIO(img_bytes), "image/png"))
                )

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 openai edit, {len(files_list)} images, size={image_size})")
            resp = _post_with_timing("gpt-image2 openai edit", {
                "url": url, "headers": headers, "data": data_dict,
                "files": files_list, "timeout": self.timeout,
            })
        else:
            url = f"{base_url}{paths.get('generate', '/v1/images/generations')}"
            # 显式要 b64_json，理由见上方 _gpt_image2_generate 的注释
            payload = {
                "model": model,
                "prompt": prompt,
                "size": image_size,
                "n": 1,
                "response_format": "b64_json",
            }

            pbar.update_absolute(40)
            print(f"[RelayAPI] POST {url} (gpt-image2 openai create, size={image_size})")
            resp = _post_with_timing("gpt-image2 openai create", {
                "url": url, "headers": headers, "json": payload,
                "timeout": self.timeout,
            })

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
        if ratio and ratio != "auto":
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
        if ratio and ratio != "auto":
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
    def _extract_image(self, result, b64_only=False):
        """抽出响应里的图数据，返回 (type, data)。
        type 只会是 "base64" 或 "url"。
        b64_only=True 时只允许返 b64（gpt-image2 场景用 —— 我们已明确
        要了 response_format=b64_json，不再做 URL 兜底）。
        """
        data_list = result.get("data", [])
        if data_list:
            item = data_list[0]
            # 先找 b64_json：请求时明确要了 response_format=b64_json；中间有时
            # 响应里同时带 url 和 b64_json，优先用 b64 省掉一次 CDN 下载
            # （taikuaila 的 b64_json 里会带 data:image/webp;base64, 前缀，
            # _base64_to_tensor 里已经做了剥离处理）
            b64 = item.get("b64_json", "")
            if b64:
                return "base64", b64

            if not b64_only:
                img_url = (item.get("url")
                           or (item.get("image_url") or {}).get("url", "")
                           or item.get("output_url")
                           or item.get("download_url"))
                if img_url:
                    return "url", img_url

        # 下面这几种兜底格式（Gemini candidates / chat choices）在 b64_only
        # 模式下只接受 base64，不接受 url/markdown 这种
        candidates = result.get("candidates", [])
        for c in candidates:
            parts = (c.get("content") or {}).get("parts", [])
            for p in parts:
                inline = p.get("inlineData") or p.get("inline_data") or {}
                if inline.get("data"):
                    return "base64", inline["data"]

        # 兼容部分中转：把图塞在 choices[*].message.content 里，
        # 格式可能是 markdown ![alt](url)、纯 url、或 data:image/...;base64,xxx
        choices = result.get("choices", [])
        for c in choices:
            content = (c.get("message") or {}).get("content", "")
            if not isinstance(content, str) or not content:
                continue
            import re
            # data URI 是 base64 的变种，b64_only 也允许
            m = re.search(r"data:image/[a-zA-Z0-9.+-]+;base64,([A-Za-z0-9+/=\s]+)", content)
            if m:
                return "base64", m.group(1).strip()
            if b64_only:
                continue
            # markdown 图片 / 裸 url，只在允许 url 兜底时才认
            m = re.search(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", content)
            if m:
                return "url", m.group(1)
            m = re.search(r"https?://\S+?\.(?:png|jpg|jpeg|webp|gif)(?:\?\S*)?", content, re.I)
            if m:
                return "url", m.group(0)

        if b64_only:
            self._err(
                f"gpt-image2 响应里没有 b64_json 字段（已明确要求 response_format=b64_json，"
                f"但中转返了别的结构）：{json.dumps(result)[:500]}"
            )
        self._err(f"No image in response: {json.dumps(result)[:500]}")

    def _download_image(self, url):
        t0 = time.time()
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        content = resp.content
        print(f"[RelayAPI] download {len(content)/1024:.1f}KB in {time.time()-t0:.1f}s | {url}")
        try:
            img = Image.open(BytesIO(content)).convert("RGB")
        except Exception as e:
            # 下载回来的不是图：通常是 HTML 错误页 / 重定向 / 半截文件。
            # 把 content-type 和前 200 字节打出来，方便定位是中转的哪一步翻车
            ctype = resp.headers.get("Content-Type", "")
            head_txt = content[:200].decode("utf-8", errors="replace")
            self._err(
                f"下载到的内容不是图片 (url={url}, status={resp.status_code}, "
                f"content-type={ctype}, size={len(content)}B)\n"
                f"前 200 字节：{head_txt}\n原始错误：{e}"
            )
        return pil2tensor(img)

    def _base64_to_tensor(self, b64_data):
        # 去掉可能存在的 data URI 前缀，例如 data:image/png;base64,
        s = b64_data.strip()
        if s.startswith("data:"):
            comma = s.find(",")
            if comma != -1:
                s = s[comma + 1:]
        # base64 里允许有空白/换行，decode 前先清掉
        s = "".join(s.split())

        try:
            img_bytes = base64.b64decode(s, validate=False)
        except Exception as e:
            self._err(
                f"base64 解码失败：{e}\n"
                f"原始前 200 字符：{b64_data[:200]!r}"
            )

        try:
            img = Image.open(BytesIO(img_bytes)).convert("RGB")
        except Exception as e:
            # 打印解码出的二进制头部，判断是不是图（PNG: 89 50 4E 47；JPEG: FF D8 FF）
            head_hex = img_bytes[:16].hex(" ")
            head_txt = img_bytes[:200].decode("utf-8", errors="replace")
            self._err(
                f"base64 解出的内容不是图片 (bytes={len(img_bytes)})\n"
                f"前 16 字节 hex：{head_hex}\n"
                f"前 200 字节文本：{head_txt}\n原始错误：{e}"
            )
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
            t_total_start = time.time()

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

            t_api = time.time() - t_total_start
            pbar.update_absolute(80)
            # gpt-image2 明确要求走 b64_json（url 模式在 t8star/bltc 会超时），
            # 不再做 URL 兜底，响应里没 b64 就直接报错
            img_type, img_data = self._extract_image(
                result, b64_only=(platform == "gpt-image2")
            )

            if img_type == "url":
                print(f"[RelayAPI] Downloading image: {img_data}")
                t_dec0 = time.time()
                img_tensor = self._download_image(img_data)
                t_dec = time.time() - t_dec0
                pbar.update_absolute(100)
                resp_json = json.dumps({"code": "success", "url": img_data})
                print(f"[RelayAPI] TIMING total={time.time()-t_total_start:.1f}s api={t_api:.1f}s decode(url)={t_dec:.1f}s")
                return (img_tensor, resp_json, img_data)
            else:
                t_dec0 = time.time()
                img_tensor = self._base64_to_tensor(img_data)
                t_dec = time.time() - t_dec0
                pbar.update_absolute(100)
                resp_json = json.dumps({"code": "success", "type": "base64"})
                print(f"[RelayAPI] TIMING total={time.time()-t_total_start:.1f}s api={t_api:.1f}s decode(b64)={t_dec:.1f}s")
                return (img_tensor, resp_json, "")

        except Exception as e:
            error_resp = json.dumps({"code": "error", "message": str(e)}, ensure_ascii=False)
            return (ExecutionBlocker(None), error_resp, "")
