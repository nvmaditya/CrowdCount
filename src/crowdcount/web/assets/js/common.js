/* Shared helpers for the CrowdCount UI: session, API calls, safe DOM building, toasts. */
"use strict";

const CC = (() => {
    const SESSION_KEY = "cc_session";

    // ------------------------------------------------------------ session

    function session() {
        for (const store of [localStorage, sessionStorage]) {
            try {
                const raw = store.getItem(SESSION_KEY);
                if (raw) return JSON.parse(raw);
            } catch (e) { /* ignore */ }
        }
        return null;
    }

    function saveSession(data, remember) {
        clearSession();
        const store = remember ? localStorage : sessionStorage;
        store.setItem(SESSION_KEY, JSON.stringify({ token: data.access_token, user: data.user }));
    }

    function clearSession() {
        localStorage.removeItem(SESSION_KEY);
        sessionStorage.removeItem(SESSION_KEY);
    }

    const isAdmin = () => session()?.user?.role === "admin";

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

    /** Call the JSON API. Signs out and redirects on 401 when a session existed. */
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

    const icon = (name, extra = "") => el("i", { class: `bi bi-${name} ${extra}`.trim(), "aria-hidden": "true" });
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

    function emptyState(iconName, title, ...hint) {
        return el("div", { class: "cc-empty" }, icon(iconName), el("div", { class: "cc-empty-title" }, title),
            hint.length ? el("div", { class: "cc-small" }, ...hint) : null);
    }

    // ------------------------------------------------------------ feedback

    function toast(message, type = "success") {
        let box = $(".cc-toasts");
        if (!box) {
            box = el("div", { class: "cc-toasts", role: "status", "aria-live": "polite" });
            document.body.append(box);
        }
        const icons = { success: "check-circle-fill", error: "exclamation-octagon-fill", warn: "exclamation-triangle-fill" };
        const node = el("div", { class: `cc-toast ${type === "success" ? "" : `is-${type}`}` },
            icon(icons[type] || icons.success), el("div", { class: "flex-grow-1" }, message),
            el("button", { class: "btn-close btn-sm", "aria-label": "Dismiss", onclick: () => node.remove() }));
        box.append(node);
        setTimeout(() => node.remove(), type === "error" ? 8000 : 4000);
    }

    /** Promise-based confirmation dialog built on a Bootstrap modal. */
    function confirm(message, { confirmLabel = "Delete", danger = true } = {}) {
        return new Promise((resolve) => {
            let result = false;
            const ok = el("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, type: "button" }, confirmLabel);
            const modalEl = el("div", { class: "modal fade", tabindex: "-1" },
                el("div", { class: "modal-dialog modal-dialog-centered modal-sm" },
                    el("div", { class: "modal-content" },
                        el("div", { class: "modal-body pt-4" }, el("p", { class: "mb-0" }, message)),
                        el("div", { class: "modal-footer border-0" },
                            el("button", { class: "btn btn-outline-secondary", type: "button", "data-bs-dismiss": "modal" }, "Cancel"),
                            ok))));
            document.body.append(modalEl);
            const modal = new bootstrap.Modal(modalEl);
            ok.addEventListener("click", () => { result = true; modal.hide(); });
            modalEl.addEventListener("hidden.bs.modal", () => { modalEl.remove(); resolve(result); });
            modal.show();
        });
    }

    // ------------------------------------------------------------ theme

    function setTheme(theme) {
        document.documentElement.setAttribute("data-bs-theme", theme);
        try { localStorage.setItem("cc_theme", theme); } catch (e) { /* ignore */ }
        document.dispatchEvent(new CustomEvent("cc:theme", { detail: theme }));
        $$("[data-cc-theme-toggle] .bi").forEach((i) => {
            i.className = `bi bi-${theme === "dark" ? "sun" : "moon-stars"}`;
        });
    }

    function initThemeToggle() {
        const current = document.documentElement.getAttribute("data-bs-theme") || "dark";
        $$("[data-cc-theme-toggle]").forEach((btn) => {
            btn.replaceChildren(icon(current === "dark" ? "sun" : "moon-stars"));
            btn.setAttribute("aria-label", "Toggle colour theme");
            btn.addEventListener("click", () => {
                setTheme(document.documentElement.getAttribute("data-bs-theme") === "dark" ? "light" : "dark");
            });
        });
    }

    const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

    // ------------------------------------------------------------ formatting

    function fmtDateTime(iso) {
        if (!iso) return "–";
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? "–" : d.toLocaleString();
    }

    function fmtTime(iso) {
        if (!iso) return "–";
        const d = new Date(iso);
        return Number.isNaN(d.getTime()) ? "–" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }

    function timeAgo(iso) {
        if (!iso) return "never";
        const secs = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
        if (secs < 5) return "just now";
        if (secs < 60) return `${secs}s ago`;
        if (secs < 3600) return `${Math.round(secs / 60)} min ago`;
        if (secs < 86400) return `${Math.round(secs / 3600)} h ago`;
        return `${Math.round(secs / 86400)} d ago`;
    }

    const humanize = (s) => String(s || "").replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

    function rgb(color, alpha = 1) {
        const [r, g, b] = Array.isArray(color) ? color : [45, 212, 191];
        return alpha === 1 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha})`;
    }

    async function signOut(redirect = "index.html") {
        try { await api("/auth/logout", { method: "POST" }); } catch (e) { /* token may already be invalid */ }
        clearSession();
        location.href = redirect;
    }

    document.addEventListener("DOMContentLoaded", initThemeToggle);

    return {
        session, saveSession, clearSession, isAdmin, signOut,
        api, ApiError, download,
        el, icon, $, $$, emptyState, toast, confirm,
        cssVar, setTheme,
        fmtDateTime, fmtTime, timeAgo, humanize, rgb,
    };
})();
