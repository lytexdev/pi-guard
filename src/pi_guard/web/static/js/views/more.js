// Mobile hub for the secondary pages, plus language. Appearance lives under Settings.
import { api, appShell, attempt, h, icon, lang, t, toast, fill } from "../api.js";
import { pinnedEntries, switchLanguage } from "../app.js";

function b64ToBytes(base64) {
  const padded = (base64 + "===".slice((base64.length + 3) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function pushPanel() {
  const info = await api("/api/push").catch(() => ({ enabled: false }));
  const panel = h("section", { class: "panel stack" }, h("h2", {}, t("web.this_app")));
  // The phone app gets alerts over its own connection; the desktop app cannot get push at all:
  // neither is a browser to offer push or installing the page in.
  const shell = appShell();
  if (shell !== "browser") { panel.append(h("p", {}, icon(shell === "android" ? "circle-check" : "info-circle"), " ", t(`web.push_in_${shell}`))); return panel; }
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

function languagePanel() {
  return h("section", { class: "panel stack more-choice" },
    h("h2", {}, icon("world"), " ", t("web.language")),
    h("p", { class: "muted" }, h("small", {}, t("web.language_hint"))),
    h("div", { class: "segmented" }, [["de", "Deutsch"], ["en", "English"]].map(([code, name]) =>
      h("button", { class: lang === code ? "on" : "", onclick: () => switchLanguage(code) },
        icon("world"), name))));
}

export async function render(main) {
  const entries = [["find", "map-pin", "web.nav_find"], ["setup/welcome", "settings", "web.setup_open"], ["addons", "cpu", "web.nav_addons"], ["monitor", "camera", "web.nav_monitor"],
    ["settings", "settings", "web.nav_settings"],
    ["settings/appearance", "palette", "web.set_appearance"],
    ["contacts", "users", "web.nav_contacts"],
    ["system", "server", "web.nav_system_page"], ["settings/logs", "list-details", "web.nav_logs"], ["system/updates", "refresh", "web.set_updates"],
    ["logout", "logout", "web.logout"]];
  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_more"))),
    h("section", { class: "panel" }, h("ul", { class: "list" }, entries.map(([route, i, label]) =>
      h("li", { class: "link-row" }, icon(i), h("a", { class: "grow", href: `#/${route}` }, t(label)), icon("chevron-right"))))),
    pinnedEntries().length ? h("section", { class: "panel" }, h("h2", {}, icon("pinned"), " ", t("web.nav_pinned")),
      h("ul", { class: "list" }, pinnedEntries().map(([route, i, label]) =>
        h("li", { class: "link-row" }, icon(i), h("a", { class: "grow", href: `#/${route}` }, t(label)), icon("chevron-right"))))) : null,
    languagePanel(),
    await pushPanel(),
    h("p", { class: "muted" }, h("small", {}, "pi-guard · made by ", h("a", { href: "https://links.lytex.de", target: "_blank", rel: "noopener" }, "lytex"))));
}
