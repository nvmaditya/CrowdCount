/* Sign-in page. */
"use strict";

(() => {
    const { $, api } = CC;
    const form = $("#login-form");
    const username = $("#username");
    const password = $("#password");
    const toggle = $("#toggle-password");
    const submit = $("#submit");
    const message = $("#message");

    /** Where to go after signing in: `?next=` when it is a path on this site, otherwise the dashboard. */
    function destination() {
        const next = new URLSearchParams(location.search).get("next");
        if (next && next.startsWith("/") && !next.startsWith("//")) {
            // Resolve it to catch tricks such as "/\evil.example", which browsers read as "//evil.example".
            const url = new URL(next, location.origin);
            if (url.origin === location.origin) return url.pathname + url.search + url.hash;
        }
        return "./";
    }

    if (CC.isSignedIn()) {
        api("/auth/me")
            .then(() => location.replace(destination()))
            .catch((e) => {
                if (e.status) CC.clearSession(); // rejected by the server; keep it if the server is unreachable
            });
    }

    $("#brand-logo").append(CC.logo(40));

    function showPassword(show) {
        password.type = show ? "text" : "password";
        toggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
        toggle.replaceChildren(CC.icon(show ? "eye-off" : "eye"));
    }
    showPassword(false);
    toggle.addEventListener("click", () => showPassword(password.type === "password"));

    function setPending(pending) {
        submit.disabled = pending;
        if (pending) submit.setAttribute("aria-busy", "true");
        else submit.removeAttribute("aria-busy");
        submit.replaceChildren(...(pending ? [CC.icon("loader-circle", { cls: "spin" })] : []), "Sign in");
    }

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const user = username.value.trim();
        if (!user || !password.value) {
            message.textContent = "Enter your username and password.";
            (user ? password : username).focus();
            return;
        }
        message.textContent = "";
        setPending(true);
        try {
            const data = await api("/auth/login", { method: "POST", body: { username: user, password: password.value } });
            CC.saveSession(data, $("#remember").checked);
            location.replace(destination());
            return; // stay disabled while the next page loads
        } catch (e) {
            message.textContent = e.status === 401 ? "Incorrect username or password." : e.message;
            password.select();
        }
        setPending(false);
    });
})();
