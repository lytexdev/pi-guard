// Phones with the app: finding them, where they are, and the app itself for updates.
import { askDialog, api, attempt, confirmDialog, fill, h, icon, t, toast, when, whileShown } from "../api.js";

const ZOOM = 16;
const TILE = "/api/mobile/map"; // through the Pi, see map_tile

function tileOf(lat, lon) {
  const n = 2 ** ZOOM;
  const rad = (lat * Math.PI) / 180;
  return [((lon + 180) / 360) * n, ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n];
}

function mapOf(info) {
  // Tiles are only fetched when someone asks for the map.
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

// A fresh place from the phone, then a link to it in the chosen map. Waits up to 30 seconds.
async function placeLink(device, place) {
  const before = device.info.located_at || "";
  if (!await attempt(() => api(`/api/mobile/devices/${device.id}/locate`, { method: "POST" }))) return;
  fill(place, h("p", { class: "muted" }, t("web.mob_locating")));
  for (let i = 0; i < 15; i++) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    if (!place.isConnected) return;
    const now = (await api("/api/mobile/devices").catch(() => ({ devices: [] }))).devices.find((d) => d.id === device.id);
    if (now?.map && (now.info.located_at || "") > before) {
      const accuracy = now.info.accuracy != null ? t("web.mob_accuracy", { meters: Math.round(now.info.accuracy) }) : "";
      const link = h("input", { class: "mono", readonly: true, value: now.map, "aria-label": t("web.mob_place_link") });
      fill(place, h("div", { class: "stack tight" },
        h("small", {}, [when(now.info.located_at), accuracy].filter(Boolean).join(" · ")), link,
        h("div", { class: "row" },
          h("a", { class: "button primary", href: now.map, target: "_blank", rel: "noopener" }, icon("world"), t("web.mob_open")),
          h("button", { onclick: async () => { try { await navigator.clipboard.writeText(now.map); toast(t("web.copied")); } catch { link.select(); } } }, icon("copy"), t("web.copy")),
          navigator.share ? h("button", { onclick: () => navigator.share({ title: device.name, url: now.map }).catch(() => {}) }, icon("upload"), t("web.share")) : null)));
      return;
    }
  }
  fill(place, h("p", { class: "notice" }, t("web.mob_no_place")));
}

// The places of a lost phone or of an emergency, newest first, refreshed while shown.
async function trackOf(device, place) {
  const box = h("div", { class: "stack tight", "aria-live": "polite" });
  fill(place, box);
  const draw = async () => {
    const data = await api(`/api/mobile/devices/${device.id}/track`).catch(() => null);
    if (!box.isConnected || !data) return;
    const points = data.track;
    fill(box, points.length ? [mapOf(points[0]),
      h("ol", { class: "list" }, points.slice(0, 30).map((p) => h("li", {},
        h("a", { href: p.map, target: "_blank", rel: "noopener" }, when(p.time)),
        p.accuracy != null ? h("small", { class: "muted" }, " · ", t("web.mob_accuracy", { meters: Math.round(p.accuracy) })) : null)))]
      : h("p", { class: "muted" }, t("web.mob_track_empty")));
    if (data.state) whileShown(box, draw, 30000);
  };
  await draw();
}

async function reportLost(device, draw) {
  const message = await askDialog(t("web.mob_lost_message"), { confirmLabel: t("web.next") });
  if (message === null) return;
  const password = await askDialog(t("web.confirm_password"), { type: "password" });
  if (!password) return;
  if (await attempt(() => api(`/api/mobile/devices/${device.id}/lost`, { method: "POST", body: { message, password } }), t("web.mob_lost_done"))) draw();
}

function card(device, draw) {
  const info = device.info;
  const place = h("div", { class: "stack tight phone-place" });
  const facts = [info.model, info.os, info.battery != null ? `${info.battery} %${info.charging ? " ⚡" : ""}` : null, info.network, info.ssid, info.ip].filter(Boolean).join(" · ");
  return h("li", { class: "phone-card" }, icon("device-mobile"),
    h("div", { class: "grow" }, h("div", {}, device.name, " ", h("span", { class: `badge ${device.online ? "ok" : ""}` }, t(device.online ? "web.mob_online" : "web.mob_offline")),
      device.tracking ? [" ", h("span", { class: "badge danger" }, t(device.tracking.kind === "lost" ? "web.mob_is_lost" : "web.mob_is_emergency"))] : null),
      h("small", { class: "block" }, device.last_seen ? t("web.last_used", { time: when(device.last_seen) }) : t("web.never_used")), facts ? h("small", { class: "block" }, facts) : null),
    h("div", { class: "phone-actions" },
    h("button", { title: t("web.mob_find_tip"), disabled: !device.online, onclick: () => attempt(() => api(`/api/mobile/devices/${device.id}/find`, { method: "POST" }), t("web.mob_ringing")) }, icon("bell-ringing"), t("web.mob_find")),
    h("button", { class: "ghost", disabled: !device.online, onclick: () => attempt(() => api(`/api/mobile/devices/${device.id}/stop`, { method: "POST" })) }, t("web.mob_stop")),
    h("button", { title: t("web.mob_place_tip"), disabled: !device.online, onclick: () => placeLink(device, place) }, icon("map-pin"), t("web.mob_place_link")),
    info.latitude != null ? h("button", { class: "icon-only", title: t("web.mob_map_tip"), onclick: () => fill(place, mapOf(info), h("a", { href: device.map, target: "_blank", rel: "noopener" }, t("web.mob_open")), info.located_at ? h("small", { class: "muted" }, when(info.located_at)) : null) }, icon("world")) : null,
    device.tracking
      ? [h("button", { onclick: () => trackOf(device, place) }, icon("walk"), t("web.mob_track")),
        h("button", { class: "primary", onclick: async () => {
          if (await confirmDialog(t("web.mob_found_confirm", { name: device.name }), t("web.mob_found")) && await attempt(() => api(`/api/mobile/devices/${device.id}/found`, { method: "POST" }), t("web.mob_found_done"))) draw();
        } }, icon("check"), t("web.mob_found"))]
      : h("button", { title: t("web.mob_lost_tip"), onclick: () => reportLost(device, draw) }, icon("alert-triangle"), t("web.mob_lost")),
    h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
      if (await confirmDialog(t("web.mob_remove", { name: device.name }), t("web.delete"), true) && await attempt(() => api(`/api/mobile/devices/${device.id}`, { method: "DELETE" }), t("web.deleted"))) draw();
    } }, icon("trash"))), place);
}

