class RelayAPINotice:
    MESSAGE = (
        "api_format： 请参考中转平台的API文档，基本都是2选1，如果碰到不能用，切换一下就可以了。\n"
        "       图像：gemini_style 指谷歌原生格式。\n"
        "       图像：relay_api_style 指中转站的格式。\n"
        "       视频和声音：relay_V1_V2_style 指不同中转站的格式。\n"
        "       文本：gemini_style 指谷歌原生格式。\n"
        "       文本：openai_style 指OPENAI的格式。\n"
        "添加模型：在 custom_model 填入新模型名\n"
        "添加 baseurl：在 custom_api_base 填入新地址\n"
        "删除模型或 baseurl：\n"
        "    1. 请使用命令： delete: xxxx\n"
        "    2. xxxx 为要删除的模型名或 baseurl\n"
        "    3. 要删除的模型填在 custom_model，\n"
        "        要删除 baseurl 填在 custom_api_base。\n"
        "如有中转平台好用，可联系我添加平台格式"
    )

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "message": ("STRING", {
                    "default": cls.MESSAGE,
                    "multiline": True,
                }),
            },
        }

    RETURN_TYPES = ()
    FUNCTION = "show_notice"
    CATEGORY = "RelayAPI"

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return False

    def show_notice(self, message):
        return ()
