// Interactive map: internet, router, this Pi with its services, and everything connected to it.
import { api, fill, h, icon, svg, t } from "../api.js";

const NODE_W = 132, NODE_H = 46, GAP_X = 14, ROW = 92;
const MAX_LAN = 12;

function node(map, item, x, y, select) {
  const group = svg("g", { class: `tnode ${item.cls || ""}`, tabindex: "0", role: "button", "aria-label": item.label });
  group.append(svg("rect", { x, y, width: NODE_W, height: NODE_H, rx: 10 }));
  group.append(svg("circle", { cx: x + 14, cy: y + 23, r: 5, class: `tdot ${item.state || ""}` }));
  const title = svg("text", { x: x + 27, y: y + (item.sub ? 20 : 28), class: "tlabel" });
  title.textContent = item.label.length > 15 ? `${item.label.slice(0, 14)}…` : item.label;
  group.append(title);
  if (item.sub) {
    const sub = svg("text", { x: x + 27, y: y + 35, class: "tsub" });
    sub.textContent = item.sub.length > 18 ? `${item.sub.slice(0, 17)}…` : item.sub;
    group.append(sub);
  }
  const pick = () => { map.querySelectorAll(".tnode.on").forEach((n) => n.classList.remove("on")); group.classList.add("on"); select(item); };
  group.addEventListener("click", pick);
  group.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  map.append(group);
  return { x: x + NODE_W / 2, top: y, bottom: y + NODE_H };
}

function link(map, a, b, cls = "") {
  const mid = (a.bottom + b.top) / 2;
  map.prepend(svg("path", { class: `tlink ${cls}`, d: `M${a.x} ${a.bottom} C${a.x} ${mid} ${b.x} ${mid} ${b.x} ${b.top}` }));
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
  const draw = () => {
    const width = Math.max(300, box.clientWidth || 800);
    const map = svg("svg", { class: "tmap", role: "group", "aria-label": t("web.topo_title") });
    const internet = node(map, { label: t("web.topo_internet"), state: "ok", detail: t("web.topo_internet_text") }, (width - NODE_W) / 2, 6, select);
    const router = node(map, { label: t("web.topo_router"), state: "ok", detail: t("web.topo_router_text") }, (width - NODE_W) / 2, 6 + ROW - 24, select);
    link(map, internet, router);
    const pi = node(map, { label: "Pi-Guard", cls: "hub", state: "ok", detail: t("web.topo_pi_text"), href: "#/overview" }, (width - NODE_W) / 2, 6 + 2 * ROW - 48, select);
    link(map, router, pi);
    let y = 6 + 3 * ROW - 62;
    const services = [
      data.services.pihole ? { label: "Pi-hole", sub: "DNS", state: "ok", detail: t("web.topo_pihole_text"), href: "#/network" } : null,
      data.services.wireguard ? { label: "WireGuard", sub: t("web.topo_vpn_count", { count: data.vpn.filter((p) => p.active).length }), state: "ok", detail: t("web.topo_vpn_text"), href: "#/network" } : null,
      data.services.mqtt ? { label: "MQTT", sub: "Smart Home", state: "ok", href: "#/addons/smarthome" } : null,
    ].filter(Boolean);
    if (services.length) {
      const r = row(map, services, y, width, select);
      r.placed.forEach((p) => link(map, pi, p));
      y += r.height + 30;
    }
    const groups = [
      ...data.cameras.map((c) => ({ label: c.name, sub: c.room || c.source, state: c.state === "running" ? "ok" : "danger", detail: t(`camera.state.${c.state}`), href: `#/live/${c.id}` })),
      ...data.vpn.map((p) => ({ label: p.name, sub: p.address, cls: "vpn", state: p.active ? "ok" : "", detail: t(p.active ? "web.topo_online" : "web.topo_offline") })),
      ...data.lan.slice(0, MAX_LAN).map((d) => ({ label: d.name, sub: d.ip || d.vendor || "", state: d.active ? "ok" : "", cls: d.trusted ? "trusted" : "",
        detail: [d.vendor, d.mac, d.kind ? t(`web.dev_kind_${d.kind}`) : null].filter(Boolean).join(" · "), device: d })),
    ];
    const r = row(map, groups, y, width, select);
    r.placed.forEach((p) => link(map, pi, p, "thin"));
    y += r.height;
    map.setAttribute("viewBox", `0 0 ${width} ${y + 6}`);
    map.setAttribute("height", y + 6);
    fill(box, map);
  };
  const panel = h("section", { class: "panel" }, h("div", { class: "row between" }, h("h2", {}, icon("network"), " ", t("web.topo_title")),
    data.lan.length > MAX_LAN ? h("small", { class: "muted" }, t("web.topo_more", { count: data.lan.length - MAX_LAN })) : null), box, info);
  requestAnimationFrame(draw);
  const observer = new ResizeObserver(() => { if (box.clientWidth && box.dataset.w !== String(box.clientWidth)) { box.dataset.w = box.clientWidth; draw(); } });
  observer.observe(box);
  return panel;
}
