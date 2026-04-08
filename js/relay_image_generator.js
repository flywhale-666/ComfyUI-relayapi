import { app } from "../../scripts/app.js";

const PRO_MAX_IMAGES = 14;
const FLASH_MAX_IMAGES = 3;

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

function applyPlatform(node, platform) {
    const maxImg = platform === "banana-2" ? FLASH_MAX_IMAGES : PRO_MAX_IMAGES;
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

    if (changed) {
        node.setSize(node.computeSize());
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
        applyPlatform(node, "banana-pro");

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
                if (ratioW) {
                    const target = hasImg ? "AUTO" : "1:1";
                    if (ratioW.options.values.includes(target) && ratioW.value !== target) {
                        ratioW.value = target;
                        app.graph.setDirtyCanvas(true);
                    }
                }
            }
        }, 500);
    },
});
