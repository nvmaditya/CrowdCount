/* Canvas polygon editor drawn over the latest camera frame (/api/snapshot). Needs icons.js and core.js.
 *
 * new ZoneEditor(host, {readOnly, onDraftChange(points), onComplete(points)}). Points are [x, y] in frame
 * pixels, the coordinates the zones file uses. A read-only editor shows the zones and ignores the pointer. */
"use strict";

class ZoneEditor {
    static INK = "#0b1220";
    static CLOSE_PX = 12; // a click this close (CSS px) to the first corner closes the shape

    /** "#0b1220" on light zone colours, white on dark ones (luma of the sRGB values, Rec. 709 weights). */
    static labelInk([r, g, b]) {
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 < 0.5 ? "#ffffff" : ZoneEditor.INK;
    }

    /**
     * @param {HTMLElement} host  element to render into
     * @param {object} options    {readOnly, onDraftChange(points), onComplete(points)}
     */
    constructor(host, options = {}) {
        this.host = host;
        this.handlers = options;
        this.readOnly = Boolean(options.readOnly);
        this.zones = [];
        this.draft = null; // array of [x, y] in frame pixels while drawing
        this.hover = null; // pointer position in frame pixels
        this.highlighted = null;
        this.image = null;
        this.canvas = null;
        this.density = 1; // canvas pixels per frame pixel
        this.resizer = typeof ResizeObserver === "function" ? new ResizeObserver(() => this.render()) : null;
        this.host.className = "zone-stage dark is-empty";
        // Labels use Geist: draw again once it has loaded, in case the first frame used the fallback.
        document.fonts?.load("600 13px Geist").then(() => this.render(), () => {});
    }

    get drawing() {
        return Array.isArray(this.draft);
    }

    get hasFrame() {
        return this.image !== null;
    }

    /** Fetch the latest raw frame. Returns false (and shows guidance) if there isn't one. */
    async loadFrame() {
        let resp;
        try {
            resp = await fetch(`/api/snapshot?t=${Date.now()}`);
        } catch (e) {
            resp = null;
        }
        let image = null;
        if (resp && resp.ok) {
            const url = URL.createObjectURL(await resp.blob());
            image = await new Promise((resolve) => {
                const img = new Image();
                img.onload = () => resolve(img);
                img.onerror = () => resolve(null);
                img.src = url;
            });
            URL.revokeObjectURL(url);
        }
        if (!image) {
            this.clearFrame();
            return false;
        }
        this.image = image;
        this._mount();
        return true;
    }

    /** Drop the frame (and any shape in progress) and show how to get one. */
    clearFrame() {
        const wasDrawing = this.drawing;
        this.image = null;
        this.draft = null;
        this._unmount();
        this.host.className = "zone-stage dark is-empty";
        this.host.replaceChildren(CC.emptyState({
            icon: "video-off",
            title: "No camera frame yet",
            hint: "Zones are drawn on the live image. Start the detector, then grab a fresh frame.",
            code: "crowdcount run --source <video or stream URL>",
        }));
        if (wasDrawing) this.handlers.onDraftChange?.(null);
    }

    _unmount() {
        if (this.canvas) this.resizer?.unobserve(this.canvas);
        this.canvas = null;
        this.density = 1;
    }

    _mount() {
        this._unmount();
        const { naturalWidth: w, naturalHeight: h } = this.image;
        this.canvas = CC.el("canvas", {
            class: "zone-canvas", width: w, height: h, role: "img", "aria-label": "Camera frame with the zones drawn on it",
        });
        this.host.replaceChildren(this.canvas);
        this._syncClass();
        if (!this.readOnly) {
            this.canvas.addEventListener("click", (e) => this._click(e));
            this.canvas.addEventListener("dblclick", (e) => {
                e.preventDefault();
                this.finish();
            });
            this.canvas.addEventListener("pointermove", (e) => {
                this.hover = this._toFrame(e);
                if (this.drawing) this.render();
            });
            this.canvas.addEventListener("pointerleave", () => {
                this.hover = null;
                if (this.drawing) this.render();
            });
        }
        this.resizer?.observe(this.canvas);
        this.render();
    }

    _syncClass() {
        if (!this.canvas) return;
        this.host.className = `zone-stage dark${this.readOnly ? " is-readonly" : this.drawing ? " is-drawing" : ""}`;
    }

    setZones(zones) {
        this.zones = zones;
        this.render();
    }

    highlight(name) {
        this.highlighted = name;
        this.render();
    }

    start() {
        if (this.readOnly || !this.hasFrame) return;
        this.draft = [];
        this._changed();
    }

    cancel() {
        this.draft = null;
        this._changed();
    }

    undo() {
        if (this.drawing && this.draft.length) {
            this.draft.pop();
            this._changed();
        }
    }

    finish() {
        if (!this.drawing || this.draft.length < 3) return;
        const points = this.draft.slice();
        this.draft = null;
        this._syncClass();
        this.render();
        this.handlers.onComplete?.(points);
    }

    _changed() {
        this._syncClass();
        this.render();
        this.handlers.onDraftChange?.(this.draft);
    }

    /** Frame pixels per CSS pixel, so strokes and labels keep their on-screen size at any frame size. */
    _unit() {
        const width = this.canvas.getBoundingClientRect().width;
        return width > 0 ? this.image.naturalWidth / width : 1;
    }

    _toFrame(event) {
        const rect = this.canvas.getBoundingClientRect();
        const sx = this.image.naturalWidth / rect.width;
        const sy = this.image.naturalHeight / rect.height;
        return [Math.round((event.clientX - rect.left) * sx), Math.round((event.clientY - rect.top) * sy)];
    }

