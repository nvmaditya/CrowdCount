/* Admin panel: zones, thresholds, cameras, users, activity log, alert history, account. */
"use strict";

(() => {
    const { el, icon, $, $$, api, toast, emptyState } = CC;

    const session = CC.session();
    if (!session?.token || session.user.role !== "admin") {
        location.replace(`login.html?next=${encodeURIComponent("/admin.html")}`);
        return;
    }

    const TITLES = {
        overview: "Overview", zones: "Zones", thresholds: "Thresholds", cameras: "Cameras",
        users: "Users", logs: "Activity log", alerts: "Alert history", account: "Account",
    };
    const LOADERS = {};
    const fail = (e) => toast(e.message, "error");

    function hexToRgb(hex) {
        const n = Number.parseInt(hex.slice(1), 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }

    function rgbToHex([r, g, b]) {
        return `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
    }

    function emptyRow(cols, ...content) {
        return el("tr", {}, el("td", { colspan: cols }, emptyState(...content)));
    }

    // ------------------------------------------------------------ navigation

    function show(section) {
        if (!TITLES[section]) section = "overview";
        $$(".cc-nav button[data-section]").forEach((b) => b.classList.toggle("active", b.dataset.section === section));
        $$(".cc-section").forEach((s) => s.classList.toggle("active", s.id === `section-${section}`));
        $("#section-title").textContent = TITLES[section];
        document.title = `CrowdCount · ${TITLES[section]}`;
        $("#sidebar").classList.remove("open");
        LOADERS[section]?.().catch(fail);
    }

    $$(".cc-nav button[data-section]").forEach((b) => b.addEventListener("click", () => { location.hash = b.dataset.section; }));
    window.addEventListener("hashchange", () => show(location.hash.slice(1)));
    $("#menu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));
    $("#signout").addEventListener("click", () => CC.signOut("login.html"));
    $("#whoami").textContent = `Signed in as ${session.user.username}`;

    // ------------------------------------------------------------ overview

    LOADERS.overview = async () => {
        const [users, cameras, zones, alerts, logs, summary] = await Promise.all([
            api("/admin/users"), api("/admin/cameras"), api("/admin/zones"),
            api("/admin/alerts/history?limit=1000"), api("/admin/logs?limit=8"), api("/summary"),
        ]);
        const pending = alerts.alerts.filter((a) => !a.acknowledged).length;
        const stat = (label, value, ic, href) => el("div", { class: "col-6 col-xl-3" },
            el("a", { class: "cc-card cc-kpi d-block text-reset text-decoration-none", href },
                el("div", { class: "cc-kpi-label" }, icon(ic), label), el("div", { class: "cc-kpi-value" }, value)));
        $("#overview-stats").replaceChildren(
            stat("Zones", zones.zones.length, "bounding-box-circles", "#zones"),
            stat("Cameras", cameras.cameras.length, "camera-video", "#cameras"),
            stat("Users", users.length, "people", "#users"),
            stat("Unacknowledged alerts", pending, "exclamation-triangle", "#alerts"));

        const s = summary.status;
        const row = (k, v) => el("div", { class: "d-flex justify-content-between py-1 border-bottom" },
            el("span", { class: "cc-muted" }, k), el("span", { class: "fw-semibold text-end" }, v ?? "–"));
        $("#overview-detector").replaceChildren(
            el("div", { class: "cc-status mb-3", dataset: { state: s.state } }, el("span", { class: "cc-dot" }),
                el("strong", {}, CC.humanize(s.state))),
            row("Source", s.source), row("Model", s.model), row("Tracker", s.tracker), row("Device", s.device),
            row("Frame", s.frame_width ? `${s.frame_width}×${s.frame_height}` : null),
            row("Throughput", s.state === "running" ? `${s.fps.toFixed(1)} fps` : null),
            row("Started", s.started_at ? CC.fmtDateTime(s.started_at) : null));

        $("#overview-activity").replaceChildren(...(logs.logs.length ? logs.logs.map(logItem)
            : [emptyState("journal", "No activity yet")]));
    };

    function logItem(log) {
        return el("div", { class: "cc-list-item" },
            el("span", { class: "cc-log-cat", dataset: { cat: log.category } }, log.category),
            el("div", { class: "flex-grow-1 min-w-0" },
                el("div", { class: "fw-semibold cc-small" }, CC.humanize(log.action)),
                el("div", { class: "cc-muted cc-small text-truncate" }, [log.username || "system", log.details].filter(Boolean).join(" · "))),
            el("span", { class: "cc-muted cc-small text-nowrap", title: CC.fmtDateTime(log.timestamp) }, CC.timeAgo(log.timestamp)));
    }

    // ------------------------------------------------------------ zones

    let zones = [];
    const editor = new ZoneEditor($("#zone-editor"), {
        onDraftChange(points) {
            const drawing = Array.isArray(points);
            $("#zone-new").disabled = drawing;
            $("#zone-cancel").disabled = !drawing;
            $("#zone-undo").disabled = !drawing || !points.length;
            $("#zone-save-form").hidden = true;
        },
        onComplete(points) {
            editor.pendingPoints = points;
            $("#zone-new").disabled = true;
            $("#zone-cancel").disabled = false;
            $("#zone-undo").disabled = true;
            $("#zone-save-form").hidden = false;
            let n = zones.length + 1;
            while (zones.some((z) => z.name === `Zone ${n}`)) n += 1;
            $("#zone-name").value = `Zone ${n}`;
            $("#zone-name").select();
        },
    });

    async function loadZoneList() {
        const data = await api("/admin/zones");
        zones = data.zones;
        $("#zones-file").textContent = data.file;
        editor.setZones(zones);
        const list = $("#zone-list");
        if (!zones.length) {
            list.replaceChildren(emptyState("bounding-box", "No zones yet", "Draw one on the frame to start counting per area."));
            return;
        }
        list.replaceChildren(...zones.map((zone) => {
            const toggle = el("input", { class: "form-check-input", type: "checkbox", role: "switch", checked: zone.enabled,
                name: `zone-enabled-${zone.name}`, "aria-label": `Enable ${zone.name}` });
            toggle.addEventListener("change", async () => {
                try {
                    await api(`/admin/zones/${encodeURIComponent(zone.name)}`, { method: "PUT", body: { enabled: toggle.checked } });
                    toast(`${zone.name} ${toggle.checked ? "enabled" : "disabled"}`);
                    loadZoneList();
                } catch (e) {
                    toggle.checked = !toggle.checked;
                    fail(e);
                }
            });
            const item = el("div", { class: "cc-list-item" },
                el("span", { class: "cc-swatch", style: { background: CC.rgb(zone.color) } }),
                el("div", { class: "flex-grow-1 min-w-0" },
                    el("div", { class: "fw-semibold text-truncate" }, zone.name),
                    el("div", { class: "cc-muted cc-small" }, `${zone.points.length} corners`)),
                el("div", { class: "form-check form-switch m-0" }, toggle),
                el("button", { class: "btn btn-sm btn-outline-secondary", title: `Delete ${zone.name}`, "aria-label": `Delete ${zone.name}`,
                    onclick: async () => {
                        if (!(await CC.confirm(`Delete zone “${zone.name}”? Its threshold is removed too.`))) return;
                        try {
                            await api(`/admin/zones/${encodeURIComponent(zone.name)}`, { method: "DELETE" });
                            toast(`Deleted ${zone.name}`);
                            loadZoneList();
                        } catch (e) { fail(e); }
                    } }, icon("trash")));
            item.addEventListener("mouseenter", () => editor.highlight(zone.name));
            item.addEventListener("mouseleave", () => editor.highlight(null));
            return item;
        }));
    }

    LOADERS.zones = async () => {
        await Promise.all([editor.hasFrame ? null : editor.loadFrame(), loadZoneList()]);
        $("#zone-new").disabled = !editor.hasFrame;
    };

    $("#zone-new").addEventListener("click", () => {
        editor.start();
        $("#zone-color").value = rgbToHex([[45, 212, 191], [245, 158, 11], [99, 102, 241], [236, 72, 153], [34, 197, 94], [14, 165, 233]][zones.length % 6]);
    });
    $("#zone-undo").addEventListener("click", () => editor.undo());
    $("#zone-cancel").addEventListener("click", () => {
        editor.pendingPoints = null;
        editor.cancel();
    });
    $("#zone-reload").addEventListener("click", async () => {
        editor.cancel();
        const ok = await editor.loadFrame();
        editor.setZones(zones);
        $("#zone-new").disabled = !ok;
        if (ok) toast("Loaded a fresh frame");
    });
    $("#zone-save-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const name = $("#zone-name").value.trim();
        if (!name || !editor.pendingPoints) return;
        try {
            await api("/admin/zones", { method: "POST", body: { name, points: editor.pendingPoints, color: hexToRgb($("#zone-color").value) } });
            toast(`Saved ${name}. The detector applies it within a second.`);
            editor.pendingPoints = null;
            editor.cancel();
            loadZoneList();
        } catch (e) { fail(e); }
    });

    // ------------------------------------------------------------ thresholds

    LOADERS.thresholds = async () => {
        const [thresholds, layout] = await Promise.all([api("/admin/config/thresholds"), api("/admin/zones")]);
        $("#global-threshold").value = thresholds.global_threshold;
        const box = $("#zone-thresholds");
        if (!layout.zones.length) {
            box.replaceChildren(emptyState("bounding-box", "No zones defined", "Create zones first, then set a limit for each."));
            return;
        }
        box.replaceChildren(...layout.zones.map((zone) => {
            const input = el("input", { type: "number", min: 1, class: "form-control form-control-sm", style: { width: "7rem" },
                value: thresholds.zone_thresholds[zone.name] ?? "", placeholder: "None", name: `threshold-${zone.name}`, "aria-label": `Threshold for ${zone.name}` });
            const save = async (value) => {
                try {
                    await api("/admin/config/thresholds", { method: "PUT", body: { zone_thresholds: { [zone.name]: value } } });
                    toast(value === null ? `Removed the threshold for ${zone.name}` : `${zone.name}: alert above ${value}`);
                    LOADERS.thresholds();
                } catch (e) { fail(e); }
            };
            return el("form", { class: "cc-list-item", onsubmit: (e) => {
                e.preventDefault();
                const v = Number.parseInt(input.value, 10);
                if (!Number.isInteger(v) || v < 1) return toast("Enter a whole number of 1 or more", "warn");
                save(v);
            } },
                el("span", { class: "cc-swatch", style: { background: CC.rgb(zone.color) } }),
                el("span", { class: "flex-grow-1 fw-semibold text-truncate" }, zone.name),
                input,
                el("button", { class: "btn btn-sm btn-primary", type: "submit" }, "Save"),
                el("button", { class: "btn btn-sm btn-outline-secondary", type: "button", title: "Remove threshold",
                    "aria-label": `Remove threshold for ${zone.name}`, onclick: () => save(null) }, icon("x-lg")));
        }));
    };

    $("#global-threshold-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const value = Number.parseInt($("#global-threshold").value, 10);
        try {
            await api("/admin/config/thresholds", { method: "PUT", body: { global_threshold: value } });
            toast(`Total threshold set to ${value}`);
        } catch (e) { fail(e); }
    });

    // ------------------------------------------------------------ cameras

    LOADERS.cameras = async () => {
        const { cameras } = await api("/admin/cameras");
        const list = $("#camera-list");
        if (!cameras.length) {
            list.replaceChildren(emptyState("camera-video", "No saved cameras", "Save a source here to start it by name later."));
            return;
        }
        list.replaceChildren(...cameras.map((cam) => {
            const command = `crowdcount run --camera "${cam.name}"`;
            const toggle = el("input", { class: "form-check-input", type: "checkbox", role: "switch", checked: cam.enabled,
                name: `camera-enabled-${cam.id}`, "aria-label": `Enable ${cam.name}` });
            toggle.addEventListener("change", async () => {
                try {
                    await api(`/admin/cameras/${cam.id}`, { method: "PUT", body: { enabled: toggle.checked } });
                    toast(`${cam.name} ${toggle.checked ? "enabled" : "disabled"}`);
                } catch (e) { toggle.checked = !toggle.checked; fail(e); }
            });
            return el("div", { class: "cc-list-item flex-wrap" },
                icon("camera-video", "cc-muted"),
                el("div", { class: "flex-grow-1 min-w-0" },
                    el("div", { class: "fw-semibold" }, cam.name),
                    el("div", { class: "cc-muted cc-small text-truncate" }, cam.source_url)),
                el("button", { class: "btn btn-sm btn-outline-secondary", title: "Copy start command", onclick: async () => {
                    try { await navigator.clipboard.writeText(command); toast("Command copied"); } catch (e) { toast(command, "warn"); }
                } }, icon("terminal"), " Copy command"),
                el("div", { class: "form-check form-switch m-0" }, toggle),
                el("button", { class: "btn btn-sm btn-outline-secondary", "aria-label": `Delete ${cam.name}`, onclick: async () => {
                    if (!(await CC.confirm(`Delete camera “${cam.name}”?`))) return;
                    try { await api(`/admin/cameras/${cam.id}`, { method: "DELETE" }); toast(`Deleted ${cam.name}`); LOADERS.cameras(); } catch (e) { fail(e); }
                } }, icon("trash")));
        }));
    };

    $("#camera-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            await api("/admin/cameras", { method: "POST", body: {
                name: $("#camera-name").value.trim(), source_url: $("#camera-source").value.trim(), enabled: $("#camera-enabled").checked } });
            bootstrap.Modal.getInstance($("#camera-modal")).hide();
            event.target.reset();
            toast("Camera saved");
            LOADERS.cameras();
        } catch (e) { fail(e); }
    });

    // ------------------------------------------------------------ users

    LOADERS.users = async () => {
        const users = await api("/admin/users");
        $("#user-rows").replaceChildren(...users.map((u) => el("tr", {},
            el("td", { class: "fw-semibold" }, u.username, u.id === session.user.id ? el("span", { class: "cc-muted cc-small ms-2" }, "(you)") : null),
            el("td", {}, el("span", { class: `badge ${u.role === "admin" ? "text-bg-warning" : "text-bg-secondary"}` }, u.role)),
            el("td", { class: "cc-muted" }, CC.fmtDateTime(u.created_at)),
            el("td", { class: "cc-muted" }, u.last_login ? CC.timeAgo(u.last_login) : "never"),
            el("td", { class: "text-end" }, u.id === session.user.id ? null : el("button", { class: "btn btn-sm btn-outline-secondary",
                "aria-label": `Delete ${u.username}`, onclick: async () => {
                    if (!(await CC.confirm(`Delete user “${u.username}”?`))) return;
                    try { await api(`/admin/users/${u.id}`, { method: "DELETE" }); toast(`Deleted ${u.username}`); LOADERS.users(); } catch (e) { fail(e); }
                } }, icon("trash"))))));
    };

    $("#user-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            await api("/admin/users", { method: "POST", body: {
                username: $("#new-username").value.trim(), password: $("#new-password").value, role: $("#new-role").value } });
            bootstrap.Modal.getInstance($("#user-modal")).hide();
            event.target.reset();
            toast("User created");
            LOADERS.users();
        } catch (e) { fail(e); }
    });

    // ------------------------------------------------------------ activity log

    let logOffset = 0;
    const LOG_PAGE = 50;

    function logQuery() {
        const params = new URLSearchParams();
        if ($("#log-category").value) params.set("category", $("#log-category").value);
        if ($("#log-from").value) params.set("start_date", `${$("#log-from").value}T00:00:00`);
        if ($("#log-to").value) params.set("end_date", `${$("#log-to").value}T23:59:59`);
        return params;
    }

    async function loadLogs(append = false) {
        logOffset = append ? logOffset + LOG_PAGE : 0;
        const params = logQuery();
        params.set("limit", LOG_PAGE);
        params.set("offset", logOffset);
        const data = await api(`/admin/logs?${params}`);
        const rows = data.logs.map((log) => el("tr", {},
            el("td", { class: "cc-muted text-nowrap" }, CC.fmtDateTime(log.timestamp)),
            el("td", {}, el("span", { class: "cc-log-cat", dataset: { cat: log.category } }, log.category)),
            el("td", { class: "fw-semibold text-nowrap" }, CC.humanize(log.action)),
            el("td", {}, log.username || el("span", { class: "cc-muted" }, "system")),
            el("td", { class: "cc-muted" }, log.details || "")));
        if (append) $("#log-rows").append(...rows);
        else $("#log-rows").replaceChildren(...(rows.length ? rows : [emptyRow(5, "journal", "No matching entries")]));
        const shown = Math.min(logOffset + data.count, data.total);
        $("#log-count").textContent = data.total ? `Showing ${shown} of ${data.total}` : "";
        $("#log-more").hidden = shown >= data.total;
    }

    LOADERS.logs = () => loadLogs(false);
    $("#log-filters").addEventListener("submit", (e) => { e.preventDefault(); loadLogs(false).catch(fail); });
    $("#log-filters").addEventListener("reset", () => setTimeout(() => loadLogs(false).catch(fail)));
    $("#log-more").addEventListener("click", () => loadLogs(true).catch(fail));
    $$("[data-log-export]").forEach((btn) => btn.addEventListener("click", () =>
        CC.download(`/admin/logs/export/${btn.dataset.logExport}?${logQuery()}`).catch(fail)));

    // ------------------------------------------------------------ alert history

    LOADERS.alerts = async () => {
        const { alerts } = await api("/admin/alerts/history?limit=200");
        $("#alert-rows").replaceChildren(...(alerts.length ? alerts.map((a) => el("tr", {},
            el("td", { class: "cc-muted text-nowrap" }, CC.fmtDateTime(a.timestamp)),
            el("td", { class: "fw-semibold" }, a.alert_type === "global" ? "Total" : a.alert_type),
            el("td", {}, el("strong", {}, a.actual_count)),
            el("td", { class: "cc-muted" }, a.threshold),
            el("td", {}, a.acknowledged
                ? el("span", { class: "cc-muted cc-small", title: CC.fmtDateTime(a.acknowledged_at) }, icon("check2-circle", "me-1"), `Acknowledged by ${a.acknowledged_by}`)
                : el("button", { class: "btn btn-sm btn-outline-secondary", onclick: async () => {
                    try { await api(`/admin/alerts/${a.id}/acknowledge`, { method: "POST" }); LOADERS.alerts(); } catch (e) { fail(e); }
                } }, "Acknowledge"))))
            : [emptyRow(5, "shield-check", "No alerts recorded", "Breaches of a threshold appear here.")]));
    };

    // ------------------------------------------------------------ account

    LOADERS.account = async () => {
        const me = await api("/auth/me");
        const row = (k, v) => el("div", { class: "d-flex justify-content-between py-1 border-bottom" }, el("span", { class: "cc-muted" }, k), el("span", { class: "fw-semibold" }, v));
        $("#pw-username").value = me.username;
        $("#profile").replaceChildren(row("Username", me.username), row("Role", me.role), row("Created", CC.fmtDateTime(me.created_at)),
            row("Last sign-in", me.last_login ? CC.fmtDateTime(me.last_login) : "–"));
    };

    $("#password-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        if ($("#pw-new").value !== $("#pw-repeat").value) return toast("The new passwords don't match", "warn");
        try {
            await api("/auth/password", { method: "POST", body: { current_password: $("#pw-current").value, new_password: $("#pw-new").value } });
            event.target.reset();
            toast("Password updated");
        } catch (e) { fail(e); }
    });

    show(location.hash.slice(1) || "overview");
})();
