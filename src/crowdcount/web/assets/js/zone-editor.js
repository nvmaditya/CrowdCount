/* Canvas polygon editor drawn over the latest camera frame (/api/snapshot). */
"use strict";

class ZoneEditor {
    /**
     * @param {HTMLElement} host  element to render into
     * @param {object} handlers   {onDraftChange(points), onComplete(points)}
     */
    constructor(host, handlers = {}) {
        this.host = host;
        this.handlers = handlers;
        this.zones = [];
        this.draft = null; // array of [x, y] in frame pixels while drawing
        this.hover = null; // mouse position in frame pixels
        this.highlighted = null;
        this.image = null;
        this.canvas = null;
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
        if (!resp || !resp.ok) {
            this.image = null;
            this.canvas = null;
            this.host.className = "cc-media rounded-3";
            this.host.replaceChildren(CC.el("div", {},
                CC.emptyState("camera-video-off", "No camera frame yet",
                    "Zones are drawn on the live image. Start the detector, then reload: "),
                CC.el("div", { class: "text-center" }, CC.el("span", { class: "cc-code" }, "crowdcount run --source 0"))));
            return false;
        }
        const url = URL.createObjectURL(await resp.blob());
        this.image = await new Promise((resolve, reject) => {
            const img = new Image();
            img.onload = () => resolve(img);
            img.onerror = reject;
            img.src = url;
        });
        URL.revokeObjectURL(url);
        this._mount();
        return true;
    }

    _mount() {
        this.canvas = CC.el("canvas", { width: this.image.naturalWidth, height: this.image.naturalHeight, role: "img",
            "aria-label": "Camera frame with zone outlines" });
        this.host.className = "cc-editor is-idle";
        this.host.replaceChildren(this.canvas);
        this.canvas.addEventListener("click", (e) => this._click(e));
        this.canvas.addEventListener("dblclick", (e) => { e.preventDefault(); this.finish(); });
        this.canvas.addEventListener("mousemove", (e) => { this.hover = this._toFrame(e); if (this.drawing) this.render(); });
        this.canvas.addEventListener("mouseleave", () => { this.hover = null; this.render(); });
        this.render();
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
        if (!this.hasFrame) return;
        this.draft = [];
        this.host.classList.remove("is-idle");
        this._changed();
    }

    cancel() {
        this.draft = null;
        if (this.canvas) this.host.classList.add("is-idle");
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
        this.host.classList.add("is-idle");
        this.render();
        this.handlers.onComplete?.(points);
    }

    _changed() {
        this.render();
        this.handlers.onDraftChange?.(this.draft);
    }

    _toFrame(event) {
        const rect = this.canvas.getBoundingClientRect();
        const sx = this.canvas.width / rect.width;
        const sy = this.canvas.height / rect.height;
        return [Math.round((event.clientX - rect.left) * sx), Math.round((event.clientY - rect.top) * sy)];
    }

    _click(event) {
        if (!this.drawing) return;
        const p = this._toFrame(event);
        if (this.draft.length >= 3) {
            const [fx, fy] = this.draft[0];
            const closeRadius = 12 * (this.canvas.width / this.canvas.getBoundingClientRect().width);
            if (Math.hypot(p[0] - fx, p[1] - fy) <= closeRadius) {
                this.finish();
                return;
            }
        }
        this.draft.push(p);
        this._changed();
    }

    render() {
        if (!this.canvas) return;
        const ctx = this.canvas.getContext("2d");
        const scale = this.canvas.width / Math.max(this.canvas.getBoundingClientRect().width, 1);
        ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        ctx.drawImage(this.image, 0, 0);
        if (this.drawing) {
            ctx.fillStyle = "rgba(0, 0, 0, 0.25)";
            ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
        }
        ctx.lineJoin = "round";
        for (const zone of this.zones) {
            const strong = zone.name === this.highlighted;
            const color = zone.enabled ? zone.color : [148, 163, 184];
            this._path(ctx, zone.points, true);
            ctx.fillStyle = CC.rgb(color, strong ? 0.38 : 0.2);
            ctx.fill();
            ctx.setLineDash(zone.enabled ? [] : [8 * scale, 6 * scale]);
            ctx.lineWidth = (strong ? 3 : 2) * scale;
            ctx.strokeStyle = CC.rgb(color);
            ctx.stroke();
            ctx.setLineDash([]);
            this._label(ctx, zone.name + (zone.enabled ? "" : " (off)"), zone.points, color, scale);
        }
        if (this.drawing && this.draft.length) {
            const pts = this.hover ? [...this.draft, this.hover] : this.draft;
            this._path(ctx, pts, false);
            ctx.strokeStyle = "#fbbf24";
            ctx.lineWidth = 2 * scale;
            ctx.stroke();
            this.draft.forEach(([x, y], i) => {
                ctx.beginPath();
                ctx.arc(x, y, (i === 0 && this.draft.length >= 3 ? 7 : 4.5) * scale, 0, Math.PI * 2);
                ctx.fillStyle = i === 0 ? "#fff" : "#fbbf24";
                ctx.fill();
                ctx.strokeStyle = "#111827";
                ctx.lineWidth = 1.5 * scale;
                ctx.stroke();
            });
        }
    }

    _path(ctx, points, close) {
        ctx.beginPath();
        points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        if (close) ctx.closePath();
    }

    _label(ctx, text, points, color, scale) {
        const minX = Math.min(...points.map((p) => p[0]));
        const minY = Math.min(...points.map((p) => p[1]));
        ctx.font = `600 ${Math.round(13 * scale)}px ui-sans-serif, system-ui, sans-serif`;
        const w = ctx.measureText(text).width + 12 * scale;
        const h = 20 * scale;
        const x = Math.max(0, Math.min(minX + 4 * scale, this.canvas.width - w));
        const y = Math.max(0, minY + 4 * scale);
        ctx.fillStyle = CC.rgb(color, 0.92);
        ctx.fillRect(x, y, w, h);
        ctx.fillStyle = "#0b1220";
        ctx.fillText(text, x + 6 * scale, y + 14.5 * scale);
    }
}
