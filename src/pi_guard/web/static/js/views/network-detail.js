// Pi-hole statistics and blocking, WireGuard peers, devices in the LAN.
import { api, attempt, confirmDialog, fill, h, icon, t, when } from "../api.js";
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

function showAccess(peer) {
  // QR code for the WireGuard app, plus the file for laptops.
  const dialog = h("dialog", { class: "wg-dialog" },
    h("h2", {}, peer.name),
    h("ol", { class: "howto" },
      h("li", {}, t("web.wg_step_app")),
      h("li", {}, t("web.wg_step_scan")),
      h("li", {}, t("web.wg_step_on"))),
    h("img", { class: "qr", src: peer.qr, alt: t("web.wg_qr_alt") }),
    h("p", { class: "muted" }, h("small", {}, t("web.wg_qr_secret"))),
    h("div", { class: "row end" },
      h("a", { class: "button", href: `/api/network/wireguard/peers/${peer.id}/file`, download: "" }, icon("download"), t("web.wg_download")),
      h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function addForm(redraw) {
  const name = h("input", { type: "text", maxlength: 32, placeholder: t("web.wg_name_placeholder"), required: true });
  const full = h("input", { type: "checkbox", checked: true });
  const form = h("form", { class: "stack" },
    h("label", {}, t("web.wg_name"), name),
    h("label", { class: "check" }, full, h("span", {}, t("web.wg_full_tunnel"), h("br"), h("small", { class: "muted" }, t("web.wg_full_tunnel_hint")))),
    h("button", { class: "primary", type: "submit" }, icon("plus"), t("web.wg_add")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const peer = await attempt(() => api("/api/network/wireguard/peers", { method: "POST", body: { name: name.value, full_tunnel: full.checked } }));
    if (!peer) return;
    showAccess(peer);
    redraw();
  });
  return form;
}

function wireguardPanel(d, redraw) {
  const wg = d.wireguard;
  const panel = h("section", { class: "panel" }, h("h2", {}, t("web.wg_title")));
  if (!d.wireguard_enabled) { panel.append(h("p", { class: "muted" }, t("web.wireguard_off"))); return panel; }
  if (!wg) { panel.append(h("p", { class: "notice" }, t("web.wireguard_no_status"))); return panel; }
  const peers = wg.peers;
  panel.append(
    h("p", { class: "muted" }, t("web.wg_intro")),
    d.wireguard_endpoint
      ? h("p", { class: "muted" }, h("small", {}, t("web.wg_reachable", { endpoint: d.wireguard_endpoint, port: wg.interface.listen_port || "51820" })))
      : h("p", { class: "notice" }, t("web.wg_no_endpoint")),
    h("ul", { class: "list" }, peers.length ? peers.map((p) => h("li", {},
      h("span", { class: `dot ${p.active ? "ok" : ""}` }),
      h("div", { class: "grow" },
        h("div", {}, p.name || p.public_key.slice(0, 10) + "…",
          p.id && !p.full_tunnel ? h("span", { class: "badge" }, t("web.wg_home_only")) : null),
        h("small", {}, p.pending ? t("web.wg_pending")
          : p.latest_handshake ? t("web.last_handshake", { time: when(p.latest_handshake) }) : t("web.never_connected"))),
      p.received || p.sent ? h("small", { class: "muted" }, `↓${(p.received / 1e6).toFixed(1)} ↑${(p.sent / 1e6).toFixed(1)} MB`) : null,
      p.id ? [
        h("button", { class: "icon-only", title: t("web.wg_show"), onclick: async () => {
          const peer = await attempt(() => api(`/api/network/wireguard/peers/${p.id}`));
          if (peer) showAccess(peer);
        } }, icon("qrcode")),
        h("button", { class: "icon-only danger", title: t("web.wg_remove"), onclick: async () => {
          if (!await confirmDialog(t("web.wg_remove_confirm", { name: p.name }), t("web.wg_remove"), true)) return;
          if (await attempt(() => api(`/api/network/wireguard/peers/${p.id}`, { method: "DELETE" }), t("web.saved"))) redraw();
        } }, icon("trash")),
      ] : null))
      : h("li", { class: "empty" }, t("web.no_peers"))),
    h("h3", {}, t("web.wg_add")),
    addForm(redraw));
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
    h("div", { class: "grid wide" }, piholePanel(d.pihole, redraw), wireguardPanel(d, redraw)),
    devicesPanel(d.devices, redraw),
  ];
  main.append(...build(data));
}
