// Phones with the app: finding them, where they are, and the app itself for updates.
import { api, attempt, confirmDialog, fill, h, icon, t, toast, when } from "../api.js";

const ZOOM = 16;
const TILE = "https://tile.openstreetmap.org";

function tileOf(lat, lon) {
  const n = 2 ** ZOOM;
  const rad = (lat * Math.PI) / 180;
  return [((lon + 180) / 360) * n, ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n];
}

function mapOf(info) {
  // Tiles load from OpenStreetMap only when someone asks for the map.
  const [x, y] = tileOf(info.latitude, info.longitude);
  const box = h("div", { class: "phone-map", role: "img", "aria-label": t("web.mob_map") });
  for (let row = -1; row <= 1; row += 1) {
    for (let column = -1; column <= 1; column += 1) {
      const tile = h("img", { src: `${TILE}/${ZOOM}/${Math.floor(x) + column}/${Math.floor(y) + row}.png`, alt: "" });
      tile.style.left = `${(column + 1) * 33.333}%`;
      tile.style.top = `${(row + 1) * 33.333}%`;
      box.append(tile);
    }
  }
  const pin = h("div", { class: "pin" });
  pin.style.left = `${(1 + (x % 1)) * 33.333}%`;
  pin.style.top = `${(1 + (y % 1)) * 33.333}%`;
  box.append(pin, h("small", {}, "© OpenStreetMap"));
  return box;
}

function card(device, draw) {
  const info = device.info;
  const place = h("div", { class: "stack tight" });
  const facts = [info.model, info.os, info.battery != null ? `${info.battery} %${info.charging ? " ⚡" : ""}` : null, info.network, info.ssid, info.ip].filter(Boolean).join(" · ");
  return h("li", { class: "phone-card" }, icon("device-mobile"),
    h("div", { class: "grow" }, h("div", {}, device.name, " ", h("span", { class: `badge ${device.online ? "ok" : ""}` }, t(device.online ? "web.mob_online" : "web.mob_offline"))),
      h("small", { class: "block" }, device.last_seen ? t("web.last_used", { time: when(device.last_seen) }) : t("web.never_used")), facts ? h("small", { class: "block" }, facts) : null),
    h("button", { title: t("web.mob_find_tip"), disabled: !device.online, onclick: () => attempt(() => api(`/api/mobile/devices/${device.id}/find`, { method: "POST" }), t("web.mob_ringing")) }, icon("bell-ringing"), t("web.mob_find")),
    h("button", { class: "ghost", disabled: !device.online, onclick: () => attempt(() => api(`/api/mobile/devices/${device.id}/stop`, { method: "POST" })) }, t("web.mob_stop")),
    info.latitude != null ? h("button", { class: "icon-only", title: t("web.mob_map_tip"), onclick: () => fill(place, mapOf(info), h("a", { href: device.map, target: "_blank", rel: "noopener" }, t("web.mob_open_map")), info.located_at ? h("small", { class: "muted" }, when(info.located_at)) : null) }, icon("world")) : null,
    h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
      if (await confirmDialog(t("web.mob_remove", { name: device.name }), t("web.delete"), true) && await attempt(() => api(`/api/mobile/devices/${device.id}`, { method: "DELETE" }), t("web.deleted"))) draw();
    } }, icon("trash")), place);
}

export async function render(main) {
  const draw = async () => {
    const data = await api("/api/mobile/devices");
    const file = h("input", { type: "file", accept: ".apk", "aria-label": t("web.mob_apk") });
    const code = h("input", { type: "number", min: 1, placeholder: t("web.mob_version_code"), "aria-label": t("web.mob_version_code") });
    const version = h("input", { placeholder: t("web.mob_version_name"), "aria-label": t("web.mob_version_name") });
    fill(main, h("div", { class: "topbar" }, h("h1", {}, t("addons.mobile")), h("a", { class: "button", href: "#/addons" }, t("web.back"))),
      h("section", { class: "panel stack" }, h("h2", {}, t("web.mob_devices")), h("p", { class: "muted" }, t("web.mob_hint")),
        h("ul", { class: "list" }, data.devices.length ? data.devices.map((d) => card(d, draw)) : h("li", { class: "empty" }, t("web.nothing")))),
      h("section", { class: "panel stack" }, h("h2", {}, t("web.mob_app")),
        data.app ? [h("p", {}, t("web.mob_current", { version: data.app.version_name, size: Math.round(data.app.size / 1e6) })),
          h("a", { class: "button", href: "/api/mobile/app/apk", download: "PiGuard.apk" }, icon("download"), t("web.mob_download"))] : h("p", { class: "muted" }, t("web.mob_no_app")),
        h("h3", {}, t("web.mob_upload")), h("p", { class: "muted" }, h("small", {}, t("web.mob_upload_hint"))),
        h("div", { class: "row" }, file, code, version, h("button", { onclick: async () => {
          if (!file.files.length || !code.value || !version.value) { toast(t("web.mob_upload_missing"), true); return; }
          const password = prompt(t("web.confirm_password"));
          if (!password) return;
          const form = new FormData();
          form.append("password", password);
          form.append("version_code", code.value);
          form.append("version_name", version.value);
          form.append("file", file.files[0], file.files[0].name);
          if (await attempt(() => api("/api/mobile/app", { method: "POST", body: form }), t("web.saved"))) draw();
        } }, icon("upload"), t("web.upload")))));
  };
  await draw();
}
