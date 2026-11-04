// Overview: what is happening right now, at a glance.
import { api, attempt, duration, h, icon, t, when, fill } from "../api.js";
import { topologyPanel } from "./topology.js";

export function meter(percent, warnAt = 75, dangerAt = 90) {
  const fill = h("i");
  fill.style.width = `${Math.min(100, Math.max(0, percent))}%`;
  const level = percent >= dangerAt ? " danger" : percent >= warnAt ? " warn" : "";
  return h("div", { class: `meter${level}` }, fill);
}

export function stateDot(state) {
  const cls = state === "running" ? "ok" : state === "starting" ? "warn" : "danger";
  return h("span", { class: `dot ${cls}` });
}

export function eventRow(event, cameras = {}) {
  const snap = event.data?.snapshot;
  return h("li", {},
    snap ? h("img", { class: "thumb", src: `/api/media/snapshots/${encodeURIComponent(snap)}`,
                      alt: "", loading: "lazy" })
         : h("span", { class: "thumb row", "aria-hidden": "true" }),
    h("div", { class: "grow" },
      h("div", {}, t(`event.${event.type}`)),
      h("small", {}, [when(event.ts), event.camera ? ` · ${cameras[event.camera] || event.camera}` : ""])));
}

let publicCheck = null;  // once per page load: the browser asks for its own public address

async function connectionPanel() {
  const c = await api("/api/connection").catch(() => null);
  if (!c) return null;
  const line = h("p", { class: "muted" }, t("web.conn_checking"));
  const panel = h("section", { class: "panel" }, h("h2", {}, icon("shield-lock"), " ", t("web.conn_title")),
    h("div", { class: "stat" }, c.peer ? t("web.conn_wireguard_peer", { name: c.peer }) : t(`web.conn_${c.kind}`)),
    h("dl", { class: "kv" }, h("dt", {}, t("web.conn_address")), h("dd", { class: "mono" }, c.ip),
      c.home_ip ? [h("dt", {}, t("web.conn_home_ip")), h("dd", { class: "mono" }, c.home_ip)] : null,
      c.vpn_clients ? [h("dt", {}, t("web.conn_vpn_clients")), h("dd", {}, c.vpn_clients)] : null),
    line);
  publicCheck ||= fetch("https://api.ipify.org", { cache: "no-store" }).then((r) => r.text())
    .then((ip) => api("/api/connection/check", { method: "POST", body: { ip: ip.trim() } }));
  publicCheck.then((r) => {
    fill(line, t(`web.conn_public_${r.kind}`, { ip: r.ip, organisation: r.organisation || "" }));
  }).catch(() => { fill(line, t("web.conn_public_unknown")); });
  return panel;
}

