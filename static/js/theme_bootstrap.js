// Apply the persisted theme before styles render to avoid a light-mode flash.
(function () {
  try {
    if (localStorage.getItem("baa_theme") === "dark") {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  } catch (_) {
    // Storage may be unavailable in hardened/private contexts; light is safe.
  }
})();
