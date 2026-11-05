// The ways home from outside. Only a device that connected from outside proves one works.
import { api, attempt, fill, h, icon, t } from "../api.js";

const ICONS = { ipv4: "world", ipv6: "network", relay: "router", tailscale: "plug-connected" };
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
