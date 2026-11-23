// The assistant's review of the security check or the logs: its real stage while it runs, then
// the result as a rating, counts by priority and one card per step. Shared by scan.js and logs.js.
import { fill, h, icon, t, when } from "../api.js";

const STAGES = ["prepare", "ask", "check"];
const LEVEL_CLASS = { high: "danger", medium: "warn", low: "" };
const RATING = { good: ["ok", "shield-check"], fair: ["warn", "alert-triangle"], poor: ["danger", "alert-octagon"] };

/** Stage list with the stage the server reports; `seconds` counts while the AI is answering. */
export function stagePanel(state, title) {
  const at = STAGES.indexOf(state.stage);
  return h("div", { class: "scan-progress stack review-progress", "aria-live": "polite" },
    h("p", { class: "muted" }, title),
    h("ul", { class: "list scan-list scan-live" }, STAGES.map((id, i) => {
      const s = i < at ? "done" : i === at ? "current" : "pending";
      return h("li", { class: `scan-step ${s}` },
        s === "done" ? icon("circle-check") : s === "current" ? h("span", { class: "scan-spinner", "aria-hidden": "true" }) : icon("circle"),
        h("div", { class: "grow" }, t(`web.review_stage_${id}`)),
        s === "current" && id === "ask" && state.seconds != null ? h("span", { class: "badge" }, t("web.review_seconds", { n: state.seconds })) : null);
    })));
}

/** Header of a result: rating, when, and counts by level, each a coloured chip. */
export function resultHead(result, levels, { outdated = false } = {}) {
  const [cls, ic] = RATING[result.rating || result.health] || ["", "sparkles"];
  const counts = { high: 0, medium: 0, low: 0 };
  levels.forEach((l) => { counts[l] = (counts[l] || 0) + 1; });
  return h("div", { class: `review-head ${cls}` },
    h("div", { class: "review-rating" }, icon(ic), h("strong", {}, t(`web.review_rating_${result.rating || result.health || "none"}`))),
    h("p", { class: "review-summary" }, result.summary),
    h("div", { class: "row wrap review-counts" },
      ["high", "medium", "low"].map((l) => counts[l] ? h("span", { class: `badge ${LEVEL_CLASS[l]}` }, t(`web.review_level_${l}`), " ", String(counts[l])) : null),
      h("small", { class: "muted grow review-when" }, t("web.review_when", { time: when(result.time) })),
      outdated ? h("span", { class: "badge warn" }, icon("clock"), " ", t("web.review_outdated")) : null));
}

/** One card: a numbered, coloured edge by level, then the parts the caller passes. */
export function card(index, level, title, ...parts) {
  return h("li", { class: `review-card ${LEVEL_CLASS[level] || "low"}` },
    h("span", { class: "review-num", "aria-hidden": "true" }, String(index + 1)),
    h("div", { class: "grow stack-tight" },
      h("div", { class: "row wrap" }, h("strong", { class: "grow" }, title), h("span", { class: `badge ${LEVEL_CLASS[level]}` }, t(`web.review_level_${level}`))),
      ...parts));
}

/** Draws state + result into `box` and keeps looking while the server works. */
export function reviewBox(box, state, { title, working, render, start, startLabel, outdated }) {
  const parts = [];
  if (state.running) parts.push(stagePanel(state, working));
  else if (state.error) parts.push(h("p", { class: "notice danger" }, icon("alert-triangle"), " ", t(state.error)));
  if (!state.running && state.last) parts.push(h("div", { class: "review-result stack" }, render(state.last, outdated?.(state.last))));
  fill(box, h("div", { class: "row between wrap" }, h("h2", {}, icon("sparkles"), " ", title),
    h("button", { class: "primary", disabled: state.running, onclick: start }, icon(state.running ? "loader-2" : "sparkles"), state.last ? t("web.review_again") : startLabel)),
  ...parts);
}
