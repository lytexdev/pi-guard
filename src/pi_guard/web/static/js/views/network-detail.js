// Pi-hole statistics and blocking, WireGuard peers, devices in the LAN.
import { askDialog, api, attempt, confirmDialog, fill, h, icon, t, toast, when } from "../api.js";
import { topologyPanel } from "./topology.js";
import { meter } from "./overview.js";
import { restartPrompt } from "./settings.js";
import { quantumPanel, reachPanel } from "./reach.js";
import { privacyPanels } from "./privacy.js";

function piholePanel(p, redraw) {
  if (!p) return h("section", { class: "panel" }, h("h2", {}, icon("brand-pihole"), " Pi-hole"), h("p", { class: "muted" }, t("web.pihole_off")));
  if (p.error) return h("section", { class: "panel" }, h("h2", {}, icon("brand-pihole"), " Pi-hole"), h("p", { class: "notice danger" }, p.error));
  const toggle = async (enabled, minutes) => {
    await attempt(() => api("/api/network/pihole/blocking", { method: "POST", body: { enabled, minutes } }), t("web.saved"));
    redraw();
  };
  return h("section", { class: "panel" },
    h("div", { class: "row between" }, h("h2", {}, icon("brand-pihole"), " Pi-hole"),
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
    p.top_blocked.length ? h("details", { class: "guide" }, h("summary", {}, t("web.top_blocked")), h("ul", { class: "list" }, p.top_blocked.map((d) =>
      h("li", {}, h("span", { class: "grow mono" }, d.domain), h("span", { class: "badge" }, d.count))))) : null,
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

function addDialog(redraw) {
  const name = h("input", { id: "wg_name", type: "text", maxlength: 32, placeholder: t("web.wg_name_placeholder"), required: true });
  const full = h("input", { type: "checkbox", checked: true, id: "wg_full" });
  const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, t("web.wg_add")),
    h("form", { class: "stack", method: "dialog" },
      h("div", { class: "field" }, h("label", { for: "wg_name" }, t("web.wg_name")), name),
      h("label", { class: "check", for: "wg_full" }, full, h("span", {}, t("web.wg_full_tunnel"), h("small", { class: "muted block" }, t("web.wg_full_tunnel_hint")))),
      h("div", { class: "row end" }, h("button", { type: "button", onclick: () => dialog.close() }, t("web.cancel")),
        h("button", { class: "primary", type: "submit" }, icon("plus"), t("web.wg_add")))));
  dialog.querySelector("form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const peer = await attempt(() => api("/api/network/wireguard/peers", { method: "POST", body: { name: name.value, full_tunnel: full.checked } }));
    if (!peer) return;
    dialog.close();
    showAccess(peer);
    redraw();
  });
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  name.focus();
}

function peerDialog(p) {
  const row = (label, value) => (value ? [h("dt", {}, label), h("dd", {}, value)] : null);
  const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, p.name || p.public_key.slice(0, 10)),
    h("p", {}, h("span", { class: `badge ${p.active ? "ok" : ""}` }, t(p.active ? "web.topo_online" : "web.topo_offline"))),
    h("dl", { class: "kv" },
      row(t("web.wg_address"), p.address || p.allowed_ips), row(t("web.wg_route"), p.id ? t(p.full_tunnel ? "web.wg_route_full" : "web.wg_home_only") : null),
      row(t("web.wg_last"), p.latest_handshake ? when(p.latest_handshake) : t("web.never_connected")), row(t("web.wg_from"), p.endpoint),
      row(t("web.wg_received"), `${((p.received || 0) / 1e6).toFixed(1)} MB`), row(t("web.wg_sent"), `${((p.sent || 0) / 1e6).toFixed(1)} MB`),
      row(t("web.wg_created"), p.created ? when(p.created) : null), row(t("web.wg_key"), h("span", { class: "mono" }, `${p.public_key.slice(0, 16)}…`))),
    h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function peerCard(p, redraw) {
  const state = p.pending ? t("web.wg_pending") : p.latest_handshake ? t("web.last_handshake", { time: when(p.latest_handshake) }) : t("web.never_connected");
  return h("article", { class: `wg-card${p.active ? " on" : ""}` },
    h("button", { class: "wg-card-main", onclick: () => peerDialog(p) },
      h("span", { class: "wg-avatar" }, icon(/phone|handy|pixel|iphone|android/i.test(p.name || "") ? "device-mobile" : "device-desktop")),
      h("span", { class: "grow" }, h("strong", { class: "block" }, p.name || `${p.public_key.slice(0, 10)}…`),
        h("small", { class: "muted block" }, state),
        h("span", { class: "row wrap" }, h("span", { class: `badge ${p.active ? "ok" : ""}` }, t(p.active ? "web.topo_online" : "web.topo_offline")),
          p.id ? h("span", { class: "badge" }, t(p.full_tunnel ? "web.wg_route_full" : "web.wg_home_only")) : null,
          p.received || p.sent ? h("small", { class: "muted" }, `↓${(p.received / 1e6).toFixed(1)} ↑${(p.sent / 1e6).toFixed(1)} MB`) : null))),
    p.id ? h("div", { class: "wg-card-actions" },
      h("button", { class: "small", title: t("web.wg_show"), onclick: async () => {
        const peer = await attempt(() => api(`/api/network/wireguard/peers/${p.id}`));
        if (peer) showAccess(peer);
      } }, icon("qrcode"), t("web.wg_access")),
      h("button", { class: "icon-only danger", title: t("web.wg_remove"), onclick: async () => {
        if (!await confirmDialog(t("web.wg_remove_confirm", { name: p.name }), t("web.wg_remove"), true)) return;
        if (await attempt(() => api(`/api/network/wireguard/peers/${p.id}`, { method: "DELETE" }), t("web.saved"))) redraw();
      } }, icon("trash"))) : null);
}

