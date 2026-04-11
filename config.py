import os
import json
from aiohttp import web

CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'relay_config.json')

DEFAULT_API_BASES = [
    "https://www.taikuaila.cn",
    "https://ai.t8star.cn",
]

TASK_TYPES = ["video", "image", "sound", "other"]

DEFAULT_MODELS = {
    # video
    "Grok": ["grok-video-3"],
    "Veo": ["veo3.1-fast", "veo3.1", "veo_3_1-lite", "veo_3_1-lite-4K"],
    # image — 通用 fallback
    "banana-pro": ["nano-banana-pro"],
    "banana-2": ["gemini-3.1-flash-image-preview"],
}

FORMAT_MODELS = {
    "banana-pro": {
        "native_style": ["gemini-3-pro-image-preview"],
        "openai_style": ["nano-banana-pro"],
    },
}

TASK_PLATFORMS = {
    "video": ["Grok", "Veo"],
    "image": ["banana-pro", "banana-2"],
    "sound": [],
    "other": [],
}

PLATFORMS = list(DEFAULT_MODELS.keys())

VIDEO_API_FORMATS = ["native_style", "openai_style"]
IMAGE_API_FORMATS = ["native_style", "openai_style"]

API_FORMATS_BY_TASK = {
    "video": VIDEO_API_FORMATS,
    "image": IMAGE_API_FORMATS,
    "sound": ["native_style"],
    "other": ["native_style"],
}

ALL_API_FORMATS = list(dict.fromkeys(VIDEO_API_FORMATS + IMAGE_API_FORMATS))

API_PATHS = {
    "video_native_style": {
        "grok_create": "/v1/video/create",
        "grok_query": "/v1/video/query?id={task_id}",
        "veo_create": "/v1/video/create",
        "veo_query": "/v1/video/query?id={task_id}",
    },
    "video_openai_style": {
        "grok_create": "/v2/videos/generations",
        "grok_query": "/v2/videos/generations/{task_id}",
        "veo_create": "/v2/videos/generations",
        "veo_query": "/v2/videos/generations/{task_id}",
    },
    "image_native_style": {
        "generate": "/v1beta/models/{model}:generateContent",
        "edit": "/v1beta/models/{model}:generateContent",
    },
    "image_openai_style": {
        "generate": "/v1/images/generations",
        "edit": "/v1/images/edits",
    },
}


def get_config():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


def get_api_base_list():
    config = get_config()
    custom_bases = config.get('custom_api_bases', [])
    removed_defaults = config.get('removed_defaults', [])
    all_bases = [b for b in DEFAULT_API_BASES if b not in removed_defaults]
    for base in custom_bases:
        base = base.strip().rstrip('/')
        if base and base not in all_bases:
            all_bases.append(base)
    if not all_bases:
        all_bases = list(DEFAULT_API_BASES)
    return all_bases


def add_custom_api_base(url):
    url = url.strip().rstrip('/')
    if not url:
        return
    config = get_config()
    custom_bases = config.get('custom_api_bases', [])
    removed_defaults = config.get('removed_defaults', [])
    if url in removed_defaults:
        removed_defaults.remove(url)
        config['removed_defaults'] = removed_defaults
    if url not in custom_bases and url not in DEFAULT_API_BASES:
        custom_bases.append(url)
        config['custom_api_bases'] = custom_bases
    save_config(config)


def get_model_list(platform, api_format=None):
    config = get_config()
    custom_models = config.get('custom_models', {}).get(platform, [])
    removed_models = config.get('removed_models', {}).get(platform, [])

    if api_format and platform in FORMAT_MODELS and api_format in FORMAT_MODELS[platform]:
        defaults = FORMAT_MODELS[platform][api_format]
    else:
        defaults = DEFAULT_MODELS.get(platform, [])

    all_models = [m for m in defaults if m not in removed_models]
    for m in custom_models:
        m = m.strip()
        if m and m not in all_models:
            all_models.append(m)
    if not all_models:
        all_models = list(defaults)
    return all_models


