/* Alerts page: risk per scope, active alerts, alert rules with their editor, and the alert history.
 * Needs icons.js, core.js and shell.js; live data arrives through Shell's cc:summary and cc:layout
 * events. Levels and warn_at come from the server; only the editor's hint recomputes warn_at. */
"use strict";

(() => {
    const { el, $, icon, api, toast, emptyState, setText, withKey, replaceKeepingFocus } = CC;
    const changed = CC.changeTracker();

    const HISTORY_MS = 10000;
    const HISTORY_LIMIT = 500;
    const PENDING_MS = 5000;
    const SPAN = 1.25; // risk bars run from 0 to 125% of the limit
    const NEW_RULE = { limit: 10, warn_percent: 80, enabled: true, banner: true };
    const FILL = { over: "is-over", near: "is-near" };
    const TABS = [
        { key: "all", label: "All", empty: "No alerts recorded yet.", keep: () => true },
        { key: "open", label: "Needs review", empty: "Nothing needs review.", keep: (r) => !r.acknowledged },
        { key: "done", label: "Acknowledged", empty: "Nothing acknowledged yet.", keep: (r) => Boolean(r.acknowledged) },
    ];

    const ui = {
        summary: null,
        connected: true,
        rules: { global: null, zones: {} }, // the summary's rules with this page's pending changes applied
        scopes: [],
        acked: new Set(), // acknowledged here; shown as acknowledged until the next poll confirms it
        editing: { mode: "edit", name: null }, // name null = Total occupancy; or {mode: "new"}
        returnTo: { mode: "edit", name: null }, // where Cancel goes from a new rule
        loaded: null, // the form values as last filled in, to tell edits apart from stored changes
        history: [],
        historyLoaded: false,
        historyAdmin: false, // whether the loaded history came from the admin endpoint
        historyAsAdmin: false, // whether the latest request went to it
        tab: "all",
        alertIds: null,
    };

    // Rules this page just saved or removed (name null = global), laid over the summary's rules for the
    // table and the editor until the summary reports them or PENDING_MS pass, so a summary fetched
    // before the change can't put the old rule back (or send the editor away from a new one).
    const pending = new Map();

    const has = (obj, key) => Boolean(obj) && Object.prototype.hasOwnProperty.call(obj, key);
    const people = (n) => CC.plural(n, "person", "people");
    const labelOf = (name) => (name === null ? CC.TOTAL_LABEL : name);
    const keyOf = (name) => (name === null ? "global" : `zone:${name}`);
    const rulePath = (name) => (name === null ? "/admin/rules/global" : `/admin/rules/zones/${encodeURIComponent(name)}`);
    const ruleOf = (name) => (name === null ? ui.rules.global : has(ui.rules.zones, name) ? ui.rules.zones[name] : null);
    /** The same rule as the server: min(limit, max(1, ceil(limit × pct / 100))), in integers. */
    const warnAt = (limit, pct) => Math.min(limit, Math.max(1, Math.floor((limit * pct + 99) / 100)));
    const sameRule = (a, b) => (!a || !b ? !a && !b
        : a.limit === b.limit && a.warn_percent === b.warn_percent && a.enabled === b.enabled && a.banner === b.banner);

    function colorOf(name) {
        if (name === null) return "var(--foreground)";
        const zone = has(Shell.layout, name) ? Shell.layout[name] : null;
        return zone?.color ? CC.rgb(zone.color) : "var(--muted-foreground)";
    }

    // ------------------------------------------------------------ state

    function serverRule(name) {
        const rules = ui.summary?.rules || {};
        if (name === null) return rules.global || null;
        return has(rules.zones, name) ? rules.zones[name] : null;
    }

    function expect(name, rule) {
        pending.set(name, { rule, at: Date.now() });
    }

    function recompute() {
        for (const [name, p] of pending) {
            if (sameRule(serverRule(name), p.rule) || Date.now() - p.at > PENDING_MS) pending.delete(name);
        }
        const rules = ui.summary?.rules || {};
        const effective = { global: rules.global || null, zones: { ...(rules.zones || {}) } };
        for (const [name, p] of pending) {
            if (name === null) effective.global = p.rule || effective.global;
            else if (p.rule) effective.zones[name] = p.rule;
            else delete effective.zones[name];
        }
        ui.rules = effective;
        // Risk and active alerts stay on the server's rules, so a limit and its level always come together.
        ui.scopes = CC.scopes(ui.summary, Shell.layout);
    }

    const isAcked = (alert) => alert.acknowledged || ui.acked.has(alert.id);

    /** Forget local acknowledgements once the server reports them (or the alert has cleared). */
    function pruneAcked() {
        const open = new Set(Object.values(ui.summary.alerts || {}).filter((a) => !a.acknowledged).map((a) => a.id));
        for (const id of ui.acked) if (!open.has(id)) ui.acked.delete(id);
    }

    /** Zones in the layout without a rule, alphabetically: what "New rule" can pick from. */
    function freeZones() {
        return Object.keys(Shell.layout).filter((name) => !has(ui.rules.zones, name))
            .sort((a, b) => a.localeCompare(b));
    }

    // ------------------------------------------------------------ risk right now

    function riskNote(s) {
        if (s.now === null) return "Waiting for the detector";
        const since = CC.fmtTime(s.alert?.since);
        if (s.level === "over") return `Over its limit since ${since}`;
        if (s.level === "near") return s.now === s.rule.limit ? `At its limit since ${since}` : `Early warning since ${since}`;
        return `Early warning at ${people(s.rule.warn_at)}`;
    }

    function riskCard(s) {
        const limit = s.rule.limit;
        const width = s.now === null ? 0 : Math.min(100, (s.now / limit / SPAN) * 100);
        const marker = (s.rule.warn_percent / 100 / SPAN) * 100;
        return el("div", { class: "chart-card risk-card", role: "group", "aria-label": s.label },
            el("div", { class: "risk-head" },
                el("span", { class: "swatch", style: { background: s.color } }),
                el("span", { class: "risk-name" }, s.label),
                CC.levelBadge(s.level)),
            el("div", { class: "risk-figures" },
                el("span", { class: "risk-now" }, s.now ?? "–"),
                el("span", { class: "risk-limit" }, `of ${limit}`),
                el("span", { class: "risk-pct" }, s.now === null ? "–" : `${Math.round((s.now / limit) * 100)}%`)),
            el("div", { class: "risk-bar" },
                el("div", { class: `risk-fill ${FILL[s.level] || ""}`.trim(), style: { width: `${width.toFixed(1)}%` } }),
                el("span", { class: "risk-marker", title: "Early warning", style: { left: `${marker.toFixed(1)}%` } })),
            el("span", { class: "risk-note" }, riskNote(s)));
    }

    function renderRisk() {
        const grid = $("#risk-grid");
        if (!ui.summary) {
            if (!ui.connected && changed("risk", "offline")) {
                grid.replaceChildren(el("div", { class: "chart-card risk-empty" },
                    emptyState({ icon: "wifi-off", title: "Disconnected", hint: "Waiting for the server…" })));
            }
            return;
        }
        const cards = ui.scopes.filter((s) => s.rule?.enabled);
        const model = cards.map((s) => [s.name, s.label, s.color, s.level, s.now, s.rule.limit,
            s.rule.warn_percent, s.rule.warn_at, s.alert?.since ?? null]);
        if (!changed("risk", model)) return;
        grid.replaceChildren(...(cards.length ? cards.map(riskCard) : [
            el("div", { class: "chart-card risk-empty" }, emptyState({ icon: "bell", title: "No alert rules on" })),
        ]));
    }

    // ------------------------------------------------------------ active now

    const countOf = (s) => s.now ?? s.alert.current;

    function activeTitle(s) {
        if (s.level === "over") return `${s.label} is over its limit`;
        return countOf(s) === s.rule.limit ? `${s.label} is at its limit` : `${s.label} is near its limit`;
    }

    function activeDetail(s) {
        const now = countOf(s);
        const limit = s.rule.limit;
        const since = CC.fmtTime(s.alert.since);
        if (s.level === "over") return `${people(now)}, limit ${limit} · since ${since}`;
        return `${now} of ${people(limit)} (${Math.round((now / limit) * 100)}%) · since ${since}`;
    }

    function activeRow(s, admin) {
        const alert = s.alert;
        let review = null;
        if (isAcked(alert)) {
            review = el("span", { class: "ack-done" }, icon("check", { size: 14 }), "Acknowledged");
        } else if (admin) {
            review = el("button", {
                type: "button", class: "ui-btn v-outline s-sm",
                onclick: (event) => acknowledge(event.currentTarget, alert.id),
            }, "Acknowledge");
        }
        return el("div", { class: "active-row" },
            el("span", { class: `active-icon is-${s.level}` }, icon("triangle-alert")),
            el("div", { class: "active-text" },
                el("span", { class: "active-title" }),
                el("span", { class: "active-detail" })),
            CC.levelBadge(s.level),
            review);
    }

    function renderActive() {
        const admin = CC.isAdmin();
        const list = ui.scopes.filter((s) => s.alert && (s.level === "over" || s.level === "near"));
        const box = $("#active-list");
        setText($("#active-count"), list.length);
        // Rebuild only when the rows' shape changes, so a button is never swapped out under the pointer.
        if (changed("active", [list.map((s) => [s.alert.id, s.level, isAcked(s.alert)]), admin])) {
            box.replaceChildren(...(list.length ? list.map((s) => activeRow(s, admin)) : [
                el("p", { class: "active-empty" }, "Nothing active. Every count is within its limits."),
            ]));
        }
        list.forEach((s, i) => {
            const row = box.children[i];
            setText($(".active-title", row), activeTitle(s));
            setText($(".active-detail", row), activeDetail(s));
        });
    }

    async function acknowledge(button, id) {
        button.disabled = true;
        let data;
        try {
            data = await api(`/admin/alerts/${encodeURIComponent(id)}/acknowledge`, { method: "POST" });
        } catch (e) {
            toast(e.message, "error");
            button.disabled = false;
            return;
        }
        ui.acked.add(id);
        toast("Acknowledged");
        const at = ui.history.findIndex((r) => r.id === id);
        if (at !== -1 && data?.alert) {
            ui.history[at] = { ...ui.history[at], ...data.alert };
            renderHistory();
        } else {
            refreshHistory();
        }
        if (ui.summary) renderActive();
    }

    // ------------------------------------------------------------ rules table

    function ruleRows() {
        const zones = Object.keys(ui.rules.zones).sort((a, b) => a.localeCompare(b));
        return [
            ...(ui.rules.global ? [{ name: null, rule: ui.rules.global }] : []),
            ...zones.map((name) => ({ name, rule: ui.rules.zones[name] })),
        ];
    }

    function ruleRow({ name, rule }, admin, selected) {
        const label = labelOf(name);
        const key = keyOf(name);
        return el("tr", { class: selected ? "sel" : null },
            el("td", {}, el("span", { class: "scope-cell" },
                el("span", { class: "swatch", style: { background: colorOf(name) } }), label)),
            el("td", {}, "More than ", el("span", { class: "num" }, rule.limit), rule.limit === 1 ? " person" : " people"),
            el("td", { class: "t-muted" }, `At ${rule.warn_percent}% (${people(rule.warn_at)})`),
            el("td", {}, withKey(CC.switchControl({
                checked: rule.enabled,
                label: `${label} rule`,
                disabled: !admin,
                onchange: admin ? (on) => toggleRule(name, on) : undefined,
            }), `switch:${key}`)),
            el("td", {}, admin ? el("button", {
                type: "button", class: "dt-row-btn", "aria-label": `Edit the ${label} rule`, title: `Edit the ${label} rule`,
                dataset: { key: `edit:${key}` },
                onclick: () => openEditor(name),
            }, icon("pencil")) : null));
    }

    function renderRules() {
        const admin = CC.isAdmin();
        const rows = ruleRows();
        const sel = ui.editing.mode === "edit" ? ["edit", ui.editing.name] : ["new"];
        const model = rows.map(({ name, rule }) => [name, colorOf(name), rule.limit, rule.warn_percent, rule.warn_at, rule.enabled]);
        if (!changed("rules", [model, admin, sel])) return;
        replaceKeepingFocus($("#rules-body"), rows.map((r) => ruleRow(r, admin,
            admin && ui.editing.mode === "edit" && ui.editing.name === r.name)));
    }

    async function toggleRule(name, on) {
        const rule = ruleOf(name);
        if (!rule) return false;
        let data;
        try {
            data = await api(rulePath(name), {
                method: "PUT",
                body: { limit: rule.limit, warn_percent: rule.warn_percent, enabled: on, banner: rule.banner },
            });
        } catch (e) {
            toast(e.message, "error");
            return false;
        }
        expect(name, data.rule);
        toast(on ? "Rule turned on" : "Rule turned off");
        refresh();
        return true;
    }

    // ------------------------------------------------------------ rule editor

    const ed = {};

    function buildEditor(role) {
        const box = $("#editor");
        ed.title = el("h2", { class: "chart-title", id: "edit-h" }, "Edit rule");
        if (role !== "admin") {
            ed.ready = false;
            box.replaceChildren(
                el("div", { class: "card-head editor-head" }, ed.title),
                el("div", { class: "editor-locked" },
                    el("p", {}, "Sign in as an admin to change alert rules."),
                    role === "anon"
                        ? el("a", { class: "ui-btn v-outline s-sm", href: "login.html?next=/alerts.html" }, icon("log-in"), "Sign in")
                        : null));
            return;
        }
        ed.swatch = el("span", { class: "swatch" });
        ed.scopeName = el("span", {});
        ed.sub = el("span", { class: "editor-sub" }, ed.swatch, ed.scopeName);
        ed.select = el("select", { id: "rule-scope", class: "login-input", onchange: updateSub });
        ed.scopeField = el("div", { class: "login-field", hidden: true },
            el("label", { for: "rule-scope" }, "Scope"), ed.select);
        ed.limit = el("input", {
            id: "rule-limit", class: "login-input editor-num", type: "number", min: 1, max: 100000, step: 1,
            inputmode: "numeric", required: true, "aria-describedby": "rule-limit-unit", oninput: updateHint,
        });
        ed.limitUnit = el("span", { class: "editor-unit", id: "rule-limit-unit" });
        ed.pct = el("input", {
            id: "rule-warn", class: "login-input editor-num", type: "number", min: 1, max: 100, step: 1,
            inputmode: "numeric", required: true, "aria-describedby": "rule-warn-unit", oninput: updateHint,
        });
        ed.pctUnit = el("span", { class: "editor-unit", id: "rule-warn-unit" });
        ed.banner = el("input", { type: "checkbox" });
        ed.remove = el("button", { type: "button", class: "ui-btn v-ghost s-default t-danger", onclick: removeRule }, "Remove rule");
        ed.cancel = el("button", { type: "button", class: "ui-btn v-ghost s-default", onclick: cancelEdit }, "Cancel");
        ed.save = el("button", { type: "submit", class: "ui-btn v-default s-default" }, "Save rule");
        // novalidate: the server checks the values and its message is shown as a toast.
        const form = el("form", { class: "editor-form", novalidate: true, onsubmit: saveRule },
            ed.scopeField,
            el("div", { class: "login-field" },
                el("label", { for: "rule-limit" }, "Alert when more than"),
                el("div", { class: "editor-inline" }, ed.limit, ed.limitUnit)),
            el("div", { class: "login-field" },
                el("label", { for: "rule-warn" }, "Early warning at"),
                el("div", { class: "editor-inline" }, ed.pct, ed.pctUnit)),
            el("div", { class: "editor-checks" },
                el("label", { class: "check-row" }, ed.banner,
                    el("span", { class: "check-text" },
                        el("span", { class: "check-title" }, "Show a banner on the dashboard"),
                        el("span", { class: "check-hint" }, "Everyone viewing Live sees it until counts drop."))),
                el("label", { class: "check-row" }, el("input", { type: "checkbox", checked: true, disabled: true }),
                    el("span", { class: "check-text" },
                        el("span", { class: "check-title" }, "Record in alert history"),
                        el("span", { class: "check-hint" }, "Always on, so every breach can be reviewed.")))),
            el("div", { class: "editor-actions" }, ed.remove, ed.cancel, ed.save));
        box.replaceChildren(el("div", { class: "card-head editor-head" }, ed.title, ed.sub), form);
        ed.ready = true;
        ui.loaded = null;
        fillEditor();
    }

    /** The scope the editor works on: null for Total occupancy, a zone name, or "" for a new rule without a zone. */
    function editorScope() {
        return ui.editing.mode === "new" ? ed.select.value : ui.editing.name;
    }

    const formValues = () => ({ limit: ed.limit.value, warn_percent: ed.pct.value, banner: ed.banner.checked });
    const valuesOf = (rule) => ({ limit: String(rule.limit), warn_percent: String(rule.warn_percent), banner: Boolean(rule.banner) });
    const sameValues = (a, b) => JSON.stringify(a) === JSON.stringify(b);

    function syncScopeOptions() {
        const zones = freeZones();
        const current = [...ed.select.options].map((o) => o.value);
        if (sameValues(zones, current)) return;
        const keep = ed.select.value;
        ed.select.replaceChildren(...zones.map((name) => el("option", { value: name }, name)));
        if (zones.includes(keep)) ed.select.value = keep;
    }

    /** Fill the form for the current scope from its stored rule (or the defaults for a new rule). */
    function fillEditor() {
        const isNew = ui.editing.mode === "new";
        if (isNew) syncScopeOptions();
        const name = editorScope();
        const rule = isNew ? NEW_RULE : ruleOf(name);
        setText(ed.title, isNew ? "New rule" : "Edit rule");
        ed.scopeField.hidden = !isNew;
        ed.remove.hidden = isNew || name === null;
        ed.limit.value = rule ? String(rule.limit) : "";
        ed.pct.value = rule ? String(rule.warn_percent) : "";
        ed.banner.checked = rule ? Boolean(rule.banner) : true;
        ui.loaded = rule ? valuesOf(rule) : null;
        updateSub();
        updateHint();
    }

    function updateSub() {
        if (!ed.ready) return;
        const name = editorScope();
        const none = name === "";
        ed.swatch.hidden = none;
        if (!none) ed.swatch.style.background = colorOf(name);
        setText(ed.scopeName, none ? "" : labelOf(name));
        setText(ed.limitUnit, name === null ? "people in total" : "people are in the zone");
    }

    function updateHint() {
        const limit = Number(ed.limit.value);
        const pct = Number(ed.pct.value);
        const valid = ed.limit.value !== "" && ed.pct.value !== "" && Number.isInteger(limit) && limit >= 1
            && limit <= 100000 && Number.isInteger(pct) && pct >= 1 && pct <= 100;
        setText(ed.pctUnit, valid ? `% of the limit (${people(warnAt(limit, pct))})` : "% of the limit");
    }

    /** Follow stored changes made elsewhere without overwriting what the user is typing. */
    function renderEditor() {
        const admin = CC.isAdmin();
        const role = admin ? "admin" : CC.isSignedIn() ? "user" : "anon";
        if (changed("editor-role", role)) buildEditor(role);
        if (!admin || !ed.ready) return;
        if (ui.editing.mode === "new") {
            syncScopeOptions();
            if (!ed.select.options.length) {
                ui.editing = { mode: "edit", name: null }; // the last free zone got a rule elsewhere
                fillEditor();
            } else {
                updateSub();
            }
            return;
        }
        if (ui.editing.name !== null && !ruleOf(ui.editing.name)) {
            ui.editing = { mode: "edit", name: null }; // its rule was removed elsewhere
            fillEditor();
            return;
        }
        const rule = ruleOf(ui.editing.name);
        const dirty = ui.loaded !== null && !sameValues(formValues(), ui.loaded);
        if (rule && !dirty && !sameValues(valuesOf(rule), ui.loaded)) fillEditor();
        else updateSub();
    }

    function openEditor(name) {
        ui.editing = { mode: "edit", name };
        fillEditor();
        renderRules();
        ed.limit.focus();
    }

    function openNew() {
        if (!CC.isAdmin() || !ed.ready || !freeZones().length) return;
        if (ui.editing.mode === "edit") ui.returnTo = ui.editing;
        ui.editing = { mode: "new" };
        ed.select.replaceChildren(); // start from the first free zone
        fillEditor();
        renderRules();
        ed.select.focus();
    }

    function cancelEdit() {
        if (ui.editing.mode === "new") {
            const back = ui.returnTo;
            ui.editing = back.name === null || ruleOf(back.name) ? back : { mode: "edit", name: null };
        }
        fillEditor();
        renderRules();
    }

    const toInt = (value) => (value.trim() === "" ? null : Number(value));

    async function saveRule(event) {
        event.preventDefault();
        const isNew = ui.editing.mode === "new";
        const name = editorScope();
        if (isNew && !name) return;
        const stored = isNew ? null : ruleOf(name);
        const body = {
            limit: toInt(ed.limit.value),
            warn_percent: toInt(ed.pct.value),
            enabled: stored ? stored.enabled : true,
            banner: ed.banner.checked,
        };
        ed.save.disabled = true;
        let data;
        try {
            data = await api(rulePath(name), { method: "PUT", body });
        } catch (e) {
            toast(e.message, "error");
            return;
        } finally {
            ed.save.disabled = false;
        }
        expect(name, data.rule);
        toast("Rule saved");
        ui.editing = { mode: "edit", name };
        recompute();
        fillEditor();
        render();
    }

    async function removeRule() {
        const name = ui.editing.mode === "edit" ? ui.editing.name : null;
        if (name === null) return;
        const ok = await CC.confirm(`Remove the alert rule for ${name}? Its limit and early warning are deleted.`,
            { confirmLabel: "Remove" });
        if (!ok) return;
        ed.remove.disabled = true;
        try {
            await api(rulePath(name), { method: "DELETE" });
        } catch (e) {
            toast(e.message, "error");
            return;
        } finally {
            ed.remove.disabled = false;
        }
        expect(name, null);
        toast("Rule removed");
        ui.editing = { mode: "edit", name: null };
        recompute();
        fillEditor();
        render();
        ed.limit.focus();
    }

    // ------------------------------------------------------------ header action

    const newButton = el("button", { type: "button", class: "ui-btn v-default s-sm", onclick: openNew },
        icon("plus"), "New rule");
    // A disabled button gets no pointer events, so its tooltip lives on this wrapper too.
    const newWrap = el("span", { class: "btn-tip" }, newButton);

    function renderNewButton() {
        newWrap.hidden = !CC.isAdmin();
        const none = !freeZones().length;
        newButton.disabled = none;
        for (const node of [newButton, newWrap]) {
            if (none) node.title = "Every zone already has a rule";
            else node.removeAttribute("title");
        }
    }

    // ------------------------------------------------------------ history

    const scopeLabel = (record) => (record.alert_type === "global" ? CC.TOTAL_LABEL : record.alert_type);
    const recordLevel = (record) => (record.level === "near" ? "near" : "over");

    let historySeq = 0;
    async function refreshHistory() {
        const seq = ++historySeq;
        const admin = CC.isAdmin();
        ui.historyAsAdmin = admin;
        let data;
        try {
            data = await api(`${admin ? "/admin" : ""}/alerts/history?limit=${HISTORY_LIMIT}`);
        } catch (e) {
            // A 401 drops the session in CC.api: load the public history instead.
            if (admin && e.status === 401 && seq === historySeq) refreshHistory();
            return;
        }
        if (seq !== historySeq) return;
        ui.history = data.alerts || [];
        ui.historyAdmin = admin;
        ui.historyLoaded = true;
        renderHistory();
    }

    function reviewCell(record, admin) {
        if (record.acknowledged) {
            let text = "Acknowledged";
            if (admin && record.acknowledged_by) {
                text += ` by ${record.acknowledged_by}`;
                if (record.acknowledged_at) text += ` at ${CC.fmtClock(record.acknowledged_at)}`;
            }
            return el("span", { class: "ack-done" }, icon("check", { size: 14 }), text);
        }
        if (!admin) return el("span", { class: "hist-open" }, "Needs review");
        return el("button", {
            type: "button", class: "ui-btn v-outline s-xs", dataset: { key: `ack:${record.id}` },
            onclick: (event) => acknowledge(event.currentTarget, record.id),
        }, "Acknowledge");
    }

    function historyRow(record, admin) {
        return el("tr", {},
            el("td", { class: "hist-time" }, CC.fmtDateTime(record.timestamp)),
            el("td", { class: "hist-scope" }, scopeLabel(record)),
            el("td", {}, CC.levelBadge(recordLevel(record))),
            el("td", { class: "mono" }, `${record.actual_count} / ${record.threshold}`),
            el("td", {}, reviewCell(record, admin)));
    }

    const tabs = TABS.map((t) => {
        const count = el("span", { class: "ui-badge b-outline dt-count" }, "0");
        const button = el("button", {
            type: "button", role: "tab", class: "dt-tab", id: `tab-${t.key}`, "aria-controls": "history-panel",
            onclick: () => pickTab(t.key),
        }, t.label, " ", count);
        return { ...t, button, count };
    });

    function pickTab(key, focus = false) {
        ui.tab = key;
        renderHistory();
        if (focus) tabs.find((t) => t.key === key).button.focus();
    }

    function renderHistory() {
        const admin = CC.isAdmin() && ui.historyAdmin;
        const tab = tabs.find((t) => t.key === ui.tab);
        for (const t of tabs) {
            const on = t === tab;
            t.button.classList.toggle("active", on);
            t.button.setAttribute("aria-selected", String(on));
            t.button.tabIndex = on ? 0 : -1;
            setText(t.count, ui.history.filter(t.keep).length);
        }
        $("#history-panel").setAttribute("aria-labelledby", tab.button.id);
        $("#history-export").disabled = !ui.history.length;
        const rows = ui.history.filter(tab.keep);
        setText($("#history-note"), ui.historyLoaded ? `Showing ${rows.length} of ${CC.plural(ui.history.length, "alert")}` : "");
        const model = rows.map((r) => [r.id, r.timestamp, r.alert_type, r.level, r.actual_count, r.threshold,
            r.acknowledged, r.acknowledged_by ?? null, r.acknowledged_at ?? null]);
        if (!changed("history", [model, ui.tab, admin, ui.historyLoaded])) return;
        if (!ui.historyLoaded) return;
        replaceKeepingFocus($("#history-body"), rows.length ? rows.map((r) => historyRow(r, admin)) : [
            el("tr", { class: "table-empty" }, el("td", { colspan: 5 }, tab.empty)),
        ]);
    }

    const pad = (n) => String(n).padStart(2, "0");

    function csvCell(value) {
        const text = String(value ?? "");
        return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    }

    /** Build the CSV from the loaded rows, in the browser (the history is already here). */
    function exportCsv() {
        const admin = CC.isAdmin() && ui.historyAdmin;
        const head = ["timestamp", "scope", "level", "count", "limit", "acknowledged", ...(admin ? ["acknowledged_by"] : [])];
        const lines = [head, ...ui.history.map((r) => [
            r.timestamp, scopeLabel(r), recordLevel(r), r.actual_count, r.threshold, r.acknowledged ? "true" : "false",
            ...(admin ? [r.acknowledged_by || ""] : []),
        ])];
        const text = `${lines.map((cols) => cols.map(csvCell).join(",")).join("\r\n")}\r\n`;
        const d = new Date();
        const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
        const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
        const a = el("a", { href: url, download: `crowdcount_alerts_${stamp}.csv` });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    // ------------------------------------------------------------ wiring

    function render() {
        renderRisk();
        if (!ui.summary) return;
        pruneAcked();
        renderEditor();
        renderActive();
        renderRules();
        renderNewButton();
        setText($("#footer-version"), `CrowdCount ${ui.summary.version} · AGPL-3.0`);
        const ids = Object.values(ui.summary.alerts || {}).map((a) => a.id).sort().join(",");
        if (ui.alertIds !== null && ids !== ui.alertIds) refreshHistory();
        ui.alertIds = ids;
        if (ui.historyAsAdmin !== CC.isAdmin()) refreshHistory(); // signed out meanwhile
    }

    function refresh() {
        recompute();
        render();
    }

    document.addEventListener("cc:summary", (event) => {
        ui.summary = event.detail.summary;
        ui.connected = event.detail.connected;
        refresh();
    });

    document.addEventListener("cc:layout", refresh);

    $("#history-tabs").append(...tabs.map((t) => t.button));
    $("#history-tabs").addEventListener("keydown", (event) => {
        const at = tabs.findIndex((t) => t.key === ui.tab);
        const moves = { ArrowRight: at + 1, ArrowLeft: at - 1, Home: 0, End: tabs.length - 1 };
        if (!(event.key in moves)) return;
        event.preventDefault();
        pickTab(tabs[(moves[event.key] + tabs.length) % tabs.length].key, true);
    });
    $("#history-export").append(icon("download"), "Export CSV");
    $("#history-export").addEventListener("click", exportCsv);

    renderHistory();
    renderEditor(); // the form (empty until the first summary) or the sign-in note
    Shell.init({
        page: "alerts",
        title: "Alerts",
        subtitle: "Limits, early warnings and what fired",
        actions: CC.isAdmin() ? [newWrap] : [],
    });
    refreshHistory();
    setInterval(() => ui.connected && refreshHistory(), HISTORY_MS);
})();
