// Shell, navigation and hash router. Views live in ./views and render into <main>.
import { accessibilityButton, applyAccessibility } from "./accessibility.js";
import { assistantButton, installLightbox, scrollTopButton } from "./widgets.js";
import { api, h, icon, loadTexts, lang, setCsrf, t, fill } from "./api.js";
import { applyAppearance, applyDesign, logoUrl } from "./theme.js";

const VIEWS = {
  overview: () => import("./views/overview.js"),
  live: () => import("./views/live.js"),
  events: () => import("./views/events.js"),
  people: () => import("./views/people.js"),
  more: () => import("./views/more.js"),
  network: () => import("./views/network.js"),
  addons: () => import("./views/addons.js"),
  settings: () => import("./views/settings-hub.js"),
  monitor: () => import("./views/monitor.js"),
  account: () => import("./views/account.js"),
  logs: () => import("./views/logs.js"),
  login: () => import("./views/login.js"),
  setup: () => import("./views/setup.js"),
};
const PRIMARY = [
  ["overview", "home", "web.nav_overview"],
  ["live", "video", "web.nav_live"],
  ["monitor", "camera", "web.nav_monitor"],
  ["events", "bell", "web.nav_events"],
  ["people", "users", "web.nav_people"],
];
const SECONDARY = [
  ["network", "network", "web.nav_network"],
  ["addons", "cpu", "web.nav_addons"],
  ["settings", "settings", "web.nav_settings"],
  ["logs", "list-details", "web.nav_logs"],
];

const VIEWER_ROUTES = ["overview", "live", "monitor", "events", "account"];

applyAccessibility();
installLightbox();

export const state = { session: null, overview: null };
let cleanup = null;

function link(route, iconName, label, current) {
  return h("a", { href: `#/${route}`, class: current === route ? "active" : "" },
    icon(iconName), h("span", {}, t(label)));
}

function shell(route) {
  const main = h("main", { id: "main" });
  const viewer = state.session?.role === "viewer";
  const primary = viewer ? PRIMARY.filter(([r]) => VIEWER_ROUTES.includes(r)) : PRIMARY;
  const mine = ["account", "user", "web.nav_account"];
  const nav = h("nav", { class: "nav", "aria-label": "pi-guard" },
    h("div", { class: "brand" }, h("img", { src: logoUrl(), alt: "" }), "pi-guard"),
    primary.map(([r, i, l]) => link(r, i, l, route)),
    h("h3", {}, t("web.nav_system")),
    (viewer ? [mine] : SECONDARY).map(([r, i, l]) => link(r, i, l, route)),
    (state.session?.pinned || []).length ? h("h3", {}, t("web.nav_pinned")) : null,
    (state.session?.pinned || []).map((id) => link(`addons/${id}`, "cpu", `addons.${id}`, route)),
    h("div", { class: "spacer" }),
    h("a", { href: "#/logout" }, icon("logout"), h("span", {}, t("web.logout"))),
    h("div", { class: "credit" }, "pi-guard · made by ", h("a", { href: "https://links.lytex.de", target: "_blank", rel: "noopener" }, "lytex")));
  const tabbar = h("nav", { class: "tabbar", "aria-label": "pi-guard" },
    viewer ? [...primary, mine].map(([r, i, l]) => link(r, i, l, route)) : [
      ...primary.filter(([r]) => r !== "monitor").map(([r, i, l]) => link(r, i, l, route)),
      link("more", "menu-2", "web.nav_more",
        SECONDARY.some(([r]) => r === route) || route === "more" ? "more" : route)]);
  return h("div", { class: "shell" }, nav, main, tabbar, scrollTopButton(), accessibilityButton(),
    state.session?.assistant ? assistantButton() : null);
}

async function refreshSession() {
  state.session = await api("/api/session");
  setCsrf(state.session.csrf);
  applyAppearance(state.session.appearance);
  return state.session;
}

async function route() {
  try { await show(); }
  catch (error) {
    fill(document.getElementById("app"), h("div", { class: "login" }, h("div", { class: "panel" },
      h("p", { class: "notice danger" }, error.status === 429 ? t("web.rate_limited") : t("web.offline")),
      h("button", { onclick: route }, t("web.retry")))));
  }
}

async function show() {
  if (cleanup) { cleanup(); cleanup = null; }
  let [name, ...params] = (location.hash.replace(/^#\/?/, "") || "overview").split("/");
  if (["devices", "notifications", "access"].includes(name)) { params = [name]; name = "settings"; }
  if (name === "addons" && params[0] === "analytics") { name = "network"; params = []; }
  const root = document.getElementById("app");
  if (name === "logout") {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    location.hash = "#/login";
    return;
  }
  const session = await refreshSession();
  if (!session.authenticated && name !== "login") { location.hash = "#/login"; return; }
  if (session.authenticated && name === "login") { location.hash = "#/overview"; return; }
  if (session.role === "viewer" && name !== "login" && !VIEWER_ROUTES.includes(name)) { location.hash = "#/overview"; return; }
  if (session.setup_required && name !== "setup") { location.hash = "#/setup/welcome"; return; }
  if (session.authenticated && session.role !== "viewer" && !session.setup_done && name === "overview"
      && !sessionStorage.getItem("pg.setup_later")) { location.hash = "#/setup/welcome"; return; }
  const loader = VIEWS[name] || VIEWS.overview;
  const view = await loader();
  if (name === "login" || name === "setup") {
    root.replaceChildren();
    cleanup = await view.render(root, params) || null;
    return;
  }
  fill(root, shell(name in VIEWS ? name : "overview"));
  const main = document.getElementById("main");
  try {
    cleanup = await view.render(main, params) || null;
  } catch (error) {
    fill(main, h("section", { class: "panel" },
      h("p", { class: "notice danger" }, error.status === 429 ? t("web.rate_limited") : (error.message || t("web.error"))),
      h("button", { onclick: route }, t("web.retry"))));
  }
}

async function start() {
  applyDesign();
  const session = await api("/api/session");
  applyAppearance(session.appearance);
  await loadTexts(localStorage.getItem("lang") || session.language || lang);
  window.addEventListener("hashchange", route);
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  }
  await route();
}

export async function switchLanguage(language) {
  await loadTexts(language);
  await route();
}

start();
