/* Live dashboard: polls /api/summary every second and renders counts, zones, charts. */
"use strict";

(() => {
    const { el, icon, $, api, toast, emptyState } = CC;

    const POLL_MS = 1000;
    const HISTORY_MS = 5000;
    const HEATMAP_MS = 5000;
    const LAYOUT_MS = 10000;
    const HISTORY_POINTS = 600; // 10 minutes at one sample per second

    const ui = {
        summary: null,
        connected: true,
        layout: {}, // zone name -> {color, enabled}
        streaming: false,
        charts: {},
        thresholdDirty: false,
    };

    // ------------------------------------------------------------ status & banner

    const STATE_TEXT = {
        running: (s) => [el("strong", {}, "Live"), ` · ${s.source || "source"} · ${(s.fps || 0).toFixed(1)} fps`],
        starting: (s) => [el("strong", {}, "Starting"), ` · loading model for ${s.source || "source"}`],
        stopped: (s) => [el("strong", {}, "Detection finished"), s.source ? ` · ${s.source}` : ""],
        error: () => [el("strong", {}, "Detector error")],
        offline: () => [el("strong", {}, "Detector offline")],
    };

    function renderStatus() {
        const box = $("#status");
        if (!ui.connected) {
            box.dataset.state = "disconnected";
            $("#status-text").replaceChildren(el("strong", {}, "Disconnected"));
            return;
        }
        const status = ui.summary.status;
        box.dataset.state = status.state;
        $("#status-text").replaceChildren(...(STATE_TEXT[status.state] || STATE_TEXT.offline)(status));
        box.title = [status.model, status.tracker, status.device].filter(Boolean).join(" · ");
    }

    function renderBanner() {
        const banner = $("#banner");
        if (!ui.connected) {
            banner.hidden = false;
            banner.replaceChildren(el("div", { class: "cc-alert-banner is-offline" }, icon("wifi-off"),
                el("div", {}, el("strong", {}, "Can't reach the CrowdCount server. "), "Retrying every second…")));
            return;
        }
        const { alerts, status } = ui.summary;
        const items = Object.values(alerts || {});
        if (items.length) {
            const parts = items.map((a) => a.type === "global"
                ? `Total ${a.current} > ${a.threshold}`
                : `${a.zone} ${a.current} > ${a.threshold}`);
            banner.hidden = false;
            banner.replaceChildren(el("div", { class: "cc-alert-banner", role: "alert" }, icon("exclamation-triangle-fill"),
                el("div", {}, el("strong", {}, "Crowd threshold exceeded: "), parts.join(" · "))));
        } else if (status.state === "error" && status.message) {
            banner.hidden = false;
            banner.replaceChildren(el("div", { class: "cc-alert-banner is-offline" }, icon("bug"),
                el("div", {}, el("strong", {}, "The detector stopped with an error: "), status.message)));
        } else {
            banner.hidden = true;
        }
    }

    // ------------------------------------------------------------ KPIs

    function renderKpis() {
        const s = ui.summary;
        const zones = Object.values(s.zones || {});
        const occupied = zones.filter((z) => z.current > 0).length;
        const alerts = Object.keys(s.alerts || {}).length;
        $("#kpi-now").textContent = s.total_count;
        $("#kpi-now-meta").textContent = s.last_update ? `Updated ${CC.fmtTime(s.last_update)}` : "No data yet";
        $("#kpi-peak").textContent = s.peak_count;
        $("#kpi-peak-meta").textContent = s.peak_at ? `at ${CC.fmtTime(s.peak_at)}` : "";
        $("#kpi-zones").textContent = zones.length ? `${occupied}/${zones.length}` : "–";
        $("#kpi-zones-meta").textContent = zones.length ? `${zones.length} zone${zones.length === 1 ? "" : "s"} defined` : "No zones defined";
        $("#kpi-alerts").textContent = alerts;
        $("#kpi-alerts-meta").textContent = `Total threshold ${s.global_threshold}`;
        $("#kpi-alerts-card").classList.toggle("is-alert", alerts > 0);
    }

    // ------------------------------------------------------------ live view

    function setStream(on) {
        const box = $("#live");
        if (on && !ui.streaming) {
            ui.streaming = true;
            delete box.dataset.view;
            box.replaceChildren(
                el("img", { src: `/api/stream?t=${Date.now()}`, alt: "Live annotated camera view" }),
                el("span", { class: "cc-badge-live" }, "LIVE"));
        } else if (!on && ui.streaming) {
            ui.streaming = false;
            box.replaceChildren(); // dropping the <img> closes the stream connection
        }
    }

    function renderLive() {
        const status = ui.connected ? ui.summary.status : { state: "disconnected" };
        const running = status.state === "running" && document.visibilityState === "visible";
        setStream(running);
        $("#live-meta").textContent = status.frame_width ? `${status.frame_width}×${status.frame_height}` : "";
        if (status.frame_width && status.frame_height) {
            // Match the boxes to the source so 4:3 cameras aren't letterboxed into 16:9.
            const ratio = `${status.frame_width} / ${status.frame_height}`;
            $("#live").style.aspectRatio = ratio;
            $("#heatmap").style.aspectRatio = ratio;
        }
        if (running) return;
        const box = $("#live");
        const views = {
            starting: ["hourglass-split", "Starting the detector…", "Loading the model. The first run downloads weights."],
            stopped: ["stop-circle", "Detection finished", "The video ended. Counts and charts below stay available."],
            error: ["bug", "The detector stopped with an error", "Check the terminal running CrowdCount."],
            disconnected: ["wifi-off", "Disconnected", "Waiting for the server…"],
        };
        const [ic, title, hint] = views[status.state] || ["camera-video-off", "No detector running",
            "Start one alongside this dashboard:"];
        const code = views[status.state] ? null : el("div", { class: "mt-2" }, el("span", { class: "cc-code" }, "crowdcount run --source 0"));
        const key = `${status.state}`;
        if (box.dataset.view !== key) {
            box.dataset.view = key;
            box.replaceChildren(el("div", {}, emptyState(ic, title, hint), code));
        }
    }

    document.addEventListener("visibilitychange", () => ui.summary && renderLive());

    // ------------------------------------------------------------ zones

    function zoneColor(name, alpha = 1) {
        return CC.rgb(ui.layout[name]?.color, alpha);
    }

    function renderZones() {
        const box = $("#zones");
        const zones = Object.entries(ui.summary.zones || {});
        if (!zones.length) {
            box.replaceChildren(emptyState("bounding-box", "No zones yet",
                CC.isAdmin() ? "Draw zones on the live image in the admin panel." : "Zones are drawn by an admin, or with ",
                CC.isAdmin() ? null : el("span", { class: "cc-code" }, "crowdcount zones edit")));
            return;
        }
        const total = Math.max(ui.summary.total_count, 1);
        box.replaceChildren(...zones.map(([name, z]) => {
            const limit = z.threshold;
            const ratio = limit ? z.current / limit : z.current / total;
            const bar = el("div", { class: `cc-bar${limit && z.current > limit ? " is-over" : limit && ratio >= 0.8 ? " is-warn" : ""}` },
                el("span", { style: { width: `${Math.min(100, Math.round(ratio * 100))}%` } }));
            return el("div", { class: "cc-zone" },
                el("div", { class: "cc-zone-head" },
                    el("span", { class: "cc-swatch", style: { background: zoneColor(name) } }),
                    el("span", { class: "cc-zone-name", title: name }, name),
                    el("span", { class: "cc-zone-count" }, z.current)),
                bar,
                el("div", { class: "cc-zone-meta" },
                    el("span", {}, limit ? `Threshold ${limit}` : "No threshold"),
                    el("span", { title: "Distinct tracker IDs seen in this zone" }, `${z.total_visitors} unique tracks`)));
        }));
    }

    // ------------------------------------------------------------ charts

    function chartTheme() {
        return { grid: CC.cssVar("--cc-border"), text: CC.cssVar("--cc-muted"), ink: CC.cssVar("--cc-text") };
    }

    function baseOptions() {
        const t = chartTheme();
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            interaction: { intersect: false, mode: "index" },
            plugins: { legend: { labels: { color: t.text, boxWidth: 10, boxHeight: 10, usePointStyle: true } } },
            scales: {
                x: { grid: { color: t.grid, drawTicks: false }, ticks: { color: t.text, maxTicksLimit: 8, maxRotation: 0 }, border: { display: false } },
                y: { beginAtZero: true, grid: { color: t.grid }, ticks: { color: t.text, precision: 0 }, border: { display: false } },
            },
        };
    }

    function ensureChart(id, type, extra = {}) {
        if (ui.charts[id]) return ui.charts[id];
        const host = $(`#${id}`);
        host.replaceChildren(el("canvas", { "aria-label": host.closest("section").querySelector("h2").textContent, role: "img" }));
        ui.charts[id] = new Chart(host.querySelector("canvas"), { type, data: { labels: [], datasets: [] }, options: { ...baseOptions(), ...extra } });
        return ui.charts[id];
    }

    function chartEmpty(id, message) {
        if (ui.charts[id]) {
            ui.charts[id].destroy();
            delete ui.charts[id];
        }
        $(`#${id}`).replaceChildren(emptyState("graph-up", message));
    }

    async function refreshHistory() {
        if (!ui.connected) return;
        let data;
        try {
            data = await api(`/history?limit=${HISTORY_POINTS}`);
        } catch (e) {
            return;
        }
        const rows = data.history;
        if (!rows.length) {
            chartEmpty("history-chart", "Waiting for data");
            return;
        }
        const chart = ensureChart("history-chart", "line");
        const t = chartTheme();
        t.text = t.ink;
        const zones = [...new Set(rows.flatMap((r) => Object.keys(r.zone_counts)))];
        chart.data.labels = rows.map((r) => CC.fmtTime(r.timestamp));
        // Total is drawn in the text colour so it never collides with a zone's colour.
        chart.data.datasets = [
            {
                label: "Total", data: rows.map((r) => r.total_count), borderColor: t.text, borderWidth: 2.5,
                backgroundColor: "rgba(148, 163, 184, 0.10)", fill: true, tension: 0.3, pointRadius: 0,
            },
            ...zones.map((name) => ({
                label: name, data: rows.map((r) => r.zone_counts[name] ?? 0), borderColor: zoneColor(name),
                borderWidth: 1.5, tension: 0.3, pointRadius: 0, fill: false,
            })),
        ];
        chart.update();
    }

    function renderZoneChart() {
        const zones = Object.entries(ui.summary.zones || {});
        if (!zones.length) {
            chartEmpty("zone-chart", "No zones to compare");
            return;
        }
        const chart = ensureChart("zone-chart", "bar", { plugins: { legend: { display: false } } });
        chart.data.labels = zones.map(([name]) => name);
        chart.data.datasets = [{
            label: "People now",
            data: zones.map(([, z]) => z.current),
            backgroundColor: zones.map(([name]) => zoneColor(name, 0.75)),
            borderRadius: 6,
            maxBarThickness: 48,
        }];
        chart.update();
    }

    document.addEventListener("cc:theme", () => {
        for (const id of Object.keys(ui.charts)) {
            ui.charts[id].destroy();
            delete ui.charts[id];
        }
        if (ui.summary) {
            renderZoneChart();
            refreshHistory();
        }
    });

    // ------------------------------------------------------------ heatmap

    function refreshHeatmap() {
        if (!ui.connected || !ui.summary) return;
        const box = $("#heatmap");
        const since = ui.summary.heatmap_since;
        $("#heatmap-since").textContent = since ? `since ${CC.fmtTime(since)}` : "";
        if (!since) {
            box.replaceChildren(emptyState("fire", "Nothing to show yet", "The heatmap builds up as people are detected."));
            return;
        }
        const img = new Image();
        img.alt = "Heatmap of where people have been, blended over the latest frame";
        img.onload = () => box.replaceChildren(img);
        img.onerror = () => box.replaceChildren(emptyState("fire", "Nothing to show yet", "The heatmap builds up as people are detected."));
        img.src = `/api/heatmap?t=${Date.now()}`;
    }

    $("#heatmap-reset").addEventListener("click", async () => {
        try {
            await api("/heatmap/reset", { method: "POST" });
            toast("Heatmap reset");
            setTimeout(refreshHeatmap, 300);
        } catch (e) {
            toast(e.message, "error");
        }
    });

    // ------------------------------------------------------------ threshold & exports

    function renderThreshold() {
        const input = $("#threshold-input");
        const admin = CC.isAdmin();
        if (!ui.thresholdDirty && document.activeElement !== input) input.value = ui.summary.global_threshold;
        input.disabled = !admin;
        $("#threshold-save").hidden = !admin;
        $("#threshold-hint").replaceChildren(admin ? "" : el("span", {}, el("a", { href: "login.html" }, "Sign in"), " as an admin to change it."));
    }

    $("#threshold-input").addEventListener("input", () => { ui.thresholdDirty = true; });
    $("#threshold-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const value = Number.parseInt($("#threshold-input").value, 10);
        if (!Number.isInteger(value) || value < 1) {
            toast("Enter a whole number of 1 or more", "warn");
            return;
        }
        try {
            await api("/admin/config/thresholds", { method: "PUT", body: { global_threshold: value } });
            ui.thresholdDirty = false;
            toast(`Alert threshold set to ${value}`);
        } catch (e) {
            toast(e.message, "error");
        }
    });

    document.querySelectorAll("[data-export]").forEach((btn) => btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
            await CC.download(btn.dataset.export);
        } catch (e) {
            toast(e.status === 404 ? "Nothing to export yet: start the detector first." : e.message, "warn");
        } finally {
            btn.disabled = false;
        }
    }));

    // ------------------------------------------------------------ auth area

    function renderAuth() {
        const s = CC.session();
        const area = $("#auth-area");
        $("#heatmap-reset").hidden = !s;
        $("#zones-edit").hidden = !CC.isAdmin();
        if (!s) {
            area.replaceChildren(el("a", { class: "btn btn-sm btn-primary", href: "login.html" }, icon("box-arrow-in-right"), " Sign in"));
            return;
        }
        const menu = el("ul", { class: "dropdown-menu dropdown-menu-end" },
            el("li", {}, el("span", { class: "dropdown-item-text cc-small cc-muted" }, `Signed in as ${s.user.role}`)),
            CC.isAdmin() ? el("li", {}, el("a", { class: "dropdown-item", href: "admin.html" }, icon("sliders", "me-2"), "Admin panel")) : null,
            el("li", {}, el("hr", { class: "dropdown-divider" })),
            el("li", {}, el("button", { class: "dropdown-item", type: "button", onclick: () => CC.signOut() }, icon("box-arrow-right", "me-2"), "Sign out")));
        area.replaceChildren(el("div", { class: "dropdown" },
            el("button", { class: "btn btn-sm btn-outline-secondary dropdown-toggle", "data-bs-toggle": "dropdown", "aria-expanded": "false" },
                icon("person-circle"), ` ${s.user.username}`),
            menu));
    }

    // ------------------------------------------------------------ polling

    async function refreshLayout() {
        try {
            const data = await api("/zones/layout");
            ui.layout = Object.fromEntries(data.zones.map((z) => [z.name, z]));
        } catch (e) { /* keep last known colours */ }
    }

    async function poll() {
        try {
            ui.summary = await api("/summary");
            ui.connected = true;
            $("#version").textContent = `v${ui.summary.version}`;
        } catch (e) {
            ui.connected = false;
        }
        renderStatus();
        renderBanner();
        renderLive();
        if (!ui.summary) return;
        renderKpis();
        renderZones();
        renderZoneChart();
        renderThreshold();
    }

    async function start() {
        renderAuth();
        await refreshLayout();
        await poll();
        refreshHistory();
        refreshHeatmap();
        setInterval(poll, POLL_MS);
        setInterval(refreshLayout, LAYOUT_MS);
        setInterval(() => ui.summary?.status.state === "running" && refreshHistory(), HISTORY_MS);
        setInterval(() => ui.summary?.status.state === "running" && refreshHeatmap(), HEATMAP_MS);
    }

    start();
})();
