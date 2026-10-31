/* The app shell shared by the Live, Alerts, Zones and Settings pages: sidebar, page header, and the
 * /api/summary and /api/zones/layout polling the pages listen to. Needs icons.js and core.js.
 *
 * Shell.init({page, title, subtitle, actions}) renders into <aside id="sidebar"> and
 * <header id="header">, then dispatches on document:
 *   cc:summary  detail {summary, connected}  every second (summary is the last good payload or null)
 *   cc:layout   detail {layout}              on start and every 10 s ({name: {color, enabled, points}})
 */
"use strict";

const Shell = (() => {
    const { el, $, icon } = CC;

    const SUMMARY_MS = 1000;
    const LAYOUT_MS = 10000;
    const NAV = [
        { group: "Monitor" },
        { page: "live", href: "./", icon: "activity", label: "Live" },
        { page: "alerts", href: "alerts.html", icon: "bell", label: "Alerts", badge: true },
        { page: "zones", href: "zones.html", icon: "pentagon", label: "Zones" },
        { group: "Manage", signedIn: true },
        { page: "settings", href: "settings.html", icon: "settings", label: "Settings", signedIn: true },
    ];

    const ui = {
        page: null,
        summary: null,
        connected: true,
        layout: {},
        polling: false,
    };

    // ------------------------------------------------------------ sidebar

    function renderSidebar() {
        const signedIn = CC.isSignedIn();
        const nav = el("nav", { class: "sidebar-nav", "aria-label": "Main" });
        for (const item of NAV) {
            if (item.signedIn && !signedIn) continue;
            if (item.group) {
                nav.append(el("span", { class: "sb-group-label" }, item.group));
                continue;
            }
            const active = item.page === ui.page;
            nav.append(el("a", {
                class: active ? "sb-item active" : "sb-item",
                href: item.href,
                "aria-current": active ? "page" : null,
            },
            icon(item.icon),
            el("span", { class: "sb-label" }, item.label),
            item.badge ? el("span", { id: "sb-alert-count", class: "ui-badge b-destructive sb-count", hidden: true }) : null));
        }
        $("#sidebar").replaceChildren(
            el("a", { class: "sidebar-brand", href: "./" },
                CC.logo(28),
                el("span", { class: "sidebar-brand-text" },
                    el("span", { class: "sidebar-brand-name" }, "CrowdCount"),
                    el("span", { id: "sb-source", class: "sidebar-brand-sub" }, "No detector running"))),
            nav,
            el("div", { class: "sidebar-grow" }),
            renderFooter());
        renderSidebarStatus();
    }

    function renderFooter() {
        const user = CC.session()?.user;
        if (!CC.isSignedIn() || !user) {
            const next = encodeURIComponent(location.pathname);
            return el("div", { class: "sidebar-foot" },
                el("a", { class: "ui-btn v-outline s-sm sidebar-signin", href: `login.html?next=${next}` },
                    icon("log-in"), "Sign in"));
        }
        const name = String(user.username || "");
        return el("div", { class: "sidebar-foot" },
            el("span", { class: "ui-avatar" }, el("span", { class: "ui-avatar-fb" }, name.slice(0, 2).toUpperCase())),
            el("span", { class: "sidebar-user" },
                el("span", { class: "sidebar-user-name" }, name),
                el("span", { class: "sidebar-user-role" }, user.role === "admin" ? "Administrator" : "User")),
            el("button", {
                type: "button",
                class: "ui-btn v-ghost s-icon s-icon-sm sidebar-signout",
                "aria-label": "Sign out",
                title: "Sign out",
                onclick: () => CC.signOut(),
            }, icon("log-out")));
    }

    /** The parts of the sidebar that follow the summary: detector source and over-limit count. */
    function renderSidebarStatus() {
        const source = $("#sb-source");
        const count = $("#sb-alert-count");
        if (!source || !count) return;
        const s = ui.summary;
        source.textContent = s?.status?.source || "No detector running";
        const over = s ? Object.values(s.alerts || {}).filter((a) => a.level === "over").length : 0;
        count.textContent = String(over);
        count.hidden = over === 0;
        count.setAttribute("aria-label", `${CC.plural(over, "alert")} over the limit`);
    }

    // ------------------------------------------------------------ header

    function themeToggle() {
        const button = el("button", { type: "button", class: "ui-btn v-ghost s-icon s-icon-sm", id: "theme-toggle" });
        const sync = () => {
            const dark = CC.theme() === "dark";
            button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
            button.title = button.getAttribute("aria-label");
            button.replaceChildren(icon(dark ? "sun" : "moon"));
        };
        button.addEventListener("click", () => CC.setTheme(CC.theme() === "dark" ? "light" : "dark"));
        document.addEventListener("cc:theme", sync);
        sync();
        return button;
    }

    function renderHeader(title, subtitle, actions) {
        // replaceChildren (unlike CC.el) would print a null child as "null", so leave it out instead.
        $("#header").replaceChildren(
            el("h1", {}, title),
            ...(subtitle === null || subtitle === undefined ? [] : [el("span", { class: "page-sub" }, subtitle)]),
            el("div", { class: "page-actions" }, ...actions, themeToggle()));
    }

    // ------------------------------------------------------------ polling

    function setOffline(offline) {
        const main = $("main");
        const bar = $(".offline-bar", main);
        if (offline && !bar) {
            main.prepend(el("div", { class: "offline-bar", role: "status" },
                icon("wifi-off"),
                el("span", {}, "Can't reach the CrowdCount server. Retrying every second…")));
        } else if (!offline && bar) {
            bar.remove();
        }
    }

    async function pollSummary() {
        if (ui.polling) return; // a slow response must not stack requests
        ui.polling = true;
        try {
            ui.summary = await CC.api("/summary");
            ui.connected = true;
        } catch (e) {
            ui.connected = false;
        } finally {
            ui.polling = false;
        }
        setOffline(!ui.connected);
        renderSidebarStatus();
        document.dispatchEvent(new CustomEvent("cc:summary", {
            detail: { summary: ui.summary, connected: ui.connected },
        }));
    }

    async function pollLayout() {
        try {
            const data = await CC.api("/zones/layout");
            ui.layout = Object.fromEntries((data.zones || []).map((z) => [z.name, {
                color: z.color, enabled: z.enabled, points: z.points,
            }]));
        } catch (e) {
            return; // keep the last known layout
        }
        document.dispatchEvent(new CustomEvent("cc:layout", { detail: { layout: ui.layout } }));
    }

    /** Check a stored session once; CC.api drops it on a 401, and the sidebar then shows "Sign in". */
    function verifySession() {
        if (!CC.isSignedIn()) return;
        CC.api("/auth/me").catch((e) => {
            if (e.status === 401) renderSidebar();
        });
    }

    // ------------------------------------------------------------ init

    /**
     * Render the shell for `page` ("live" | "alerts" | "zones" | "settings") and start polling.
     * `subtitle` may be text or a node; `actions` are nodes placed before the theme toggle.
     */
    function init({ page, title, subtitle = null, actions = [] }) {
        ui.page = page;
        renderSidebar();
        renderHeader(title, subtitle, actions);
        verifySession();
        pollLayout();
        pollSummary();
        setInterval(pollSummary, SUMMARY_MS);
        setInterval(pollLayout, LAYOUT_MS);
    }

    return {
        init,
        get summary() { return ui.summary; },
        get layout() { return ui.layout; },
        get connected() { return ui.connected; },
    };
})();
