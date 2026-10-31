/* Zones page: the zone editor over the latest camera frame, the zone list, and how counting works.
 * Admins draw, rename, recolour, limit, switch off and delete zones; everyone else sees them read-only.
 * Needs icons.js, core.js, shell.js and zone-editor.js. */
"use strict";

(() => {
    const { el, $, icon, api, toast, emptyState, setText, withKey, replaceKeepingFocus } = CC;
    const changed = CC.changeTracker();

    const PALETTE = [
        ["Teal", [46, 196, 182]], ["Amber", [245, 158, 11]], ["Indigo", [99, 102, 241]],
        ["Pink", [236, 72, 153]], ["Green", [34, 197, 94]], ["Sky", [14, 165, 233]],
    ];
    const PENDING_MS = 5000;

    const admin = CC.isAdmin();

    const ui = {
        summary: null,
        connected: true,
        zones: null, // [{name, color, enabled, points}] in file order; null until loaded
        file: null,
        mode: "idle", // idle | drawing | ready (shape drawn, naming it) | edit
        corners: 0,
        points: null, // the drawn shape while "ready"
        editing: null, // the name of the zone being edited
        color: PALETTE[0][1],
        hover: null, // the zone under the pointer (or focus) in the list
        frameRun: undefined, // the detector run whose frame was fetched automatically; undefined before the first look
        loadingFrame: false,
    };

    let zonesSeq = 0; // the latest /admin/zones request; older answers are dropped

    // Rules saved or removed here, laid over the summary's until it reports them or PENDING_MS pass, so a
    // summary fetched before the change can't show the old limit (or prefill the form with it).
    const pending = new Map();

    const has = (obj, key) => Boolean(obj) && Object.prototype.hasOwnProperty.call(obj, key);
    const sameColor = (a, b) => Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v === b[i]);
    const zonePath = (name) => `/admin/zones/${encodeURIComponent(name)}`;
    const rulePath = (name) => `/admin/rules/zones/${encodeURIComponent(name)}`;
    const zoneNamed = (name) => (ui.zones || []).find((z) => z.name === name) || null;
    const flowing = () => ui.mode === "drawing" || ui.mode === "ready";

    function serverRule(name) {
        const zones = ui.summary?.rules?.zones;
        return has(zones, name) ? zones[name] : null;
    }

    function ruleOf(name) {
        const p = pending.get(name);
        if (p) {
            const server = serverRule(name);
            const settled = p.rule ? Boolean(server) && server.limit === p.rule.limit : !server;
            if (!settled && Date.now() - p.at <= PENDING_MS) return p.rule;
            pending.delete(name);
        }
        return serverRule(name);
    }

    const expect = (name, rule) => pending.set(name, { rule, at: Date.now() });

    // ------------------------------------------------------------ editor

    const editor = new ZoneEditor($("#zone-stage"), {
        readOnly: !admin,
        onDraftChange: (points) => {
            if (points) {
                ui.mode = "drawing";
                ui.corners = points.length;
            } else if (ui.mode === "drawing") {
                ui.mode = "idle";
            }
            renderToolbar();
        },
        onComplete: (points) => {
            ui.mode = "ready";
            ui.points = points;
            fillForm(nextName(), defaultColor(), "");
            renderToolbar();
            syncCanvas();
            f.name.focus();
            f.name.select();
        },
    });

    /** The zones as drawn: the list, with the form's colour on the zone being edited and the new shape added. */
    function canvasZones() {
        const list = (ui.zones || []).map((z) => (ui.mode === "edit" && z.name === ui.editing ? { ...z, color: ui.color } : z));
        if (ui.mode === "ready" && ui.points) {
            list.push({ name: f.name.value.trim() || "New zone", color: ui.color, enabled: true, points: ui.points });
        }
        return list;
    }

    function syncCanvas() {
        const zones = canvasZones();
        const drawn = ui.mode === "ready" && ui.points ? zones[zones.length - 1].name : null;
        const target = ui.hover ?? (ui.mode === "edit" ? ui.editing : drawn);
        if (!changed("canvas", [zones, target])) return;
        editor.highlight(target);
        editor.setZones(zones);
    }

    function hoverZone(name) {
        if (ui.hover === name) return;
        ui.hover = name;
        syncCanvas();
    }

    /** Fetch the latest frame (the "Grab a fresh frame" button, or once per detector run on its own). */
    async function grabFrame() {
        if (ui.loadingFrame) return;
        ui.loadingFrame = true;
        $("#zone-grab svg")?.classList.add("spin");
        await editor.loadFrame();
        ui.loadingFrame = false;
        $("#zone-grab svg")?.classList.remove("spin");
        renderToolbar();
    }

    /**
     * Load the frame without being asked once the detector has published one (fetching earlier would only
     * 404), and again for a new run while no frame is shown. Before any run, show how to start one.
     */
    function autoFrame() {
        const st = ui.connected && ui.summary?.last_update ? ui.summary.status : null;
        const run = st ? String(st.started_at ?? "") : null;
        if (run === null) {
            if (ui.frameRun === undefined) {
                ui.frameRun = null;
                editor.clearFrame();
                renderToolbar();
            }
            return;
        }
        if (editor.hasFrame || ui.loadingFrame || run === ui.frameRun) return;
        ui.frameRun = run;
        grabFrame();
    }

    // ------------------------------------------------------------ toolbar and form

    function hint() {
        if (ui.mode === "drawing") {
            const n = ui.corners;
            if (n === 0) return "Click the frame to place the first corner";
            if (n < 3) return `${CC.plural(n, "corner")} · at least 3 needed`;
            return `${n} corners · click the first one to close`;
        }
        if (ui.mode === "ready") return "Shape ready · name it below";
        if (ui.mode === "edit") return `Editing ${ui.editing}`;
        return "Click New zone, then click the frame to place corners";
    }

    function renderToolbar() {
        if (!admin) return;
        const newButton = $("#zone-new");
        newButton.setAttribute("aria-pressed", String(flowing()));
        newButton.disabled = !editor.hasFrame && !flowing();
        $("#zone-undo").disabled = !(ui.mode === "drawing" && ui.corners > 0);
        $("#zone-cancel").hidden = ui.mode === "idle";
        setText($("#zone-hint"), hint());
        f.form.hidden = !(ui.mode === "ready" || ui.mode === "edit");
        setText(f.save, ui.mode === "edit" ? "Save changes" : "Save zone");
    }

    const f = {};

    function buildForm() {
        f.form = $("#zone-form");
        f.name = $("#zone-name");
        f.limit = $("#zone-limit");
        f.save = $("#zone-save");
        f.swatches = PALETTE.map(([label, color]) => {
            const button = el("button", {
                type: "button", class: "zone-swatch", "aria-label": label, "aria-pressed": "false",
                style: { background: CC.rgb(color) },
                onclick: () => {
                    pickColor(color);
                    syncCanvas();
                },
            });
            return [button, color];
        });
        $("#zone-swatches").replaceChildren(...f.swatches.map(([button]) => button));
        f.form.addEventListener("submit", save);
        f.form.addEventListener("keydown", (event) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            cancelFlow(true);
        });
        f.name.addEventListener("input", () => {
            if (ui.mode === "ready") syncCanvas();
        });
    }

    function pickColor(color) {
        ui.color = color;
        for (const [button, c] of f.swatches) button.setAttribute("aria-pressed", String(sameColor(c, color)));
    }

    function fillForm(name, color, limit) {
        f.name.value = name;
        f.limit.value = limit;
        f.save.disabled = false;
        pickColor(color);
    }

    function nextName() {
        let n = (ui.zones || []).length + 1;
        while (zoneNamed(`Zone ${n}`)) n += 1;
        return `Zone ${n}`;
    }

    /** The first palette colour no zone uses yet (the first one when all are taken). */
    function defaultColor() {
        const free = PALETTE.find(([, c]) => !(ui.zones || []).some((z) => sameColor(z.color, c)));
        return (free || PALETTE[0])[1];
    }

    function focusMenu(name) {
        const trigger = $(`[data-key="${CSS.escape(`menu:${name}`)}"]`);
        (trigger || $("#zone-new")).focus();
    }

    function newZone() {
        if (flowing()) {
            cancelFlow(false);
            return;
        }
        ui.mode = "drawing";
        ui.editing = null;
        ui.points = null;
        ui.corners = 0;
        editor.start();
        syncCanvas();
    }

    /** Leave drawing, naming or editing. With `refocus`, focus goes back to where the flow started. */
    function cancelFlow(refocus) {
        const was = ui.mode;
        const name = ui.editing;
        ui.mode = "idle";
        ui.points = null;
        ui.editing = null;
        if (editor.drawing) editor.cancel();
        renderToolbar();
        syncCanvas();
        if (!refocus) return;
        if (was === "edit") focusMenu(name);
        else $("#zone-new").focus();
    }

    function openEdit(name) {
        const zone = zoneNamed(name);
        if (!zone) return;
        if (editor.drawing) editor.cancel();
        ui.mode = "edit";
        ui.editing = name;
        ui.points = null;
        const rule = ruleOf(name);
        fillForm(name, zone.color, rule ? String(rule.limit) : "");
        renderToolbar();
        syncCanvas();
        f.name.focus();
    }

    async function save(event) {
        event.preventDefault(); // runs once the browser's own checks (a name, a whole limit of 1 or more) pass
        if (ui.mode !== "ready" && ui.mode !== "edit") return;
        const name = f.name.value.trim();
        if (!name) {
            toast("Give the zone a name", "error");
            f.name.focus();
            return;
        }
        const limit = f.limit.value.trim() === "" ? null : Number(f.limit.value);
        zonesSeq += 1; // a list fetched before this change must not land after it
        f.save.disabled = true;
        try {
            if (ui.mode === "ready") await createZone(name, limit);
            else await updateZone(name, limit);
        } finally {
            f.save.disabled = false;
        }
    }

    /** The zone is saved but its limit isn't: stay on it in edit mode, so only the limit needs another try. */
    async function limitFailed(zone, message, what) {
        toast(`${what}, but its alert limit wasn't: ${message}`, "error");
        ui.mode = "edit";
        ui.editing = zone.name;
        ui.points = null;
        f.name.value = zone.name;
        renderToolbar();
        await loadZones();
    }

    async function createZone(name, limit) {
        let zone;
        try {
            zone = (await api("/admin/zones", { method: "POST", body: { name, points: ui.points, color: ui.color } })).zone;
        } catch (e) {
            toast(e.message, "error");
            return;
        }
        if (limit !== null) {
            try {
                const data = await api(rulePath(zone.name), { method: "PUT", body: { limit } });
                expect(zone.name, data.rule);
            } catch (e) {
                await limitFailed(zone, e.message, "Zone saved");
                return;
            }
        }
        toast("Zone saved");
        cancelFlow(false);
        await loadZones();
        $("#zone-new").focus();
    }

    async function updateZone(name, limit) {
        const old = ui.editing;
        const before = ruleOf(old);
        let zone;
        try {
            zone = (await api(zonePath(old), { method: "PUT", body: { name, color: ui.color } })).zone;
        } catch (e) {
            toast(e.message, "error");
            return;
        }
        // A rename carries the zone's rule over on the server.
        if (zone.name !== old && before) expect(zone.name, before);
        ui.editing = zone.name;
        try {
            if (limit !== null && (!before || before.limit !== limit)) {
                // Keep the rule's other options; a new rule takes the server's defaults.
                const keep = before ? { warn_percent: before.warn_percent, enabled: before.enabled, banner: before.banner } : {};
                const data = await api(rulePath(zone.name), { method: "PUT", body: { limit, ...keep } });
                expect(zone.name, data.rule);
            } else if (limit === null && before) {
                await api(rulePath(zone.name), { method: "DELETE" });
                expect(zone.name, null);
            }
        } catch (e) {
            await limitFailed(zone, e.message, "Zone updated");
            return;
        }
        toast("Zone updated");
        cancelFlow(false);
        await loadZones();
        focusMenu(zone.name);
    }

    // ------------------------------------------------------------ zone list

    async function setEnabled(zone, on) {
        try {
            await api(zonePath(zone.name), { method: "PUT", body: { enabled: on } });
        } catch (e) {
            toast(e.message, "error");
            return false;
        }
        const stored = zoneNamed(zone.name);
        if (stored) stored.enabled = on;
        toast(on ? "Zone turned on" : "Zone turned off");
        renderList();
        syncCanvas();
        return true;
    }

    async function deleteZone(zone) {
        if (!(await CC.confirm(`Delete zone ${zone.name}? Its alert rule is removed too.`))) return;
        try {
            await api(zonePath(zone.name), { method: "DELETE" });
        } catch (e) {
            toast(e.message, "error");
            return;
        }
        pending.delete(zone.name);
        if (ui.mode === "edit" && ui.editing === zone.name) cancelFlow(false);
        toast("Zone deleted");
        await loadZones();
        $("#zone-new").focus();
    }

    function meta(zone) {
        const rule = ruleOf(zone.name);
        const corners = CC.plural(zone.points.length, "corner");
        return rule ? `${corners} · alert above ${rule.limit}` : `${corners} · no limit`;
    }

    function zoneRow(zone) {
        const off = zone.enabled === false;
        const row = el("div", {
            class: "zl-row",
            onmouseenter: () => hoverZone(zone.name),
            onmouseleave: () => hoverZone(null),
            onfocusin: () => hoverZone(zone.name),
            onfocusout: () => hoverZone(null),
        },
        el("span", { class: off ? "zl-swatch is-off" : "zl-swatch", style: { background: CC.rgb(zone.color) } }),
        el("span", { class: "zl-text" },
            el("span", { class: "zl-name" }, zone.name),
            el("span", { class: "zl-meta" }, meta(zone))));
        if (admin) {
            const actions = CC.menu(`More actions for ${zone.name}`, [
                { label: "Edit details", icon: "pencil", onselect: () => openEdit(zone.name) },
                { label: "Delete zone", icon: "trash-2", danger: true, onselect: () => deleteZone(zone) },
            ]);
            withKey(actions.firstChild, `menu:${zone.name}`);
            row.append(
                withKey(CC.switchControl({
                    checked: !off,
                    label: `Count ${zone.name}`,
                    onchange: (on) => setEnabled(zone, on),
                }), `switch:${zone.name}`),
                actions);
        }
        return row;
    }

    function renderList() {
        if (ui.zones === null) return;
        const model = ui.zones.map((z) => [z.name, z.color, z.enabled !== false, z.points.length, ruleOf(z.name)?.limit ?? null]);
        if (!changed("list", model)) return;
        setText($("#zone-count"), ui.zones.length);
        replaceKeepingFocus($("#zone-list"), ui.zones.length ? ui.zones.map(zoneRow) : [emptyState({
            icon: "pentagon", title: "No zones yet", hint: "Draw zones on the live frame to count areas separately.",
        })]);
    }

    function zonesLoaded() {
        if (ui.mode === "edit" && !zoneNamed(ui.editing)) cancelFlow(false); // deleted elsewhere
        if (ui.file) {
            setText($("#zone-file-path"), ui.file);
            $("#zone-file").hidden = false;
        }
        renderList();
        syncCanvas();
        renderToolbar();
    }

    /** Admins read the zones file (with its path); everyone else the public layout Shell polls. */
    async function loadZones() {
        if (!admin) {
            ui.zones = Object.entries(Shell.layout).map(([name, z]) => ({ name, ...z }));
            zonesLoaded();
            return;
        }
        const seq = ++zonesSeq;
        let data;
        try {
            data = await api("/admin/zones");
        } catch (e) {
            // A lost connection shows the offline bar and reloads on reconnect.
            if (seq === zonesSeq && e.status && e.status !== 401) toast(e.message, "error");
            return;
        }
        if (seq !== zonesSeq) return;
        ui.zones = Array.isArray(data.zones) ? data.zones : [];
        ui.file = data.file || null;
        zonesLoaded();
    }

    // ------------------------------------------------------------ wiring

    document.addEventListener("cc:summary", (event) => {
        const wasConnected = ui.connected;
        ui.summary = event.detail.summary;
        ui.connected = event.detail.connected;
        if (ui.summary) setText($("#footer-version"), `CrowdCount ${ui.summary.version} · AGPL-3.0`);
        if (admin && ui.connected && !wasConnected) loadZones();
        autoFrame();
        renderList(); // limits come from the summary's rules
    });

    // Every 10 s: picks up zones changed elsewhere (admins re-read the zones file).
    document.addEventListener("cc:layout", () => loadZones());

    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape" || event.defaultPrevented || ui.mode !== "drawing" || $("dialog[open]")) return;
        cancelFlow(true);
    });

    $("#zone-info-icon").append(icon("info", { size: 18 }));

    if (admin) {
        buildForm();
        $("#zone-toolbar").hidden = false;
        $("#zone-new").prepend(icon("plus"));
        $("#zone-new").addEventListener("click", newZone);
        $("#zone-undo").prepend(icon("undo-2"));
        $("#zone-undo").addEventListener("click", () => editor.undo());
        $("#zone-cancel").addEventListener("click", () => cancelFlow(true));
        $("#zone-grab").append(icon("rotate-ccw"));
        $("#zone-grab").addEventListener("click", () => {
            ui.frameRun = null; // asked for: a later run may fetch again on its own
            grabFrame();
        });
        renderToolbar();
    } else {
        const locked = $("#zone-locked");
        locked.hidden = false;
        if (!CC.isSignedIn()) {
            locked.append(el("a", { class: "ui-btn v-outline s-sm", href: "login.html?next=/zones.html" },
                icon("log-in"), "Sign in"));
        }
    }

    Shell.init({
        page: "zones",
        title: "Zones",
        subtitle: "Draw areas on the live frame. The detector picks up changes within a second.",
    });

    if (admin) loadZones();
})();
