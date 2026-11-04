// Mobile hub for the secondary pages, plus language and app settings.
import { api, attempt, h, icon, lang, t, toast, fill } from "../api.js";
import { switchLanguage } from "../app.js";
import { setDesign, SIZES, THEMES } from "../theme.js";

function b64ToBytes(base64) {
  const padded = (base64 + "===".slice((base64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function pushPanel() {
  const info = await api("/api/push").catch(() => ({ enabled: false }));
  const panel = h("section", { class: "panel stack" }, h("h2", {}, t("web.this_app")));
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (!standalone) panel.append(h("p", { class: "muted" }, t("web.install_hint")));
  if (!info.enabled) { panel.append(h("p", { class: "muted" }, t("web.push_disabled"))); return panel; }
  if (!window.isSecureContext || !("PushManager" in window) || !navigator.serviceWorker) {
    panel.append(h("p", { class: "notice" }, t("web.push_unsupported"))); return panel;
  }
  const registration = await navigator.serviceWorker.ready;
  let subscription = await registration.pushManager.getSubscription();
  const status = h("p", {}, "");
  const toggle = h("button", { class: "big" });
  const draw = () => {
    status.textContent = subscription ? t("web.push_on") : t("web.push_off");
    fill(toggle, icon(subscription ? "bell-ringing" : "bell"), subscription ? t("web.push_turn_off") : t("web.push_turn_on"));
  };
  toggle.addEventListener("click", async () => {
    if (subscription) {
      await attempt(() => api("/api/push/unsubscribe", { method: "POST", body: { endpoint: subscription.endpoint } }));
      await subscription.unsubscribe(); subscription = null; draw(); return;
    }
    if (await Notification.requestPermission() !== "granted") { toast(t("web.push_denied"), true); return; }
    subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(info.public_key) });
    await attempt(() => api("/api/push/subscribe", { method: "POST", body: { subscription: subscription.toJSON(), language: lang } }), t("web.saved"));
    draw();
  });
  draw();
  panel.append(status, h("div", { class: "row" }, toggle,
    h("button", { onclick: () => attempt(() => api("/api/push/test", { method: "POST" }), t("web.push_test_sent")) }, t("web.push_test"))),
    h("p", { class: "muted" }, h("small", {}, t("web.push_privacy"))));
  return panel;
}

function designPanel() {
  const panel = h("section", { class: "panel" });
  const draw = () => fill(panel, h("h2", {}, t("web.design")),
    h("div", { class: "segmented" }, THEMES.map((theme) => h("button", {
      class: (localStorage.getItem("theme") || "dark") === theme ? "on" : "",
      onclick: () => { setDesign(theme); draw(); } }, t(`web.theme_${theme}`)))),
    h("p", {}),
    h("div", { class: "segmented" }, SIZES.map((size) => h("button", {
      class: (localStorage.getItem("text") || "normal") === size ? "on" : "",
      onclick: () => { setDesign(null, size); draw(); } }, t(`web.text_${size}`)))));
  draw();
  return panel;
}

export async function render(main) {
  const entries = [["setup/welcome", "settings", "web.setup_open"], ["network", "network", "web.nav_network"], ["addons", "cpu", "web.nav_addons"], ["devices", "camera", "web.nav_devices"],
    ["settings", "settings", "web.nav_settings"], ["access", "shield-lock", "web.nav_access"],
    ["logs", "list-details", "web.nav_logs"], ["logout", "logout", "web.logout"]];
  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_more"))),
    h("section", { class: "panel" }, h("ul", { class: "list" }, entries.map(([route, i, label]) =>
      h("li", {}, icon(i), h("a", { class: "grow", href: `#/${route}` }, t(label)), icon("chevron-right"))))),
    h("section", { class: "panel" }, h("h2", {}, t("web.language")),
      h("div", { class: "segmented" }, [["de", "Deutsch"], ["en", "English"]].map(([code, name]) =>
        h("button", { class: lang === code ? "on" : "", onclick: () => switchLanguage(code) }, name)))),
    designPanel(),
    await pushPanel(),
    h("p", { class: "muted" }, h("small", {}, "pi-guard · made by lytex")));
}
