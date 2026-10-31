// Runs before first paint so the page never flashes the wrong theme.
(function () {
    var theme = "dark";
    try {
        theme = localStorage.getItem("cc_theme") ||
            (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    } catch (e) { /* storage blocked: keep dark */ }
    document.documentElement.setAttribute("data-bs-theme", theme);
})();
