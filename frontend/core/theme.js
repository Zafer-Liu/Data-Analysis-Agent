const STORAGE_KEY = "baa_theme";

export function getTheme() {
  return localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
}

export function applyTheme(theme) {
  const html = document.documentElement;
  if (theme === "dark") html.setAttribute("data-theme", "dark");
  else html.removeAttribute("data-theme");

  const button = document.getElementById("theme-toggle");
  if (!button) return;

  button.textContent = theme === "dark" ? "☀" : "🌙";
  button.title =
    theme === "dark"
      ? globalThis.t
        ? globalThis.t("theme.to_light")
        : "Light mode"
      : globalThis.t
        ? globalThis.t("theme.to_dark")
        : "Dark mode";
}

export function setTheme(theme) {
  localStorage.setItem(STORAGE_KEY, theme);
  applyTheme(theme);
}

export function toggleTheme() {
  setTheme(getTheme() === "dark" ? "light" : "dark");
}

export const theme = Object.freeze({
  getTheme,
  setTheme,
  toggleTheme,
  applyTheme,
});

applyTheme(getTheme());
document.addEventListener("langchange", () => applyTheme(getTheme()));
