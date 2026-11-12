// Per-device design: theme and text size, remembered in this browser.
export const THEMES = ["dark", "light", "contrast"];
export const SIZES = ["normal", "large"];

export const FONTS = {
  system: 'system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif',
  readable: 'Verdana, "DejaVu Sans", Tahoma, sans-serif',
  rounded: 'ui-rounded, "SF Pro Rounded", Nunito, "Varela Round", system-ui, sans-serif',
  serif: 'Georgia, "Noto Serif", "DejaVu Serif", serif',
  mono: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
};
const LOGO = "/static/img/logo.svg";
let shared = {};

export const logoUrl = () => LOGO;

function inkFor(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.45 ? "#0b1a29" : "#ffffff";
}

// What the admin chose for everybody. The high-contrast theme keeps its own colours.
export function applyAppearance(appearance = shared) {
  shared = appearance || {};
  document.title = shared.name || "pi-guard";
  const root = document.documentElement;
  if (shared.accent && root.dataset.theme !== "contrast") {
    root.style.setProperty("--accent", shared.accent);
    root.style.setProperty("--accent-ink", inkFor(shared.accent));
  } else {
    root.style.removeProperty("--accent");
    root.style.removeProperty("--accent-ink");
  }
  root.style.setProperty("--font", FONTS[shared.font] || FONTS.system);
  root.classList.toggle("no-motion", shared.animations === false);
}

export function applyDesign() {
  const root = document.documentElement;
  root.dataset.theme = localStorage.getItem("theme") || "dark";
  root.dataset.text = localStorage.getItem("text") || "normal";
  const color = getComputedStyle(root).getPropertyValue("--bg").trim();
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", color || "#121418");
  applyAppearance();
}

export function setDesign(theme, size) {
  if (theme) localStorage.setItem("theme", theme);
  if (size) localStorage.setItem("text", size);
  applyDesign();
}