function wireguardPanel(d, redraw) {
  const wg = d.wireguard;
  const panel = h("section", { class: "panel stack wg-panel" });
  if (!d.wireguard_enabled) { panel.append(h("h2", {}, icon("brand-wireguard"), " ", t("web.wg_title")), h("p", { class: "muted" }, t("web.wireguard_off"))); return panel; }
  if (!wg) { panel.append(h("h2", {}, icon("brand-wireguard"), " ", t("web.wg_title")), h("p", { class: "notice" }, t("web.wireguard_no_status"))); return panel; }
  const peers = [...wg.peers].sort((a, b) => Number(b.active) - Number(a.active) || String(a.name).localeCompare(String(b.name)));
  panel.append(
    h("div", { class: "row between wrap" }, h("h2", {}, t("web.wg_devices")), h("button", { class: "primary", onclick: () => addDialog(redraw) }, icon("plus"), t("web.wg_add"))),
    h("p", { class: "muted" }, t("web.wg_intro"), " ", h("small", {}, t("web.wg_app_hint"))),
    peers.length ? h("div", { class: "wg-grid" }, peers.map((p) => peerCard(p, redraw)))
      : h("div", { class: "wg-empty stack" }, icon("brand-wireguard", "lg"), h("p", {}, t("web.no_peers")),
        h("ol", { class: "howto" }, h("li", {}, t("web.wg_step_app")), h("li", {}, t("web.wg_step_scan")), h("li", {}, t("web.wg_step_on"))),
        h("div", {}, h("button", { class: "primary", onclick: () => addDialog(redraw) }, icon("plus"), t("web.wg_add")))));
  return panel;
}

const VPN_PARTS = ["devices", "reach", "privacy", "quantum"];

function vpnSummary(d, part) {
  const peers = d.wireguard?.peers || [];
  const outside = d.wireguard_endpoint || d.wireguard_endpoint_v6 || d.wireguard_relay;
  const way = h("small", { class: "muted" }, outside ? t("web.vpn_sum_way", { way: outside }) : t("web.vpn_sum_no_way"));
  const pq = h("span", { class: "badge" }, "…");
  api("/api/network/quantum").then((q) => { pq.className = `badge ${q.enabled && q.installed ? "ok" : ""}`; pq.textContent = t(q.enabled && q.installed ? "web.vpn_sum_pq_on" : "web.vpn_sum_pq_off"); }).catch(() => pq.remove());
  const on = d.wireguard_enabled && d.wireguard;
  return h("section", { class: `panel vpn-summary ${on ? "on" : ""}` },
    h("div", { class: "vpn-head" }, h("span", { class: "vpn-ring" }, icon("brand-wireguard", "lg")),
      h("div", { class: "grow" }, h("h2", {}, t(on ? "web.vpn_sum_on" : "web.vpn_sum_off")),
        h("p", {}, on ? t("web.vpn_sum_devices", { active: peers.filter((p) => p.active).length, total: peers.length }) : t("web.wireguard_off"), " "), way),
      pq),
    h("nav", { class: "segmented vpn-parts", "aria-label": t("web.nm_tab_vpn") }, VPN_PARTS.map((id) =>
      h("a", { href: `#/network/vpn/${id}`, class: id === part ? "on" : "" }, t(`web.vpn_part_${id}`)))));
}

function vpnTab(body, d, params, redraw) {
  const part = VPN_PARTS.includes(params[1]) ? params[1] : "devices";
  const hint = (key) => h("p", { class: "muted" }, t(key));
  fill(body, vpnSummary(d, part),
    part === "devices" ? wireguardPanel(d, redraw) : null,
    part === "reach" ? [hint("web.vpn_part_reach_hint"), reachPanel()] : null,
    part === "privacy" ? [hint("web.vpn_part_privacy_hint"), privacyPanels()] : null,
    part === "quantum" ? [hint("web.vpn_part_quantum_hint"), quantumPanel()] : null);
}

