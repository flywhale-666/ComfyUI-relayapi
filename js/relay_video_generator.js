import { app } from "../../scripts/app.js";

const VEO_ONLY_WIDGETS = ["enhance_prompt", "enable_upsample"];

const GROK_RATIOS = ["AUTO", "16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3"];
const VEO_RATIOS = ["16:9", "9:16"];

const GROK_SIZES = ["720P", "480P"];
const VEO_SIZES = ["720P", "1080P"];

const GROK_DURATIONS = ["6", "10"];
const VEO_DURATIONS = ["4", "6", "8"];

const GROK_MAX_IMAGES = 7;
const VEO_MAX_IMAGES = 3;

function hideWidget(node, widget) {
    if (widget.type === "hidden") return;
    widget._origType = widget.type;
    widget._origComputeSize = widget.computeSize;
    widget.type = "hidden";
    widget.computeSize = () => [0, -4];
}

function showWidget(node, widget) {
    if (widget.type !== "hidden") return;
    widget.type = widget._origType || "combo";
    if (widget._origComputeSize) {
        widget.computeSize = widget._origComputeSize;
    } else {
        delete widget.computeSize;
    }
}

function applyPlatform(node, platform) {
    const plat = (platform || "Grok").trim();
    let changed = false;
    const maxImg = plat === "Veo" ? VEO_MAX_IMAGES : GROK_MAX_IMAGES;

    for (const w of node.widgets || []) {
        if (VEO_ONLY_WIDGETS.includes(w.name)) {
            const shouldHide = plat !== "Veo";
            if (shouldHide && w.type !== "hidden") { hideWidget(node, w); changed = true; }
            if (!shouldHide && w.type === "hidden") { showWidget(node, w); changed = true; }
        }

        if (w.name === "ratio") {
            const newValues = plat === "Veo" ? VEO_RATIOS : GROK_RATIOS;
            if (JSON.stringify(w.options.values) !== JSON.stringify(newValues)) {
                w.options.values = newValues;
                if (!newValues.includes(w.value)) w.value = newValues[0];
                changed = true;
            }
        }

        if (w.name === "size") {
            const newValues = plat === "Veo" ? VEO_SIZES : GROK_SIZES;
            if (JSON.stringify(w.options.values) !== JSON.stringify(newValues)) {
                w.options.values = newValues;
                if (!newValues.includes(w.value)) w.value = newValues[0];
                changed = true;
            }
        }

        if (w.name === "duration") {
            const newValues = plat === "Veo" ? VEO_DURATIONS : GROK_DURATIONS;
            if (JSON.stringify(w.options.values) !== JSON.stringify(newValues)) {
                w.options.values = newValues;
                if (!newValues.includes(w.value)) w.value = newValues[newValues.length - 1];
                changed = true;
            }
        }
    }

    for (const input of node.inputs || []) {
        const m = input.name.match(/^image(\d+)$/);
        if (!m) continue;
        const idx = parseInt(m[1], 10);
        const shouldHide = idx > maxImg;

        if (shouldHide && !input._hidden) {
            if (input.link) {
                const linkInfo = app.graph.links[input.link];
                if (linkInfo) {
                    const srcNode = app.graph.getNodeById(linkInfo.origin_id);
                    if (srcNode) srcNode.disconnectOutput(linkInfo.origin_slot);
                }
            }
            input._hidden = true;
            input._origType = input.type;
            input.type = -1;
            changed = true;
        }
        if (!shouldHide && input._hidden) {
            input._hidden = false;
            input.type = input._origType || "IMAGE";
            changed = true;
        }
    }

    if (changed) {
        node.setSize(node.computeSize());
        app.graph.setDirtyCanvas(true);
    }
}

function getPlatformFromSource(node) {
    const infoSlot = node.inputs?.find(i => i.name === "info");
    if (!infoSlot || !infoSlot.link) return "Grok";

    const link = app.graph.links[infoSlot.link];
    if (!link) return "Grok";

    const srcNode = app.graph.getNodeById(link.origin_id);
    if (!srcNode) return "Grok";

    const pw = srcNode.widgets?.find(w => w.name === "platform");
    return pw ? pw.value : "Grok";
}

function hasImageConnected(node) {
    for (const input of node.inputs || []) {
        if (/^image\d+$/.test(input.name) && input.link) return true;
    }
    return false;
}

app.registerExtension({
    name: "RelayAPI.VideoGenerator",

    async nodeCreated(node) {
        if (node.comfyClass !== "RelayVideoGenerator") return;

        await new Promise(r => setTimeout(r, 200));

        node._lastPlatform = null;
        node._lastHasImage = null;
        applyPlatform(node, "Grok");

        setInterval(() => {
            const plat = getPlatformFromSource(node);
            if (plat !== node._lastPlatform) {
                node._lastPlatform = plat;
                applyPlatform(node, plat);
            }

            const hasImg = hasImageConnected(node);
            if (hasImg !== node._lastHasImage) {
                node._lastHasImage = hasImg;
                const ratioW = node.widgets?.find(w => w.name === "ratio");
                if (ratioW && plat !== "Veo") {
                    const target = hasImg ? "AUTO" : "16:9";
                    if (ratioW.options.values.includes(target) && ratioW.value !== target) {
                        ratioW.value = target;
                        app.graph.setDirtyCanvas(true);
                    }
                }
            }
        }, 500);
    },
});