    _click(event) {
        if (!this.drawing) return;
        const p = this._toFrame(event);
        const unit = this._unit();
        const near = (q, px) => Math.hypot(p[0] - q[0], p[1] - q[1]) <= px * unit;
        if (this.draft.length >= 3 && near(this.draft[0], ZoneEditor.CLOSE_PX)) {
            this.finish();
            return;
        }
        // A double click sends two clicks first: don't add the same corner twice.
        if (this.draft.length && near(this.draft[this.draft.length - 1], 3)) return;
        this.draft.push(p);
        this._changed();
    }

    /** Size the canvas to its on-screen pixels (at least the frame's), so lines and labels stay sharp. */
    _fit() {
        const { naturalWidth: w, naturalHeight: h } = this.image;
        const shown = this.canvas.getBoundingClientRect().width * (window.devicePixelRatio || 1);
        const density = Math.min(4, Math.max(1, shown / w));
        if (Math.abs(density - this.density) > 0.05 || this.canvas.width !== Math.round(w * this.density)) {
            this.density = density;
            this.canvas.width = Math.round(w * density);
            this.canvas.height = Math.round(h * density);
        }
    }

    render() {
        if (!this.canvas || !this.image) return;
        this._fit();
        const ctx = this.canvas.getContext("2d");
        const { naturalWidth: w, naturalHeight: h } = this.image;
        const u = this._unit();
        ctx.setTransform(this.density, 0, 0, this.density, 0, 0);
        ctx.clearRect(0, 0, w, h);
        ctx.drawImage(this.image, 0, 0, w, h);
        if (this.drawing) {
            ctx.fillStyle = "rgba(0, 0, 0, 0.22)";
            ctx.fillRect(0, 0, w, h);
        }
        ctx.lineJoin = "round";
        for (const zone of this.zones) this._zone(ctx, zone, u);
        for (const zone of this.zones) {
            const text = zone.enabled === false ? `${zone.name} · off` : zone.name;
            this._label(ctx, text, zone.points, zone.color, u);
        }
        if (this.drawing) this._draft(ctx, u);
    }

    _zone(ctx, zone, u) {
        const strong = zone.name === this.highlighted;
        const on = zone.enabled !== false;
        this._path(ctx, zone.points, true);
        ctx.fillStyle = CC.rgb(zone.color, strong ? 0.38 : on ? 0.22 : 0.1);
        ctx.fill();
        ctx.setLineDash(on ? [] : [6 * u, 5 * u]);
        ctx.lineWidth = (strong ? 3 : 2) * u;
        ctx.strokeStyle = CC.rgb(zone.color);
        ctx.stroke();
        ctx.setLineDash([]);
    }

    _draft(ctx, u) {
        const pts = this.draft;
        const white = "#ffffff";
        if (pts.length > 1) {
            this._path(ctx, pts, false);
            ctx.strokeStyle = white;
            ctx.lineWidth = 2 * u;
            ctx.stroke();
        }
        if (pts.length && this.hover) {
            const [x, y] = pts[pts.length - 1];
            ctx.beginPath();
            ctx.moveTo(x, y);
            ctx.lineTo(...this.hover);
            ctx.setLineDash([6 * u, 5 * u]);
            ctx.strokeStyle = white;
            ctx.lineWidth = 1.5 * u;
            ctx.stroke();
            ctx.setLineDash([]);
        }
        pts.forEach(([x, y], i) => {
            ctx.beginPath();
            ctx.arc(x, y, (i === 0 ? 7 : 5) * u, 0, Math.PI * 2);
            ctx.fillStyle = i === 0 ? white : ZoneEditor.INK;
            ctx.fill();
            ctx.strokeStyle = i === 0 ? ZoneEditor.INK : white;
            ctx.lineWidth = 2 * u;
            ctx.stroke();
        });
        if (this.hover) {
            ctx.beginPath();
            ctx.arc(this.hover[0], this.hover[1], 4 * u, 0, Math.PI * 2);
            ctx.strokeStyle = white;
            ctx.lineWidth = 1.5 * u;
            ctx.stroke();
        }
    }

    _path(ctx, points, close) {
        ctx.beginPath();
        points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        if (close) ctx.closePath();
    }

    /** A pill in the zone's colour at its top-left corner, kept inside the frame. */
    _label(ctx, text, points, color, u) {
        const { naturalWidth: w, naturalHeight: h } = this.image;
        const minX = Math.min(...points.map((p) => p[0]));
        const minY = Math.min(...points.map((p) => p[1]));
        ctx.font = `600 ${13 * u}px Geist, sans-serif`;
        const room = w - 16 * u;
        if (ctx.measureText(text).width > room) {
            // Wider than the frame: cut it short rather than off at the edge.
            while (text.length > 1 && ctx.measureText(`${text}…`).width > room) text = text.slice(0, -1);
            text = `${text.trimEnd()}…`;
        }
        const bw = ctx.measureText(text).width + 16 * u;
        const bh = 22 * u;
        const x = Math.max(0, Math.min(minX + 6 * u, w - bw));
        const y = Math.max(0, Math.min(minY + 6 * u, h - bh));
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, y, bw, bh, 4 * u);
        else ctx.rect(x, y, bw, bh);
        ctx.fillStyle = CC.rgb(color);
        ctx.fill();
        ctx.fillStyle = ZoneEditor.labelInk(Array.isArray(color) ? color : [45, 212, 191]);
        ctx.fillText(text, x + 8 * u, y + 15 * u);
    }
}
