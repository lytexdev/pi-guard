// Pi-hole statistics and blocking, WireGuard peers, devices in the LAN.
import { api, attempt, confirmDialog, fill, h, icon, t, toast, when } from "../api.js";
import { topologyPanel } from "./topology.js";
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

function addBox(redraw) {
  // The form stays out of the way until someone wants it.
  const box = h("div", { class: "wg-add hidden" }, h("h3", {}, t("web.wg_add")), addForm(redraw));
  const open = h("button", { class: "primary", onclick: () => { box.classList.remove("hidden"); open.classList.add("hidden"); box.querySelector("input")?.focus(); } }, icon("plus"), t("web.wg_add"));
  return h("div", { class: "wg-actions" }, open, box);
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

function wireguardPanel(d, redraw) {
  const wg = d.wireguard;
  const panel = h("section", { class: "panel wg" }, h("h2", {}, t("web.wg_title")));
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
      h("div", { class: "grow clickable", role: "button", tabindex: "0", onclick: () => peerDialog(p), onkeydown: (e) => { if (e.key === "Enter") peerDialog(p); } },
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
    addBox(redraw));
  return panel;
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
      h("h3", {}, t("web.dev_ports")),
      r.ports.length ? h("div", { class: "row wrap" }, r.ports.map((p) => h("span", { class: "badge" }, `${p.port} ${p.service}`))) : h("p", { class: "muted" }, t("web.dev_no_ports")),
      h("p", { class: "muted" }, h("small", {}, t("web.dev_estimate"))));
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
          const name = prompt(t("web.trust_device_name"), d.name || "");
          if (!name) return;
          if (await attempt(() => api("/api/network/trust", { method: "POST", body: { mac: d.mac, name } }), t("web.saved"))) restartPrompt();
        } }, icon("user-check")))))))));
}

function wifiPanel(d) {
  return h("section", { class: "panel stack" }, h("h2", {}, icon("wifi"), " ", t("web.nm_wifi")),
    d.wlans.length ? h("ul", { class: "list" }, d.wlans.map((w) => h("li", {}, h("span", { class: `dot ${w.enabled ? "ok" : ""}` }),
      h("div", { class: "grow" }, h("div", {}, w.ssid || "–", w.guest ? h("span", { class: "badge accent" }, t("web.nm_guest")) : null),
        h("small", {}, `${w.enabled ? t("web.nm_on") : t("web.nm_off")} · ${t("web.nm_channel", { channel: w.channel })} · ${t("web.nm_clients", { count: w.clients })}`)))))
      : h("p", { class: "muted" }, t("web.nm_no_router")),
    d.nearby.length ? [h("h3", {}, t("web.nm_nearby")), h("ul", { class: "list" }, d.nearby.map((n) => h("li", {}, icon("wifi"),
      h("div", { class: "grow" }, h("div", {}, n.ssid), h("small", {}, `${n.security || t("web.nm_open")} · ${t("web.nm_channel", { channel: n.channel })}`)),
      h("span", { class: "badge" }, `${n.signal} %`))))] : null);
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
  if (tab === "overview") fill(body, h("div", { class: "grid wide" }, piholePanel(data.pihole, redraw), wifiPanel(data)));
  if (tab === "devices") fill(body, devicesPanel(data.devices, redraw));
  if (tab === "blocks") fill(body, await blocksPanel(data));
  if (tab === "lists") fill(body, await blockPanel(data.pihole_enabled));
  if (tab === "vpn") fill(body, wireguardPanel(data, redraw));
  if (tab === "map") fill(body, await topologyPanel(deviceDialog));
  if (tab === "analysis") {
    const view = await import("./addons.js");
    await view.renderDetail("analytics", body);
  }
}
