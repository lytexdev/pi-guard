// Recent service logs and the audit trail.
import { api, h, t, when, fill } from "../api.js";

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
  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_logs")), level),
    h("section", { class: "panel" }, h("h2", {}, t("web.service_log")), logBox),
    h("section", { class: "panel" }, h("h2", {}, t("web.audit")),
      h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, h("th", {}, t("web.time")), h("th", {}, t("web.actor")), h("th", {}, t("web.action")), h("th", {}, t("web.detail")))), auditBody))));
  await draw();
  const timer = setInterval(() => draw().catch(() => {}), 10000);
  return () => clearInterval(timer);
}
