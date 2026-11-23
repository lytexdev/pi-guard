// Security scan: run it, read the result, optionally get the report in the messenger.
import { api, askDialog, attempt, confirmDialog, fill, h, icon, t, toast, when, whileShown } from "../api.js";
import { card, resultHead, reviewBox } from "./ai-review.js";

const BADGE = { bad: "danger", warn: "warn", ok: "ok", unknown: "" };
const STATUS_ICON = { ok: "circle-check", warn: "alert-triangle", bad: "alert-triangle", unknown: "help" };
let pendingSince = 0;  // a fix was started: look again until its result is there

export async function render(box) {
  let timer = null;
  const report = h("input", { type: "checkbox" });
  const draw = async () => {
    clearTimeout(timer);
    const s = await api("/api/scan");
    const last = s.last;
    const progress = s.progress;
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("web.set_security")), h("p", { class: "muted" }, t("web.scan_intro")),
      s.root_job ? null : h("p", { class: "notice" }, t("web.scan_no_job")),
      h("label", { class: "check", title: t("web.scan_report_tip") }, report, t("web.scan_report")),
      h("button", { class: "primary", disabled: s.running, onclick: async () => {
        if (await attempt(() => api("/api/scan", { method: "POST", body: { report: report.checked } }))) draw();
      } }, icon("shield-check"), s.running ? t("web.scan_running") : t("web.scan_start")),
      s.running ? progressPanel(progress) : null),
    last && !s.running ? resultPanel(last) : null,
    last ? fixesPanel(s, draw) : null,
    last && s.review ? reviewPanel(s, draw) : null);
    const waiting = pendingSince && !((s.fixes?.result?.time || 0) >= pendingSince);
    if (!waiting) pendingSince = 0;
    const reviewing = s.review_state?.running;
    if (s.running || waiting || reviewing) timer = whileShown(box, draw, s.running || reviewing ? 1000 : 3000);
  };
  await draw();
  return () => clearTimeout(timer);
}

function progressPanel(progress) {
  const ids = progress?.checks || [];
  const done = new Set(progress?.done || []);
  const current = new Set([].concat(progress?.current || []));
  const total = ids.length || 1;
  const pct = Math.min(100, Math.round((done.size / total) * 100));
  const fillBar = h("i", { class: "scan-bar-fill" });
  fillBar.style.width = `${pct}%`;
  return h("div", { class: "scan-progress stack", "aria-live": "polite" },
    h("p", { class: "muted" }, t("web.scan_wait")),
    h("div", { class: "scan-bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(pct) }, fillBar),
    h("ul", { class: "list scan-list scan-live" }, ids.map((id) => {
      const state = done.has(id) ? "done" : current.has(id) ? "current" : "pending";
      return h("li", { class: `scan-step ${state}` },
        state === "done" ? icon("circle-check") : state === "current" ? h("span", { class: "scan-spinner", "aria-hidden": "true" }) : icon("circle"),
        h("div", { class: "grow" }, t(`web.scan_${id}`)),
        state === "current" ? h("span", { class: "badge" }, t("web.scan_checking")) : null);
    })));
}

function resultPanel(last) {
  const counts = last.counts || {};
  const total = Math.max(1, (counts.bad || 0) + (counts.warn || 0) + (counts.ok || 0) + (counts.unknown || 0));
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.scan_result")),
    h("p", { class: "muted" }, t("web.scan_summary", { time: when(last.time), bad: counts.bad || 0, warn: counts.warn || 0, ok: counts.ok || 0 })),
    h("div", { class: "scan-summary-bar", role: "img", "aria-label": t("web.scan_summary", { time: when(last.time), bad: counts.bad || 0, warn: counts.warn || 0, ok: counts.ok || 0 }) },
      segment("bad", counts.bad, total), segment("warn", counts.warn, total),
      segment("ok", counts.ok, total), segment("unknown", counts.unknown, total)),
    h("ul", { class: "list scan-list" }, (last.checks || []).map((c) => h("li", { class: `scan-result ${c.status}` },
      icon(STATUS_ICON[c.status] || "help"),
      h("div", { class: "grow" }, h("div", {}, t(`web.scan_${c.id}`)),
        c.detail ? h("small", { class: "mono block" }, c.detail) : null,
        c.status === "ok" ? null : h("small", { class: "block" }, t(c.fix || `web.scan_${c.id}_fix`))),
      h("span", { class: `badge ${BADGE[c.status]}` }, t(`web.scan_state_${c.status}`))))));
}

function segment(kind, n, total) {
  if (!n) return null;
  const el = h("span", { class: `scan-seg ${kind}` });
  el.style.flexGrow = String(n);
  el.style.flexBasis = `${(n / total) * 100}%`;
  return el;
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

const PRIORITY_ORDER = { high: 0, medium: 1, low: 2 };

function reviewPanel(s, draw) {
  const box = h("section", { class: "panel stack review-panel" });
  const offered = new Set(s.fixes?.offered || []);
  const state = s.review_state || {};
  const start = async () => {
    if (!await confirmDialog(t("web.review_confirm"), t("web.review_start"))) return;
    if (await attempt(() => api("/api/scan/review", { method: "POST", body: {} }))) draw();
  };
  const render = (r, outdated) => {
    const steps = [...(r.steps || [])].sort((a, b) => PRIORITY_ORDER[a.priority] - PRIORITY_ORDER[b.priority]);
    return [resultHead(r, steps.map((x) => x.priority), { outdated }),
      steps.length ? h("ol", { class: "review-cards" }, steps.map((step, i) => card(i, step.priority, step.title,
        step.check ? h("small", { class: "muted" }, icon("shield-check"), " ", t(`web.scan_${step.check}`)) : null,
        step.why ? h("p", { class: "muted" }, step.why) : null,
        step.fix && offered.has(step.fix) ? h("div", {}, h("button", { class: "small primary", onclick: () => runFix(step.fix, false, draw) }, icon("shield-check"), t("web.fix_apply_named", { name: t(`web.fix_${step.fix}`) }))) : null,
        step.commands.length ? h("details", { class: "review-commands" }, h("summary", {}, icon("terminal-2"), " ", t("web.review_commands")),
          h("pre", { class: "mono" }, step.commands.join("\n")),
          h("button", { class: "small ghost", onclick: async () => { try { await navigator.clipboard.writeText(step.commands.join("\n")); toast(t("web.copied")); } catch { toast(t("web.error"), true); } } }, icon("copy"), t("web.copy"))) : null)))
        : h("p", { class: "notice ok" }, icon("circle-check"), " ", t("web.review_nothing")),
      h("p", { class: "muted" }, h("small", {}, t("web.review_note")))];
  };
  reviewBox(box, state, { title: t("web.review_title"), working: t("web.review_working"), render, start, startLabel: t("web.review_start"),
    outdated: (r) => Boolean(r.basis && s.last && r.basis !== s.last.time) });
  if (!state.running && !state.last) box.append(h("p", { class: "muted" }, t("web.review_hint")));
  return box;
}
