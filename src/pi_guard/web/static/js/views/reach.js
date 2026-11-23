// The ways home from outside. Only a device that connected from outside proves one works.
import { api, attempt, fill, h, icon, t, when } from "../api.js";
import { restartPrompt } from "./settings.js";

const ICONS = { ipv4: "world", ipv6: "network", relay: "router", tailscale: "plug-connected", awg: "eye-off" };
const BADGE = { ok: "ok", warn: "danger", off: "" };

function row(way) {
  return h("li", { class: "reach-way" },
    icon(ICONS[way.id], "lg"),
    h("div", { class: "grow" },
      h("div", { class: "row between" }, h("strong", {}, t(`web.reach_${way.id}`)),
        h("span", { class: `badge ${BADGE[way.state]}` }, t(`web.reach_state_${way.state}`))),
      way.host ? h("small", { class: "mono" }, way.host) : null,
      h("p", { class: "muted" }, t(way.key, way)),
      way.login ? h("p", {}, h("a", { class: "button", href: way.login, target: "_blank", rel: "noopener" }, t("web.reach_ts_login_button"))) : null,
      h("details", {}, h("summary", {}, t("web.reach_how")),
        h("p", { class: "muted" }, h("small", {}, t(`web.reach_how_${way.id}`))),
        h("code", {}, t(`web.reach_cmd_${way.id}`)))));
}

// Post-quantum keys cost a little memory and processor time, so they can be switched off.
export function quantumPanel() {
  const panel = h("section", { class: "panel stack" });
  const draw = async () => {
    const q = await attempt(() => api("/api/network/quantum"));
    if (!q) return;
    const toggle = h("input", { id: "pqon", type: "checkbox", checked: q.enabled });
    toggle.addEventListener("change", async () => {
      await attempt(() => api("/api/network/quantum", { method: "PUT", body: { enabled: toggle.checked } }), t("web.saved"));
      draw();
    });
    fill(panel, h("h2", {}, icon("shield-lock"), " ", t("web.pq_title")), h("p", { class: "muted" }, t("web.pq_intro")),
      h("label", { class: "check", for: "pqon" }, toggle, t("web.pq_toggle")),
      !q.installed ? h("p", { class: "notice" }, t("web.pq_not_installed")) : h("small", { class: "muted" }, t("web.pq_active", { count: q.active })),
      !q.installed ? h("pre", { class: "mono wrap" }, "sudo ./scripts/vpn-server.sh install") : null);
  };
  draw();
  return panel;
}

export function reachPanel() {
  const list = h("ul", { class: "list reach" });
  const note = h("p", { class: "muted", "aria-live": "polite" });
  const draw = (data) => {
    fill(list, data.ways.map(row));
    fill(note, data.public ? h("small", {}, t("web.reach_public", { address: data.public })) : "");
  };
  const check = h("button", { type: "button", onclick: async () => {
    check.disabled = true;
    const data = await attempt(() => api("/api/network/reach?check=1"));
    check.disabled = false;
    if (data) draw(data);
  } }, icon("refresh"), t("web.reach_check"));
  attempt(() => api("/api/network/reach")).then((data) => { if (data) draw(data); });
  return h("section", { class: "panel" }, h("h2", {}, icon("world"), " ", t("web.reach_title")),
    h("p", { class: "muted" }, t("web.reach_intro")), list, note,
    h("div", { class: "row" }, check),
    h("p", { class: "muted" }, h("small", {}, t("web.reach_check_hint"))));
}

