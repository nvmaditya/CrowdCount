/* Live page: detector status, alert banner, stat cards, camera, zones, people over time,
 * heatmap and recent alerts. Needs Chart.js, icons.js, core.js and shell.js; all live data
 * arrives through Shell's cc:summary and cc:layout events. */
"use strict";

(() => {
    const { el, $, $$, icon, api, toast, emptyState } = CC;

    const CHART_MS = 5000;
    const HEATMAP_MS = 5000;
    const EVENTS_MS = 10000;
    const RANGE_KEY = "cc_chart_range";
    const RANGES = {
        600: { window: 10, sub: "Peak count per 10-second window" },
        1800: { window: 30, sub: "Peak count per 30-second window" },
        3600: { window: 60, sub: "Peak count per minute" },
    };

    const PILL = {
        running: { dot: "is-ok", label: "Detecting", detail: (s) => [s.source, `${(s.fps || 0).toFixed(1)} fps`, (s.model || "").replace(/\.pt$/, "")] },
        starting: { dot: "is-warn", label: "Starting", detail: (s) => [s.source] },
        stopped: { dot: "", label: "Finished", detail: (s) => [s.source] },
        error: { dot: "is-danger", label: "Error", detail: () => [] },
        offline: { dot: "", label: "Offline", detail: () => [] },
        disconnected: { dot: "", label: "Disconnected", detail: () => [] },
    };

    const CAMERA_EMPTY = {
        starting: { icon: "loader-circle", title: "Starting the detector…", hint: "Loading the model. The first run downloads weights." },
        stopped: { icon: "circle-stop", title: "Detection finished", hint: "The video ended. Counts and charts below stay available." },
        error: { icon: "bug", title: "The detector stopped with an error", hint: "Check the terminal running CrowdCount." },
        disconnected: { icon: "wifi-off", title: "Disconnected", hint: "Waiting for the server…" },
        offline: {
            icon: "video-off", title: "No detector running", hint: "Start one alongside this dashboard:",
            code: "crowdcount run --source <video or stream URL>",
        },
    };

    const HEAT_EMPTY = { icon: "flame", title: "Nothing to show yet", hint: "The heatmap builds up as people are detected." };

    const TOTAL_FOOT = {
        over: ["Over limit", "t-destructive"],
        near: ["Elevated", "t-warn"],
        normal: ["Normal", "t-muted"],
        off: ["Alert off", "t-muted"],
        none: [CC.LEVEL_TEXT.none, "t-muted"],
    };

    const FILL = { over: "is-over", near: "is-near", normal: "", off: "is-off" };

    const ui = {
        summary: null,
        connected: true,
        scopes: [],
        acked: new Set(), // acknowledged here; hidden until the next poll confirms it
        cameraView: null,
        range: loadRange(),
        rows: [],
        starts: [],
        chart: null,
        chartZones: [],
        chartLimit: null,
        heatSince: undefined,
        alertIds: null,
        keys: {},
    };

    /** True when `value` differs from what `region` last rendered (and remembers it). */
    function changed(region, value) {
        const key = JSON.stringify(value);
        if (ui.keys[region] === key) return false;
        ui.keys[region] = key;
        return true;
    }

    function setText(selector, text, cls) {
        const node = $(selector);
        const value = String(text);
        if (node.textContent !== value) node.textContent = value;
        if (cls !== undefined && node.className !== cls) node.className = cls;
    }

    function detectorState() {
        if (!ui.connected) return "disconnected";
        const state = ui.summary?.status?.state;
        return PILL[state] ? state : "offline";
    }

    function frameRatio() {
        const st = ui.summary?.status;
        return st?.frame_width && st?.frame_height ? `${st.frame_width} / ${st.frame_height}` : "4 / 3";
    }

    // ------------------------------------------------------------ header

    const pill = el("span", { class: "status-pill", hidden: true },
        el("span", { class: "dot" }),
        el("span", { class: "status-label" }),
        el("span", { class: "status-detail" }));

    function renderPill() {
        const state = detectorState();
        const status = (ui.connected && ui.summary?.status) || {};
        const spec = PILL[state];
        const detail = spec.detail(status).filter(Boolean).join(" · ");
        const title = [status.model, status.tracker, status.device].filter(Boolean).join(" · ");
        if (!changed("pill", [state, detail, title])) return;
        const [dot, label, detailNode] = pill.children;
        dot.className = `dot ${spec.dot}`.trim();
        label.textContent = spec.label;
        detailNode.textContent = detail;
        detailNode.hidden = !detail;
        if (title) pill.title = title;
        else pill.removeAttribute("title");
        pill.hidden = false;
    }

    function exportButton(label, iconName, path) {
        const button = el("button", { type: "button", class: "ui-btn v-outline s-sm" }, icon(iconName), label);
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                await CC.download(path);
            } catch (e) {
                if (e.status === 404) toast("Nothing to export yet: start the detector first.", "warn");
                else toast(e.message, "error");
            } finally {
                button.disabled = false;
            }
        });
        return button;
    }

    // ------------------------------------------------------------ banner

    /** How a scope reads inside a sentence. */
    const inSentence = (scope) => (scope.kind === "global" ? "total occupancy" : scope.name);

    /** "a", "a and b", "a, b and c". */
    function listText(items) {
        return items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
    }

    function bannerModel() {
        const s = ui.summary;
        if (s.status?.state === "error" && s.status.message) {
            return { danger: false, icon: "bug", title: "The detector stopped with an error", detail: s.status.message, ids: [] };
        }
        const over = ui.scopes.filter((x) => x.level === "over" && x.alert?.banner && !x.alert.acknowledged
            && !ui.acked.has(x.alert.id));
        if (!over.length) return null;
        const [first, ...rest] = over;
        const title = rest.length
            ? `${first.label} and ${rest.length} more are over their limits`
            : `${first.label} is over its limit`;
        const now = first.now ?? first.alert.current;
        let detail = `${CC.plural(now, "person", "people")} against a limit of ${first.rule.limit}, `
            + `since ${CC.fmtTime(first.alert.since)}.`;
        if (rest.length) detail += ` Also over: ${rest.map(inSentence).join(", ")}.`;
        const near = ui.scopes.filter((x) => x.level === "near" && x.alert?.banner);
        if (near.length) {
            // Starts a sentence, so the total is capitalised there; zone names are left as written.
            const names = near.map((x, i) => (i === 0 && x.kind === "global" ? CC.TOTAL_LABEL : inSentence(x)));
            const one = near.length === 1;
            detail += ` ${listText(names)} ${one ? "is" : "are"} close to ${one ? "its limit" : "their limits"}.`;
        }
        return { danger: true, icon: "triangle-alert", title, detail, ids: over.map((x) => x.alert.id) };
    }

    function renderBanner() {
        const box = $("#banner");
        const model = bannerModel();
        if (!model) {
            box.hidden = true;
            return;
        }
        const admin = CC.isAdmin();
        // Rebuild the frame only when its shape changes, so a button is never swapped out under the pointer.
        if (changed("banner", [model.danger, model.icon, model.ids, admin])) {
            box.className = model.danger ? "banner is-danger" : "banner is-neutral";
            const actions = el("div", { class: "banner-actions" },
                admin ? el("button", {
                    type: "button", class: "ui-btn v-outline s-sm",
                    onclick: (event) => acknowledge(event.currentTarget, model.ids),
                }, "Acknowledge") : null,
                el("a", { class: "ui-btn v-default s-sm", href: "alerts.html" }, "Review alerts"));
            box.replaceChildren(
                icon(model.icon, { size: 18, cls: "banner-icon" }),
                el("div", { class: "banner-body" },
                    el("span", { class: "banner-title" }),
                    el("span", { class: "banner-detail" })),
                ...(model.ids.length ? [actions] : []));
        }
        setText("#banner .banner-title", model.title);
        setText("#banner .banner-detail", model.detail);
        box.hidden = false;
    }

    async function acknowledge(button, ids) {
        button.disabled = true;
        const results = await Promise.allSettled(ids.map((id) => api(
            `/admin/alerts/${encodeURIComponent(id)}/acknowledge`, { method: "POST" })));
        results.forEach((r, i) => r.status === "fulfilled" && ui.acked.add(ids[i]));
        const failed = results.find((r) => r.status === "rejected");
        if (failed) {
            toast(failed.reason.message, "error");
            button.disabled = false;
        } else {
            toast("Acknowledged");
        }
        if (ui.summary) renderBanner();
    }

    /** Forget local acknowledgements once the server reports them (or the alert has cleared). */
    function pruneAcked() {
        const pending = new Set(Object.values(ui.summary.alerts || {}).filter((a) => !a.acknowledged).map((a) => a.id));
        for (const id of ui.acked) if (!pending.has(id)) ui.acked.delete(id);
    }

    // ------------------------------------------------------------ stat cards

    function renderStats() {
        const s = ui.summary;
        const zoneCount = ui.scopes.filter((x) => x.kind === "zone").length;
        setText("#stat-now", s.last_update ? s.total_count : "–");
        setText("#stat-now-main", zoneCount ? `Across ${CC.plural(zoneCount, "zone")}` : "No zones drawn");
        setText("#stat-now-sub", s.last_update ? `Updated ${CC.fmtTime(s.last_update)}` : "Waiting for the detector");

        setText("#stat-peak", s.peak_count);
        setText("#stat-peak-main", s.peak_at ? `at ${CC.fmtTime(s.peak_at)}` : "–");
        setText("#stat-peak-sub", s.status?.started_at ? `Since detection started at ${CC.fmtClock(s.status.started_at)}` : "");

        const total = ui.scopes.find((x) => x.kind === "global");
        const [levelText, tone] = TOTAL_FOOT[total?.level || "none"];
        setText("#stat-total", total && total.ratio !== null ? `${Math.round(total.ratio * 100)}%` : "–");
        setText("#stat-total-main", levelText, `stat-foot-main ${tone}`);
        setText("#stat-total-sub", total?.rule ? `${s.total_count} of a ${total.rule.limit}-person limit` : "");

        const alerts = Object.values(s.alerts || {});
        const over = alerts.filter((a) => a.level === "over").length;
        const near = alerts.filter((a) => a.level === "near").length;
        const rules = [s.rules?.global, ...Object.values(s.rules?.zones || {})].filter((r) => r?.enabled).length;
        setText("#stat-alerts", over, over ? "stat-value t-destructive" : "stat-value");
        setText("#stat-alerts-main", `${over} over, ${near} near the limit`);
        setText("#stat-alerts-sub", `${CC.plural(rules, "alert rule")} on`);
    }

    // ------------------------------------------------------------ camera

    function renderCamera() {
        const state = detectorState();
        const st = (ui.connected && ui.summary?.status) || {};
        const streaming = state === "running" && document.visibilityState === "visible";
        const size = st.frame_width && st.frame_height ? `${st.frame_width} × ${st.frame_height}` : "";
        setText("#camera-size", size);
        $("#camera-size").hidden = !size;
        $("#camera-live").hidden = !streaming;
        $("#camera-fullscreen").hidden = !streaming;

        const media = $("#camera-media");
        const view = streaming ? "stream" : state === "running" ? "paused" : state;
        if (view !== ui.cameraView) {
            ui.cameraView = view;
            media.classList.toggle("is-empty", !streaming);
            if (streaming) {
                // Dropping this <img> later closes the MJPEG connection.
                media.replaceChildren(el("img", {
                    class: "media-img", src: `/api/stream?t=${Date.now()}`,
                    alt: "Live camera view with tracked people and the zones drawn over it",
                }));
            } else if (view === "paused") {
                media.replaceChildren(); // tab hidden: stop streaming until it is visible again
            } else {
                const empty = emptyState(CAMERA_EMPTY[state]);
                if (state === "starting") $(".empty-icon svg", empty)?.classList.add("spin");
                media.replaceChildren(empty);
            }
        }
        const img = $(".media-img", media);
        if (img && img.style.aspectRatio !== frameRatio()) img.style.aspectRatio = frameRatio();
    }

    // ------------------------------------------------------------ zones

    function zoneRow(z, visitors) {
        const pct = z.ratio === null ? null : Math.round(z.ratio * 100);
        return el("div", { class: "zone-row" },
            el("div", { class: "zone-row-head" },
                el("span", { class: "swatch", style: { background: z.color } }),
                el("span", { class: "zone-name" }, z.name),
                CC.levelBadge(z.level)),
            el("div", { class: "zone-figures" },
                el("span", { class: "zone-now" }, z.now ?? "–"),
                z.rule ? el("span", { class: "zone-limit" }, `/ ${z.rule.limit} limit`) : null,
                z.rule && pct !== null ? el("span", { class: "zone-pct" }, `${pct}% of limit`) : null),
            z.rule && pct !== null
                ? el("div", { class: "bar" },
                    el("span", { class: `fill ${FILL[z.level] || ""}`.trim(), style: { width: `${Math.min(pct, 100)}%` } }))
                : null,
            typeof visitors === "number"
                ? el("span", { class: "zone-tracks" }, `${CC.plural(visitors, "unique track")} this run`)
                : null);
    }

    function renderZones() {
        const admin = CC.isAdmin();
        if (changed("zones-edit", admin)) {
            $("#zones-edit").replaceChildren(...(admin ? [el("a", { class: "card-link", href: "zones.html" }, "Edit zones")] : []));
        }
        const zones = ui.scopes.filter((x) => x.kind === "zone");
        const visitors = (name) => ui.summary.zones?.[name]?.total_visitors;
        const model = zones.map((z) => [z.name, z.color, z.level, z.now, z.rule?.limit, z.ratio, visitors(z.name)]);
        if (!changed("zones", [model, admin])) return;
        $("#zones-note").hidden = !zones.length;
        if (!zones.length) {
            const empty = emptyState({
                icon: "pentagon", title: "No zones yet", hint: "Draw zones on the live frame to count areas separately.",
            });
            if (admin) empty.append(el("a", { class: "ui-btn v-outline s-sm empty-action", href: "zones.html" }, "Draw zones"));
            $("#zones-list").replaceChildren(empty);
            return;
        }
        $("#zones-list").replaceChildren(...zones.map((z) => zoneRow(z, visitors(z.name))));
    }

    // ------------------------------------------------------------ people over time

    function loadRange() {
        try {
            const value = Number(localStorage.getItem(RANGE_KEY));
            if (RANGES[value]) return value;
        } catch (e) { /* storage blocked */ }
        return 600;
    }

    function renderRange() {
        for (const button of $$("[data-range]")) {
            const on = Number(button.dataset.range) === ui.range;
            button.classList.toggle("active", on);
            button.setAttribute("aria-pressed", String(on));
        }
        setText("#chart-sub", RANGES[ui.range].sub);
    }

    let historySeq = 0;
    async function refreshHistory() {
        const seq = ++historySeq;
        let data;
        try {
            data = await api(`/history?limit=${ui.range}`);
        } catch (e) {
            return; // keep the last chart; the offline bar explains why
        }
        if (seq !== historySeq) return; // a newer request (another range) superseded this one
        ui.rows = data.history || [];
        drawChart();
    }

    /** Group one-second rows into `seconds`-long windows, keeping each series' peak per window. */
    function bucket(rows, seconds) {
        const windows = new Map();
        for (const row of rows) {
            const ms = Date.parse(row.timestamp);
            if (Number.isNaN(ms)) continue;
            const key = Math.floor(ms / 1000 / seconds);
            let w = windows.get(key);
            if (!w) {
                w = { start: key * seconds * 1000, total: 0, zones: {} };
                windows.set(key, w);
            }
            w.total = Math.max(w.total, row.total_count);
            for (const [zone, n] of Object.entries(row.zone_counts || {})) w.zones[zone] = Math.max(w.zones[zone] ?? 0, n);
        }
        return [...windows.values()].sort((a, b) => a.start - b.start);
    }

    /** A token colour as an rgba() string Chart.js can draw with. */
    function tone(name, alpha = 1) {
        const [r, g, b, a] = CC.tokenRgb(name);
        return `rgba(${r}, ${g}, ${b}, ${Math.round(a * alpha * 1000) / 1000})`;
    }

    function zoneCss(name) {
        const color = Shell.layout[name]?.color;
        return color ? CC.rgb(color) : null;
    }

    function globalLimit() {
        const rule = ui.summary?.rules?.global;
        return rule?.enabled ? rule.limit : null;
    }

    function destroyChart() {
        if (ui.chart) ui.chart.destroy();
        ui.chart = null;
    }

    function createChart() {
        const canvas = el("canvas", { role: "img", "aria-label": "Line chart of people over time, in total and per zone" });
        $("#chart-box").replaceChildren(canvas, el("span", { class: "chart-limit", id: "chart-limit", hidden: true }));
        const css = getComputedStyle(document.documentElement);
        const sans = css.getPropertyValue("--font-sans").trim();
        const mono = css.getPropertyValue("--font-mono").trim();
        const ticks = { color: tone("--muted-foreground"), font: { family: mono, size: 11 } };
        Chart.defaults.font.family = sans;
        ui.chart = new Chart(canvas, {
            type: "line",
            data: { labels: [], datasets: [] },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                interaction: { mode: "index", intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        mode: "index",
                        intersect: false,
                        backgroundColor: tone("--popover"),
                        titleColor: tone("--popover-foreground"),
                        bodyColor: tone("--popover-foreground"),
                        borderColor: tone("--border"),
                        borderWidth: 1,
                        cornerRadius: 8,
                        padding: 10,
                        boxPadding: 4,
                        titleFont: { family: sans, size: 12, weight: "600" },
                        bodyFont: { family: sans, size: 12 },
                        filter: (item) => !item.dataset.isLimit,
                        callbacks: {
                            title: (items) => (items.length ? CC.fmtTime(ui.starts[items[0].dataIndex]) : ""),
                            labelColor: (item) => ({ borderColor: item.dataset.borderColor, backgroundColor: item.dataset.borderColor }),
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { display: false },
                        ticks: { ...ticks, maxTicksLimit: 6, maxRotation: 0, autoSkip: true },
                    },
                    y: {
                        beginAtZero: true,
                        suggestedMax: 4,
                        grid: { color: tone("--border") },
                        border: { display: false },
                        ticks: { ...ticks, precision: 0 },
                    },
                },
            },
            // Runs after every update, resizes included, once the scales are laid out.
            plugins: [{ id: "ccLimitLabel", afterUpdate: placeLimitLabel }],
        });
    }

    function placeLimitLabel() {
        const label = $("#chart-limit");
        const chart = ui.chart;
        if (!label || !chart?.scales?.y || ui.chartLimit === null) {
            if (label) label.hidden = true;
            return;
        }
        label.textContent = `Total limit ${ui.chartLimit}`;
        label.style.top = `${chart.scales.y.getPixelForValue(ui.chartLimit)}px`;
        label.style.right = `${chart.width - chart.chartArea.right}px`;
        label.hidden = false;
    }

    function drawChart() {
        const windows = bucket(ui.rows, RANGES[ui.range].window);
        if (!windows.length) {
            destroyChart();
            ui.chartZones = [];
            if (!$("#chart-box .empty")) $("#chart-box").replaceChildren(emptyState({ icon: "chart-line", title: "Waiting for data" }));
            renderLegend();
            return;
        }
        // Zones in drawing order (as in the zones file), then any that have since been removed.
        const seen = new Set(windows.flatMap((w) => Object.keys(w.zones)));
        const names = [...Object.keys(Shell.layout).filter((n) => seen.has(n)),
            ...[...seen].filter((n) => !(n in Shell.layout)).sort()];
        const limit = globalLimit();
        const muted = tone("--muted-foreground");
        if (!ui.chart) createChart();
        ui.chartZones = names;
        ui.chartLimit = limit;
        ui.starts = windows.map((w) => w.start);
        const chart = ui.chart;
        chart.data.labels = windows.map((w) => CC.fmtClock(w.start));
        chart.data.datasets = [
            {
                label: "Total", data: windows.map((w) => w.total),
                borderColor: tone("--foreground"), backgroundColor: tone("--foreground", 0.06),
                borderWidth: 2.25, fill: true, tension: 0.3, pointRadius: 0, pointHoverRadius: 3,
            },
            ...names.map((name) => ({
                label: name, data: windows.map((w) => w.zones[name] ?? null),
                borderColor: zoneCss(name) || muted, backgroundColor: zoneCss(name) || muted,
                borderWidth: 1.75, fill: false, tension: 0.3, pointRadius: 0, pointHoverRadius: 3,
            })),
            ...(limit === null ? [] : [{
                label: "Total limit", isLimit: true, data: windows.map(() => limit),
                borderColor: tone("--destructive"), borderWidth: 1.25, borderDash: [5, 5],
                fill: false, tension: 0, pointRadius: 0, pointHoverRadius: 0,
            }]),
        ];
        chart.options.scales.y.suggestedMax = Math.max(4, limit === null ? 0 : limit * 1.1);
        chart.update("none");
        renderLegend();
    }

    function renderLegend() {
        const s = ui.summary;
        const items = ui.chart ? [
            { name: "Total", color: "var(--foreground)", now: s?.last_update ? s.total_count : null },
            ...ui.chartZones.map((name) => ({
                name, color: zoneCss(name) || "var(--muted-foreground)", now: s?.zones?.[name]?.current ?? null,
            })),
        ] : [];
        if (!changed("legend", items)) return;
        const legend = $("#chart-legend");
        legend.hidden = !items.length;
        legend.replaceChildren(...items.map((item) => el("span", { class: "legend-item" },
            el("span", { class: "swatch is-line", style: { background: item.color } }),
            el("span", { class: "legend-name" }, item.name),
            el("span", { class: "legend-value" }, item.now ?? "–"))));
    }

    // ------------------------------------------------------------ heatmap

    function showHeatEmpty() {
        if (!$("#heat-media .empty")) $("#heat-media").replaceChildren(emptyState(HEAT_EMPTY));
        $("#heat-scale").hidden = true;
    }

    let heatSeq = 0;
    function refreshHeatmap() {
        if (!ui.connected || !ui.summary) return; // keep the last image while the server is away
        if (!ui.summary.heatmap_since) {
            showHeatEmpty();
            return;
        }
        const seq = ++heatSeq;
        const img = el("img", { class: "heat-img", alt: "Heatmap of where people spent time, over the latest camera frame" });
        img.style.aspectRatio = frameRatio();
        img.addEventListener("load", () => {
            if (seq !== heatSeq) return;
            $("#heat-media").replaceChildren(img);
            $("#heat-scale").hidden = false;
        });
        img.addEventListener("error", () => seq === heatSeq && showHeatEmpty());
        img.src = `/api/heatmap?t=${Date.now()}`; // loads off-screen, then swaps in without a flash
    }

    function renderHeatmap() {
        const since = ui.summary.heatmap_since;
        setText("#heat-sub", since ? `Where people spent time since ${CC.fmtClock(since)}` : "Builds up as people are detected");
        $("#heat-reset").hidden = !CC.isSignedIn();
        if (since !== ui.heatSince) {
            ui.heatSince = since;
            refreshHeatmap();
        }
    }

    async function resetHeatmap(event) {
        const button = event.currentTarget;
        button.disabled = true;
        try {
            await api("/heatmap/reset", { method: "POST" });
            toast("Heatmap reset");
            setTimeout(refreshHeatmap, 300);
        } catch (e) {
            toast(e.message, "error");
        } finally {
            button.disabled = false;
        }
    }

    // ------------------------------------------------------------ recent events

    function eventRow(record) {
        const label = record.alert_type === "global" ? CC.TOTAL_LABEL : record.alert_type;
        const level = record.level === "near" ? "near" : "over";
        const text = level === "over"
            ? `${label} went over its limit: ${CC.plural(record.actual_count, "person", "people")}, limit ${record.threshold}`
            : `${label} came close to its limit: ${record.actual_count} of ${record.threshold}`;
        return el("tr", {},
            el("td", { class: "events-time" }, CC.fmtStamp(record.timestamp)),
            el("td", {}, CC.levelBadge(level)),
            el("td", {}, text),
            el("td", { class: "t-muted" }, label));
    }

    let eventsSeq = 0;
    async function refreshEvents() {
        const seq = ++eventsSeq;
        let data;
        try {
            data = await api("/alerts/history?limit=5");
        } catch (e) {
            return;
        }
        if (seq !== eventsSeq) return;
        const records = data.alerts || [];
        $("#events-body").replaceChildren(...(records.length ? records.map(eventRow) : [
            el("tr", { class: "events-empty" }, el("td", { colspan: 4 },
                "No alerts yet. Events appear here when a count reaches an early warning or a limit.")),
        ]));
    }

    // ------------------------------------------------------------ wiring

    function render() {
        renderPill();
        renderCamera();
        if (!ui.summary) return;
        pruneAcked();
        renderBanner();
        renderStats();
        renderZones();
        renderLegend();
        renderHeatmap();
        setText("#footer-version", `CrowdCount ${ui.summary.version} · AGPL-3.0`);
        if (changed("limit", globalLimit())) drawChart();
        const ids = Object.values(ui.summary.alerts || {}).map((a) => a.id).sort().join(",");
        if (ui.alertIds !== null && ids !== ui.alertIds) refreshEvents();
        ui.alertIds = ids;
    }

    document.addEventListener("cc:summary", (event) => {
        ui.summary = event.detail.summary;
        ui.connected = event.detail.connected;
        ui.scopes = CC.scopes(ui.summary, Shell.layout);
        render();
    });

    document.addEventListener("cc:layout", () => {
        ui.scopes = CC.scopes(ui.summary, Shell.layout);
        if (ui.summary) renderZones();
        const colours = Object.entries(Shell.layout).map(([name, z]) => [name, z.color]);
        if (changed("layout", colours) && ui.chart) drawChart();
    });

    document.addEventListener("cc:theme", () => {
        destroyChart();
        drawChart();
    });

    document.addEventListener("visibilitychange", renderCamera);

    $$("[data-range]").forEach((button) => button.addEventListener("click", () => {
        const value = Number(button.dataset.range);
        if (value === ui.range) return;
        ui.range = value;
        try { localStorage.setItem(RANGE_KEY, String(value)); } catch (e) { /* storage blocked */ }
        renderRange();
        refreshHistory();
    }));

    $("#camera-fullscreen").append(icon("maximize-2"));
    $("#camera-fullscreen").addEventListener("click", () => {
        const media = $("#camera-media");
        if (media.requestFullscreen) media.requestFullscreen().catch(() => { /* refused: stay inline */ });
    });

    $("#heat-reset").append(icon("rotate-ccw", { size: 14 }), "Reset");
    $("#heat-reset").addEventListener("click", resetHeatmap);

    $("#events-all").append(el("a", { class: "card-link", href: "alerts.html" }, "View all alerts"));

    renderRange();
    drawChart(); // the empty state until history arrives
    Shell.init({
        page: "live",
        title: "Live",
        subtitle: pill,
        actions: [
            exportButton("Export CSV", "download", "/export/csv"),
            exportButton("PDF report", "file-text", "/export/pdf"),
        ],
    });
    refreshHistory();
    refreshEvents();
    // Canvas text can't swap fonts by itself: redraw once Geist Mono has loaded.
    document.fonts?.load('11px "Geist Mono"').then(() => ui.chart?.update("none")).catch(() => { /* fallback font */ });

    const running = () => ui.connected && ui.summary?.status?.state === "running";
    setInterval(() => running() && refreshHistory(), CHART_MS);
    setInterval(() => running() && refreshHeatmap(), HEATMAP_MS);
    setInterval(() => ui.connected && refreshEvents(), EVENTS_MS);
})();
