import { app } from "../../scripts/app.js";

const MODE_DESCRIPTION = "\u63cf\u8ff0\u6a21\u5f0f";
const MODE_CUSTOM = "\u6b4c\u8bcd\u5b9a\u5236\u6a21\u5f0f";

function applyMinSize(node, preferred) {
    if (!node || typeof node.computeSize !== "function") return;
    const computed = node.computeSize();
    const current = Array.isArray(preferred) ? preferred : (Array.isArray(node.size) ? node.size : computed);
    node.setSize([
        Math.max(current[0] || 0, computed[0] || 0),
        Math.max(current[1] || 0, computed[1] || 0),
    ]);
}

function preserveNodeSize(node, preferred) {
    if (!Array.isArray(preferred)) return;
    applyMinSize(node, preferred);
    setTimeout(() => applyMinSize(node, preferred), 0);
    requestAnimationFrame(() => applyMinSize(node, preferred));
}

function hideWidget(widget) {
    if (!widget || widget.type === "hidden") return;
    widget._origType = widget.type;
    widget._origComputeSize = widget.computeSize;
    widget.hidden = true;
    widget.type = "hidden";
    widget.computeSize = () => [0, 0];
    if (widget.inputEl) {
        widget.inputEl.style.display = "none";
        widget.inputEl.style.visibility = "hidden";
        widget.inputEl.style.pointerEvents = "none";
        widget.inputEl.tabIndex = -1;
    }
}

function showWidget(widget) {
    if (!widget || widget.type !== "hidden") return;
    widget.hidden = false;
    widget.type = widget._origType || "string";
    if (widget._origComputeSize) {
        widget.computeSize = widget._origComputeSize;
    } else {
        delete widget.computeSize;
    }
    if (widget.inputEl) {
        widget.inputEl.style.display = "";
        widget.inputEl.style.visibility = "";
        widget.inputEl.style.pointerEvents = "";
        widget.inputEl.tabIndex = 0;
    }
}

app.registerExtension({
    name: "RelayAPI.SoundGenerator",

    async nodeCreated(node) {
        if (node.comfyClass !== "RelaySoundGenerator") return;

        await new Promise((resolve) => setTimeout(resolve, 100));

        const widgets = {};
        for (const widget of node.widgets || []) {
            widgets[widget.name] = widget;
        }

        const {
            generation_mode,
            prompt,
            make_instrumental,
            seed,
            version,
            extend_mode,
            continue_clip_id,
            continue_at,
        } = widgets;
        if (!generation_mode || !prompt || !make_instrumental) return;

        function applyMode(preferredSize) {
            const mode = generation_mode.value || MODE_DESCRIPTION;
            const instrumental = !!make_instrumental.value;
            const extendEnabled = !!extend_mode?.value;
            const shouldHidePrompt = mode === MODE_CUSTOM && instrumental;

            if (shouldHidePrompt) {
                hideWidget(prompt);
            } else {
                showWidget(prompt);
            }

            if (!shouldHidePrompt && prompt.inputEl) {
                if (mode === MODE_DESCRIPTION) {
                    prompt.inputEl.placeholder = instrumental
                        ? "Describe the instrumental track you want."
                        : "Describe the song you want to generate.";
                } else {
                    prompt.inputEl.placeholder = instrumental
                        ? "Enter an instrumental composition prompt."
                        : "Enter lyrics or a full composition prompt.";
                }
            }

            if (seed) showWidget(seed);
            if (version) showWidget(version);
            if (extend_mode) showWidget(extend_mode);

            if (continue_clip_id) {
                if (extendEnabled) showWidget(continue_clip_id);
                else hideWidget(continue_clip_id);
            }

            if (continue_at) {
                if (extendEnabled) showWidget(continue_at);
                else hideWidget(continue_at);
            }

            preserveNodeSize(node, preferredSize);
            app.graph.setDirtyCanvas(true);
        }

        const origModeCb = generation_mode.callback;
        generation_mode.callback = function (value) {
            const preferredSize = Array.isArray(node.size) ? [...node.size] : null;
            if (origModeCb) origModeCb.call(this, value);
            applyMode(preferredSize);
        };

        const origInstrumentalCb = make_instrumental.callback;
        make_instrumental.callback = function (value) {
            const preferredSize = Array.isArray(node.size) ? [...node.size] : null;
            if (origInstrumentalCb) origInstrumentalCb.call(this, value);
            applyMode(preferredSize);
        };

        if (extend_mode) {
            const origExtendCb = extend_mode.callback;
            extend_mode.callback = function (value) {
                const preferredSize = Array.isArray(node.size) ? [...node.size] : null;
                if (origExtendCb) origExtendCb.call(this, value);
                applyMode(preferredSize);
            };
        }

        applyMode(Array.isArray(node.size) ? [...node.size] : null);
    },
});