// A name that follows the home's public address (Cloudflare), for the VPN and linked households.
export function ddnsPanel() {
  const panel = h("div", { class: "stack" });  // inside a folded panel that has the title
  const draw = async () => {
    const d = await attempt(() => api("/api/network/ddns"));
    if (!d) return;
    const on = h("input", { type: "checkbox", class: "toggle", role: "switch", id: "ddns_on", checked: d.provider === "cloudflare" });
    const host = h("input", { id: "ddns_host", value: d.hostname, placeholder: "home.example.org", autocomplete: "off", spellcheck: false });
    const token = h("input", { id: "ddns_token", type: "password", autocomplete: "off", placeholder: d.token ? t("web.ddns_token_kept") : "" });
    const v6 = h("input", { type: "checkbox", id: "ddns_v6", checked: d.ipv6 });
    const s = d.status || {};
    const save = async () => {
      const body = { ddns_provider: on.checked ? "cloudflare" : "off", ddns_hostname: host.value.trim(), ddns_ipv6: v6.checked };
      if (token.value) body.ddns_cloudflare_token = token.value;
      if (await attempt(() => api("/api/settings", { method: "PUT", body }), t("web.saved"))) { restartPrompt(); draw(); }
    };
    const update = async () => {
      const r = await attempt(() => api("/api/network/ddns/update", { method: "POST" }), t("web.ddns_updated"));
      if (r) draw();
    };
    const useName = d.provider === "cloudflare" && d.hostname && d.endpoint && d.endpoint.split(":")[0] !== d.hostname
      ? h("p", { class: "notice" }, t("web.ddns_endpoint_other", { endpoint: d.endpoint }), " ",
        h("button", { class: "small", onclick: async () => {
          if (await attempt(() => api("/api/settings", { method: "PUT", body: { wireguard_endpoint: d.hostname } }), t("web.saved"))) { restartPrompt(); draw(); }
        } }, t("web.ddns_use_name"))) : null;
    fill(panel, h("p", { class: "muted" }, t("web.ddns_intro")),
      h("label", { class: "row", for: "ddns_on" }, on, h("span", { class: "grow" }, t("web.ddns_on"))),
      h("div", { class: "field" }, h("label", { for: "ddns_host" }, t("web.ddns_hostname")), host),
      h("div", { class: "field" }, h("label", { for: "ddns_token" }, t("web.ddns_token")), token,
        h("small", { class: "muted" }, t("web.ddns_token_hint"))),
      h("label", { class: "check", for: "ddns_v6" }, v6, t("web.ddns_ipv6")),
      h("div", { class: "row wrap" }, h("button", { class: "primary", onclick: save }, icon("check"), t("web.save")),
        d.provider === "cloudflare" ? h("button", { onclick: update }, icon("refresh"), t("web.ddns_update_now")) : null),
      s.time ? h("p", { class: s.ok ? "muted" : "notice danger" }, s.ok
        ? t("web.ddns_status_ok", { host: s.hostname, ipv4: s.ipv4 || "–", ipv6: s.ipv6 || "–", time: when(s.time) })
        : `${t(s.error || "web.error")}${s.detail ? ` (${s.detail})` : ""} · ${when(s.time)}`) : null,
      useName,
      h("p", { class: "muted" }, h("small", {}, t("web.ddns_privacy"))));
  };
  draw();
  return panel;
}

// How to make the Pi reachable with the router at hand. The VPN works with every router: what it
// needs is a forwarded UDP port, a fixed address for the Pi, and a name for the home's address.
// Each router keeps these in other menus; the steps are in the texts web.rg_<router>_*.
export const ROUTERS = ["fritzbox", "vodafone_station", "easybox", "speedport", "o2_homebox", "other"];
const ROUTER_STEPS = ["fixed", "forward", "name"];

export function routerGuide(port = 51820) {
  const body = h("div", { class: "stack" });
  const pick = h("select", { "aria-label": t("web.rg_pick") }, ROUTERS.map((id) => h("option", { value: id }, t(`web.rg_${id}`))));
  const draw = () => {
    const id = pick.value;
    fill(body, h("ol", { class: "guide-steps stack" }, ROUTER_STEPS.map((step) => h("li", {},
      h("strong", {}, t(`web.rg_step_${step}`)), h("p", { class: "muted" }, t(`web.rg_${id}_${step}`, { port }))))),
    h("p", { class: "notice" }, icon("alert-triangle"), " ", t("web.rg_cgnat")));
  };
  pick.addEventListener("change", draw);
  draw();
  return h("div", { class: "stack router-guide" }, h("p", { class: "muted" }, t("web.rg_intro")),
    h("label", { class: "field" }, t("web.rg_pick"), pick), body);
}
