// Mobile hub for the secondary pages, plus language and app settings.
import { h, icon, lang, t, fill } from "../api.js";
import { switchLanguage } from "../app.js";

export async function render(main) {
  const entries = [["network", "network", "web.nav_network"], ["devices", "camera", "web.nav_devices"],
    ["settings", "settings", "web.nav_settings"], ["access", "shield-lock", "web.nav_access"],
    ["logs", "list-details", "web.nav_logs"], ["logout", "logout", "web.logout"]];
  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_more"))),
    h("section", { class: "panel" }, h("ul", { class: "list" }, entries.map(([route, i, label]) =>
      h("li", {}, icon(i), h("a", { class: "grow", href: `#/${route}` }, t(label)), icon("chevron-right"))))),
    h("section", { class: "panel" }, h("h2", {}, t("web.language")),
      h("div", { class: "segmented" }, [["de", "Deutsch"], ["en", "English"]].map(([code, name]) =>
        h("button", { class: lang === code ? "on" : "", onclick: () => switchLanguage(code) }, name)))),
    h("p", { class: "muted" }, h("small", {}, "pi-guard · made by lytex")));
}