async function internetControl(ip) {
  // The router has no call to throw a device out of the Wi-Fi: it can cut its internet access.
  if (!ip || ip.includes(":")) return null;
  const url = `/api/network/internet/${encodeURIComponent(ip)}`;
  const state = await api(url).catch(() => null);
  if (!state?.available) return null;
  const box = h("div", { class: "stack" });
  const draw = (allowed) => fill(box, h("h3", {}, t("web.ia_title")), h("p", { class: "muted" }, t(allowed ? "web.ia_allowed" : "web.ia_blocked")),
    h("button", { class: allowed ? "danger" : "primary", title: t("web.ia_tip"), onclick: async () => {
      if (allowed && !await confirmDialog(t("web.ia_confirm", { ip }), t("web.ia_block"), true)) return;
      const password = await askDialog(t("web.confirm_password"), { type: "password" });
      if (!password) return;
      const done = await attempt(() => api(url, { method: "POST", body: { allowed: !allowed, password } }), t("web.saved"));
      if (done) draw(done.allowed);
    } }, icon(allowed ? "ban" : "check"), t(allowed ? "web.ia_block" : "web.ia_allow")),
    h("p", { class: "muted" }, h("small", {}, t("web.ia_hint"))));
  draw(state.allowed);
  return box;
}

function deviceDialog(device) {
  const body = h("div", { class: "stack" }, h("p", { class: "muted" }, t("web.dev_scanning")));
  const label = h("input", { value: device.label || "", placeholder: device.router_name || device.name || "", maxlength: 40, "aria-label": t("web.dev_label") });
  const owner = h("input", { value: device.owner || "", placeholder: t("web.dev_owner_hint"), maxlength: 40, "aria-label": t("web.dev_owner") });
  const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, device.label || device.trusted || device.router_name || device.name || device.ip || device.ips?.[0] || device.mac),
    device.mac ? h("div", { class: "row" }, label, owner, h("button", { onclick: () => attempt(() => api(`/api/network/label/${device.mac}`, { method: "PUT", body: { name: label.value, owner: owner.value } }), t("web.saved")) }, t("web.save"))) : null,
    body, h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  const ip = device.ip || device.ips?.[0];
  if (!ip) { fill(body, h("p", { class: "muted" }, t("web.nothing"))); return; }
  const query = new URLSearchParams({ mac: device.mac || "", vendor: device.vendor || "" });
  attempt(() => api(`/api/network/device/${encodeURIComponent(ip)}?${query}`)).then((r) => {
    if (!r) { fill(body, h("p", { class: "notice danger" }, t("web.error"))); return; }
    const row = (label, value) => (value ? [h("dt", {}, label), h("dd", {}, value)] : null);
    fill(body, h("dl", { class: "kv" },
      row(t("web.dev_kind"), t(`web.dev_kind_${r.kind}`)), row(t("web.dev_system"), t(`web.dev_system_${r.system}`)),
      row(t("web.dev_maker"), r.maker || device.vendor), row(t("web.address"), ip), row("MAC", device.mac),
      row(t("web.dev_hostname"), r.name), row(t("web.dev_latency"), r.latency_ms != null ? `${r.latency_ms} ms` : null), row("TTL", r.ttl),
      row(t("web.dev_web"), [r.web_title, r.web_server].filter(Boolean).join(" · ")),
      row(t("web.dev_private"), r.private_mac ? t("web.yes") : null),
      row(t("web.dev_router_name"), device.router_name), row(t("web.dev_connection"), device.connection), row(t("web.dev_speed"), device.speed ? `${device.speed} Mbit/s` : null)),
      h("div", { class: "hints" }),
      h("div", { class: "access-box" }),
      h("h3", {}, t("web.dev_ports")),
      r.ports.length ? h("div", { class: "row wrap" }, r.ports.map((p) => h("span", { class: "badge" }, `${p.port} ${p.service}`))) : h("p", { class: "muted" }, t("web.dev_no_ports")),
      h("p", { class: "muted" }, h("small", {}, t("web.dev_estimate"))));
    internetControl(ip).then((box) => { if (box) body.querySelector(".access-box")?.replaceChildren(box); });
    api(`/api/addons/analytics/device/${encodeURIComponent(ip)}`).then((a) => {
      if (a.hints?.length) body.querySelector(".hints")?.replaceChildren(h("h3", {}, t("web.dev_hints")), h("div", { class: "row wrap" }, a.hints.map((x) => h("span", { class: "badge accent" }, x))));
    }).catch(() => {});
  });
}

