/* Shared helpers for the CrowdCount UI: session, API calls, safe DOM building, render helpers, feedback, controls
 * (switch, menu, confirm dialog), theme, formatting, alert scopes.
 * Needs icons.js. Replaces common.js on the new pages (the two are never loaded together). */
"use strict";

const CC = (() => {
    const SESSION_KEY = "cc_session";
    const THEME_KEY = "cc_theme";
    const SVG_NS = "http://www.w3.org/2000/svg";

    // ------------------------------------------------------------ session

    function session() {
        for (const store of [localStorage, sessionStorage]) {
            try {
                const raw = store.getItem(SESSION_KEY);
                if (raw) return JSON.parse(raw);
            } catch (e) { /* storage blocked or bad JSON: try the next store */ }
        }
        return null;
    }

    function saveSession(data, remember) {
        clearSession();
        const store = remember ? localStorage : sessionStorage;
        store.setItem(SESSION_KEY, JSON.stringify({ token: data.access_token, user: data.user }));
    }

    function clearSession() {
        for (const store of [localStorage, sessionStorage]) {
            try { store.removeItem(SESSION_KEY); } catch (e) { /* ignore */ }
        }
    }

    const isSignedIn = () => Boolean(session()?.token);
    const isAdmin = () => session()?.user?.role === "admin";

    async function signOut(redirect = "./") {
        try { await api("/auth/logout", { method: "POST" }); } catch (e) { /* token may already be invalid */ }
        clearSession();
        location.href = redirect;
    }

    // ------------------------------------------------------------ API

    class ApiError extends Error {
        constructor(message, status) {
            super(message);
            this.status = status;
        }
    }

    function errorMessage(body, status) {
        const detail = body && body.detail;
        if (typeof detail === "string") return detail;
        if (Array.isArray(detail) && detail.length) {
            return detail.map((d) => `${(d.loc || []).slice(1).join(".") || "value"}: ${d.msg}`).join("; ");
        }
        return `Request failed (${status})`;
    }

    /** Call the JSON API. On a 401 with a session: sign out, and go to sign-in if the page requires it. */
    async function api(path, { method = "GET", body, raw = false } = {}) {
        const headers = {};
        const s = session();
        if (s?.token) headers.Authorization = `Bearer ${s.token}`;
        if (body !== undefined) headers["Content-Type"] = "application/json";
        let resp;
        try {
            resp = await fetch(`/api${path}`, {
                method,
                headers,
                body: body === undefined ? undefined : JSON.stringify(body),
            });
        } catch (e) {
            throw new ApiError("Cannot reach the CrowdCount server", 0);
        }
        if (resp.status === 401 && s?.token) {
            clearSession();
            if (document.body.dataset.requiresAuth) {
                location.href = `login.html?next=${encodeURIComponent(location.pathname)}`;
            }
        }
        if (!resp.ok) {
            let payload = null;
            try { payload = await resp.json(); } catch (e) { /* not JSON */ }
            throw new ApiError(errorMessage(payload, resp.status), resp.status);
        }
        if (raw) return resp;
        return resp.status === 204 ? null : resp.json();
    }

    /** Download an authenticated file (anchor links can't send the bearer token). */
    async function download(path) {
        const resp = await api(path, { raw: true });
        const disposition = resp.headers.get("Content-Disposition") || "";
        const name = (disposition.match(/filename="?([^"]+)"?/) || [])[1] || "download";
        const url = URL.createObjectURL(await resp.blob());
        const a = el("a", { href: url, download: name });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    // ------------------------------------------------------------ DOM

    /**
     * Build an element without innerHTML: el("div", {class: "x", onclick: fn}, "text", child).
     * Strings become text nodes, so user data is never parsed as HTML.
     */
    function el(tag, attrs = {}, ...children) {
        const node = document.createElement(tag);
        for (const [key, value] of Object.entries(attrs || {})) {
            if (value === null || value === undefined || value === false) continue;
            if (key.startsWith("on") && typeof value === "function") {
                node.addEventListener(key.slice(2), value);
            } else if (key === "class") {
                node.className = value;
            } else if (key === "dataset") {
                Object.assign(node.dataset, value);
            } else if (key === "style" && typeof value === "object") {
                Object.assign(node.style, value);
            } else {
                node.setAttribute(key, value === true ? "" : value);
            }
        }
        for (const child of children.flat()) {
            if (child === null || child === undefined || child === false) continue;
            node.append(child instanceof Node ? child : document.createTextNode(String(child)));
        }
        return node;
    }

    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

    function svgNode(tag, attrs) {
        const node = document.createElementNS(SVG_NS, tag);
        for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
        return node;
    }

    /** A Lucide icon from icons.js as an inline <svg>, hidden from assistive technology. */
    function icon(name, { size = 16, cls } = {}) {
        const node = svgNode("svg", {
            width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
            "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true",
        });
        if (cls) node.setAttribute("class", cls);
        const shapes = ICONS[name];
        if (!shapes) console.warn(`CC.icon: unknown icon "${name}"`);
        for (const [tag, attrs] of shapes || []) node.append(svgNode(tag, attrs));
        return node;
    }

    /** The CrowdCount brand mark, coloured by the current theme. */
    function logo(size = 28) {
        const node = svgNode("svg", { width: size, height: size, viewBox: "0 0 32 32", "aria-hidden": "true" });
        for (const [tag, attrs] of LOGO) node.append(svgNode(tag, attrs));
        return node;
    }

    /** A centred placeholder for an empty list or a missing feed. `hint` may be text or nodes. */
    function emptyState({ icon: iconName, title, hint, code } = {}) {
        return el("div", { class: "empty" },
            iconName ? el("span", { class: "empty-icon" }, icon(iconName, { size: 20 })) : null,
            title ? el("div", { class: "empty-title" }, title) : null,
            hint ? el("p", { class: "empty-hint" }, hint) : null,
            code ? el("span", { class: "code" }, code) : null);
    }

    // ------------------------------------------------------------ rendering (pages re-render on every poll)

    /**
     * A render memo for one page: the returned changed(region, value) is true when `value` differs from
     * what `region` last rendered (compared as JSON), and remembers it. Each page makes its own.
     */
    function changeTracker() {
        const keys = new Map();
        return (region, value) => {
            const key = JSON.stringify(value);
            if (keys.get(region) === key) return false;
            keys.set(region, key);
            return true;
        };
    }

    /** Set a node's text only when it differs, so a poll that changes nothing touches nothing. */
    function setText(node, text) {
        const value = String(text);
        if (node.textContent !== value) node.textContent = value;
    }

    /** Give a focusable node the data-key that replaceKeepingFocus finds it by after a re-render; returns it. */
    function withKey(node, key) {
        node.dataset.key = key;
        return node;
    }

    /**
     * Replace a container's children without losing keyboard focus: a focused node that is put back (an open
     * form) keeps focus and its caret, otherwise focus moves to the new element with the same data-key.
     */
    function replaceKeepingFocus(box, nodes) {
        const active = document.activeElement;
        const inside = Boolean(active) && box.contains(active);
        let caret = null;
        try {
            if (inside && typeof active.selectionStart === "number") caret = [active.selectionStart, active.selectionEnd];
        } catch (e) { /* inputs without a selection */ }
        box.replaceChildren(...nodes);
        if (!inside) return;
        if (active.isConnected) {
            active.focus({ preventScroll: true });
            try {
                if (caret) active.setSelectionRange(...caret);
            } catch (e) { /* inputs without a selection */ }
        } else if (active.dataset?.key) {
            box.querySelector(`[data-key="${CSS.escape(active.dataset.key)}"]`)?.focus();
        }
    }

    // ------------------------------------------------------------ feedback

    const TOAST_ICONS = { success: "check", warn: "triangle-alert", error: "triangle-alert" };

    /** The warning for /api/export/* answering 404 (no history recorded yet). */
    const NO_HISTORY_MESSAGE = "Nothing to export yet: start the detector first.";

    /** Show a short message in the top-right stack. Errors stay for 8 s, everything else for 4 s. */
    function toast(message, type = "success") {
        const kind = TOAST_ICONS[type] ? type : "success";
        let box = $(".toasts");
        if (!box) {
            box = el("div", { class: "toasts", role: "status", "aria-live": "polite" });
            document.body.append(box);
        }
        const node = el("div", { class: kind === "success" ? "toast" : `toast is-${kind}` },
            icon(TOAST_ICONS[kind], { cls: "toast-icon" }),
            el("div", { class: "toast-text" }, message),
            el("button", { type: "button", class: "toast-close", "aria-label": "Dismiss", onclick: () => node.remove() },
                icon("x", { size: 14 })));
        box.append(node);
        setTimeout(() => node.remove(), kind === "error" ? 8000 : 4000);
        return node;
    }

    // ------------------------------------------------------------ controls

    /**
     * An on/off switch (<button role="switch">). A click flips it and calls onchange(checked, button).
     * When onchange returns a promise, further clicks are ignored until it settles (the button stays
     * focusable), and the switch flips back if it rejects or resolves to false.
     */
    function switchControl({ checked, label, disabled = false, onchange }) {
        const button = el("button", {
            type: "button", class: "switch", role: "switch",
            "aria-checked": String(Boolean(checked)), "aria-label": label, disabled,
        }, el("span", { class: "switch-thumb" }));
        let busy = false;
        button.addEventListener("click", async () => {
            if (busy) return;
            const next = button.getAttribute("aria-checked") !== "true";
            button.setAttribute("aria-checked", String(next));
            if (!onchange) return;
            busy = true;
            button.setAttribute("aria-busy", "true");
            let ok;
            try {
                ok = (await onchange(next, button)) !== false;
            } catch (e) {
                ok = false;
            }
            busy = false;
            button.removeAttribute("aria-busy");
            if (!ok) button.setAttribute("aria-checked", String(!next));
        });
        return button;
    }

    /**
     * A row-actions menu: a dt-row-btn trigger (ellipsis-vertical, aria-label `label`) and a popover
     * list of `items` ({label, icon, danger, disabled, onselect}). It closes on a selection, an
     * outside click, scrolling, Tab and Escape; Escape and a selection return focus to the trigger, and
     * Tab moves on from it.
     */
    function menu(label, items) {
        const trigger = el("button", {
            type: "button", class: "dt-row-btn", "aria-label": label, title: label,
            "aria-haspopup": "menu", "aria-expanded": "false",
        }, icon("ellipsis-vertical"));
        const list = el("div", { class: "menu-list", role: "menu", "aria-label": label, hidden: true });
        const wrap = el("div", { class: "menu" }, trigger, list);
        const enabled = () => $$(".menu-item:not(:disabled)", list);

        for (const item of items) {
            list.append(el("button", {
                type: "button", role: "menuitem", tabindex: "-1", disabled: Boolean(item.disabled),
                class: item.danger ? "menu-item is-danger" : "menu-item",
                onclick: () => {
                    close(true);
                    if (item.onselect) item.onselect();
                },
            }, item.icon ? icon(item.icon) : null, el("span", {}, item.label)));
        }

        function onOutside(event) {
            if (!wrap.contains(event.target)) close(false);
        }
        function onKey(event) {
            if (event.key === "Escape") {
                event.preventDefault();
                close(true);
            }
        }
        function onScroll() {
            close(false);
        }

        function open() {
            list.hidden = false;
            // Fixed, so the list is never clipped by a scrolling table or a card's overflow.
            const r = trigger.getBoundingClientRect();
            const left = Math.max(8, Math.min(r.right - list.offsetWidth, window.innerWidth - list.offsetWidth - 8));
            const below = r.bottom + 4;
            const top = below + list.offsetHeight > window.innerHeight - 8 && r.top - list.offsetHeight - 4 >= 8
                ? r.top - list.offsetHeight - 4
                : below;
            list.style.left = `${left}px`;
            list.style.top = `${top}px`;
            trigger.setAttribute("aria-expanded", "true");
            document.addEventListener("pointerdown", onOutside, true);
            document.addEventListener("keydown", onKey, true);
            window.addEventListener("scroll", onScroll, true);
            window.addEventListener("resize", onScroll);
            enabled()[0]?.focus({ preventScroll: true });
        }

        function close(refocus) {
            if (list.hidden) return;
            list.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
            document.removeEventListener("pointerdown", onOutside, true);
            document.removeEventListener("keydown", onKey, true);
            window.removeEventListener("scroll", onScroll, true);
            window.removeEventListener("resize", onScroll);
            if (refocus) trigger.focus();
        }

        trigger.addEventListener("click", () => (list.hidden ? open() : close(false)));
        list.addEventListener("keydown", (event) => {
            if (event.key === "Tab") {
                // Close and let Tab move on from the trigger, as for any menu button.
                close(true);
                return;
            }
            const buttons = enabled();
            const at = buttons.indexOf(document.activeElement);
            const moves = { ArrowDown: at + 1, ArrowUp: at - 1, Home: 0, End: buttons.length - 1 };
            if (!(event.key in moves) || !buttons.length) return;
            event.preventDefault();
            buttons[(moves[event.key] + buttons.length) % buttons.length].focus();
        });
        // Pressing an item must not move focus: Safari and Firefox on macOS don't focus buttons on
        // mousedown, so focus would leave the list (relatedTarget null) and close it before the click.
        list.addEventListener("mousedown", (event) => event.preventDefault());
        // Focus moving to another element closes the list. A null relatedTarget (a press somewhere that
        // takes no focus) is left to the outside-pointerdown handler, which ignores presses inside.
        wrap.addEventListener("focusout", (event) => {
            if (event.relatedTarget && !wrap.contains(event.relatedTarget)) close(false);
        });
        return wrap;
    }

    let confirmSeq = 0;

    /** Ask before a destructive action. Resolves true on confirm, false on Cancel, Escape or a backdrop click. */
    function confirm(message, { confirmLabel = "Delete", danger = true } = {}) {
        return new Promise((resolve) => {
            const id = `confirm-msg-${++confirmSeq}`;
            const cancel = el("button", {
                type: "button", class: "ui-btn v-outline s-default", onclick: () => dialog.close("cancel"),
            }, "Cancel");
            const ok = el("button", {
                type: "button", class: `ui-btn ${danger ? "v-destructive" : "v-default"} s-default`,
                onclick: () => dialog.close("confirm"),
            }, confirmLabel);
            const dialog = el("dialog", { class: "confirm", role: "alertdialog", "aria-labelledby": id },
                el("div", { class: "confirm-body" },
                    el("p", { class: "confirm-message", id }, message),
                    el("div", { class: "confirm-actions" }, cancel, ok)));
            // Escape fires "cancel" and then "close" with an empty returnValue.
            dialog.addEventListener("close", () => {
                dialog.remove();
                resolve(dialog.returnValue === "confirm");
            });
            // The dialog element itself is only hit outside .confirm-body, i.e. on the backdrop.
            dialog.addEventListener("click", (event) => {
                if (event.target === dialog) dialog.close("cancel");
            });
            document.body.append(dialog);
            dialog.showModal();
            cancel.focus();
        });
    }

    // ------------------------------------------------------------ theme

    const theme = () => (document.documentElement.classList.contains("dark") ? "dark" : "light");

    function applyTheme(t) {
        document.documentElement.classList.toggle("dark", t === "dark");
        document.dispatchEvent(new CustomEvent("cc:theme", { detail: t }));
    }

    /** Switch to "light" or "dark" and remember the choice. */
    function setTheme(t) {
        const next = t === "dark" ? "dark" : "light";
        try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* storage blocked: still switch */ }
        applyTheme(next);
    }

    // Follow the OS until the user picks a theme (theme-init.js sets the initial one).
    try {
        const media = window.matchMedia("(prefers-color-scheme: dark)");
        media.addEventListener("change", (event) => {
            let stored = null;
            try { stored = localStorage.getItem(THEME_KEY); } catch (e) { /* ignore */ }
            if (stored !== "dark" && stored !== "light") applyTheme(event.matches ? "dark" : "light");
        });
    } catch (e) { /* matchMedia unavailable */ }

    // ------------------------------------------------------------ formatting

    const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const pad = (n) => String(n).padStart(2, "0");

    function toDate(iso) {
        if (iso === null || iso === undefined || iso === "") return null;
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? null : d;
    }

    /** "16:35:02" (local time), or "–". */
    function fmtTime(iso) {
        const d = toDate(iso);
        return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}` : "–";
    }

    /** "16:35", or "–". */
    function fmtClock(iso) {
        const d = toDate(iso);
        return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : "–";
    }

    /** "Oct 31, 16:35:02", or "–". */
    function fmtDateTime(iso) {
        const d = toDate(iso);
        return d ? `${MONTHS[d.getMonth()]} ${d.getDate()}, ${fmtTime(d)}` : "–";
    }

    /** The time alone for today, the date and time otherwise. */
    function fmtStamp(iso) {
        const d = toDate(iso);
        if (!d) return "–";
        return d.toDateString() === new Date().toDateString() ? fmtTime(d) : fmtDateTime(d);
    }

    /** CSS colour for an [r, g, b] zone colour (teal when missing). */
    function rgb(color, alpha = 1) {
        const [r, g, b] = Array.isArray(color) ? color : [45, 212, 191];
        return alpha === 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    /** plural(3, "zone") -> "3 zones"; plural(1, "person", "people") -> "1 person". */
    const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

    let tokenCtx = null;

    /**
     * The current value of a colour token such as "--foreground" as [r, g, b, a] (a is 0–1, 1 for
     * opaque tokens), resolved through a 1×1 canvas so Chart.js gets plain sRGB instead of oklch().
     */
    function tokenRgb(name) {
        if (!tokenCtx) {
            const canvas = el("canvas", { width: 1, height: 1 });
            tokenCtx = canvas.getContext("2d", { willReadFrequently: true });
        }
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        tokenCtx.clearRect(0, 0, 1, 1);
        tokenCtx.fillStyle = "#000"; // an unparseable value leaves fillStyle unchanged
        tokenCtx.fillStyle = value || "#000";
        tokenCtx.fillRect(0, 0, 1, 1);
        const [r, g, b, a] = tokenCtx.getImageData(0, 0, 1, 1).data;
        return [r, g, b, Math.round((a / 255) * 100) / 100];
    }

    // ------------------------------------------------------------ alert scopes

    const TOTAL_LABEL = "Total occupancy";
    const LEVEL_TEXT = { over: "Over limit", near: "Near limit", normal: "Normal", off: "Off", none: "No limit" };
    const LEVEL_ORDER = ["over", "near", "normal", "off", "none"];

    /** A badge for an alert level: destructive when over, amber when near, muted outline otherwise. */
    function levelBadge(level) {
        const cls = level === "over" ? "b-destructive" : level === "near" ? "b-warn" : "b-outline t-muted";
        return el("span", { class: `ui-badge ${cls}` }, LEVEL_TEXT[level] || LEVEL_TEXT.none);
    }

    /**
     * Total occupancy and every zone with its count, rule, active alert and level, most at risk first.
     * `layout` maps zone names to {color, enabled, points} (Shell.layout). Levels come from the server.
     */
    function scopes(summary, layout) {
        if (!summary) return [];
        layout = layout || {};
        const rules = summary.rules || {};
        const alerts = Object.values(summary.alerts || {});
        const zones = summary.zones || {};
        const make = (kind, name, label, color, now, rule, alert) => {
            const level = !rule ? "none" : !rule.enabled ? "off" : alert ? alert.level : "normal";
            const ratio = rule && now !== null ? now / rule.limit : null;
            return { kind, name, label, color, now, rule, alert, level, ratio };
        };
        const list = [make("global", null, TOTAL_LABEL, "var(--foreground)",
            summary.last_update ? summary.total_count : null, rules.global || null,
            alerts.find((a) => a.type === "global") || null)];
        const names = new Set([...Object.keys(zones), ...Object.keys(rules.zones || {})]);
        for (const name of names) {
            const color = layout[name]?.color ? rgb(layout[name].color) : "var(--muted-foreground)";
            list.push(make("zone", name, name, color, zones[name]?.current ?? null, rules.zones?.[name] || null,
                alerts.find((a) => a.type === "zone" && a.zone === name) || null));
        }
        return list.sort((a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level)
            || (b.ratio ?? -1) - (a.ratio ?? -1)
            || a.label.localeCompare(b.label));
    }

    return {
        session, saveSession, clearSession, isSignedIn, isAdmin, signOut,
        api, ApiError, download,
        el, $, $$, icon, logo, emptyState,
        changeTracker, setText, withKey, replaceKeepingFocus,
        toast, NO_HISTORY_MESSAGE,
        switchControl, menu, confirm,
        theme, setTheme,
        fmtTime, fmtClock, fmtDateTime, fmtStamp,
        rgb, plural, tokenRgb,
        TOTAL_LABEL, LEVEL_TEXT, levelBadge, scopes,
    };
})();
