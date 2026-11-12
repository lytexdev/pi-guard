// Security scan: run it, read the result, optionally get the report in the messenger.
import { api, askDialog, attempt, confirmDialog, fill, h, icon, t, toast, when, whileShown } from "../api.js";

const BADGE = { bad: "danger", warn: "warn", ok: "ok", unknown: "" };
let pendingSince = 0;  // a fix was started: look again until its result is there

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
          c.status === "ok" ? null : h("small", { class: "block" }, t(c.fix || `web.scan_${c.id}_fix`))),
        h("span", { class: `badge ${BADGE[c.status]}` }, t(`web.scan_state_${c.status}`)))))) : null,
    last ? fixesPanel(s, draw) : null,
    last && s.review ? reviewPanel(s, draw) : null);
    const waiting = pendingSince && !((s.fixes?.result?.time || 0) >= pendingSince);
    if (!waiting) pendingSince = 0;
    if (s.running || waiting) timer = whileShown(box, draw, 3000);
  };
  await draw();
  return () => clearTimeout(timer);
}

// A fix from the fixed list: confirmed twice (what it does, then its name typed) and the password.
async function runFix(id, undo, draw) {
  const name = t(`web.fix_${id}`);
  if (!await confirmDialog(t(undo ? "web.fix_undo_confirm" : "web.fix_confirm", { name, what: t(`web.fix_${id}_what`) }), t(undo ? "web.fix_undo" : "web.fix_apply"), true)) return;
  const body = { id, undo };
  if (!undo) {
    const typed = await askDialog(t("web.fix_type", { id }));
    if (typed == null) return;
    body.confirm = typed;
  }
  body.password = await askDialog(t("web.confirm_password"), { type: "password" });
  if (!body.password) return;
  if (await attempt(() => api("/api/scan/fix", { method: "POST", body }), t("web.fix_started"))) {
    pendingSince = Math.floor(Date.now() / 1000) - 5;
    draw();
  }
}

function fixesPanel(s, draw) {
  const { offered = [], applied = {}, result } = s.fixes || {};
  const done = Object.keys(applied);
  if (!offered.length && !done.length && !result) return null;
  return h("section", { class: "panel stack" }, h("h2", {}, icon("shield-check"), " ", t("web.fix_title")), h("p", { class: "muted" }, t("web.fix_hint")),
    result ? h("p", { class: `notice ${result.ok ? "" : "danger"}` }, t(result.ok ? (result.undo ? "web.fix_undone" : "web.fix_done") : "web.fix_failed", { name: t(`web.fix_${result.id}`), why: result.message || "" }), " ", h("small", { class: "muted" }, when(new Date(result.time * 1000).toISOString()))) : null,
    h("ul", { class: "list" },
      offered.filter((id) => !applied[id]).map((id) => h("li", {}, icon("refresh"), h("div", { class: "grow" }, h("div", {}, t(`web.fix_${id}`)), h("small", { class: "muted" }, t(`web.fix_${id}_what`))),
        h("button", { class: "small", onclick: () => runFix(id, false, draw) }, t("web.fix_apply")))),
      done.map((id) => h("li", {}, icon("circle-check"), h("div", { class: "grow" }, h("div", {}, t(`web.fix_${id}`)), h("small", { class: "muted" }, t("web.fix_in_place", { time: when(new Date(applied[id].applied * 1000).toISOString()) }))),
        h("button", { class: "small ghost", onclick: () => runFix(id, true, draw) }, icon("arrow-back-up"), t("web.fix_undo"))))));
}

function reviewPanel(s, draw) {
  const out = h("div", { class: "stack" });
  const offered = new Set(s.fixes?.offered || []);
  const ask = async () => {
    if (!await confirmDialog(t("web.review_confirm"), t("web.review_start"))) return;
    fill(out, h("p", { class: "muted" }, t("web.ai_thinking")));
    const r = await attempt(() => api("/api/scan/review", { method: "POST", body: {} }));
    if (!r) { fill(out); return; }
    fill(out, h("p", {}, r.summary),
      r.steps.length ? h("ol", { class: "review-steps" }, r.steps.map((step) => h("li", {},
        h("strong", {}, step.title), step.why ? h("p", { class: "muted" }, step.why) : null,
        step.fix && offered.has(step.fix) ? h("button", { class: "small primary", onclick: () => runFix(step.fix, false, draw) }, icon("shield-check"), t("web.fix_apply_named", { name: t(`web.fix_${step.fix}`) })) : null,
        step.commands.length ? h("div", { class: "review-commands" }, h("small", { class: "muted" }, t("web.review_commands")),
          h("pre", { class: "mono" }, step.commands.join("\n")),
          h("button", { class: "small ghost", onclick: async () => { try { await navigator.clipboard.writeText(step.commands.join("\n")); toast(t("web.copied")); } catch { toast(t("web.error"), true); } } }, icon("copy"), t("web.copy"))) : null))) : h("p", {}, icon("circle-check"), " ", t("web.review_nothing")),
      h("p", { class: "muted" }, h("small", {}, t("web.review_note"))));
  };
  return h("section", { class: "panel stack" }, h("h2", {}, icon("sparkles"), " ", t("web.review_title")), h("p", { class: "muted" }, t("web.review_hint")),
    h("div", { class: "row" }, h("button", { onclick: ask }, icon("sparkles"), t("web.review_start"))), out);
}
