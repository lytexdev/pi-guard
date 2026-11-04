// Interactive map: internet, router, this Pi with its services, and everything connected to it.
import { api, fill, h, icon, svg, t } from "../api.js";
import { logoUrl } from "../theme.js";

const NODE_W = 132, NODE_H = 46, GAP_X = 14, ROW = 112;
const MAX_LAN = 12;
const KIND_ICONS = { phone: "device-mobile", computer: "device-desktop", printer: "printer", camera: "camera", router: "router", media: "device-tv", iot: "bulb" };

function deviceIcon(d) {
  // What the device was recognised as, otherwise a guess from its name and maker.
  if (KIND_ICONS[d.kind]) return KIND_ICONS[d.kind];
  const text = `${d.name} ${d.vendor || ""}`.toLowerCase();
  if (/iphone|ipad|galaxy|pixel|xiaomi|redmi|huawei|oneplus|android|handy|phone/.test(text)) return "device-mobile";
  if (/fritz|repeater|router|avm/.test(text)) return "router";
  if (/drucker|printer|brother|canon|epson/.test(text)) return "printer";
  if (/tv|fire|chromecast|sonos|roku/.test(text)) return "device-tv";
  if (/laptop|desktop|\bpc\b|macbook|imac|dell|lenovo|asus|hewlett/.test(text)) return "device-desktop";
  if (/esp|shelly|tasmota|tuya|hue|plug|steckdose/.test(text)) return "bulb";
  return "wifi";
}

function node(map, item, x, y, select) {
  const group = svg("g", { class: `tnode ${item.cls || ""}`, tabindex: "0", role: "button", "aria-label": item.label });
  group.append(svg("rect", { x, y, width: NODE_W, height: NODE_H, rx: 10 }));
  if (item.logo) {
    group.append(svg("image", { href: logoUrl(), x: x + 7, y: y + 9, width: 28, height: 28 }));
  } else {
    const use = svg("use", { href: `/static/icons/sprite.svg#${item.icon || "router"}`, x: x + 10, y: y + 13, width: 20, height: 20, class: "ticon" });
    group.append(use);
  }
  group.append(svg("circle", { cx: x + NODE_W - 12, cy: y + 12, r: 4, class: `tdot ${item.state || ""}` }));
  const title = svg("text", { x: x + 38, y: y + (item.sub ? 20 : 28), class: "tlabel" });
  title.textContent = item.label.length > 12 ? `${item.label.slice(0, 11)}…` : item.label;
  group.append(title);
  if (item.sub) {
    const sub = svg("text", { x: x + 38, y: y + 35, class: "tsub" });
    sub.textContent = item.sub.length > 15 ? `${item.sub.slice(0, 14)}…` : item.sub;
    group.append(sub);
  }
  const pick = () => { map.querySelectorAll(".tnode.on").forEach((n) => n.classList.remove("on")); group.classList.add("on"); select(item); };
  group.addEventListener("click", pick);
  group.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  map.append(group);
  return { x: x + NODE_W / 2, top: y, bottom: y + NODE_H };
}