def add_custom_model(platform, model):
    model = model.strip()
    if not model:
        return
    config = get_config()
    custom_models = config.get('custom_models', {})
    removed_models = config.get('removed_models', {})
    if platform not in custom_models:
        custom_models[platform] = []
    if platform in removed_models and model in removed_models[platform]:
        removed_models[platform].remove(model)
        config['removed_models'] = removed_models
    defaults = DEFAULT_MODELS.get(platform, [])
    if model not in custom_models[platform] and model not in defaults:
        custom_models[platform].append(model)
    config['custom_models'] = custom_models
    save_config(config)


def remove_model(platform, model):
    model = model.strip()
    if not model:
        return
    config = get_config()
    custom_models = config.get('custom_models', {})
    removed_models = config.get('removed_models', {})
    if platform in custom_models and model in custom_models[platform]:
        custom_models[platform].remove(model)
        config['custom_models'] = custom_models
    defaults = DEFAULT_MODELS.get(platform, [])
    if model in defaults:
        if platform not in removed_models:
            removed_models[platform] = []
        if model not in removed_models[platform]:
            removed_models[platform].append(model)
        config['removed_models'] = removed_models
    save_config(config)


def get_current_base_url():
    config = get_config()
    return config.get('base_url', DEFAULT_API_BASES[0])


def set_current_base_url(url):
    config = get_config()
    config['base_url'] = url
    save_config(config)


def register_routes():
    try:
        from server import PromptServer
        if not hasattr(PromptServer, 'instance') or PromptServer.instance is None:
            print("[RelayAPI] PromptServer not ready, skipping route registration")
            return
    except ImportError:
        print("[RelayAPI] Cannot import PromptServer, skipping route registration")
        return

    @PromptServer.instance.routes.get("/relayapi/api_bases")
    async def get_api_bases(request):
        return web.json_response(get_api_base_list())

    @PromptServer.instance.routes.post("/relayapi/api_bases/add")
    async def api_base_add(request):
        data = await request.json()
        url = data.get("url", "")
        if url.strip():
            add_custom_api_base(url)
            return web.json_response({"success": True, "list": get_api_base_list()})
        return web.json_response({"success": False, "message": "URL is empty"}, status=400)

    @PromptServer.instance.routes.post("/relayapi/api_bases/remove")
    async def api_base_remove(request):
        data = await request.json()
        url = data.get("url", "").strip().rstrip('/')
        if not url:
            return web.json_response({"success": False, "message": "URL is empty"}, status=400)
        config = get_config()
        custom_bases = config.get('custom_api_bases', [])
        removed_defaults = config.get('removed_defaults', [])
        if url in custom_bases:
            custom_bases.remove(url)
            config['custom_api_bases'] = custom_bases
        if url in DEFAULT_API_BASES and url not in removed_defaults:
            removed_defaults.append(url)
            config['removed_defaults'] = removed_defaults
        save_config(config)
        return web.json_response({"success": True, "list": get_api_base_list()})

    @PromptServer.instance.routes.get("/relayapi/models")
    async def get_models(request):
        platform = request.rel_url.query.get("platform", "Grok")
        api_format = request.rel_url.query.get("api_format", "")
        return web.json_response(get_model_list(platform, api_format or None))

    @PromptServer.instance.routes.post("/relayapi/models/add")
    async def model_add(request):
        data = await request.json()
        platform = data.get("platform", "Grok")
        model = data.get("model", "")
        if model.strip():
            add_custom_model(platform, model)
            return web.json_response({"success": True, "list": get_model_list(platform)})
        return web.json_response({"success": False, "message": "Model is empty"}, status=400)

    @PromptServer.instance.routes.post("/relayapi/models/remove")
    async def model_remove(request):
        data = await request.json()
        platform = data.get("platform", "Grok")
        model = data.get("model", "")
        if model.strip():
            remove_model(platform, model)
            return web.json_response({"success": True, "list": get_model_list(platform)})
        return web.json_response({"success": False, "message": "Model is empty"}, status=400)
