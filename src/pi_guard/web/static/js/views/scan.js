// Security scan: run it, read the result, optionally get the report in the messenger.
import { api, attempt, fill, h, icon, t, when, whileShown } from "../api.js";

const BADGE = { bad: "danger", warn: "warn", ok: "ok", unknown: "" };

export async function render(box) {
  let timer = null;
  const report = h("input", { type: "checkbox" });
  const draw = async () => {
    clearTimeout(timer);
    const s = await api("/api/scan");
    const last = s.last;
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("web.set_security")), h("p", { class: "muted" }, t("web.scan_intro")),
      s.root_job ? null : h("p", { class: "notice" }, t("web.scan_no_job")),
      h("label", { class: "check", title: t("web.scan_report_tip") }, report, t("web.scan_report")),
      h("button", { class: "primary", disabled: s.running, onclick: async () => {
        if (await attempt(() => api("/api/scan", { method: "POST", body: { report: report.checked } }))) draw();
      } }, icon("shield-check"), s.running ? t("web.scan_running") : t("web.scan_start")),
      s.running ? h("p", { class: "muted", "aria-live": "polite" }, t("web.scan_wait")) : null),
    last ? h("section", { class: "panel stack" }, h("h2", {}, t("web.scan_result")),
      h("p", { class: "muted" }, t("web.scan_summary", { time: when(last.time), bad: last.counts.bad, warn: last.counts.warn, ok: last.counts.ok })),
      h("ul", { class: "list scan-list" }, last.checks.map((c) => h("li", {},
        icon(c.status === "ok" ? "circle-check" : "alert-triangle"),
        h("div", { class: "grow" }, h("div", {}, t(`web.scan_${c.id}`)),
          c.detail ? h("small", { class: "mono block" }, c.detail) : null,
          c.status === "ok" ? null : h("small", { class: "block" }, t(`web.scan_${c.id}_fix`))),
        h("span", { class: `badge ${BADGE[c.status]}` }, t(`web.scan_state_${c.status}`)))))) : null);
    if (s.running) timer = whileShown(box, draw, 3000);
  };
  await draw();
  return () => clearTimeout(timer);
}
