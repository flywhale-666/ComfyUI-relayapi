import json
import time

import comfy.utils  # type: ignore[reportMissingImports]
import requests
from comfy.comfy_types import IO  # type: ignore[reportMissingImports]
from comfy_api_nodes.util import audio_bytes_to_audio_input  # type: ignore[reportMissingImports]
from comfy_execution.graph_utils import ExecutionBlocker  # type: ignore[reportMissingImports]

from .config import API_PATHS, get_config, get_current_base_url


MODE_DESCRIPTION = "\u63cf\u8ff0\u6a21\u5f0f"
MODE_CUSTOM = "\u6b4c\u8bcd\u5b9a\u5236\u6a21\u5f0f"

SOUND_GENERATION_MODES = [MODE_DESCRIPTION, MODE_CUSTOM]
SOUND_VERSIONS = ["V3", "V3.5", "V4", "V4.5", "V4.5+", "V5", "V5.5"]
SOUND_VERSION_MODEL_MAP = {
    "V3": "chirp-v3.0",
    "V3.5": "chirp-v3.5",
    "V4": "chirp-v4",
    "V4.5": "chirp-auk",
    "V4.5+": "chirp-bluejay",
    "V5": "chirp-crow",
    "V5.5": "chirp-fenix",
}
SOUND_SETTINGS_MODEL_BY_FORMAT = {
    "native_style": "suno_music",
    "openai_style": "suno_music",
}