async function blockPanel(enabled) {
  if (!enabled) return null;
  const panel = h("section", { class: "panel stack" });
  const draw = async () => {
    const data = await api("/api/network/blocklists").catch(() => null);
    if (!data) { fill(panel, h("h2", {}, t("web.bl_title")), h("p", { class: "muted" }, t("web.error"))); return; }
    const refresh = async () => { await attempt(() => api("/api/network/blocklists/update", { method: "POST" }), t("web.bl_updating")); draw(); };
    const add = async (address, comment) => { if (await attempt(() => api("/api/network/blocklists", { method: "POST", body: { address, comment } }), t("web.bl_added"))) draw(); };
    const url = h("input", { type: "url", placeholder: "https://…/hosts.txt", "aria-label": t("web.bl_url") });
    const file = h("input", { type: "file", accept: ".txt,text/plain", "aria-label": t("web.bl_file") });
    const known = new Set(data.lists.map((l) => l.address));
    fill(panel, h("div", { class: "row between" }, h("h2", {}, icon("shield-lock"), " ", t("web.bl_title")),
      h("button", { disabled: data.updating, onclick: refresh }, icon("refresh"), data.updating ? t("web.bl_updating") : t("web.bl_update"))),
      h("p", { class: "muted" }, t("web.bl_intro")),
      h("ul", { class: "list" }, data.lists.map((l) => {
        const on = h("input", { type: "checkbox", checked: l.enabled, "aria-label": l.address });
        on.addEventListener("change", () => attempt(() => api("/api/network/blocklists", { method: "PUT", body: { address: l.address, enabled: on.checked, comment: l.comment } }), t("web.saved")));
        return h("li", {}, on, h("div", { class: "grow clip" }, h("div", { class: "mono" }, l.address), h("small", {}, `${l.domains.toLocaleString()} ${t("web.bl_domains")}${l.comment ? ` · ${l.comment}` : ""}`)),
          h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
            if (await confirmDialog(t("web.bl_delete"), t("web.delete"), true) && await attempt(() => api("/api/network/blocklists", { method: "DELETE", body: { address: l.address } }))) draw();
          } }, icon("trash")));
      })),
      h("h3", {}, t("web.bl_presets")),
      h("div", { class: "row wrap" }, data.presets.filter((p) => !known.has(p.address)).map((p) => h("button", { onclick: () => add(p.address, p.name) }, icon("plus"), `${p.name} (${t(`web.bl_kind_${p.kind}`)})`))),
      h("h3", {}, t("web.bl_add")),
      h("div", { class: "row" }, url, h("button", { onclick: () => url.value && add(url.value.trim(), "pi-guard") }, icon("plus"), t("web.add"))),
      h("h3", {}, t("web.bl_file")), h("p", { class: "muted" }, h("small", {}, t("web.bl_file_hint"))),
      h("div", { class: "row" }, file, h("button", { onclick: async () => {
        if (!file.files.length) return;
        const form = new FormData();
        form.append("file", file.files[0], file.files[0].name);
        const r = await attempt(() => api("/api/network/blocklists/upload", { method: "POST", body: form }));
        if (r) { toast(t("web.bl_uploaded", { added: r.added, skipped: r.skipped })); file.value = ""; }
      } }, icon("upload"), t("web.upload"))));
  };
  await draw();
  return panel;
}

function devicesPanel(devices, redraw) {
  return h("section", { class: "panel devices" },
    h("h2", {}, t("web.lan_devices"), ` (${devices.length})`),
    h("p", { class: "muted" }, h("small", {}, t("web.lan_hint"))),
    h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["web.device_name", "web.dev_kind", "web.address", "MAC", "web.vendor", "web.last_seen", ""].map((k) => h("th", {}, k.startsWith("web.") ? t(k) : k)))),
      h("tbody", {}, devices.map((d) => h("tr", {},
        h("td", {}, d.trusted ? [icon("shield-check"), " "] : null, d.label || d.trusted || d.router_name || d.name || "–", d.owner ? h("small", { class: "muted block" }, d.owner) : null,
          d.connection ? h("small", { class: "muted block" }, `${d.connection === "802.11" ? "WLAN" : d.connection}${d.speed ? ` · ${d.speed} Mbit/s` : ""}`) : null),
        h("td", {}, d.details ? `${t(`web.dev_kind_${d.details.kind}`)} · ${t(`web.dev_system_${d.details.system}`)}` : "–"),
        h("td", { class: "mono" }, d.ips.join(", ")),
        h("td", { class: "mono" }, d.mac, d.randomized ? h("span", { class: "badge", title: t("web.random_mac") }, "R") : null),
        h("td", {}, d.vendor || "–"),
        h("td", {}, d.last_query ? when(d.last_query) : d.state ? d.state.toLowerCase() : "–"),
        h("td", { class: "actions" }, h("button", { class: "icon-only", title: t("web.dev_details"), onclick: () => deviceDialog(d) }, icon("zoom-in")),
          d.trusted ? null : h("button", { class: "icon-only", title: t("web.trust_device"), onclick: async () => {
          const name = await askDialog(t("web.trust_device_name"), { value: d.name || "" });
          if (!name) return;
          if (await attempt(() => api("/api/network/trust", { method: "POST", body: { mac: d.mac, name } }), t("web.saved"))) restartPrompt();
        } }, icon("user-check")))))))));
}

