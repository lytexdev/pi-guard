// Shell, navigation and hash router. Views live in ./views and render into <main>.
import { accessibilityButton, applyAccessibility } from "./accessibility.js";
import { installTooltips } from "./tooltip.js";
import { assistantButton, installLightbox, scrollTopButton, skeleton } from "./widgets.js";
import { api, h, icon, loadIcons, loadTexts, lang, setCsrf, t, fill } from "./api.js";
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
  help: () => import("./views/help.js"),
};
const PRIMARY = [
  ["overview", "home", "web.nav_overview"],
  ["live", "video", "web.nav_live"],
  ["events", "bell", "web.nav_events"],
  ["people", "users", "web.nav_people"],
];
const SECONDARY = [
  ["network", "network", "web.nav_network"],
  ["addons", "cpu", "web.nav_addons"],
  ["settings", "settings", "web.nav_settings"],
  ["logs", "list-details", "web.nav_logs"],
  ["help", "bulb", "web.nav_help"],
];

const VIEWER_ROUTES = ["overview", "live", "monitor", "events", "account", "help"];

applyAccessibility();
installLightbox();
installTooltips();

export const state = { session: null, overview: null };
let cleanup = null;
let started = false;  // start() has just asked for the session
let showing = 0;  // the newest page change; older ones still running must step aside
let asked = 0;  // the newest session request; an older answer must not replace it

function link(route, iconName, label, current) {
  return h("a", { href: `#/${route}`, title: t(`web.tip_nav_${route}`), class: current === route ? "active" : "" },
    icon(iconName), h("span", {}, t(label)));
}

// The main entries in this person's own order; anything new goes to the end.
export function navItems() {
  const viewer = state.session?.role === "viewer";
  const items = viewer ? [...PRIMARY.filter(([r]) => VIEWER_ROUTES.includes(r)), ["account", "user", "web.nav_account"]] : [...PRIMARY, ...SECONDARY];
  const order = state.session?.nav || [];
  const rank = (route) => (order.includes(route) ? order.indexOf(route) : order.length);
  return items.map((item, index) => [item, index]).sort((a, b) => rank(a[0][0]) - rank(b[0][0]) || a[1] - b[1]).map(([item]) => item);
}

function shell(route) {
  const main = h("main", { id: "main", tabindex: "-1" });
  // For keyboards and screen readers: past the navigation in one step. A button, because a
  // link to #main would change the page here.
  const skip = h("button", { class: "skip-link", type: "button", onclick: () => main.focus() }, t("web.skip_to_content"));
  const viewer = state.session?.role === "viewer";
  const items = navItems();
  const custom = (state.session?.nav || []).length > 0;
  const tabs = route === "monitor" ? "live" : route;  // the camera room is a view of Live
  const entry = ([r, i, l]) => link(r, i, l, tabs);
  const nav = h("nav", { class: "nav", "aria-label": "pi-guard" },
    h("div", { class: "brand" }, h("img", { src: logoUrl(), alt: "" }), "pi-guard"),
    custom || viewer ? items.map(entry) : [
      items.filter(([r]) => PRIMARY.some(([p]) => p === r)).map(entry),
      h("h3", {}, t("web.nav_system")),
      items.filter(([r]) => SECONDARY.some(([p]) => p === r)).map(entry)],
    (state.session?.pinned || []).length ? h("h3", {}, t("web.nav_pinned")) : null,
    (state.session?.pinned || []).map((id) => link(`addons/${id}`, "cpu", `addons.${id}`, route)),
    h("div", { class: "spacer" }),
    state.session?.voice_listening ? h("a", { href: "#/settings/audio", class: "voice-badge", title: t("web.vo_listening_tip") }, icon("microphone"), h("span", {}, t("web.vo_listening"))) : null,
    h("a", { href: "#/logout" }, icon("logout"), h("span", {}, t("web.logout"))),
    h("div", { class: "credit" }, "pi-guard · made by ", h("a", { href: "https://links.lytex.de", target: "_blank", rel: "noopener" }, "lytex")));
  const tabbar = h("nav", { class: "tabbar", "aria-label": "pi-guard" },
    viewer ? items.map(([r, i, l]) => link(r, i, l, tabs)) : [
      ...items.slice(0, 4).map(([r, i, l]) => link(r, i, l, tabs)),
      link("more", "menu-2", "web.nav_more", items.slice(0, 4).some(([r]) => r === tabs) ? tabs : "more")]);
  return h("div", { class: "shell" }, skip, nav, main, tabbar, scrollTopButton(), accessibilityButton(),
    state.session?.assistant ? assistantButton() : null);
}