class RelaySoundGenerator:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "generation_mode": (SOUND_GENERATION_MODES, {"default": MODE_DESCRIPTION}),
                "title": ("STRING", {"default": ""}),
                "tags": ("STRING", {"default": "", "placeholder": "pop, electronic, cinematic"}),
                "prompt": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": True,
                        "placeholder": "\u63cf\u8ff0\u6a21\u5f0f\u586b\u6b4c\u66f2\u63cf\u8ff0\uff1b\u6b4c\u8bcd\u5b9a\u5236\u6a21\u5f0f\u586b\u6b4c\u8bcd\u6216\u5b8c\u6574\u521b\u4f5c\u63d0\u793a\u8bcd\u3002",
                    },
                ),
                "make_instrumental": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "\u7eaf\u97f3\u4e50",
                        "label_off": "\u5e26\u6b4c\u8bcd",
                    },
                ),
                "version": (SOUND_VERSIONS, {"default": "V5"}),
            },
            "optional": {
                "info": ("STRING", {"default": "", "forceInput": True}),
                "negative_tags": (
                    "STRING",
                    {"default": "", "placeholder": "\u4e0d\u60f3\u8981\u7684\u98ce\u683c\uff0c\u53ef\u9009"},
                ),
                "extend_mode": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "label_on": "\u5ef6\u957f\u97f3\u4e50",
                        "label_off": "\u666e\u901a\u751f\u6210",
                    },
                ),
                "continue_clip_id": (
                    "STRING",
                    {
                        "default": "",
                        "placeholder": "\u586b\u5199\u4e0a\u4e00\u6b21\u751f\u6210\u8fd4\u56de\u7684 clip_id",
                    },
                ),
                "continue_at": ("FLOAT", {"default": 0.0, "min": 0.0, "step": 0.1}),
                "seed": (
                    "INT",
                    {"default": 0, "min": 0, "max": 0xFFFFFFFFFFFFFFFF, "control_after_generate": True},
                ),
            },
        }

    RETURN_TYPES = (IO.AUDIO, "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("audio", "clip_id", "task_id", "response", "audio_url")
    FUNCTION = "generate_sound"
    CATEGORY = "RelayAPI"

    def __init__(self):
        self.timeout = 120
        self.poll_timeout = 900

    def _err(self, msg):
        full_msg = f"[RelayAPI] {msg}"
        print(full_msg)
        raise RuntimeError(full_msg)

    def _get_api_key(self, api_key):
        if api_key and api_key.strip():
            return api_key.strip()
        return get_config().get("api_key", "")

    def _headers_auth(self, api_key):
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _post_json_utf8(self, url, api_key, payload):
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        return requests.post(url, headers=self._headers_auth(api_key), data=body, timeout=self.timeout)

    def _get_paths(self, api_format):
        key = f"sound_{api_format}"
        return API_PATHS.get(key, API_PATHS["sound_openai_style"])

    def _build_native_payload(
        self,
        generation_mode,
        version_model,
        title,
        tags,
        prompt,
        make_instrumental,
        continue_clip_id,
        continue_at,
    ):
        cleaned_title = title.strip()
        cleaned_tags = tags.strip()
        cleaned_prompt = prompt.strip()
        cleaned_clip_id = continue_clip_id.strip()

        if generation_mode == MODE_DESCRIPTION:
            payload = {"gpt_description_prompt": cleaned_prompt}
            if version_model:
                payload["mv"] = version_model
            if cleaned_title:
                payload["title"] = cleaned_title
            if make_instrumental:
                payload["make_instrumental"] = True
            return payload

        payload = {
            "prompt": cleaned_prompt,
            "mv": version_model,
            "title": cleaned_title,
            "tags": cleaned_tags,
        }
        if cleaned_clip_id:
            payload["continue_clip_id"] = cleaned_clip_id
            payload["continue_at"] = max(0, int(float(continue_at)))
        return payload

    def _build_openai_payload(
        self,
        version_model,
        title,
        tags,
        prompt,
        make_instrumental,
        negative_tags,
        continue_clip_id,
        continue_at,
    ):
        payload = {
            "prompt": prompt.strip(),
            "mv": version_model,
            "title": title.strip(),
            "tags": tags.strip(),
            "make_instrumental": bool(make_instrumental),
        }

        cleaned_negative_tags = negative_tags.strip()
        if cleaned_negative_tags:
            payload["negative_tags"] = cleaned_negative_tags

        cleaned_clip_id = continue_clip_id.strip()
        if cleaned_clip_id:
            payload["continue_clip_id"] = cleaned_clip_id
            payload["continue_at"] = max(0, int(float(continue_at)))
            payload["task"] = "extend"

        return payload

    def _submit_suno(
        self,
        base_url,
        api_key,
        api_format,
        version_model,
        generation_mode,
        title,
        tags,
        prompt,
        make_instrumental,
        negative_tags,
        continue_clip_id,
        continue_at,
        pbar,
    ):
        paths = self._get_paths(api_format)
        url = base_url + paths.get("suno_create", "/suno/submit/music")

        if api_format == "native_style":
            payload = self._build_native_payload(
                generation_mode=generation_mode,
                version_model=version_model,
                title=title,
                tags=tags,
                prompt=prompt,
                make_instrumental=make_instrumental,
                continue_clip_id=continue_clip_id,
                continue_at=continue_at,
            )
        else:
            payload = self._build_openai_payload(
                version_model=version_model,
                title=title,
                tags=tags,
                prompt=prompt,
                make_instrumental=make_instrumental,
                negative_tags=negative_tags,
                continue_clip_id=continue_clip_id,
                continue_at=continue_at,
            )

        pbar.update_absolute(30)
        print(f"[RelayAPI] POST {url} (Suno, {api_format}, {generation_mode})")
        resp = self._post_json_utf8(url, api_key, payload)
        print(f"[RelayAPI] -> {resp.status_code}")
        if resp.status_code != 200:
            self._err(f"Suno create error: {resp.status_code} - {resp.text[:500]}")

        result = resp.json()
        task_id = result.get("id") or result.get("task_id")
        if not task_id:
            self._err(f"No task ID returned: {json.dumps(result, ensure_ascii=False)[:500]}")
        return task_id, result

    def _iter_clip_lists(self, query_result):
        if isinstance(query_result.get("clips"), list):
            yield query_result["clips"]

        data = query_result.get("data")
        if isinstance(data, dict):
            nested_data = data.get("data")
            if isinstance(nested_data, list):
                yield nested_data
            nested_clips = data.get("clips")
            if isinstance(nested_clips, list):
                yield nested_clips

    def _extract_first_clip(self, query_result):
        for clips in self._iter_clip_lists(query_result):
            for clip in clips:
                if not isinstance(clip, dict):
                    continue
                audio_url = clip.get("audio_url", "")
                if isinstance(audio_url, str) and audio_url.startswith("http"):
                    return {
                        "clip_id": clip.get("clip_id") or clip.get("id") or "",
                        "audio_url": audio_url,
                        "clip": clip,
                    }
        return None

    def _extract_status(self, query_result):
        data = query_result.get("data")
        if isinstance(data, dict) and data.get("status"):
            return str(data.get("status"))
        if query_result.get("status"):
            return str(query_result.get("status"))
        return ""

    def _extract_progress(self, query_result):
        data = query_result.get("data")
        if isinstance(data, dict) and data.get("progress") is not None:
            return data.get("progress")
        if query_result.get("progress") is not None:
            return query_result.get("progress")
        return None

    def _query_suno(self, base_url, api_key, task_id, api_format):
        paths = self._get_paths(api_format)
        url = base_url + paths.get("suno_query", "/suno/fetch/{task_id}").format(task_id=task_id)
        resp = requests.get(
            url,
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        return resp.json()

    def _poll(self, base_url, api_key, task_id, api_format, pbar):
        start = time.time()

        while time.time() - start <= self.poll_timeout:
            time.sleep(5)
            try:
                result = self._query_suno(base_url, api_key, task_id, api_format)
                if not result:
                    continue

                status = self._extract_status(result).lower()
                progress = self._extract_progress(result)
                if isinstance(progress, str) and progress.endswith("%"):
                    try:
                        pbar.update_absolute(min(90, 40 + int(progress[:-1]) // 2))
                    except ValueError:
                        pass

                clip_info = self._extract_first_clip(result)
                if clip_info:
                    return clip_info, result

                if any(flag in status for flag in ("fail", "error")):
                    data = result.get("data") or {}
                    if not isinstance(data, dict):
                        data = {}
                    reason = data.get("fail_reason") or result.get("message") or status
                    self._err(f"Suno task failed: {reason}")
            except requests.exceptions.Timeout:
                continue
            except RuntimeError:
                raise
            except Exception:
                continue

        self._err(f"Suno polling timeout after {round(time.time() - start, 1)}s")

    def _download_audio(self, url):
        resp = requests.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.content

    def generate_sound(
        self,
        generation_mode,
        title,
        tags,
        prompt,
        make_instrumental,
        version,
        info="",
        negative_tags="",
        extend_mode=False,
        continue_clip_id="",
        continue_at=0.0,
        seed=0,
    ):
        parsed = {}
        if info and info.strip():
            try:
                parsed = json.loads(info)
            except Exception:
                pass

        task_id = ""
        clip_id = ""
        audio_url = ""

        try:
            api_key = self._get_api_key(parsed.get("apikey", ""))
            if not api_key:
                self._err("API key not found. Please set via Relay API Settings node.")

            raw_base = parsed.get("api_base", "")
            base_url = raw_base.strip().rstrip("/") if raw_base.strip() else get_current_base_url()
            platform = (parsed.get("platform") or "Suno").strip()
            settings_model = (parsed.get("model") or "").strip()
            api_format = (parsed.get("api_format") or "native_style").strip()
            task_type = (parsed.get("task_type") or "sound").strip()
            version_model = SOUND_VERSION_MODEL_MAP.get(version, "chirp-crow")

            if task_type != "sound":
                self._err("Relay API Settings task_type must be sound.")
            if platform != "Suno":
                self._err(f"Unsupported sound platform: {platform}")
            if api_format not in {"native_style", "openai_style"}:
                self._err(f"Unsupported sound api_format: {api_format}")

            expected_settings_model = SOUND_SETTINGS_MODEL_BY_FORMAT.get(api_format, "")
            if settings_model and expected_settings_model and settings_model != expected_settings_model:
                print(
                    f"[RelayAPI] sound settings model mismatch: got={settings_model}, "
                    f"expected={expected_settings_model}"
                )

            cleaned_prompt = prompt.strip()
            if generation_mode == MODE_DESCRIPTION:
                if not cleaned_prompt:
                    self._err("Song description cannot be empty.")
            else:
                if not title.strip():
                    self._err("Title is required in custom lyrics mode.")
                if not tags.strip():
                    self._err("Tags are required in custom lyrics mode.")
                if not make_instrumental and not cleaned_prompt:
                    self._err("Lyrics or composition prompt is required in custom lyrics mode.")

            if extend_mode and not continue_clip_id.strip():
                self._err("continue_clip_id is required when extend mode is enabled.")

            pbar = comfy.utils.ProgressBar(100)
            pbar.update_absolute(10)

            task_id, submit_result = self._submit_suno(
                base_url=base_url,
                api_key=api_key,
                api_format=api_format,
                version_model=version_model,
                generation_mode=generation_mode,
                title=title,
                tags=tags,
                prompt=cleaned_prompt,
                make_instrumental=make_instrumental,
                negative_tags=negative_tags,
                continue_clip_id=continue_clip_id if extend_mode else "",
                continue_at=continue_at if extend_mode else 0.0,
                pbar=pbar,
            )

            pbar.update_absolute(40)
            clip_info, query_result = self._poll(base_url, api_key, task_id, api_format, pbar)
            clip_id = str(clip_info.get("clip_id") or "")
            audio_url = str(clip_info.get("audio_url") or "")

            if not audio_url:
                self._err("Suno task completed without audio_url.")

            print(f"[RelayAPI] Downloading audio: {audio_url}")
            audio_bytes = self._download_audio(audio_url)
            audio_input = audio_bytes_to_audio_input(audio_bytes)
            pbar.update_absolute(100)

            response_payload = {
                "code": "success",
                "submit": submit_result,
                "query": query_result,
                "api_format": api_format,
                "settings_model": settings_model or expected_settings_model,
                "mv": version_model,
            }
            return (
                audio_input,
                clip_id,
                task_id,
                json.dumps(response_payload, ensure_ascii=False),
                audio_url,
            )

        except Exception as e:
            error_resp = json.dumps({"code": "error", "message": str(e)}, ensure_ascii=False)
            return (ExecutionBlocker(None), clip_id, task_id, error_resp, audio_url)
