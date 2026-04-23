from .nodes_api_settings import RelayAPISettings
from .nodes_video_generator import RelayVideoGenerator
from .nodes_image_generator import RelayImageGenerator
from .nodes_notice import RelayAPINotice
from .nodes_sound_generator import RelaySoundGenerator
from .nodes_text_generator import RelayTextGenerator

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
    "RelaySoundGenerator": RelaySoundGenerator,
    "RelayTextGenerator": RelayTextGenerator,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "RelayAPISettings": "Relay API Settings",
    "RelayVideoGenerator": "Relay Video Generator",
    "RelayImageGenerator": "Relay Image Generator",
    "RelayAPINotice": "Relay API Notice",
    "RelaySoundGenerator": "Relay Sound Generator",
    "RelayTextGenerator": "Relay Text Generator",
}

WEB_DIRECTORY = "./js"

__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']
