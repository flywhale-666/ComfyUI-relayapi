# ComfyUI-relayapi

通过 API 中转站（Relay Station）调用主流 AI 生成服务的 ComfyUI 自定义节点包，支持 **视频生成** 和 **图像生成**。

## 功能概览

| 节点 | 说明 |
|------|------|
| **Relay API Settings** | 统一配置 API 中转站地址、密钥、平台和模型 |
| **Relay Video Generator** | 通过中转站生成视频（支持 Grok / Veo 平台） |
| **Relay Image Generator** | 通过中转站生成或编辑图像（支持 banana-pro / banana-2 / gpt-image2 平台） |

## 安装

将本仓库克隆或复制到 ComfyUI 的 `custom_nodes` 目录下：

```
ComfyUI/custom_nodes/ComfyUI-relayapi/
```

安装依赖：

```bash
pip install -r requirements.txt
```

> 如果你使用的是 ComfyUI 便携版（Embedded Python），大部分依赖已经预装，一般无需额外安装。

重启 ComfyUI 即可在节点菜单的 **RelayAPI** 分类下找到所有节点。

## 节点说明

### Relay API Settings

所有生成器节点的前置配置节点，通过 `info` 输出口将配置传递给下游节点。

| 参数 | 说明 |
|------|------|
| **task_type** | 任务类型：`video`、`image`、`sound`、`other` |
| **platform** | 平台选择，跟随 task_type 自动切换。视频：`Grok` / `Veo`；图像：`banana-pro` / `banana-2` / `gpt-image2` |
| **api_format** | API 协议格式：`native_style`（平台原生风格）/ `openai_style`（OpenAI 兼容风格） |
| **api_base** | 中转站地址，支持下拉选择或通过 `custom_api_base` 手动添加 |
| **model** | 模型名称，跟随平台和格式自动刷新，也可通过 `custom_model` 手动添加 |
| **apikey** | API 密钥，输入后自动保存到本地配置文件，界面上以部分遮盖形式显示（前后各保留 6 位） |

**管理地址和模型：**
- 在 `custom_api_base` 中输入新地址后回车即可添加到下拉列表
- 输入 `delete:地址` 可删除对应地址
- `custom_model` 的添加和删除方式相同

---

### Relay Video Generator

通过中转站调用 Grok 或 Veo 平台生成视频。

| 参数 | 说明 |
|------|------|
| **prompt** | 视频描述提示词 |
| **ratio** | 宽高比。Grok 支持 `AUTO`、`16:9`、`9:16`、`1:1` 等；Veo 支持 `16:9`、`9:16` |
| **size** | 分辨率。Grok：`720P` / `480P`；Veo：`720P` / `1080P` |
| **duration** | 视频时长（秒）。Grok：`6` / `10`；Veo：`4` / `6` / `8` |
| **image1~7** | 可选参考图片输入（Grok 最多 7 张，Veo 最多 3 张） |
| **seed** | 随机种子，支持 ComfyUI 的 `control_after_generate` |

**输出：**
- `video` — 生成的视频
- `task_id` — 任务 ID
- `response` — API 返回的完整响应（JSON）
- `video_url` — 视频下载链接

> 连接参考图片时，ratio 会自动切换为 `AUTO`；未连接图片时默认 `16:9`。

![视频生成示例](assets/screenshot_video.png)

---

### Relay Image Generator

通过中转站调用 Gemini 模型生成或编辑图像。

- **未连接图片** → 文生图模式
- **连接图片** → 图像编辑模式

| 参数 | 说明 |
|------|------|
| **prompt** | 图像描述或编辑指令 |
| **ratio** | 宽高比，默认 `1:1`（无图时）或 `AUTO`（有图时） |
| **size** | 输出尺寸：`1K` / `2K` / `4K`，默认 `2K` |
| **image1~16** | 可选输入图片（banana-pro 最多 14 张，banana-2 最多 14 张，gpt-image2 最多 16 张） |
| **seed** | 随机种子 |

> `gpt-image2` 的 `ratio` 下拉仅显示 `auto` / `1:1` / `3:2` / `2:3` / `16:9` / `9:16`，`size` 下拉仅显示 `1K`；实际 API 尺寸分别映射为 `auto`（有 `image1` 时按其宽高比选择最大输出尺寸）、`1024x1024`、`1536x1024`、`1024x1536`、`1755x896`、`896x1755`。

**输出：**
- `image` — 生成的图像
- `response` — API 返回的完整响应（JSON）
- `image_url` — 图像下载链接

**平台与模型对应关系：**

| 平台 | api_format | 模型 |
|------|-----------|------|
| banana-pro | native_style | gemini-3-pro-image-preview |
| banana-pro | openai_style | nano-banana-pro |
| banana-2 | native_style / openai_style | gemini-3.1-flash-image-preview |
| gpt-image2 | native_style | gpt-image-2-all |
| gpt-image2 | openai_style | gpt-image-2 |

![图像生成示例](assets/screenshot_image.png)

---

## 错误处理

所有 API 错误（HTTP 状态码异常、任务失败、超时等）都会输出到 `response` 端口，格式为：

```json
{"code": "error", "message": "错误详情..."}
```

下游节点不会因为上游生成失败而崩溃（使用 `ExecutionBlocker` 机制）。

## 配置文件

插件会在自身目录下生成 `relay_config.json`，用于持久化保存：
- 自定义中转站地址
- 自定义模型
- API 密钥（加密存储于本地，不会上传）

内置地址包含 `https://www.taikuaila.cn`、`https://ai.t8star.cn`、`https://api.bltcy.ai`。`gpt-image2` 在 taikuaila 使用 `native_style` / `gpt-image-2-all`，在 bltcy 使用 `openai_style` / `gpt-image-2`。

## 许可

本项目采用 [MIT License](LICENSE)。