function arrowHead() {
  const defs = svg("defs");
  const marker = svg("marker", { id: "tarrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerUnits: "userSpaceOnUse", markerWidth: 9, markerHeight: 9, orient: "auto-start-reverse" });
  marker.append(svg("path", { d: "M0 0L10 5L0 10z", class: "tarrow" }));
  defs.append(marker);
  return defs;
}

function link(map, a, b, cls = "", label = "") {
  // Arrows at both ends: data flows both ways; the moving dashes show an active connection.
  const mid = (a.bottom + b.top) / 2;
  const path = svg("path", { class: `tlink ${cls}`, d: `M${a.x} ${a.bottom} C${a.x} ${mid} ${b.x} ${mid} ${b.x} ${b.top - 2}`,
    "marker-end": "url(#tarrow)", "marker-start": "url(#tarrow)" });
  map.prepend(path);
  if (label) {
    const text = svg("text", { x: (a.x + b.x) / 2 + 8, y: mid + 4, class: "tedge" });
    text.textContent = label;
    map.append(text);
  }
}

function row(map, items, y, width, select) {
  const perRow = Math.max(1, Math.floor((width + GAP_X) / (NODE_W + GAP_X)));
  const placed = [];
  items.forEach((item, index) => {
    const line = Math.floor(index / perRow);
    const inLine = Math.min(perRow, items.length - line * perRow);
    const start = (width - (inLine * NODE_W + (inLine - 1) * GAP_X)) / 2;
    placed.push(node(map, item, start + (index % perRow) * (NODE_W + GAP_X), y + line * (NODE_H + 12), select));
  });
  return { placed, height: Math.ceil(items.length / perRow) * (NODE_H + 12) };
}

export async function topologyPanel(onDevice) {
  const data = await api("/api/topology").catch(() => null);
  if (!data) return null;
  const box = h("div", { class: "topology" });
  const info = h("div", { class: "tinfo", "aria-live": "polite" }, h("small", { class: "muted" }, t("web.topo_hint")));
  const select = (item) => {
    fill(info, h("strong", {}, item.label), item.detail ? h("div", { class: "muted" }, item.detail) : null,
      item.href ? h("a", { href: item.href }, t("web.topo_open")) : null,
      item.device && onDevice ? h("button", { onclick: () => onDevice(item.device) }, icon("zoom-in"), t("web.dev_details")) : null);
  };
  const draw = (target = box, forced = 0) => {
    const width = forced || Math.max(300, target.clientWidth || 800);
    const map = svg("svg", { class: "tmap", role: "group", "aria-label": t("web.topo_title") });
    const internet = node(map, { label: t("web.topo_internet"), icon: "world", state: "ok", detail: t("web.topo_internet_text") }, (width - NODE_W) / 2, 6, select);
    const router = node(map, { label: t("web.topo_router"), icon: "router", state: "ok", detail: t("web.topo_router_text") }, (width - NODE_W) / 2, 6 + ROW - 24, select);
    link(map, internet, router, "live");
    const pi = node(map, { label: "Pi-Guard", logo: true, cls: "hub", state: "ok", detail: t("web.topo_pi_text"), href: "#/overview" }, (width - NODE_W) / 2, 6 + 2 * ROW - 48, select);
    link(map, router, pi, "live", data.services.wireguard ? "UDP 51820" : "");
    let y = 6 + 3 * ROW - 48;
    const services = [
      data.services.pihole ? { label: "Pi-hole", icon: "brand-pihole", sub: "DNS", state: "ok", detail: t("web.topo_pihole_text"), href: "#/network" } : null,
      data.services.wireguard ? { label: "WireGuard", icon: "brand-wireguard", sub: t("web.topo_vpn_count", { count: data.vpn.filter((p) => p.active).length }), state: "ok", detail: t("web.topo_vpn_text"), href: "#/network" } : null,
      data.services.mqtt ? { label: "MQTT", icon: "brand-mqtt", sub: "Smart Home", state: "ok", href: "#/addons/smarthome" } : null,
    ].filter(Boolean);
    if (services.length) {
      const r = row(map, services, y, width, select);
      r.placed.forEach((p) => link(map, pi, p, "live"));
      y += r.height + 30;
    }
    const groups = [
      ...(data.agents || []).map((a) => ({ label: a.name, sub: a.os, icon: "device-desktop", state: a.online ? "ok" : "", detail: t(a.online ? "web.topo_online" : "web.topo_offline"), href: "#/addons/agents" })),
      ...data.cameras.map((c) => ({ label: c.name, icon: "camera", sub: c.room || c.source, state: c.state === "running" ? "ok" : "danger", detail: t(`camera.state.${c.state}`), href: `#/live/${c.id}` })),
      ...data.vpn.map((p) => ({ label: p.name, sub: p.address, icon: "device-mobile", cls: "vpn", state: p.active ? "ok" : "", detail: t(p.active ? "web.topo_online" : "web.topo_offline") })),
      ...data.lan.slice(0, MAX_LAN).map((d) => ({ label: d.name, sub: d.ip || d.vendor || "", icon: deviceIcon(d), state: d.active ? "ok" : "", cls: d.trusted ? "trusted" : "",
        detail: [d.vendor, d.mac, d.kind ? t(`web.dev_kind_${d.kind}`) : null].filter(Boolean).join(" · "), device: d })),
    ];
    const r = row(map, groups, y, width, select);
    r.placed.forEach((p, i) => link(map, pi, p, groups[i].state === "ok" ? "thin live" : "thin"));
    y += r.height;
    map.prepend(arrowHead());
    map.setAttribute("viewBox", `0 0 ${width} ${y + 6}`);
    map.setAttribute("height", y + 6);
    fill(target, map);
  };
  const enlarge = () => {
    // Like a picture: opens large, with room to zoom and scroll.
    const view = h("div", { class: "topology large" });
    let zoom = 1;
    const size = (step) => { zoom = Math.min(3, Math.max(1, zoom * step)); draw(view, Math.max(innerWidth - 80, 900) * zoom); };
    const dialog = h("dialog", { class: "lightbox map" },
      view, h("div", { class: "row end" },
        h("button", { class: "icon-only", title: t("web.zoom_out"), onclick: () => size(1 / 1.4) }, icon("zoom-out")),
        h("button", { class: "icon-only", title: t("web.zoom_in"), onclick: () => size(1.4) }, icon("zoom-in")),
        h("button", { class: "primary", onclick: () => dialog.close() }, t("web.close"))));
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
    size(1);
  };
  box.addEventListener("click", (event) => { if (!event.target.closest("g, a, button")) enlarge(); });
  const panel = h("section", { class: "panel" }, h("div", { class: "row between" }, h("h2", {}, icon("network"), " ", t("web.topo_title")),
    h("div", { class: "row" }, data.lan.length > MAX_LAN ? h("small", { class: "muted" }, t("web.topo_more", { count: data.lan.length - MAX_LAN })) : null,
      h("button", { class: "icon-only", title: t("web.topo_enlarge"), onclick: enlarge }, icon("maximize")))), box, info);
  requestAnimationFrame(() => draw());
  const observer = new ResizeObserver(() => { if (box.clientWidth && box.dataset.w !== String(box.clientWidth)) { box.dataset.w = box.clientWidth; draw(); } });
  observer.observe(box);
  return panel;
}