function wifiDialog(wifi, index) {
  // Saved as a picture, for the fridge or a guest. The key stays in this browser window.
  const key = h("code", { class: "hidden" }, wifi.key);
  const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, wifi.ssid), h("p", { class: "muted" }, t("web.wifi_scan")),
    h("img", { class: "qr light", src: wifi.qr, alt: t("web.wifi_qr_alt", { name: wifi.ssid }) }),
    h("p", {}, h("button", { class: "ghost", title: t("web.wifi_show_key"), onclick: () => key.classList.toggle("hidden") }, icon("eye"), t("web.wifi_key")), " ", key),
    h("div", { class: "row end" },
      h("a", { class: "button", href: wifi.qr, download: `wlan-${wifi.ssid.replace(/[^\w-]+/g, "_")}.svg`, title: t("web.wifi_save_tip") }, icon("download"), t("web.wifi_save")),
      index ? h("button", { class: "danger", title: t("web.wifi_renew_tip"), onclick: () => { dialog.close(); renewWifi(index); } }, icon("refresh"), t("web.wifi_renew")) : null,
      h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

async function renewWifi(index) {
  const text = t(index === 3 ? "web.wifi_renew_guest_confirm" : "web.wifi_renew_confirm");
  if (!await confirmDialog(text, t("web.wifi_renew"), true)) return;
  const password = await askDialog(t("web.confirm_password"), { type: "password" });
  if (!password) return;
  const wifi = await attempt(() => api(`/api/network/wifi/${index}/renew`, { method: "POST", body: { password } }), t("web.saved"));
  if (wifi) wifiDialog(wifi, index);
}

function manualWifi() {
  const ssid = h("input", { placeholder: t("web.wifi_name"), "aria-label": t("web.wifi_name"), autocomplete: "off" });
  const key = h("input", { type: "password", placeholder: t("web.wifi_key"), "aria-label": t("web.wifi_key"), autocomplete: "off" });
  return h("div", { class: "row" }, ssid, key, h("button", { title: t("web.wifi_qr_tip"), onclick: async () => {
    const wifi = await attempt(() => api("/api/network/wifi/qr", { method: "POST", body: { ssid: ssid.value, key: key.value } }));
    if (wifi) wifiDialog(wifi, null);
  } }, icon("qrcode"), t("web.wifi_qr")));
}

async function exposureDialog() {
  const e = await attempt(() => api("/api/network/exposure"));
  if (!e) return;
  const list = (items) => h("ul", { class: "seen" }, items.filter(Boolean).map(([seen, text]) => h("li", {}, icon(seen ? "eye" : "ban"), h("span", {}, text))));
  const domains = e.usage?.top_domains?.slice(0, 8) || [];
  const dialog = h("dialog", { class: "wg-dialog wide" }, h("h2", {}, t("web.ex_title")),
    h("p", { class: "muted" }, t("web.ex_intro", { device: e.name || e.ip })),
    e.vpn ? h("p", { class: "notice" }, t("web.ex_vpn")) : null,
    h("h3", {}, t("web.ex_provider")),
    list([[true, t("web.ex_p_ip")], [true, t("web.ex_p_volume")],
      [!e.dns_encrypted, t(e.dns_encrypted ? "web.ex_p_dns_hidden" : "web.ex_p_dns")], [true, t("web.ex_p_sni")], [false, t("web.ex_p_content")]]),
    e.upstreams.length ? h("small", { class: "muted" }, t("web.ex_upstreams", { names: e.upstreams.map((u) => `${u.name}:${u.port}`).join(", ") })) : null,
    h("h3", {}, t("web.ex_others")),
    list([[true, t("web.ex_o_name", { name: e.name || "–", mac: e.mac || "–" })], [true, t("web.ex_o_services")], [false, t("web.ex_o_content")]]),
    h("h3", {}, t("web.ex_admin")),
    list([[true, t("web.ex_a_domains")], [false, t("web.ex_a_content")]]),
    domains.length ? [h("h3", {}, t("web.ex_example", { count: e.usage.queries })), h("div", { class: "row wrap" }, domains.map((d) => h("span", { class: "badge" }, d.domain)))] : null,
    e.usage?.hints?.length ? h("p", { class: "muted" }, t("web.ex_hints"), " ", e.usage.hints.join(", ")) : null,
    h("p", { class: "muted" }, h("small", {}, t("web.ex_estimate"))),
    h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

// --- overview: the state at a glance, what happened today, what needs a look ----------------

const shown = (x) => x.label || x.trusted || x.router_name || x.name;
const online = (x) => x.online === true || (x.online == null && ["REACHABLE", "DELAY", "PROBE"].includes(x.state));

function statusBanner(d) {
  const peers = d.wireguard?.peers || [];
  const unknown = d.devices.filter((x) => !shown(x)).length;
  const pihole = d.pihole && !d.pihole.error;
  const chip = (tone, ic, text, href) => h("a", { class: `nm-chip ${tone}`, href }, icon(ic), h("span", {}, text));
  const problems = [!pihole && d.pihole_enabled, pihole && !d.pihole.blocking].filter(Boolean).length;
  return h("section", { class: `nm-banner ${problems ? "warn" : "ok"}` },
    h("div", { class: "nm-banner-head" }, icon(problems ? "alert-triangle" : "shield-check", "lg"),
      h("div", {}, h("h2", {}, t(problems ? "web.nm_state_warn" : "web.nm_state_ok")),
        h("p", { class: "muted" }, t("web.nm_state_line", { online: d.devices.filter(online).length, total: d.devices.length })))),
    h("div", { class: "nm-chips" },
      chip("", "router", t("web.nm_chip_devices", { count: d.devices.length }), "#/network/devices"),
      unknown ? chip("warn", "user-x", t("web.nm_chip_unknown", { count: unknown }), "#/network/devices") : null,
      d.pihole_enabled ? chip(pihole && d.pihole.blocking ? "ok" : "warn", "brand-pihole",
        pihole ? (d.pihole.blocking ? t("web.nm_chip_pihole", { percent: d.pihole.percent_blocked }) : t("web.blocking_off")) : t("web.nm_chip_pihole_error"), "#/network/lists") : null,
      d.wlans.length ? chip("", "wifi", t("web.nm_chip_wifi", { count: d.wlans.filter((w) => w.enabled).length }), "#/network/overview") : null,
      d.wireguard_enabled ? chip(peers.some((x) => x.active) ? "ok" : "", "brand-wireguard", t("web.nm_chip_vpn", { active: peers.filter((x) => x.active).length, total: peers.length }), "#/network/vpn") : null));
}

// Queries per hour of the last day, allowed and blocked stacked; colours checked for colour blindness.
function historyChart(history) {
  const hours = new Map();
  for (const point of history) {
    const hour = Math.floor(point.time / 3600) * 3600;
    const bucket = hours.get(hour) || { total: 0, blocked: 0 };
    bucket.total += point.total;
    bucket.blocked += point.blocked;
    hours.set(hour, bucket);
  }
  const rows = [...hours.entries()].sort((a, b) => a[0] - b[0]).slice(-24);
  if (!rows.length) return null;
  const width = 720, height = 160, pad = 22;
  const max = Math.max(...rows.map(([, b]) => b.total), 1);
  const step = (width - pad) / rows.length;
  const bar = Math.max(4, step - 4);
  const ns = "http://www.w3.org/2000/svg";
  const el = (tag, attrs, ...kids) => { const n = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); n.append(...kids); return n; };
  const hourText = (ts) => new Date(ts * 1000).toLocaleTimeString(document.documentElement.lang || "de", { hour: "2-digit", minute: "2-digit" });
  const svg = el("svg", { viewBox: `0 0 ${width} ${height + 18}`, class: "nm-chart", role: "img", "aria-label": t("web.nm_chart_label") });
  svg.append(el("line", { x1: pad, x2: width, y1: height, y2: height, class: "axis" }));
  svg.append(el("text", { x: 0, y: 10, class: "tick" }, max.toLocaleString()));
  rows.forEach(([ts, b], i) => {
    const x = pad + i * step + 2;
    const allowed = b.total - b.blocked;
    const hA = (allowed / max) * (height - 16);
    const hB = (b.blocked / max) * (height - 16);
    const g = el("g", { class: "bar" });
    g.append(el("title", {}, `${hourText(ts)}: ${b.total.toLocaleString()} ${t("web.nm_chart_queries")}, ${b.blocked.toLocaleString()} ${t("web.nm_chart_blocked")}`));
    g.append(el("rect", { x, y: 0, width: bar, height, class: "hit" }));
    if (hA > 0) g.append(el("rect", { x, y: height - hA, width: bar, height: hA, rx: 2, class: "allowed" }));
    if (hB > 1) g.append(el("rect", { x, y: height - hA - hB - (hA > 0 ? 2 : 0), width: bar, height: hB, rx: 2, class: "blocked" }));
    svg.append(g);
    if (i % 6 === 0) svg.append(el("text", { x, y: height + 14, class: "tick" }, hourText(ts)));
  });
  const total = rows.reduce((sum, [, b]) => sum + b.total, 0);
  const blocked = rows.reduce((sum, [, b]) => sum + b.blocked, 0);
  return h("section", { class: "panel stack" },
    h("div", { class: "row between wrap" }, h("h2", {}, icon("chart-bar"), " ", t("web.nm_chart_title")),
      h("div", { class: "nm-legend" }, h("span", { class: "key allowed" }, t("web.nm_chart_allowed")), h("span", { class: "key blocked" }, t("web.nm_chart_blocked")))),
    h("p", { class: "muted" }, t("web.nm_chart_sum", { total: total.toLocaleString(), blocked: blocked.toLocaleString(), percent: total ? Math.round((blocked / total) * 100) : 0 })),
    svg,
    h("details", {}, h("summary", {}, t("web.nm_chart_table")), h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, h("th", {}, t("web.nm_chart_hour")), h("th", {}, t("web.nm_chart_queries")), h("th", {}, t("web.nm_chart_blocked")))),
      h("tbody", {}, rows.map(([ts, b]) => h("tr", {}, h("td", {}, hourText(ts)), h("td", {}, b.total.toLocaleString()), h("td", {}, b.blocked.toLocaleString()))))))));
}

