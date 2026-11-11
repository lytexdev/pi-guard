// Ask about settings, and every change with the value before and after, to undo it.
import { api, attempt, confirmDialog, fill, h, icon, t, when } from "../api.js";
import { restartPrompt } from "./settings.js";

const shownValue = (value) => (value === true ? t("web.sh_on") : value === false ? t("web.sh_off") : value == null ? t("web.sh_default") : value === "" ? "—" : String(value));
const label = (key) => (t(`web.setting_${key}`) === `web.setting_${key}` ? key : t(`web.setting_${key}`));

export function helpPanel(box, onChanged) {
  const input = h("input", { class: "grow", type: "search", placeholder: t("web.sh_placeholder"), "aria-label": t("web.sh_title"), maxlength: 500 });
  const out = h("div", { class: "stack tight", "aria-live": "polite" });
  const ask = async () => {
    const question = input.value.trim();
    if (!question) return;
    fill(out, h("p", { class: "muted" }, t("web.sh_thinking")));
    let data;
    try {
      data = await api("/api/settings/ask", { method: "POST", body: { question } });
    } catch (error) {
      fill(out, h("p", { class: "notice danger" }, t(error.message || "web.error")));
      return;
    }
    if (!out.isConnected) return;
    const p = data.proposal;
    fill(out,
      data.answer ? h("p", { class: "pre-line" }, data.answer) : h("p", { class: "muted" }, t(data.found.length ? "web.sh_local" : "web.sh_nothing")),
      p ? h("div", { class: "notice stack tight" },
        h("strong", {}, t("web.sh_proposal", { name: p.label })),
        h("span", {}, `${shownValue(p.before)} → ${shownValue(p.after)}`),
        h("div", { class: "row" }, h("button", { class: "primary", onclick: async () => {
          if (!await confirmDialog(t("web.sh_confirm", { name: p.label, before: shownValue(p.before), after: shownValue(p.after) }), t("web.sh_apply"))) return;
          if (await attempt(() => api("/api/settings?source=assistant", { method: "PUT", body: { [p.key]: p.after } }), t("web.saved"))) {
            fill(out, h("p", {}, t("web.sh_applied")));
            onChanged?.();
            restartPrompt();
          }
        } }, icon("check"), t("web.sh_apply")))) : null,
      data.found.length ? h("ul", { class: "list" }, data.found.map((f) => h("li", { class: "history-row" },
        h("span", {}, h("strong", {}, f.label), "value" in f ? h("span", { class: "muted" }, ` · ${shownValue(f.value)}`) : null),
        f.tip ? h("small", { class: "muted" }, f.tip) : null))) : null);
  };
  input.addEventListener("keydown", (event) => { if (event.key === "Enter") ask(); });
  fill(box, h("section", { class: "panel stack" }, h("h2", {}, icon("message"), " ", t("web.sh_title")),
    h("p", { class: "muted" }, t("web.sh_hint")),
    h("div", { class: "row" }, input, h("button", { class: "primary", onclick: ask }, icon("search"), t("web.sh_ask"))), out));
}

export async function historyPanel(box) {
  const draw = async () => {
    const rows = await api("/api/settings/history").catch(() => []);
    if (!box.isConnected) return;
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, icon("refresh"), " ", t("web.sh_history")),
      rows.length ? h("ul", { class: "list" }, rows.slice(0, 15).map((row) => {
        const changes = Object.entries(row.changes);
        const undoable = changes.some(([, change]) => change);
        return h("li", { class: "history-row" },
          h("div", { class: "row" }, h("small", { class: "grow muted" }, `${when(row.time)} · ${row.actor.replace(/^web:/, "")}`,
            row.source !== "dashboard" ? [" · ", h("span", { class: "badge" }, t(`web.sh_source_${row.source}`))] : null),
          undoable ? h("button", { onclick: async () => {
            if (!await confirmDialog(t("web.sh_undo_confirm"), t("web.sh_undo"))) return;
            if (await attempt(() => api(`/api/settings/history/${row.id}/revert`, { method: "POST" }), t("web.saved"))) { draw(); restartPrompt(); }
          } }, icon("refresh"), t("web.sh_undo")) : null),
          changes.map(([key, change]) => h("small", {}, h("strong", {}, label(key)), ": ",
            change ? `${shownValue(change[0])} → ${shownValue(change[1])}` : t("web.sh_secret"))));
      })) : h("p", { class: "muted" }, t("web.sh_no_history"))));
  };
  await draw();
  return draw;
}
