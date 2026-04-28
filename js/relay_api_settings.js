import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const TASK_PLATFORMS = {
    image: ["banana-pro", "banana-2", "gpt-image2"],
    video: ["Grok", "Veo"],
    sound: ["Suno"],
    text: ["GeminiText", "OpenaiText"],
};

const TASK_API_FORMATS = {
    image: ["v1beta/models", "v1/images", "v1/chat/completions"],
    video: ["v1/video", "v1/videos", "v2/videos"],
    sound: ["suno/submit"],
    text: ["v1beta/models", "v1/chat/completions"],
};

const PLATFORM_API_FORMATS = {
    "gpt-image2": ["v1/images"],
    "Veo": ["v1/video", "v1/videos", "v2/videos"],
    "OpenaiText": ["v1/chat/completions"],
};

app.registerExtension({
    name: "RelayAPI.Settings",

    async nodeCreated(node) {
        if (node.comfyClass !== "RelayAPISettings") return;

        await new Promise(r => setTimeout(r, 100));

        const w = {};
        for (const widget of node.widgets) {
            w[widget.name] = widget;
        }

        const { task_type, platform, api_format, api_base, model, custom_api_base, custom_model, apikey } = w;
        if (!task_type || !platform || !api_base || !model) return;

        // ── apikey 密码遮盖 ──
        if (apikey) {
            const mask = (s) => {
                if (!s) return "";
                const len = s.length;
                if (len <= 10) return "•".repeat(len);
                const head = 6;
                const tail = 6;
                const dots = len - head - tail;
                return s.slice(0, head) + "•".repeat(dots) + s.slice(len - tail);
            };

            apikey._realKey = apikey.value || "";

            if (apikey._realKey) {
                apikey.value = mask(apikey._realKey);
            }

            const origCallback = apikey.callback;
            apikey.callback = function (v) {
                if (v && !v.includes("•")) {
                    apikey._realKey = v;
                    apikey.value = mask(v);
                }
                if (origCallback) origCallback.call(this, apikey._realKey);
            };

            apikey.serializeValue = function () {
                return apikey._realKey;
            };

            const origEl = apikey.inputEl;
            if (origEl) {
                origEl.type = "password";
                origEl.addEventListener("focus", () => {
                    origEl.value = apikey._realKey;
                });
                origEl.addEventListener("blur", () => {
                    if (origEl.value && !origEl.value.includes("•")) {
                        apikey._realKey = origEl.value;
                    }
                    apikey.value = mask(apikey._realKey);
                });
            }
        }

        // api_format uses endpoint path names. The task/platform decides which endpoints are selectable.
        function applyApiFormats(tt, plat) {
            const formats = PLATFORM_API_FORMATS[plat] || TASK_API_FORMATS[tt] || [];

            if (api_format && formats.length > 0) {
                api_format.options.values = formats;
                if (!formats.includes(api_format.value)) api_format.value = formats[0];
            }
        }

        function applyTaskType(tt) {
            const platforms = TASK_PLATFORMS[tt] || [];

            if (platform && platforms.length > 0) {
                platform.options.values = platforms;
                if (!platforms.includes(platform.value)) platform.value = platforms[0];
            }

            const plat = platform.value || platforms[0] || "Grok";
            applyApiFormats(tt, plat);
            refreshModels(plat, api_format ? api_format.value : "");
            app.graph.setDirtyCanvas(true);
        }

        // ── api_base 动态管理 ──
        async function refreshBases() {
            try {
                const resp = await api.fetchApi("/relayapi/api_bases");
                if (!resp.ok) return;
                const list = await resp.json();
                if (Array.isArray(list) && list.length > 0) {
                    api_base.options.values = list;
                    if (!list.includes(api_base.value)) api_base.value = list[0];
                    refreshModels(platform.value || "Grok", api_format ? api_format.value : "");
                    app.graph.setDirtyCanvas(true);
                }
            } catch (e) { console.warn("[RelayAPI]", e); }
        }

        async function handleBaseInput(raw) {
            raw = (raw || "").trim();
            if (!raw) return;
            const del = "delete:";
            if (raw.toLowerCase().startsWith(del)) {
                const target = raw.substring(del.length).trim().replace(/\/+$/, "");
                if (!target) return;
                try {
                    const resp = await api.fetchApi("/relayapi/api_bases/remove", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ url: target }),
                    });
                    if (!resp.ok) return;
                    const r = await resp.json();
                    if (r.success && Array.isArray(r.list)) {
                        api_base.options.values = r.list;
                        if (!r.list.includes(api_base.value)) api_base.value = r.list[0];
                        if (custom_api_base) custom_api_base.value = "";
                        refreshModels(platform.value || "Grok", api_format ? api_format.value : "");
                        app.graph.setDirtyCanvas(true);
                    }
                } catch (e) { console.warn("[RelayAPI]", e); }
                return;
            }
            const url = raw.replace(/\/+$/, "");
            try {
                const resp = await api.fetchApi("/relayapi/api_bases/add", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url }),
                });
                if (!resp.ok) return;
                const r = await resp.json();
                if (r.success && Array.isArray(r.list)) {
                    api_base.options.values = r.list;
                    api_base.value = url;
                    if (custom_api_base) custom_api_base.value = "";
                    refreshModels(platform.value || "Grok", api_format ? api_format.value : "");
                    app.graph.setDirtyCanvas(true);
                }
            } catch (e) { console.warn("[RelayAPI]", e); }
        }

        // ── model 动态管理 ──
        async function refreshModels(plat, fmt) {
            const f = fmt || (api_format ? api_format.value : "");
            try {
                let url = `/relayapi/models?platform=${encodeURIComponent(plat)}`;
                if (f) url += `&api_format=${encodeURIComponent(f)}`;
                const resp = await api.fetchApi(url);
                if (!resp.ok) return;
                const list = await resp.json();
                if (Array.isArray(list) && list.length > 0) {
                    model.options.values = list;
                    if (!list.includes(model.value)) model.value = list[0];
                    app.graph.setDirtyCanvas(true);
                }
            } catch (e) { console.warn("[RelayAPI]", e); }
        }

        async function handleModelInput(raw) {
            raw = (raw || "").trim();
            if (!raw) return;
            const plat = platform.value || "Grok";
            const del = "delete:";
            if (raw.toLowerCase().startsWith(del)) {
                const target = raw.substring(del.length).trim();
                if (!target) return;
                try {
                    const resp = await api.fetchApi("/relayapi/models/remove", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ platform: plat, model: target }),
                    });
                    if (!resp.ok) return;
                    const r = await resp.json();
                    if (r.success && Array.isArray(r.list)) {
                        model.options.values = r.list;
                        if (!r.list.includes(model.value)) model.value = r.list[0];
                        if (custom_model) custom_model.value = "";
                        app.graph.setDirtyCanvas(true);
                    }
                } catch (e) { console.warn("[RelayAPI]", e); }
                return;
            }
            try {
                const resp = await api.fetchApi("/relayapi/models/add", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ platform: plat, model: raw }),
                });
                if (!resp.ok) return;
                const r = await resp.json();
                if (r.success && Array.isArray(r.list)) {
                    model.options.values = r.list;
                    model.value = raw;
                    if (custom_model) custom_model.value = "";
                    app.graph.setDirtyCanvas(true);
                }
            } catch (e) { console.warn("[RelayAPI]", e); }
        }

        // ── 初始化 ──
        await refreshBases();
        applyTaskType(task_type.value || "video");

        // ── task_type 切换 ──
        const origTaskTypeCb = task_type.callback;
        task_type.callback = function (value) {
            if (origTaskTypeCb) origTaskTypeCb.call(this, value);
            applyTaskType(value);
        };

        // ── platform 切换时刷新 model 列表 ──
        const origPlatformCb = platform.callback;
        platform.callback = function (value) {
            if (origPlatformCb) origPlatformCb.call(this, value);
            applyApiFormats(task_type.value || "video", value);
            refreshModels(value, api_format ? api_format.value : "");
        };

        // ── api_base 切换时刷新模型列表 ──
        const origApiBaseCb = api_base.callback;
        api_base.callback = function (value) {
            if (origApiBaseCb) origApiBaseCb.call(this, value);
            refreshModels(platform.value || "Grok", api_format ? api_format.value : "");
        };

        if (api_format) {
            const origFormatCb = api_format.callback;
            api_format.callback = function (value) {
                if (origFormatCb) origFormatCb.call(this, value);
                refreshModels(platform.value, value);
            };
        }

        // ── custom_api_base 输入 ──
        if (custom_api_base) {
            const origCb = custom_api_base.callback;
            custom_api_base.callback = function (value) {
                if (origCb) origCb.call(this, value);
                handleBaseInput(value);
            };
            const el = custom_api_base.inputEl;
            if (el) {
                el.addEventListener("change", () => handleBaseInput(el.value));
                el.addEventListener("keydown", (e) => {
                    if (e.key === "Enter") { e.preventDefault(); handleBaseInput(el.value); }
                });
            }
        }

        // ── custom_model 输入 ──
        if (custom_model) {
            const origCb = custom_model.callback;
            custom_model.callback = function (value) {
                if (origCb) origCb.call(this, value);
                handleModelInput(value);
            };
            const el = custom_model.inputEl;
            if (el) {
                el.addEventListener("change", () => handleModelInput(el.value));
                el.addEventListener("keydown", (e) => {
                    if (e.key === "Enter") { e.preventDefault(); handleModelInput(el.value); }
                });
            }
        }
    },
});