function devicesGlance(d, analytics) {
  // Devices without a name first: an unknown device in the home network is worth a look.
  const unknown = d.devices.filter((x) => !shown(x)).sort((a, b) => Number(online(b)) - Number(online(a))).slice(0, 6);
  const busy = (analytics?.devices || []).slice(0, 5);
  const most = Math.max(...busy.map((x) => x.count), 1);
  const byIp = new Map(d.devices.flatMap((x) => x.ips.map((ip) => [ip, x])));
  return h("div", { class: "grid wide" },
    h("section", { class: "panel stack" }, h("div", { class: "row between" }, h("h2", {}, icon("user-x"), " ", t("web.nm_unknown_title")), h("a", { href: "#/network/devices" }, t("web.nm_all_devices"))),
      unknown.length ? h("ul", { class: "list" }, unknown.map((x) => h("li", {}, h("span", { class: `dot ${online(x) ? "ok" : ""}` }),
        h("div", { class: "grow" }, h("div", {}, shown(x) || x.vendor || x.ips[0] || x.mac), h("small", { class: "muted mono" }, [x.ips[0], x.vendor].filter(Boolean).join(" · "))),
        h("button", { class: "small", onclick: () => deviceDialog(x) }, icon("pencil"), t("web.nm_name_it")))))
        : h("p", { class: "muted" }, icon("circle-check"), " ", t("web.nm_unknown_none"))),
    busy.length ? h("section", { class: "panel stack" }, h("h2", {}, icon("cpu"), " ", t("web.nm_busy_title")),
      h("ul", { class: "nm-bars" }, busy.map((x) => {
        const known = byIp.get(x.ip);
        return h("li", {}, h("div", { class: "row between" }, h("span", { class: "clip" }, (known && shown(known)) || x.name || x.ip), h("small", { class: "muted" }, x.count.toLocaleString())),
          h("div", { class: "nm-bar" }, h("span", { style: `width:${Math.max(3, (x.count / most) * 100)}%` })));
      })), h("p", { class: "muted" }, h("small", {}, t("web.nm_busy_hint")))) : null);
}

