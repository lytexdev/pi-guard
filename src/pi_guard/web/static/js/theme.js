// Per-device design: theme and text size, remembered in this browser.
export const THEMES = ["dark", "light", "contrast"];
export const SIZES = ["normal", "large"];

export function applyDesign() {
  const root = document.documentElement;
  root.dataset.theme = localStorage.getItem("theme") || "dark";
  root.dataset.text = localStorage.getItem("text") || "normal";
  const color = getComputedStyle(root).getPropertyValue("--bg").trim();
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", color || "#121418");
}

export function setDesign(theme, size) {
  if (theme) localStorage.setItem("theme", theme);
  if (size) localStorage.setItem("text", size);
  applyDesign();
}
