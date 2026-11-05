// Appearance. Theme and text size belong to this device, the rest to everyone.
import { api, attempt, fill, h, icon, t } from "../api.js";
import { navItems, state } from "../app.js";
import { applyAppearance, FONTS, logoUrl, setDesign, SIZES, THEMES } from "../theme.js";

const DEFAULT_ACCENT = "#5cb3ff";
const SWATCHES = [DEFAULT_ACCENT, "#4fd18b", "#f2b64c", "#ff7a59", "#e36bd2", "#9a8cff"];

export function sharedDesign(box) {
  const saved = state.session?.appearance || {};
  const color = h("input", { id: "apcolor", type: "color", value: saved.accent || DEFAULT_ACCENT });
  const font = h("select", { id: "apfont" }, Object.keys(FONTS).map((f) => h("option", { value: f, selected: f === (saved.font || "system") }, t(`web.ap_font_${f}`))));
  const motion = h("input", { type: "checkbox", checked: saved.animations !== false });
  const appName = h("input", { id: "apname", maxlength: 30, value: saved.name || "", placeholder: "pi-guard" });
  const logoFile = h("input", { id: "aplogo", type: "file", accept: "image/png,image/jpeg,image/webp" });
  const current = () => ({ ...state.session.appearance, accent: color.value, font: font.value, animations: motion.checked, name: appName.value.trim() });
  const preview = () => applyAppearance(current());
  const save = async () => {
    const body = current();
    if (await attempt(() => api("/api/appearance", { method: "PUT", body }), t("web.saved"))) state.session.appearance = body;
  };
  const changeLogo = async (request) => {
    if (!await attempt(request, t("web.saved"))) return;
    state.session = await api("/api/session");
    applyAppearance(state.session.appearance);
    sharedDesign(box);
    document.querySelectorAll(".brand img").forEach((img) => { img.src = `${logoUrl()}?${Date.now()}`; });
  };
  const swatch = (value) => {
    const button = h("button", { class: "swatch", title: value, "aria-label": value, onclick: () => { color.value = value; preview(); } });
    button.style.background = value;  // through the DOM: the CSP forbids style attributes
    return button;
  };
  [color, font, motion].forEach((input) => input.addEventListener("input", preview));
  fill(box,
    h("section", { class: "panel stack" }, h("h2", {}, t("web.ap_everyone")), h("p", { class: "muted" }, t("web.ap_everyone_hint")),
      h("div", { class: "field" }, h("label", { for: "apname" }, t("web.ap_name")), appName, h("small", { class: "muted" }, t("web.ap_name_hint"))),
      h("div", { class: "field" }, h("label", { for: "apcolor" }, t("web.ap_color")),
        h("div", { class: "row" }, color, SWATCHES.map(swatch))),
      h("div", { class: "field" }, h("label", { for: "apfont" }, t("web.ap_font")), font),
      h("label", { class: "check" }, motion, t("web.ap_animations")),
      h("button", { class: "primary", onclick: save }, icon("check"), t("web.save"))),
    h("section", { class: "panel stack" }, h("h2", {}, t("web.ap_logo")), h("p", { class: "muted" }, t("web.ap_logo_hint")),
      h("img", { class: "ap-logo", src: logoUrl(), alt: "" }),
      h("div", { class: "row" }, logoFile,
        h("button", { onclick: () => { if (logoFile.files.length) changeLogo(() => api("/api/appearance/logo", { method: "POST", body: logoFile.files[0] })); } }, icon("upload"), t("web.ap_logo_upload")),
        saved.logo ? h("button", { class: "ghost", onclick: () => changeLogo(() => api("/api/appearance/logo", { method: "DELETE" })) }, t("web.ap_logo_reset")) : null)));
}

function navPanel() {
  const list = h("ul", { class: "list nav-order" });
  const save = async () => {
    const order = [...list.children].map((row) => row.dataset.route);
    if (!await attempt(() => api("/api/me/nav", { method: "POST", body: { order } }))) return;
    state.session.nav = order;
    dispatchEvent(new HashChangeEvent("hashchange"));
  };
  const move = (row, step) => {
    const other = step < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!other) return;
    if (step < 0) other.before(row); else other.after(row);
    save();
  };
  const grab = (row, event) => {
    event.preventDefault();
    row.classList.add("dragging");
    const follow = (e) => {
      const other = [...list.children].find((item) => {
        const r = item.getBoundingClientRect();
        return item !== row && e.clientY > r.top && e.clientY < r.bottom;
      });
      if (!other) return;
      if (other.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING) other.before(row); else other.after(row);
    };
    const drop = () => {
      removeEventListener("pointermove", follow);
      removeEventListener("pointerup", drop);
      row.classList.remove("dragging");
      save();
    };
    addEventListener("pointermove", follow);
    addEventListener("pointerup", drop);
  };
  navItems().forEach(([route, name, label]) => {
    const row = h("li", { dataset: { route } },
      h("button", { class: "icon-only grip", title: t("web.ap_nav_drag"), onpointerdown: (e) => grab(row, e) }, icon("grip-vertical")),
      icon(name), h("span", { class: "grow" }, t(label)),
      h("button", { class: "icon-only", title: t("web.ap_nav_up"), onclick: () => move(row, -1) }, icon("arrow-up")),
      h("button", { class: "icon-only", title: t("web.ap_nav_down"), onclick: () => move(row, 1) }, icon("arrow-down")));
    list.append(row);
  });
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.ap_nav")), h("p", { class: "muted" }, t("web.ap_nav_hint")), list,
    h("button", { class: "ghost", onclick: async () => {
      if (await attempt(() => api("/api/me/nav", { method: "POST", body: { order: [] } }))) { state.session.nav = []; dispatchEvent(new HashChangeEvent("hashchange")); }
    } }, t("web.ap_nav_reset")));
}

export function render(box) {
  const shared = h("div", { class: "stack" });
  const device = h("section", { class: "panel stack" });
  const drawDevice = () => fill(device, h("h2", {}, t("web.ap_device")), h("p", { class: "muted" }, t("web.design_per_device")),
    h("h3", {}, t("web.theme")),
    h("div", { class: "choice" }, THEMES.map((theme) => h("button", { class: (localStorage.getItem("theme") || "dark") === theme ? "on" : "",
      onclick: () => { setDesign(theme); drawDevice(); } }, t(`web.theme_${theme}`)))),
    h("h3", {}, t("web.text_size")),
    h("div", { class: "choice" }, SIZES.map((size) => h("button", { class: (localStorage.getItem("text") || "normal") === size ? "on" : "",
      onclick: () => { setDesign(null, size); drawDevice(); } }, t(`web.text_${size}`)))));
  drawDevice();
  sharedDesign(shared);
  fill(box, device, navPanel(), shared);
}