function shortcuts(d) {
  const item = (ic, label, hint, href, onclick) => h(href ? "a" : "button", { class: "nm-shortcut", href, onclick }, icon(ic), h("span", {}, h("strong", {}, label), h("small", { class: "muted block" }, hint)));
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.nm_shortcuts")),
    h("div", { class: "nm-shortcuts" },
      item("ban", t("web.nm_sc_block"), t("web.nm_sc_block_hint"), "#/network/blocks"),
      item("mood-happy", t("web.nm_sc_kids"), t("web.nm_sc_kids_hint"), "#/addons/kids"),
      item("qrcode", t("web.nm_sc_guest"), t("web.nm_sc_guest_hint"), null, () => document.querySelector(".nm-wifi")?.scrollIntoView({ behavior: "smooth" })),
      item("network", t("web.nm_sc_map"), t("web.nm_sc_map_hint"), "#/network/map"),
      item("eye", t("web.ex_title"), t("web.ex_teaser"), null, exposureDialog),
      item("map-pin", t("web.nm_sc_move"), t("web.nm_sc_move_hint"), "#/settings/move")));
}

async function overviewTab(body, d, redraw) {
  const charts = h("div", { class: "stack" });
  fill(body, statusBanner(d), charts, h("div", { class: "grid wide" }, piholePanel(d.pihole, redraw), wifiPanel(d)), shortcuts(d));
  // Statistics come from Pi-hole and take a moment; the rest of the page does not wait for them.
  const analytics = d.pihole && !d.pihole.error ? await api("/api/addons/analytics/overview").catch(() => null) : null;
  fill(charts, analytics ? historyChart(analytics.history || []) : null, devicesGlance(d, analytics));
}

