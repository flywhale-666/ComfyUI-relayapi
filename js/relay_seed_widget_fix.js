import { app } from "../../scripts/app.js";

const TARGET_CLASSES = new Set([
    "RelayImageGenerator",
    "RelayVideoGenerator",
    "RelaySoundGenerator",
]);

function getWidget(node, name) {
    return node?.widgets?.find((widget) => widget.name === name);
}

function getDefaultControlValue(widget) {
    const values = widget?.options?.values;
    if (Array.isArray(values) && values.length > 0) {
        return values.includes(true) ? true : values[0];
    }
    return true;
}

function sanitizeSeedWidget(widget) {
    if (!widget) return false;

    let changed = false;
    const raw = widget.value;
    const numeric = Number(raw);
    if (raw == null || raw === "" || Number.isNaN(numeric)) {
        widget.value = 0;
        changed = true;
    }

    if (!widget._relaySerializeWrapped) {
        const originalSerialize = widget.serializeValue?.bind(widget);
        widget.serializeValue = function (...args) {
            const current = Number(widget.value);
            if (widget.value == null || widget.value === "" || Number.isNaN(current)) {
                widget.value = 0;
            }
            return originalSerialize ? originalSerialize(...args) : widget.value;
        };
        widget._relaySerializeWrapped = true;
    }

    return changed;
}

function sanitizeControlWidget(widget) {
    if (!widget) return false;

    const validValues = Array.isArray(widget.options?.values) ? widget.options.values : null;
    const current = widget.value;
    const currentAsJoined = Array.isArray(current) ? current.join(",") : current;
    const looksLikeOptionsText =
        typeof currentAsJoined === "string" &&
        Array.isArray(validValues) &&
        currentAsJoined === validValues.join(",");
    const invalid =
        current == null ||
        Array.isArray(current) ||
        looksLikeOptionsText ||
        (Array.isArray(validValues) && validValues.length > 0 && !validValues.includes(current));

    let changed = false;
    if (invalid) {
        widget.value = getDefaultControlValue(widget);
        changed = true;
    }

    if (!widget._relaySerializeWrapped) {
        const originalSerialize = widget.serializeValue?.bind(widget);
        widget.serializeValue = function (...args) {
            const values = Array.isArray(widget.options?.values) ? widget.options.values : null;
            const value = widget.value;
            const joined = Array.isArray(value) ? value.join(",") : value;
            const bad =
                value == null ||
                Array.isArray(value) ||
                (typeof joined === "string" && Array.isArray(values) && joined === values.join(",")) ||
                (Array.isArray(values) && values.length > 0 && !values.includes(value));
            if (bad) {
                widget.value = getDefaultControlValue(widget);
            }
            return originalSerialize ? originalSerialize(...args) : widget.value;
        };
        widget._relaySerializeWrapped = true;
    }

    return changed;
}

function sanitizeNode(node) {
    if (!TARGET_CLASSES.has(node?.comfyClass)) return;

    const seedWidget = getWidget(node, "seed");
    const controlWidget = getWidget(node, "control_after_generate");

    const seedChanged = sanitizeSeedWidget(seedWidget);
    const controlChanged = sanitizeControlWidget(controlWidget);
    const changed = seedChanged || controlChanged;

    if (changed) {
        app.graph.setDirtyCanvas(true);
    }
}

function scheduleSanitize(node) {
    setTimeout(() => sanitizeNode(node), 0);
    setTimeout(() => sanitizeNode(node), 100);
}

app.registerExtension({
    name: "RelayAPI.SeedWidgetFix",

    async nodeCreated(node) {
        scheduleSanitize(node);
    },

    async loadedGraphNode(node) {
        scheduleSanitize(node);
    },
});