// Which versions this Pi runs and hands out, and links that let someone download the apps.
async function appsPanels() {
  const info = await api("/api/apps");
  const row = (iconName, label, value) => h("li", {}, icon(iconName), h("span", { class: "grow" }, label), h("strong", {}, value || t("web.apps_none")));
  const desktop = Object.entries(info.desktop || {});
  const result = h("div", { class: "stack" });
  const share = async (path) => {
    const url = new URL(path, location.origin).href;
    const text = t("web.apps_invite_text", { url, days: info.invite_days });
    try {
      if (navigator.share) { await navigator.share({ title: "pi-guard", text, url }); return; }
    } catch { /* closed or not allowed: show it instead */ }
    fill(result, h("p", { class: "notice" }, t("web.apps_invite_ready", { days: info.invite_days })),
      h("div", { class: "row" }, h("input", { class: "grow mono", readonly: true, value: url, "aria-label": t("web.apps_invite") }),
        h("button", { onclick: async () => { try { await navigator.clipboard.writeText(text); toast(t("web.copied")); } catch { toast(t("web.error"), true); } } }, icon("copy"), t("web.copy"))));
  };
  return [
    h("section", { class: "panel stack" }, h("h2", {}, icon("refresh"), " ", t("web.apps_versions")),
      h("ul", { class: "list compact" },
        row("brand-raspberrypi", t("web.apps_pi"), info.pi),
        row("brand-android", t("web.apps_android"), info.android),
        desktop.length ? desktop.map(([platform, version]) => row(platform.startsWith("win") ? "brand-windows" : "terminal-2", t("web.apps_desktop", { platform: platform.startsWith("win") ? "Windows" : "Linux" }), version))
          : row("device-laptop", t("web.apps_desktop", { platform: "" }), null)),
      h("p", { class: "muted" }, h("small", {}, t("web.apps_versions_hint"))),
      h("a", { class: "button", href: "#/settings/updates" }, icon("refresh"), t("web.apps_to_updates"))),
    h("section", { class: "panel stack" }, h("h2", {}, icon("share"), " ", t("web.apps_invite")), h("p", { class: "muted" }, t("web.apps_invite_hint", { days: info.invite_days })),
      h("div", { class: "row" },
        h("button", { class: "primary", disabled: !info.android && !desktop.length, onclick: async () => {
          const made = await attempt(() => api("/api/apps/invite", { method: "POST", body: {} }));
          if (made) share(made.path);
        } }, icon("share"), t("web.apps_invite_make")),
        info.invites ? h("button", { class: "ghost", onclick: async () => {
          if (await confirmDialog(t("web.apps_invite_clear_confirm", { count: info.invites }), t("web.apps_invite_clear"), true)
            && await attempt(() => api("/api/apps/invite", { method: "DELETE" }), t("web.saved"))) fill(result);
        } }, t("web.apps_invite_clear")) : null),
      result),
  ];
}

export async function render(main, { embedded = false } = {}) {
  const top = embedded ? await appsPanels() : null;
  const draw = async () => {
    const data = await api("/api/mobile/devices");
    const file = h("input", { type: "file", accept: ".apk", "aria-label": t("web.mob_apk") });
    const code = h("input", { type: "number", min: 1, placeholder: t("web.mob_version_code"), "aria-label": t("web.mob_version_code") });
    const version = h("input", { placeholder: t("web.mob_version_name"), "aria-label": t("web.mob_version_name") });
    fill(main, embedded ? top : h("div", { class: "topbar" }, h("h1", {}, t("addons.mobile")), h("a", { class: "button", href: "#/settings/apps" }, t("web.back"))),
      h("section", { class: "panel stack" }, h("h2", {}, t("web.mob_devices")), h("p", { class: "muted" }, t("web.mob_hint")),
        h("ul", { class: "list" }, data.devices.length ? data.devices.map((d) => card(d, draw)) : h("li", { class: "empty" }, t("web.nothing")))),
      h("section", { class: "panel stack" }, h("h2", {}, t("web.mob_app")),
        data.app ? [h("p", {}, t("web.mob_current", { version: data.app.version_name, size: Math.round(data.app.size / 1e6) })),
          h("a", { class: "button", href: "/api/mobile/app/apk", download: "PiGuard.apk" }, icon("download"), t("web.mob_download"))] : h("p", { class: "muted" }, t("web.mob_no_app")),
        h("h3", {}, t("web.mob_upload")), h("p", { class: "muted" }, h("small", {}, t("web.mob_upload_hint"))),
        h("div", { class: "row" }, file, code, version, h("button", { onclick: async () => {
          if (!file.files.length || !code.value || !version.value) { toast(t("web.mob_upload_missing"), true); return; }
          const password = await askDialog(t("web.confirm_password"), { type: "password" });
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
