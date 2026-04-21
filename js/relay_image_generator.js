import { app } from "../../scripts/app.js";

const PRO_MAX_IMAGES = 14;
const FLASH_MAX_IMAGES = 14;
const GPT_IMAGE2_MAX_IMAGES = 16;
// 统一的比例列表：gpt-image2 / banana-pro / banana-2 都用这一个
// 顺序与 Python 端 IMAGE_RATIOS 保持一致，保证前后端完全对齐
const IMAGE_RATIOS = ["auto", "1:1", "2:3", "3:2", "4:3", "3:4", "9:16", "16:9", "9:21", "21:9"];
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
        // 三个平台共用同一套比例列表（见顶部 IMAGE_RATIOS）
        const values = IMAGE_RATIOS;
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
        const isGpt = platform === "gpt-image2";
        const values = isGpt ? GPT_IMAGE2_SIZES : DEFAULT_IMAGE_SIZES;
        // 各平台的默认档位：gpt-image2 只有 1K；banana-pro / banana-2 默认 2K
        const defaultSize = isGpt ? "1K" : "2K";
        const listChanged = !sameValues(sizeW.options?.values, values);
        if (listChanged) {
            sizeW.options.values = values;
            changed = true;
        }
        // 列表变了（= 真正切换了 gpt-image2 ↔ banana 系）或者当前值不在新列表里，
        // 都按目标平台的默认档位重置；banana-pro ↔ banana-2 之间切换列表不变，
        // 用户手选的 1K / 2K / 4K 会被保留
        if (listChanged || !values.includes(sizeW.value)) {
            if (sizeW.value !== defaultSize) {
                sizeW.value = defaultSize;
                changed = true;
            }
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
                const prevPlat = node._lastPlatform;
                node._lastPlatform = plat;
                applyPlatform(node, plat, preferredSize);

                // prevPlat === null 代表是节点刚加载时的首次同步（不是用户手动切换），
                // 这种情况下必须尊重工作流里保存的 size，不做任何调整。
                // 只有真正的"用户切平台"才把 banana-pro ↔ banana-2 的 1K 自动升到 2K。
                // （gpt-image2 ↔ banana 之间的切换由 applyPlatform 里列表变更逻辑负责）
                if (prevPlat !== null && plat !== "gpt-image2") {
                    const sizeW = node.widgets?.find(w => w.name === "size");
                    if (sizeW && sizeW.value === "1K" && sizeW.options.values.includes("2K")) {
                        sizeW.value = "2K";
                        app.graph.setDirtyCanvas(true);
                    }
                }
            }

            const hasImg = hasImageConnected(node);
            if (hasImg !== node._lastHasImage) {
                node._lastHasImage = hasImg;
                const ratioW = node.widgets?.find(w => w.name === "ratio");
                if (ratioW) {
                    // 接了参考图默认切 auto（按原图比例出），没接则回 1:1
                    const target = hasImg ? "auto" : "1:1";
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
