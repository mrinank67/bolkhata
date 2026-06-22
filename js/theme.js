/**
 * theme.js — Application-wide light/dark mode toggle.
 *
 * The theme is stored in localStorage and applied as data-theme on <html>.
 * An inline script in index.html applies the saved theme before first paint
 * (to avoid a flash); this module keeps the toggle buttons and meta colour
 * in sync and handles clicks.
 */

const STORAGE_KEY = "bk-theme";
const META = document.querySelector('meta[name="theme-color"]');
const META_COLORS = { dark: "#0a0a0f", light: "#f4f4f7" };
const ICONS = { dark: "🌙", light: "☀️" };

function getTheme() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  if (META) META.setAttribute("content", META_COLORS[theme]);

  document.querySelectorAll(".theme-toggle-icon").forEach(el => {
    el.textContent = ICONS[theme];
  });
  document.querySelectorAll(".theme-toggle").forEach(btn => {
    btn.setAttribute(
      "title",
      theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
    );
  });
}

function setTheme(theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {}
  applyTheme(theme);
}

function toggleTheme() {
  setTheme(getTheme() === "dark" ? "light" : "dark");
}

// Sync UI to the already-applied theme and wire every toggle button.
applyTheme(getTheme());
document.querySelectorAll(".theme-toggle").forEach(btn => {
  btn.addEventListener("click", toggleTheme);
});