async function refreshSession() {
  const mine = ++asked;
  const session = await api("/api/session");
  if (mine === asked) {
    state.session = session;
    setCsrf(session.csrf);
    applyAppearance(session.appearance);
  }
  return session;
}

// Waits for the server only where its answer decides what to show. Otherwise the page opens
// at once and the session is checked alongside.
async function currentSession(name) {
  if (!started) { started = true; return state.session; }
  const known = state.session;
  if (!known?.authenticated || known.setup_required || !known.setup_done || name === "login" || name === "setup") {
    return refreshSession();
  }
  refreshSession().then(() => {
    if (state.session && !state.session.authenticated) location.hash = "#/login";
  }).catch(() => {});
  return known;
}

async function route() {
  const turn = ++showing;
  try { await show(turn); }
  catch (error) {
    if (turn !== showing) return;
    fill(document.getElementById("app"), h("div", { class: "login" }, h("div", { class: "panel" },
      h("p", { class: "notice danger" }, error.status === 429 ? t("web.rate_limited") : t("web.offline")),
      h("button", { onclick: route }, t("web.retry")))));
  }
}

// A view that finished after the next page opened must stop at once, or its timers run on.
function keep(turn, stop) {
  if (turn === showing) cleanup = stop;
  else stop?.();
}

async function show(turn) {
  if (cleanup) { cleanup(); cleanup = null; }
  let [name, ...params] = (location.hash.replace(/^#\/?/, "") || "overview").split("/");
  if (["devices", "notifications", "access"].includes(name)) { params = [name]; name = "settings"; }
  if (name === "addons" && params[0] === "analytics") { name = "network"; params = []; }
  const root = document.getElementById("app");
  if (name === "logout") {
    await api("/api/logout", { method: "POST" }).catch(() => {});
    asked += 1;
    state.session = null;
    started = true;
    location.hash = "#/login";
    return;
  }
  const session = await currentSession(name);
  if (turn !== showing) return;
  if (!session.authenticated && name !== "login") { location.hash = "#/login"; return; }
  if (session.authenticated && name === "login") { location.hash = "#/overview"; return; }
  if (session.role === "viewer" && name !== "login" && !VIEWER_ROUTES.includes(name)) { location.hash = "#/overview"; return; }
  if (session.setup_required && name !== "setup") { location.hash = "#/setup/welcome"; return; }
  if (session.authenticated && session.role !== "viewer" && !session.setup_done && name === "overview"
      && !sessionStorage.getItem("pg.setup_later")) { location.hash = "#/setup/welcome"; return; }
  const loader = VIEWS[name] || VIEWS.overview;
  if (name === "login" || name === "setup") {
    const view = await loader();
    if (turn !== showing) return;
    const page = h("div", { class: "page" });  // left behind when the next page fills the root
    root.replaceChildren(page);
    keep(turn, await view.render(page, params) || null);
    return;
  }
  fill(root, shell(name in VIEWS ? name : "overview"));
  const main = document.getElementById("main");
  const waiting = skeleton();
  main.append(waiting);
  try {
    const view = await loader();
    if (turn !== showing) return;
    keep(turn, await view.render(main, params) || null);
  } catch (error) {
    if (turn !== showing) return;
    fill(main, h("section", { class: "panel" },
      h("p", { class: "notice danger" }, error.status === 429 ? t("web.rate_limited") : (error.message || t("web.error"))),
      h("button", { onclick: route }, t("web.retry"))));
  } finally {
    waiting.remove();  // some views add to <main> instead of replacing it
  }
}

async function start() {
  applyDesign();
  loadIcons().catch(() => {});
  const saved = localStorage.getItem("lang");
  const [session] = await Promise.all([refreshSession(), saved && loadTexts(saved)]);
  if (!saved) await loadTexts(session.language || lang);
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
