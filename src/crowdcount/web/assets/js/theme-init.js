// Runs before first paint so the page never flashes the wrong theme.
// Uses the stored choice, otherwise the OS preference. Sets the `.dark` class (new pages)
// and `data-bs-theme` (pages still on Bootstrap).
(function () {
    "use strict";
    var theme = null;
    try {
        theme = localStorage.getItem("cc_theme");
    } catch (e) { /* storage blocked: follow the OS */ }
    if (theme !== "dark" && theme !== "light") {
        theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    var root = document.documentElement;
    if (theme === "dark") root.classList.add("dark");
    else root.classList.remove("dark");
    root.setAttribute("data-bs-theme", theme);
})();
