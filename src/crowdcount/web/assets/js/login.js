/* Sign-in page. */
"use strict";

(() => {
    const { $, el, icon, api } = CC;
    const params = new URLSearchParams(location.search);

    function destination(role) {
        const next = params.get("next");
        // Only same-site relative paths, to avoid an open redirect.
        if (next && next.startsWith("/") && !next.startsWith("//")) return next;
        return role === "admin" ? "admin.html" : "./";
    }

    const existing = CC.session();
    if (existing?.token) {
        api("/auth/me").then((user) => location.replace(destination(user.role))).catch(() => CC.clearSession());
    }

    function showMessage(text, kind = "danger") {
        $("#message").replaceChildren(el("div", { class: `alert alert-${kind} py-2 cc-small d-flex gap-2 align-items-start` },
            icon(kind === "danger" ? "exclamation-circle" : "info-circle"), el("span", {}, text)));
    }

    $("#toggle-password").addEventListener("click", (event) => {
        const input = $("#password");
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        event.currentTarget.replaceChildren(icon(show ? "eye-slash" : "eye"));
        event.currentTarget.setAttribute("aria-label", show ? "Hide password" : "Show password");
    });

    $("#login-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        const username = $("#username").value.trim();
        const password = $("#password").value;
        if (!username || !password) {
            showMessage("Enter your username and password.");
            return;
        }
        $("#submit").disabled = true;
        $("#spinner").classList.remove("d-none");
        try {
            const data = await api("/auth/login", { method: "POST", body: { username, password } });
            CC.saveSession(data, $("#remember").checked);
            location.replace(destination(data.user.role));
        } catch (e) {
            showMessage(e.status === 401 ? "Incorrect username or password." : e.message);
            $("#password").select();
        } finally {
            $("#submit").disabled = false;
            $("#spinner").classList.add("d-none");
        }
    });
})();
