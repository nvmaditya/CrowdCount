/* Settings page. Admins get video sources, detection settings, users, data retention and exports, and the
 * activity log; every signed-in user gets "Your account" (password change). Signed-out visitors are sent
 * to sign in. Needs icons.js, core.js and shell.js. */
"use strict";

(() => {
    if (!CC.isSignedIn()) {
        location.replace(`login.html?next=${encodeURIComponent(location.pathname)}`);
        return;
    }

    const { el, $, $$, icon, api, toast, emptyState, setText, withKey, replaceKeepingFocus } = CC;
    const changed = CC.changeTracker();

    const LOG_PAGE = 50;
    const LOG_MAX = 1000; // the largest page the server returns
    const TRACKERS = { bytetrack: "ByteTrack", botsort: "BoT-SORT" };
    const CATEGORIES = { AUTH: "Sign-in", CONFIG: "Change", ALERT: "Alert", SYSTEM: "System" };

    const admin = CC.isAdmin();
    const me = CC.session()?.user || {};

    const ui = {
        summary: null,
        connected: true,
        detectorKey: undefined, // detector state and source, to refresh the sources' Running badge
        cameras: null, // null until loaded
        editing: null, // the open source form: null, "new" or a camera id
        users: null,
        runtime: null,
        retentionLoaded: false,
        logs: [],
        logTotal: 0,
        logDone: true,
        logLoaded: false,
        logCategory: "",
    };

    function field(id, label, input, cls) {
        return el("div", { class: cls ? `login-field ${cls}` : "login-field" }, el("label", { for: id }, label), input);
    }

    /** Show a load error, except a lost connection: the offline bar covers that, and loads retry on reconnect. */
    function loadFailed(e) {
        if (e.status && e.status !== 401) toast(e.message, "error");
    }

    /** A button that downloads a file, with the Live page's warning when there is no history yet. */
    function downloadButton(button, path) {
        button.addEventListener("click", async () => {
            button.disabled = true;
            try {
                await CC.download(typeof path === "function" ? path() : path);
            } catch (e) {
                if (e.status === 404) toast(CC.NO_HISTORY_MESSAGE, "warn");
                else toast(e.message, "error");
            } finally {
                button.disabled = false;
            }
        });
    }

    // ------------------------------------------------------------ video sources

    /** The start command, with the characters a shell treats specially inside double quotes escaped. */
    function startCommand(name) {
        const quoted = String(name).replace(/["\\$`]/g, (c) => `\\${c}`);
        return `crowdcount run --camera "${quoted}"`;
    }

    async function copyCommand(command, code) {
        try {
            await navigator.clipboard.writeText(command);
            toast("Command copied");
        } catch (e) {
            // No clipboard access (an insecure origin or a refusal): select the text to copy by hand.
            const range = document.createRange();
            range.selectNodeContents(code);
            const selection = getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
        }
    }

    function sourceBadge(cam) {
        if (cam.running) return el("span", { class: "ui-badge b-default" }, "Running");
        return el("span", { class: "ui-badge b-outline" }, cam.enabled ? "Ready" : "Off");
    }

    function sourceRow(cam) {
        const command = startCommand(cam.name);
        const code = el("span", { class: "code source-cmd", title: command }, command);
        const copyLabel = `Copy the start command for ${cam.name}`;
        const actions = CC.menu(`More actions for ${cam.name}`, [
            { label: "Edit", icon: "pencil", onselect: () => openSourceForm(cam.id) },
            { label: "Delete", icon: "trash-2", danger: true, onselect: () => deleteSource(cam) },
        ]);
        withKey(actions.firstChild, `menu:${cam.id}`);
        return el("div", { class: "source-row" },
            el("span", { class: "source-icon" }, icon("video", { size: 18 })),
            el("div", { class: "source-main" },
                el("span", { class: "source-name" }, el("span", { class: "source-name-text" }, cam.name), sourceBadge(cam)),
                el("span", { class: "source-url", title: cam.source_url }, cam.source_url)),
            code,
            el("span", { class: "source-actions" },
                el("button", {
                    type: "button", class: "ui-btn v-ghost s-icon s-icon-sm", "aria-label": copyLabel, title: copyLabel,
                    dataset: { key: `copy:${cam.id}` }, onclick: () => copyCommand(command, code),
                }, icon("copy")),
                withKey(CC.switchControl({
                    checked: cam.enabled,
                    label: `Enable ${cam.name}`,
                    onchange: (on) => setSourceEnabled(cam, on),
                }), `switch:${cam.id}`),
                actions));
    }

    function renderSources() {
        if (ui.editing !== null && ui.editing !== "new" && !ui.cameras?.some((c) => c.id === ui.editing)) {
            ui.editing = null; // its source was deleted elsewhere
        }
        const model = [ui.cameras?.map((c) => [c.id, c.name, c.source_url, c.enabled, c.running]) ?? null, ui.editing];
        if (!changed("sources", model) || ui.cameras === null) return;
        // The open form is the same node on every render, so what is typed in it survives a refresh.
        const nodes = ui.cameras.map((c) => (c.id === ui.editing ? src.form : sourceRow(c)));
        if (ui.editing === "new") nodes.unshift(src.form);
        replaceKeepingFocus($("#source-list"), nodes.length ? nodes : [emptyState({
            icon: "video", title: "No saved sources", hint: "Add a stream URL or video file to start it by name.",
        })]);
    }

    let camerasSeq = 0;
    async function loadCameras() {
        const seq = ++camerasSeq;
        let data;
        try {
            data = await api("/admin/cameras");
        } catch (e) {
            if (seq === camerasSeq) loadFailed(e);
            return;
        }
        if (seq !== camerasSeq) return;
        ui.cameras = data.cameras || [];
        renderSources();
    }

    const src = {};

    function buildSourceForm() {
        src.name = el("input", { id: "source-name", class: "login-input", type: "text", maxlength: 100, autocomplete: "off" });
        src.url = el("input", {
            id: "source-url", class: "login-input mono", type: "text", maxlength: 500, autocomplete: "off",
            spellcheck: "false", placeholder: "rtsp://camera.local/stream or /path/to/video.mp4",
        });
        src.save = el("button", { type: "submit", class: "ui-btn v-default s-default" }, "Save");
        // novalidate: the server checks the values and its message is shown as a toast.
        src.form = el("form", {
            class: "inline-form", novalidate: true, onsubmit: saveSource,
            onkeydown: (event) => {
                if (event.key !== "Escape") return;
                event.preventDefault();
                closeSourceForm(true);
            },
        },
        field("source-name", "Name", src.name, "inline-field"),
        field("source-url", "Video file or stream URL", src.url, "inline-field is-wide"),
        el("div", { class: "inline-actions" },
            el("button", { type: "button", class: "ui-btn v-ghost s-default", onclick: () => closeSourceForm(true) }, "Cancel"),
            src.save));
    }

    /** Open the source form for a new source ("new") or in the row of the camera with id `target`. */
    function openSourceForm(target) {
        if (ui.editing === target) {
            src.name.focus();
            return;
        }
        const cam = target === "new" ? null : ui.cameras?.find((c) => c.id === target);
        if (target !== "new" && !cam) return;
        ui.editing = target;
        src.name.value = cam ? cam.name : "";
        src.url.value = cam ? cam.source_url : "";
        src.form.setAttribute("aria-label", cam ? `Edit ${cam.name}` : "Add a video source");
        src.save.disabled = false;
        renderSources();
        src.name.focus();
    }

    function closeSourceForm(refocus) {
        const was = ui.editing;
        ui.editing = null;
        renderSources();
        if (!refocus) return;
        const back = was === "new" ? $("#source-add") : $(`[data-key="${CSS.escape(`menu:${was}`)}"]`);
        back?.focus();
    }

    async function saveSource(event) {
        event.preventDefault();
        const target = ui.editing;
        if (target === null) return;
        const body = { name: src.name.value.trim(), source_url: src.url.value.trim() };
        src.save.disabled = true;
        try {
            if (target === "new") await api("/admin/cameras", { method: "POST", body });
            else await api(`/admin/cameras/${encodeURIComponent(target)}`, { method: "PUT", body });
        } catch (e) {
            toast(e.message, "error");
            return;
        } finally {
            src.save.disabled = false;
        }
        toast(target === "new" ? "Source added" : "Source saved");
        closeSourceForm(true);
        loadCameras();
        refreshLog();
    }

    async function setSourceEnabled(cam, on) {
        let data;
        try {
            data = await api(`/admin/cameras/${encodeURIComponent(cam.id)}`, { method: "PUT", body: { enabled: on } });
        } catch (e) {
            toast(e.message, "error");
            return false;
        }
        const stored = ui.cameras?.find((c) => c.id === cam.id);
        if (stored) stored.enabled = data.enabled;
        toast(on ? "Source turned on" : "Source turned off");
        renderSources();
        refreshLog();
        return true;
    }

    async function deleteSource(cam) {
        if (!(await CC.confirm(`Delete the source ${cam.name}?`))) return;
        try {
            await api(`/admin/cameras/${encodeURIComponent(cam.id)}`, { method: "DELETE" });
        } catch (e) {
            toast(e.message, "error");
            return;
        }
        toast("Source deleted");
        $("#source-add").focus();
        loadCameras();
        refreshLog();
    }

    // ------------------------------------------------------------ detection

    /** [value, hint] for a device name, as the running detector or the configuration reports it. */
    function deviceText(device) {
        const raw = String(device);
        if (raw.startsWith("cuda")) return ["NVIDIA GPU", `(${raw})`];
        if (raw === "mps") return ["Apple GPU", "(mps)"];
        if (raw === "cpu") return ["CPU", ""];
        if (raw === "auto") return ["Auto", "(uses a GPU when there is one)"];
        return [raw, ""];
    }

    function framesText(stride) {
        if (!Number.isInteger(stride)) return ["–", ""];
        return stride === 1 ? ["Every frame", "(stride 1)"] : [`One in ${stride} frames`, `(stride ${stride})`];
    }

    /** [label, value, hint] rows: the running detector's model, tracker and device, else the configuration's. */
    function detectionRows() {
        const status = (ui.connected && ui.summary?.status) || {};
        const running = status.state === "running";
        const rt = ui.runtime || {};
        const pick = (key) => (running && status[key]) || rt[key] || null;
        const tracker = pick("tracker");
        const device = pick("device");
        const num = (value) => typeof value === "number" && Number.isFinite(value);
        return [
            ["Model", pick("model") || "–", ""],
            ["Tracker", tracker ? TRACKERS[tracker] || tracker : "–", ""],
            ["Device", ...(device ? deviceText(device) : ["–", ""])],
            ["Minimum confidence", num(rt.confidence) ? rt.confidence.toFixed(2) : "–", ""],
            ["Minimum box size", num(rt.min_box_area) ? `${rt.min_box_area.toLocaleString()} px` : "–", ""],
            ["Frames processed", ...framesText(rt.stride)],
        ];
    }

    function renderDetection() {
        const rows = detectionRows();
        if (!changed("detection", rows)) return;
        $("#detect-list").replaceChildren(...rows.map(([label, value, hint]) => el("div", { class: "detect-item" },
            el("dt", {}, label),
            el("dd", {}, value, hint ? " " : null, hint ? el("span", { class: "detect-hint" }, hint) : null))));
    }

    async function loadConfig() {
        let data;
        try {
            data = await api("/admin/config");
        } catch (e) {
            loadFailed(e);
            return;
        }
        ui.runtime = data.runtime || {};
        setText($("#data-dir"), ui.runtime.data_dir || "–");
        renderDetection();
    }

    // ------------------------------------------------------------ users

    function userRow(user) {
        const self = user.id === me.id;
        const isAdminRole = user.role === "admin";
        const actions = CC.menu(`More actions for ${user.username}`, [
            { label: "Delete user", icon: "trash-2", danger: true, disabled: self, onselect: () => deleteUser(user) },
        ]);
        withKey(actions.firstChild, `user:${user.id}`);
        return el("tr", {},
            el("td", {}, el("span", { class: "user-cell" },
                el("span", { class: "ui-avatar is-sm" },
                    el("span", { class: "ui-avatar-fb" }, String(user.username).slice(0, 2).toUpperCase())),
                el("span", { class: "user-name" }, user.username),
                self ? el("span", { class: "user-you" }, "You") : null)),
            el("td", {}, el("span", { class: `ui-badge ${isAdminRole ? "b-default" : "b-secondary"}` }, isAdminRole ? "Admin" : "User")),
            el("td", { class: "t-muted" }, user.last_login ? CC.fmtDateTime(user.last_login) : "Never"),
            el("td", {}, actions));
    }

    function renderUsers() {
        if (ui.users === null) return;
        replaceKeepingFocus($("#user-body"), ui.users.map(userRow));
    }

    let usersSeq = 0;
    async function loadUsers() {
        const seq = ++usersSeq;
        let data;
        try {
            data = await api("/admin/users");
        } catch (e) {
            if (seq === usersSeq) loadFailed(e);
            return;
        }
        if (seq !== usersSeq) return;
        ui.users = Array.isArray(data) ? data : [];
        renderUsers();
    }

    const uf = {};

    function buildUserForm() {
        uf.name = el("input", {
            id: "user-name", class: "login-input", type: "text", maxlength: 50, autocomplete: "off",
            autocapitalize: "none", spellcheck: "false",
        });
        uf.password = el("input", {
            id: "user-password", class: "login-input", type: "password", autocomplete: "new-password",
            placeholder: "At least 8 characters",
        });
        uf.role = el("select", { id: "user-role", class: "login-input" },
            el("option", { value: "user" }, "User"),
            el("option", { value: "admin" }, "Admin"));
        uf.save = el("button", { type: "submit", class: "ui-btn v-default s-default" }, "Save");
        uf.form = el("form", {
            class: "inline-form", novalidate: true, "aria-label": "Add a user", onsubmit: saveUser,
            onkeydown: (event) => {
                if (event.key !== "Escape") return;
                event.preventDefault();
                closeUserForm(true);
            },
        },
        field("user-name", "Username", uf.name, "inline-field"),
        field("user-password", "Password", uf.password, "inline-field"),
        field("user-role", "Role", uf.role, "inline-field is-narrow"),
        el("div", { class: "inline-actions" },
            el("button", { type: "button", class: "ui-btn v-ghost s-default", onclick: () => closeUserForm(true) }, "Cancel"),
            uf.save));
    }

    function openUserForm() {
        if (!uf.form.isConnected) {
            uf.name.value = "";
            uf.password.value = "";
            uf.role.value = "user";
            uf.save.disabled = false;
            $("#user-form-slot").replaceChildren(uf.form);
        }
        uf.name.focus();
    }

    function closeUserForm(refocus) {
        uf.form.remove();
        if (refocus) $("#user-add").focus();
    }

    async function saveUser(event) {
        event.preventDefault();
        const body = { username: uf.name.value.trim(), password: uf.password.value, role: uf.role.value };
        uf.save.disabled = true;
        try {
            await api("/admin/users", { method: "POST", body });
        } catch (e) {
            toast(e.message, "error");
            return;
        } finally {
            uf.save.disabled = false;
        }
        toast("User added");
        closeUserForm(true);
        loadUsers();
        refreshLog();
    }

    async function deleteUser(user) {
        if (!(await CC.confirm(`Delete the user ${user.username}?`))) return;
        try {
            await api(`/admin/users/${encodeURIComponent(user.id)}`, { method: "DELETE" });
        } catch (e) {
            toast(e.message, "error");
            return;
        }
        toast("User deleted");
        $("#user-add").focus();
        loadUsers();
        refreshLog();
    }

    // ------------------------------------------------------------ data

    async function loadRetention() {
        let data;
        try {
            data = await api("/admin/config/retention");
        } catch (e) {
            loadFailed(e);
            return;
        }
        ui.retentionLoaded = true;
        $("#retention").value = String(data.log_retention_days);
    }

    async function saveRetention(event) {
        event.preventDefault(); // only fires once the browser's own checks (1 to 3650, whole days) pass
        const save = $("#retention-save");
        save.disabled = true;
        let data;
        try {
            data = await api(`/admin/config/retention?retention_days=${encodeURIComponent($("#retention").value)}`, { method: "PUT" });
        } catch (e) {
            toast(e.message, "error");
            return;
        } finally {
            save.disabled = false;
        }
        const days = data.log_retention_days;
        $("#retention").value = String(days);
        toast(`Logs and alerts are kept for ${CC.plural(days, "day")}`);
        refreshLog();
    }

    // ------------------------------------------------------------ activity log

    /** "LOGIN_SUCCESS" -> "Login success", for entries without details. */
    function humanize(action) {
        const text = String(action || "").toLowerCase().replace(/_/g, " ");
        return text.charAt(0).toUpperCase() + text.slice(1);
    }

    function logRow(entry) {
        return el("tr", {},
            el("td", { class: "hist-time" }, CC.fmtDateTime(entry.timestamp)),
            el("td", {}, el("span", { class: "ui-badge b-outline" }, CATEGORIES[entry.category] || entry.category)),
            el("td", { class: "log-text" }, entry.details || humanize(entry.action)),
            el("td", { class: "t-muted log-by" }, entry.username || "System"));
    }

    function renderLog() {
        setText($("#log-note"), `Showing ${ui.logs.length} of ${ui.logTotal}`);
        const more = $("#log-more");
        more.hidden = ui.logDone;
        more.disabled = false;
        $("#log-body").replaceChildren(...(ui.logs.length ? ui.logs.map(logRow) : [
            el("tr", { class: "table-empty" },
                el("td", { colspan: 4 }, ui.logCategory ? "Nothing in this category yet." : "No activity recorded yet.")),
        ]));
    }

    let logSeq = 0;
    /** Load the first `limit` entries of the chosen category (replacing the rows), or with `more` the next page. */
    async function loadLog({ more = false, limit = LOG_PAGE } = {}) {
        const seq = ++logSeq;
        const category = $("#log-category").value;
        const params = new URLSearchParams({ limit: String(limit), offset: String(more ? ui.logs.length : 0) });
        if (category) params.set("category", category);
        if (more) $("#log-more").disabled = true;
        let data;
        try {
            data = await api(`/admin/logs?${params}`);
        } catch (e) {
            if (seq !== logSeq) return;
            loadFailed(e);
            $("#log-more").disabled = false;
            return;
        }
        if (seq !== logSeq) return;
        const rows = data.logs || [];
        if (more) {
            // Entries logged since the first page shift the offsets: skip the ones already shown.
            const seen = new Set(ui.logs.map((entry) => entry.id));
            ui.logs = ui.logs.concat(rows.filter((entry) => !seen.has(entry.id)));
        } else {
            ui.logs = rows;
        }
        ui.logTotal = Number.isInteger(data.total) ? data.total : ui.logs.length;
        ui.logDone = rows.length < limit || ui.logs.length >= ui.logTotal;
        ui.logCategory = category;
        ui.logLoaded = true;
        renderLog();
    }

    /** Reload after a change made here, keeping as many entries as are shown. */
    function refreshLog() {
        loadLog({ limit: Math.min(LOG_MAX, Math.max(LOG_PAGE, ui.logs.length)) });
    }

    const logExport = (kind) => () => {
        const category = $("#log-category").value;
        return `/admin/logs/export/${kind}${category ? `?category=${encodeURIComponent(category)}` : ""}`;
    };

    // ------------------------------------------------------------ account

    async function changePassword(event) {
        event.preventDefault();
        const now = $("#pw-now");
        const next = $("#pw-new");
        const again = $("#pw-again");
        if ([...next.value].length < 8) {
            toast("Use at least 8 characters", "error");
            next.focus();
            return;
        }
        if (next.value !== again.value) {
            toast("The new passwords don't match", "error");
            again.focus();
            return;
        }
        const save = $("#pw-save");
        save.disabled = true;
        try {
            await api("/auth/password", { method: "POST", body: { current_password: now.value, new_password: next.value } });
        } catch (e) {
            toast(e.message, "error");
            if (e.status === 400) now.select();
            return;
        } finally {
            save.disabled = false;
        }
        now.value = "";
        next.value = "";
        again.value = "";
        toast("Password updated");
        if (admin) refreshLog();
    }

    // ------------------------------------------------------------ tabs

    const tabs = $$("#settings-tabs .dt-tab");

    function markTab(hash) {
        const current = tabs.some((t) => t.hash === hash) ? hash : "#sources";
        for (const t of tabs) {
            const on = t.hash === current;
            t.classList.toggle("active", on);
            if (on) t.setAttribute("aria-current", "true");
            else t.removeAttribute("aria-current");
        }
    }

    // ------------------------------------------------------------ wiring

    function retryMissing() {
        if (ui.cameras === null) loadCameras();
        if (ui.runtime === null) loadConfig();
        if (ui.users === null) loadUsers();
        if (!ui.retentionLoaded) loadRetention();
        if (!ui.logLoaded) loadLog();
    }

    document.addEventListener("cc:summary", (event) => {
        const wasConnected = ui.connected;
        ui.summary = event.detail.summary;
        ui.connected = event.detail.connected;
        if (ui.summary) setText($("#footer-version"), `CrowdCount ${ui.summary.version} · AGPL-3.0`);
        if (!admin) return;
        if (ui.connected && !wasConnected) retryMissing();
        renderDetection();
        // Starting or stopping the detector changes which source is running.
        const status = ui.summary?.status || {};
        const key = `${status.state ?? ""}|${status.source ?? ""}`;
        if (ui.detectorKey !== undefined && key !== ui.detectorKey && ui.cameras !== null) loadCameras();
        ui.detectorKey = key;
    });

    setText($("#account-note"), `Signed in as ${me.username ?? ""}. Sessions last 24 hours.`);
    $("#pw-user").value = me.username ?? "";
    $("#password-form").addEventListener("submit", changePassword);

    if (admin) {
        $("#settings-tabs").hidden = false;
        for (const section of $$("[data-admin]")) section.hidden = false;
        buildSourceForm();
        buildUserForm();
        $("#source-add").append(icon("plus"), "Add source");
        $("#source-add").addEventListener("click", () => openSourceForm("new"));
        $("#user-add").append(icon("plus"), "Add user");
        $("#user-add").addEventListener("click", openUserForm);
        $("#retention-form").addEventListener("submit", saveRetention);
        downloadButton($("#export-csv"), "/export/csv");
        downloadButton($("#export-pdf"), "/export/pdf");
        $("#log-category").addEventListener("change", () => loadLog());
        $("#log-more").addEventListener("click", () => loadLog({ more: true }));
        downloadButton($("#log-csv"), logExport("csv"));
        downloadButton($("#log-pdf"), logExport("pdf"));
        for (const t of tabs) t.addEventListener("click", () => markTab(t.hash));
        window.addEventListener("hashchange", () => markTab(location.hash));
        markTab(location.hash);
        renderDetection(); // dashes until the configuration and the summary arrive
    }

    // The sections were hidden when the browser looked for the #fragment, and fill in as their data arrives:
    // scroll to it now and again once everything has loaded, unless the visitor has scrolled meanwhile.
    let moved = false;
    for (const type of ["wheel", "touchmove", "keydown", "mousedown"]) {
        addEventListener(type, () => { moved = true; }, { once: true, passive: true });
    }
    function scrollToHash() {
        if (moved || location.hash.length < 2) return;
        const target = document.getElementById(location.hash.slice(1));
        if (target && !target.hidden) target.scrollIntoView();
    }

    Shell.init({ page: "settings", title: "Settings" });
    scrollToHash();

    if (admin) {
        Promise.allSettled([loadCameras(), loadConfig(), loadUsers(), loadRetention(), loadLog()]).then(scrollToHash);
    }
})();