function wifiPanel(d) {
  return h("section", { class: "panel stack nm-wifi" }, h("h2", {}, icon("wifi"), " ", t("web.nm_wifi")),
    d.wlans.length ? h("ul", { class: "list" }, d.wlans.map((w) => h("li", {}, h("span", { class: `dot ${w.enabled ? "ok" : ""}` }),
      h("div", { class: "grow" }, h("div", {}, w.ssid || "–", w.guest ? h("span", { class: "badge accent" }, t("web.nm_guest")) : null),
        h("small", {}, `${w.enabled ? t("web.nm_on") : t("web.nm_off")} · ${t("web.nm_channel", { channel: w.channel })} · ${t("web.nm_clients", { count: w.clients })}`)),
      h("button", { class: "icon-only", title: t("web.wifi_qr_tip"), onclick: async () => {
        const wifi = await attempt(() => api("/api/network/wifi/qr", { method: "POST", body: { index: w.index } }));
        if (wifi) wifiDialog(wifi, w.index);
      } }, icon("qrcode")))))
      : [h("p", { class: "muted" }, t("web.nm_no_router")), manualWifi()],
    d.nearby.length ? h("details", { class: "guide" }, h("summary", {}, t("web.nm_nearby"), ` (${d.nearby.length})`), h("ul", { class: "list" }, d.nearby.map((n) => h("li", {}, icon("wifi"),
      h("div", { class: "grow" }, h("div", {}, n.ssid), h("small", {}, `${n.security || t("web.nm_open")} · ${t("web.nm_channel", { channel: n.channel })}`)),
      h("span", { class: "badge" }, `${n.signal} %`))))) : null);
}

async function blocksPanel(d) {
  const panel = h("section", { class: "panel stack" });
  const draw = async () => {
    const blocks = await api("/api/network/blocks").catch(() => null);
    if (blocks === null) { fill(panel, h("h2", {}, t("web.nm_blocks")), h("p", { class: "notice" }, t("web.nm_needs_addon"), " ", h("a", { href: "#/addons" }, t("web.nav_addons")))); return; }
    const domain = h("input", { placeholder: "tiktok.com", "aria-label": t("web.nm_domain") });
    const time = h("select", { "aria-label": t("web.nm_duration") }, [["", "web.nm_forever"], ["30", "web.nm_30m"], ["60", "web.nm_1h"], ["180", "web.nm_3h"], ["1440", "web.nm_1d"]].map(([v, k]) => h("option", { value: v }, t(k))));
    const who = h("select", { "aria-label": t("web.nm_for") }, h("option", { value: "" }, t("web.nm_everyone")),
      d.devices.filter((x) => x.ips.length).map((x) => h("option", { value: x.ips[0] }, x.label || x.trusted || x.router_name || x.name || x.ips[0])));
    fill(panel, h("h2", {}, icon("ban"), " ", t("web.nm_blocks")), h("p", { class: "muted" }, t("web.nm_blocks_hint")),
      h("div", { class: "row wrap" }, domain, time, who, h("button", { class: "primary", onclick: async () => {
        const option = who.selectedOptions[0];
        if (await attempt(() => api("/api/network/blocks", { method: "POST", body: { domain: domain.value, minutes: time.value ? Number(time.value) : null, device: who.value || null, label: who.value ? option.textContent : "" } }), t("web.saved"))) draw();
      } }, icon("ban"), t("web.nm_block"))),
      h("ul", { class: "list" }, blocks.length ? blocks.map((b) => h("li", {}, icon("ban"),
        h("div", { class: "grow" }, h("div", {}, b.domain), h("small", {}, `${b.label || t("web.nm_everyone")} · ${b.until ? t("web.nm_until", { time: when(b.until) }) : t("web.nm_forever")}`)),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => { if (await attempt(() => api(`/api/network/blocks/${b.id}`, { method: "DELETE" }))) draw(); } }, icon("trash")))) : h("li", { class: "empty" }, t("web.nothing"))),
      h("p", { class: d.blockpage ? "muted" : "notice" }, h("small", {}, d.blockpage ? t("web.nm_page_on", { ip: d.blockpage }) : t("web.nm_page_off"))));
  };
  await draw();
  return panel;
}

const TABS = ["overview", "devices", "blocks", "lists", "vpn", "map", "analysis"];

export async function render(main, data, params = []) {
  const tab = TABS.includes(params[0]) ? params[0] : "overview";
  const body = h("div", { class: "stack" });
  const redraw = () => location.reload();
  main.append(h("nav", { class: "tabs", "aria-label": t("web.nav_network") }, TABS.map((id) => h("a", { href: `#/network/${id}`, class: id === tab ? "active" : "" }, t(`web.nm_tab_${id}`)))), body);
  if (tab === "overview") await overviewTab(body, data, redraw);
  if (tab === "devices") fill(body, devicesPanel(data.devices, redraw));
  if (tab === "blocks") fill(body, await blocksPanel(data));
  if (tab === "lists") fill(body, await blockPanel(data.pihole_enabled));
  if (tab === "vpn") vpnTab(body, data, params, redraw);
  if (tab === "map") fill(body, await topologyPanel(deviceDialog));
  if (tab === "analysis") {
    const view = await import("./addons.js");
    await view.renderDetail("analytics", body);
  }
}