export async function render(main) {
  const topology = await topologyPanel();
  const draw = async () => {
    const [o, events, open] = await Promise.all([api("/api/overview"), api("/api/events?limit=6"), api("/api/alerts")]);
    const cameras = Object.fromEntries(o.cameras.map((c) => [c.id, c.name]));
    const mode = o.mode;
    const modeButtons = h("div", { class: "segmented" },
      ["HOME", "AWAY", ...(mode.presence_enabled ? ["AUTO"] : [])].map((m) => {
        const on = m === "AUTO" ? mode.selection === "AUTO" : mode.selection !== "AUTO" && mode.effective === m;
        return h("button", { class: on ? "on" : "", onclick: async () => {
          await attempt(() => api("/api/mode", { method: "POST", body: { mode: m } }), t("web.saved"));
          draw();
        } }, icon(m === "HOME" ? "home-shield" : m === "AWAY" ? "walk" : "wifi"), t(`web.mode_${m.toLowerCase()}`));
      }));
    const sys = o.system;
    const problems = o.cameras.filter((c) => c.state !== "running").length + o.components.filter((c) => !c.running && c.restarts).length;
    const level = open.length ? "alert" : problems ? "warn" : "ok";
    const rooms = Object.entries(Object.groupBy ? Object.groupBy(o.cameras, (c) => c.room || "") : { "": o.cameras });
    const count = Object.values(o.last_24h).reduce((a, b) => a + b, 0);
    const tile = (label, value, hint, iconName) => h("div", { class: "tile" }, icon(iconName), h("div", {}, h("div", { class: "tile-value" }, value), h("small", {}, label), hint ? h("small", { class: "muted block" }, hint) : null));
    fill(main,
      h("section", { class: `hero ${level}` },
        h("div", { class: "hero-text" },
          h("div", { class: "hero-state" }, icon(level === "ok" ? "shield-check" : "alert-triangle", "lg"), t(`web.hero_${level}`, { count: open.length })),
          h("h1", {}, t(`mode.${mode.effective.toLowerCase()}`)),
          h("p", { class: "muted" }, mode.selection === "AUTO" ? t("web.mode_auto_note")
            : mode.until ? t("web.mode_until", { time: when(mode.until) }) : t("web.mode_manual_note"),
          mode.presence_enabled ? ` · ${t(`presence.${mode.presence.toLowerCase()}`)}` : "")),
        h("div", { class: "hero-actions" }, modeButtons,
          open.length ? h("button", { class: "primary", onclick: async () => {
            await attempt(() => api("/api/alerts/ack", { method: "POST" }), t("web.saved")); draw();
          } }, icon("circle-check"), t("web.ack_all")) : null)),
      h("div", { class: "tiles" },
        tile(t("web.cameras"), `${o.cameras.filter((c) => c.state === "running").length} / ${o.cameras.length}`, null, "camera"),
        tile(t("web.last_24h"), count, null, "bell"),
        tile(t("web.storage"), `${sys.disk_free_gb} GB`, t("web.free"), "database"),
        tile(t("web.load"), `${sys.load}`, sys.temperature != null ? `${Math.round(sys.temperature)} °C` : null, "cpu")),
      open.length ? h("section", { class: "panel" }, h("h2", {}, icon("alert-triangle"), " ", t("web.open_alerts"), ` (${open.length})`),
        h("ul", { class: "list" }, open.slice(-5).reverse().map((a) => eventRow({ type: a.type, ts: a.ts, camera: a.camera, data: { snapshot: a.snapshot } }, cameras))),
        o.emergency_contacts ? h("p", { class: "muted" }, h("small", {}, t("web.open_alerts_note", { count: o.emergency_after_alerts ?? "", minutes: o.emergency_after_minutes ?? "" }))) : null) : null,
      h("div", { class: "columns" },
        h("div", { class: "stack" },
          h("section", { class: "panel" }, h("h2", {}, icon("video"), " ", t("web.cameras")),
            o.cameras.length ? rooms.map(([room, list]) => [room ? h("h3", {}, room) : null, h("ul", { class: "list" }, list.map((c) => h("li", {},
              stateDot(c.state),
              h("div", { class: "grow" }, h("a", { href: `#/live/${c.id}` }, c.name),
                h("div", {}, h("small", {}, `${t(`camera.state.${c.state}`)} · ${c.resolution}`)), c.error ? h("small", { class: "muted" }, c.error) : null),
              c.recording ? h("span", { class: "badge accent" }, "REC") : null)))]) : h("p", { class: "empty" }, t("web.no_cameras"))),
          h("section", { class: "panel" },
            h("div", { class: "row between" }, h("h2", {}, icon("bell"), " ", t("web.recent_events")), h("a", { href: "#/events" }, t("web.all"))),
            h("ul", { class: "list" }, events.length ? events.map((e) => eventRow(e, cameras)) : h("li", { class: "empty" }, t("web.nothing"))))),
        h("div", { class: "stack" },
          await connectionPanel(),
          h("section", { class: "panel" }, h("h2", {}, icon("cpu"), " ", t("web.system")),
            h("dl", { class: "kv" }, h("dt", {}, t("web.storage")), h("dd", {}, `${sys.disk_used_percent} %`)), meter(sys.disk_used_percent),
            h("dl", { class: "kv" }, h("dt", {}, t("web.memory")), h("dd", {}, `${sys.memory_used_percent} %`)), meter(sys.memory_used_percent),
            h("p", { class: "muted" }, h("small", {}, `v${o.version} · ${t("web.uptime")} ${duration(o.uptime)}`)),
            o.components.filter((c) => !c.running && c.restarts).map((c) => h("p", { class: "notice danger" }, `${c.name}: ${c.error || t("web.not_running")}`))))),
      topology);
  };
  await draw();
  const timer = setInterval(() => draw().catch(() => {}), 15000);
  return () => clearInterval(timer);
}
