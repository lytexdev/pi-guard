// Pi-hole statistics and blocking, WireGuard peers, devices in the LAN.
import { api, attempt, fill, h, icon, t, when } from "../api.js";
import { meter } from "./overview.js";
import { restartPrompt } from "./settings.js";

function piholePanel(p, redraw) {
  if (!p) return h("section", { class: "panel" }, h("h2", {}, "Pi-hole"), h("p", { class: "muted" }, t("web.pihole_off")));
  if (p.error) return h("section", { class: "panel" }, h("h2", {}, "Pi-hole"), h("p", { class: "notice danger" }, p.error));
  const toggle = async (enabled, minutes) => {
    await attempt(() => api("/api/network/pihole/blocking", { method: "POST", body: { enabled, minutes } }), t("web.saved"));
    redraw();
  };
  return h("section", { class: "panel" },
    h("div", { class: "row between" }, h("h2", {}, "Pi-hole"),
      h("span", { class: `badge ${p.blocking ? "ok" : "danger"}` }, p.blocking ? t("web.blocking_on") : t("web.blocking_off"))),
    h("div", { class: "stat" }, `${p.percent_blocked} %`),
    h("p", { class: "muted" }, t("web.pihole_blocked", { blocked: p.blocked.toLocaleString(), total: p.queries.toLocaleString() })),
    meter(p.percent_blocked, 101, 101),
    h("dl", { class: "kv" },
      h("dt", {}, t("web.domains_on_list")), h("dd", {}, p.domains_blocked.toLocaleString()),
      h("dt", {}, t("web.active_clients")), h("dd", {}, p.clients)),
    h("div", { class: "row" }, p.blocking
      ? [h("button", { onclick: () => toggle(false, 5) }, t("web.pause_5")), h("button", { onclick: () => toggle(false, 60) }, t("web.pause_60"))]
      : h("button", { class: "primary", onclick: () => toggle(true) }, icon("shield-check"), t("web.blocking_resume"))),
    p.top_blocked.length ? [h("h3", {}, t("web.top_blocked")), h("ul", { class: "list" }, p.top_blocked.map((d) =>
      h("li", {}, h("span", { class: "grow mono" }, d.domain), h("span", { class: "badge" }, d.count))))] : null,
    h("p", {}, h("a", { href: p.url.replace(/\/$/, "") + "/admin/", target: "_blank", rel: "noopener" }, t("web.open_pihole"))));
}

function wireguardPanel(enabled, wg) {
  const panel = h("section", { class: "panel" }, h("h2", {}, "WireGuard"));
  if (!enabled) { panel.append(h("p", { class: "muted" }, t("web.wireguard_off"))); return panel; }
  if (!wg) { panel.append(h("p", { class: "notice" }, t("web.wireguard_no_status"))); return panel; }
  panel.append(
    h("p", { class: "muted" }, t("web.wireguard_port", { port: wg.interface.listen_port || "–" })),
    h("ul", { class: "list" }, wg.peers.length ? wg.peers.map((p) => h("li", {},
      h("span", { class: `dot ${p.active ? "ok" : ""}` }),
      h("div", { class: "grow" }, h("div", {}, p.name || p.public_key.slice(0, 10) + "…"),
        h("small", {}, p.latest_handshake ? t("web.last_handshake", { time: when(p.latest_handshake) }) : t("web.never_connected"))),
      h("small", { class: "muted" }, `↓${(p.received / 1e6).toFixed(1)} ↑${(p.sent / 1e6).toFixed(1)} MB`)))
      : h("li", { class: "empty" }, t("web.no_peers"))),
    h("p", { class: "muted" }, h("small", {}, t("web.wireguard_add_hint"))));
  return panel;
}

function devicesPanel(devices, redraw) {
  return h("section", { class: "panel" },
    h("h2", {}, t("web.lan_devices"), ` (${devices.length})`),
    h("p", { class: "muted" }, h("small", {}, t("web.lan_hint"))),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["web.device_name", "web.address", "MAC", "web.vendor", "web.last_seen", ""].map((k) => h("th", {}, k.startsWith("web.") ? t(k) : k)))),
      h("tbody", {}, devices.map((d) => h("tr", {},
        h("td", {}, d.trusted ? [icon("shield-check"), " "] : null, d.trusted || d.name || "–"),
        h("td", { class: "mono" }, d.ips.join(", ")),
        h("td", { class: "mono" }, d.mac, d.randomized ? h("span", { class: "badge", title: t("web.random_mac") }, "R") : null),
        h("td", {}, d.vendor || "–"),
        h("td", {}, d.last_query ? when(d.last_query) : d.state ? d.state.toLowerCase() : "–"),
        h("td", {}, d.trusted ? null : h("button", { class: "icon-only", title: t("web.trust_device"), onclick: async () => {
          const name = prompt(t("web.trust_device_name"), d.name || "");
          if (!name) return;
          if (await attempt(() => api("/api/network/trust", { method: "POST", body: { mac: d.mac, name } }), t("web.saved"))) restartPrompt();
        } }, icon("user-check")))))))));
}

export async function render(main, data) {
  const redraw = async () => {
    const fresh = await api("/api/network");
    fill(main, main.firstChild, build(fresh));
  };
  const build = (d) => [
    h("div", { class: "grid wide" }, piholePanel(d.pihole, redraw), wireguardPanel(d.wireguard_enabled, d.wireguard)),
    devicesPanel(d.devices, redraw),
  ];
  main.append(...build(data));
}
