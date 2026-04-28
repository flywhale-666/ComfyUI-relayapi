class RelayAPINotice:
    MESSAGE = (
        "api_format 使用模型需要选择正确的端点名称，参考中转站的API文档\n"
        "图片: v1beta/models || v1/chat/completions || v1/images\n"
        "视频: v1/video || v1/videos || v2/videos\n"
        "声音: suno/submit\n"
        "文本: v1beta/models || v1/chat/completions\n"
        "添加模型: 在custom_model里输入模型名称.\n"
        "添加baseurl:在custom_api_base里输入中转站网址.\n"
        "删除模型或baseurl: 在对应的自定义字段里输入命令 delete:xxxx.\n"
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
    FUNCTION = "show"
    CATEGORY = "RelayAPI"

    def show(self, message):
        return ()
