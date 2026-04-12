from .nodes_api_settings import RelayAPISettings
from .nodes_video_generator import RelayVideoGenerator
from .nodes_image_generator import RelayImageGenerator
from .nodes_notice import RelayAPINotice

try:
    from .config import register_routes
    register_routes()
except Exception:
    pass

NODE_CLASS_MAPPINGS = {
    "RelayAPISettings": RelayAPISettings,
    "RelayVideoGenerator": RelayVideoGenerator,
    "RelayImageGenerator": RelayImageGenerator,
    "RelayAPINotice": RelayAPINotice,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RelayAPISettings": "Relay API Settings",
    "RelayVideoGenerator": "Relay Video Generator",
    "RelayImageGenerator": "Relay Image Generator",
    "RelayAPINotice": "Relay API Notice",
}

WEB_DIRECTORY = "./js"

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']
