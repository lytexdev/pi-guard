// Recent service logs and the audit trail.
import { api, h, icon, t, when, fill } from "../api.js";
import { supportNote } from "../widgets.js";

function securityPanels(s) {
  const tile = (key, value, bad) => h("div", { class: `tile ${bad && value ? "bad" : ""}` }, h("div", {}, h("div", { class: "tile-value" }, value), h("small", {}, t(`web.sec_${key}`))));
  const c = s.counts;
  return [
    h("section", { class: "panel" }, h("h2", {}, icon("shield-check"), " ", t("web.sec_title", { days: s.days })),
      h("div", { class: "tiles" }, tile("logins", c.logins), tile("failed_logins", c.failed_logins, true), tile("honeypot_hits", c.honeypot_hits, true),
        tile("bans", c.bans, true), tile("bad_hosts", c.bad_hosts, true), tile("rejected_messages", c.rejected_messages, true), tile("denied_commands", c.denied_commands, true)),
      s.recommendations.map((r) => h("p", { class: "notice row between" }, h("span", {}, t(r.key)), h("a", { class: "button", href: r.href }, t("web.sec_fix"))))),
    h("div", { class: "grid wide" },
      h("section", { class: "panel" }, h("h2", {}, t("web.sec_sources")), h("ul", { class: "list" }, s.top_sources.length ? s.top_sources.map((x) => h("li", {}, h("span", { class: "grow mono" }, x.ip), h("span", { class: "badge" }, x.count))) : h("li", { class: "empty" }, t("web.nothing")))),
      h("section", { class: "panel" }, h("h2", {}, t("web.sec_honeypots")), h("ul", { class: "list" }, s.honeypot_paths.length ? s.honeypot_paths.map((x) => h("li", {}, h("span", { class: "grow mono" }, x.path), h("span", { class: "badge" }, x.count))) : h("li", { class: "empty" }, t("web.nothing")))),
      h("section", { class: "panel" }, h("h2", {}, t("web.sec_new_devices")), h("ul", { class: "list" }, s.new_devices.length ? s.new_devices.map((x) => h("li", {}, h("div", { class: "grow" }, h("div", { class: "mono" }, x.ip || x.mac), h("small", {}, `${x.mac} · ${when(x.first_seen)}`)))) : h("li", { class: "empty" }, t("web.nothing")))),
      h("section", { class: "panel" }, h("h2", {}, t("web.sec_network")), h("dl", { class: "kv" },
        h("dt", {}, t("web.sec_blocked")), h("dd", {}, s.percent_blocked == null ? "–" : `${s.percent_blocked} %`),
        h("dt", {}, t("web.sec_sessions")), h("dd", {}, s.sessions),
        h("dt", {}, t("web.sec_active_bans")), h("dd", {}, Object.keys(s.active_bans).length)))),
    h("section", { class: "panel" }, h("h2", {}, t("web.sec_timeline")), h("ul", { class: "list" }, s.timeline.length ? s.timeline.map((r) => h("li", {},
      h("div", { class: "grow" }, h("div", {}, r.action), h("small", { class: "mono" }, `${r.actor} ${r.detail}`)), h("small", {}, when(r.ts)))) : h("li", { class: "empty" }, t("web.nothing")))),
  ];
}

export async function render(main) {
  const level = h("select", { id: "level" }, ["", "INFO", "WARNING", "ERROR"].map((l) => h("option", { value: l }, l || t("web.all"))));
  const logBox = h("div", { class: "log" });
  const auditBody = h("tbody");
  const draw = async () => {
    const [records, audit] = await Promise.all([api(`/api/logs?level=${level.value}&limit=300`), api("/api/audit")]);
    fill(logBox, ...records.map((r) => h("div", { class: r.level }, `${when(r.ts)} ${r.level.padEnd(7)} ${r.logger}: ${r.message}`)));
    fill(auditBody, ...audit.map((a) => h("tr", {}, h("td", {}, when(a.ts)), h("td", { class: "mono" }, a.actor), h("td", {}, a.action), h("td", { class: "mono" }, a.detail))));
  };
  level.addEventListener("change", draw);
  const security = await api("/api/security").catch(() => null);
  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_logs")), level),
    security ? securityPanels(security) : null,
    h("section", { class: "panel" }, h("h2", {}, t("web.service_log")), logBox),
    h("section", { class: "panel" }, h("h2", {}, t("web.audit")),
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, t("web.time")), h("th", {}, t("web.actor")), h("th", {}, t("web.action")), h("th", {}, t("web.detail")))), auditBody))),
    supportNote());
  await draw();
  const timer = setInterval(() => draw().catch(() => {}), 10000);
  return () => clearInterval(timer);
}
