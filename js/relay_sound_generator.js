import { app } from "../../scripts/app.js";

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
            tags,
        } = widgets;
        if (!generation_mode || !prompt || !make_instrumental) return;

        function applyMode() {
            const mode = generation_mode.value || "描述模式";
            const instrumental = !!make_instrumental.value;
            const extendEnabled = !!extend_mode?.value;

            if (prompt.inputEl) {
                if (mode === "描述模式") {
                    prompt.inputEl.placeholder = instrumental
                        ? "Describe the instrumental track you want."
                        : "Describe the song you want to generate.";
                } else {
                    prompt.inputEl.placeholder = instrumental
                        ? "Enter an instrumental composition prompt."
                        : "Enter lyrics or a full composition prompt.";
                }
            }

            if (tags) {
                if (mode === "歌词定制模式") {
                    showWidget(tags);
                } else {
                    hideWidget(tags);
                }
            }

            if (seed) {
                showWidget(seed);
            }

            if (version) {
                showWidget(version);
            }

            if (extend_mode) {
                showWidget(extend_mode);
            }

            if (continue_clip_id) {
                if (extendEnabled) {
                    showWidget(continue_clip_id);
                } else {
                    hideWidget(continue_clip_id);
                }
            }

            if (continue_at) {
                if (extendEnabled) {
                    showWidget(continue_at);
                } else {
                    hideWidget(continue_at);
                }
            }

            if (typeof node.computeSize === "function") {
                node.setSize(node.computeSize());
            }
            app.graph.setDirtyCanvas(true);
        }

        const origModeCb = generation_mode.callback;
        generation_mode.callback = function (value) {
            if (origModeCb) origModeCb.call(this, value);
            applyMode();
        };

        const origInstrumentalCb = make_instrumental.callback;
        make_instrumental.callback = function (value) {
            if (origInstrumentalCb) origInstrumentalCb.call(this, value);
            applyMode();
        };

        if (extend_mode) {
            const origExtendCb = extend_mode.callback;
            extend_mode.callback = function (value) {
                if (origExtendCb) origExtendCb.call(this, value);
                applyMode();
            };
        }

        applyMode();
    },
});
