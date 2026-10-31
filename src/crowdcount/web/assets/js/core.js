/* Shared helpers for the CrowdCount UI: session, API calls, safe DOM building, feedback, theme, formatting.
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

    // ------------------------------------------------------------ feedback

    const TOAST_ICONS = { success: "check", warn: "triangle-alert", error: "triangle-alert" };

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

    return {
        session, saveSession, clearSession, isSignedIn, isAdmin, signOut,
        api, ApiError, download,
        el, $, $$, icon, logo, emptyState,
        toast,
        theme, setTheme,
        fmtTime, fmtClock, fmtDateTime, fmtStamp,
        rgb, plural,
    };
})();
