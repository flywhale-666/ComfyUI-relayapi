import { app } from "../../scripts/app.js";

const PRO_MAX_IMAGES = 14;
const FLASH_MAX_IMAGES = 14;
const GPT_IMAGE2_MAX_IMAGES = 16;
const DEFAULT_IMAGE_RATIOS = ["AUTO", "1:1", "16:9", "9:16", "4:3", "3:4", "21:9"];
const GPT_IMAGE2_RATIOS = ["auto", "1:1", "3:2", "2:3", "16:9", "9:16"];
const DEFAULT_IMAGE_SIZES = ["1K", "2K", "4K"];
const GPT_IMAGE2_SIZES = ["1K"];

function sameValues(a, b) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v === b[i]);
}

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

function getPlatformFromSource(node) {
    const infoSlot = node.inputs?.find(i => i.name === "info");
    if (!infoSlot || !infoSlot.link) return "banana-pro";

    const link = app.graph.links[infoSlot.link];
    if (!link) return "banana-pro";

    const srcNode = app.graph.getNodeById(link.origin_id);
    if (!srcNode) return "banana-pro";

    const pw = srcNode.widgets?.find(w => w.name === "platform");
    return pw ? pw.value : "banana-pro";
}

function hasImageConnected(node) {
    for (const input of node.inputs || []) {
        if (/^image\d+$/.test(input.name) && input.link) return true;
    }
    return false;
}

function applyPlatform(node, platform, preferredSize) {
    const maxImg = platform === "gpt-image2"
        ? GPT_IMAGE2_MAX_IMAGES
        : (platform === "banana-2" ? FLASH_MAX_IMAGES : PRO_MAX_IMAGES);
    let changed = false;

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

    const ratioW = node.widgets?.find(w => w.name === "ratio");
    if (ratioW) {
        const values = platform === "gpt-image2" ? GPT_IMAGE2_RATIOS : DEFAULT_IMAGE_RATIOS;
        if (!sameValues(ratioW.options?.values, values)) {
            ratioW.options.values = values;
            changed = true;
        }
        if (!values.includes(ratioW.value)) {
            ratioW.value = values[0];
            changed = true;
        }
    }

    const sizeW = node.widgets?.find(w => w.name === "size");
    if (sizeW) {
        const values = platform === "gpt-image2" ? GPT_IMAGE2_SIZES : DEFAULT_IMAGE_SIZES;
        if (!sameValues(sizeW.options?.values, values)) {
            sizeW.options.values = values;
            changed = true;
        }
        if (!values.includes(sizeW.value)) {
            sizeW.value = values[0];
            changed = true;
        }
    }

    if (changed) {
        preserveNodeSize(node, preferredSize);
        app.graph.setDirtyCanvas(true);
    }
}

app.registerExtension({
    name: "RelayAPI.ImageGenerator",

    async nodeCreated(node) {
        if (node.comfyClass !== "RelayImageGenerator") return;

        await new Promise(r => setTimeout(r, 200));

        node._lastPlatform = null;
        node._lastHasImage = null;
        applyPlatform(node, "banana-pro", Array.isArray(node.size) ? [...node.size] : null);

        setInterval(() => {
            const preferredSize = Array.isArray(node.size) ? [...node.size] : null;
            const plat = getPlatformFromSource(node);
            if (plat !== node._lastPlatform) {
                node._lastPlatform = plat;
                applyPlatform(node, plat, preferredSize);
            }

            const hasImg = hasImageConnected(node);
            if (hasImg !== node._lastHasImage) {
                node._lastHasImage = hasImg;
                const ratioW = node.widgets?.find(w => w.name === "ratio");
                if (ratioW) {
                    const target = ratioW.options.values.includes("auto")
                        ? (hasImg ? "auto" : "1:1")
                        : (hasImg ? "AUTO" : "1:1");
                    if (ratioW.options.values.includes(target) && ratioW.value !== target) {
                        ratioW.value = target;
                        preserveNodeSize(node, preferredSize);
                        app.graph.setDirtyCanvas(true);
                    }
                }
            }
        }, 500);
    },
});
