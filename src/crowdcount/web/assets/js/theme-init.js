// Runs before first paint so the page never flashes the wrong theme.
// Uses the stored choice, otherwise the OS preference, and sets the `.dark` class on <html>.
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
})();
