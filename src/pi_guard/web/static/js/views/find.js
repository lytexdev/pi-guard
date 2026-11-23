// Find devices: every phone with the app in one place, to ring it, let it vibrate, see where it
// is, or report it lost. The same cards as under System › Updates, here first and large.
import { api, fill, h, icon, t, whileShown } from "../api.js";
import { card } from "./mobile.js";

export async function render(main) {
  const list = h("ul", { class: "list find-list" });
  const lost = h("div");
  const draw = async () => {
    const data = await api("/api/mobile/devices").catch((error) => (error.message === "web.addon_not_running" ? null : Promise.reject(error)));
    if (!list.isConnected) return;
    if (!data) { fill(list, h("li", { class: "empty" }, t("web.mob_off"))); return; }
    const devices = [...data.devices].sort((a, b) => Number(Boolean(b.tracking)) - Number(Boolean(a.tracking)) || Number(b.online) - Number(a.online));
    const missing = devices.filter((d) => d.tracking);
    fill(lost, missing.length ? h("p", { class: "notice danger" }, icon("alert-triangle"), " ",
      t("web.find_missing", { names: missing.map((d) => d.name).join(", ") })) : null);
    fill(list, devices.length ? devices.map((d) => card(d, draw))
      : h("li", { class: "empty stack" }, h("p", {}, t("web.find_none")), h("a", { class: "button primary", href: "#/system/updates" }, icon("share"), t("web.apps_invite"))));
    if (missing.length) whileShown(list, draw, 30000);
  };
  fill(main, h("div", { class: "topbar" }, h("h1", {}, icon("map-pin"), " ", t("web.nav_find"))),
    h("p", { class: "muted" }, t("web.find_intro")),
    lost,
    h("section", { class: "panel stack" }, list),
    h("section", { class: "panel stack find-help" }, h("h2", {}, icon("help-circle"), " ", t("web.find_how_title")),
      h("ul", { class: "list compact" },
        [["bell-ringing", "web.find_how_ring"], ["device-mobile-vibration", "web.find_how_vibrate"], ["map-pin", "web.find_how_place"],
          ["alert-triangle", "web.find_how_lost"]].map(([ic, key]) => h("li", {}, icon(ic), h("span", { class: "grow" }, t(key)))))));
  await draw();
  return null;
}
