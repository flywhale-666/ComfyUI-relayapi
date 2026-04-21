import json
from .config import (
    get_config, get_api_base_list,
    add_custom_api_base, add_custom_model,
    set_current_base_url, get_node_api_key, save_node_settings,
    PLATFORMS, TASK_TYPES, ALL_API_FORMATS, DEFAULT_MODELS, FORMAT_MODELS,
)


def _all_models():
    """收集所有平台、所有 format 的模型合集（含用户自定义），用于 ComfyUI 验证"""
    seen = []
    for plat in PLATFORMS:
        for m in DEFAULT_MODELS.get(plat, []):
            if m not in seen:
                seen.append(m)
        if plat in FORMAT_MODELS:
            for fmt_models in FORMAT_MODELS[plat].values():
                for m in fmt_models:
                    if m not in seen:
                        seen.append(m)
    config = get_config()
    custom_models = config.get('custom_models', {})
    for plat_models in custom_models.values():
        for m in plat_models:
            m = m.strip()
            if m and m not in seen:
                seen.append(m)
    return seen if seen else [""]


class RelayAPISettings:
    @classmethod
    def INPUT_TYPES(cls):
        api_base_list = get_api_base_list()
        all_models = _all_models()
        return {
            "required": {
                "task_type": (TASK_TYPES, {"default": "video"}),
                "platform": (PLATFORMS, {"default": PLATFORMS[0]}),
                "api_format": (ALL_API_FORMATS, {"default": "native_style"}),
                "api_base": (api_base_list, {"default": api_base_list[0]}),
                "model": (all_models, {"default": all_models[0]}),
                "apikey": ("STRING", {"default": ""}),
            },
            "optional": {
                "custom_api_base": ("STRING", {
                    "default": "",
                    "placeholder": "输入地址添加 | 输入 delete:地址 删除",
                }),
                "custom_model": ("STRING", {
                    "default": "",
                    "placeholder": "输入模型名添加 | 输入 delete:模型名 删除",
                }),
            },
            "hidden": {
                "unique_id": "UNIQUE_ID",
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("info",)
    FUNCTION = "set_api"
    CATEGORY = "RelayAPI"

    @classmethod
    def VALIDATE_INPUTS(cls, model=None, api_base=None, **kwargs):
        return True

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("NaN")

    def set_api(self, task_type, platform, api_format, api_base, model, apikey="",
                custom_api_base="", custom_model="", unique_id=None):
        custom_api_base = custom_api_base.strip().rstrip('/')
        custom_model = custom_model.strip()

        if custom_api_base:
            base_url = custom_api_base
            add_custom_api_base(custom_api_base)
        else:
            base_url = api_base

        is_bltcy_gpt_image2 = (
            platform == "gpt-image2"
            and base_url.strip().rstrip('/').lower() == "https://api.bltcy.ai"
        )
        if is_bltcy_gpt_image2:
            api_format = "openai_style"
        elif platform == "gpt-image2":
            api_format = "native_style"

        plain_apikey = apikey.strip()
        has_plain_apikey = bool(plain_apikey and plain_apikey.isascii() and "\u2022" not in plain_apikey)

        if custom_model:
            used_model = custom_model
            add_custom_model(platform, custom_model)
        elif is_bltcy_gpt_image2:
            used_model = "gpt-image-2"
        else:
            used_model = model

        set_current_base_url(base_url)

        if has_plain_apikey:
            save_node_settings(unique_id, api_key=plain_apikey, base_url=base_url)
        elif unique_id is not None:
            save_node_settings(unique_id, base_url=base_url)

        if has_plain_apikey:
            real_key = plain_apikey
        else:
            real_key = get_node_api_key(unique_id)

        info = json.dumps({
            "apikey": real_key,
            "api_base": base_url,
            "model": used_model,
            "platform": platform,
            "api_format": api_format,
            "task_type": task_type,
        })

        print(f"[RelayAPI] {task_type} | {platform} | {api_format} | {base_url} | {used_model}")

        return (info,)
