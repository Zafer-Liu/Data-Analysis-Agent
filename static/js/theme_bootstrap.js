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

// Mark the initial-paint window so first-render lists (e.g. the sidebar's
// saved sessions) can stagger in. Removed shortly after load so later
// list re-renders (save / rename / delete) stay calm and don't replay.
(function () {
  document.documentElement.classList.add("page-boot");
  window.addEventListener("load", function () {
    setTimeout(function () {
      document.documentElement.classList.remove("page-boot");
    }, 1600);
  });
})();
