// .env settings the dashboard may change, grouped, with a restart prompt.
import { api, attempt, confirmDialog, h, icon, lang, t, fill } from "../api.js";
import { switchLanguage } from "../app.js";

export async function restartPrompt() {
  if (await confirmDialog(t("web.restart_confirm"), t("web.restart"))) {
    await attempt(() => api("/api/system/restart", { method: "POST" }), t("web.restarting"));
    setTimeout(() => location.reload(), 8000);
  }
}

const tip = (key) => (t(key) === key ? null : t(key));

function listEditor(field, values, id) {
  // One value per line: numbers and IDs are easier to check and change than a comma list.
  const rows = h("div", { class: "stack tight" });
  const box = h("div", { id, class: "list-edit" });
  const changed = () => box.dispatchEvent(new Event("input", { bubbles: true }));
  const add = (text = "") => {
    const input = h("input", { value: text, "aria-label": t(`web.setting_${field.key}`) });
    const row = h("div", { class: "list-row" }, input,
      h("button", { class: "icon-only danger", type: "button", title: t("web.list_remove"), onclick: () => { row.remove(); changed(); } }, icon("trash")));
    rows.append(row);
    return input;
  };
  (values || []).forEach((value) => add(String(value)));
  box.append(rows, h("button", { class: "ghost", type: "button", title: t("web.list_add_tip"), onclick: () => add().focus() }, icon("plus"), t("web.list_add")));
  box.values = () => [...rows.querySelectorAll("input")].map((input) => input.value.trim()).filter(Boolean);
  return box;
}

function control(field, value) {
  const id = `s_${field.key}`;
  if (field.kind === "list") return listEditor(field, value, id);
  if (field.kind === "bool") return h("input", { id, type: "checkbox", checked: Boolean(value) });
  if (field.kind === "secret") {
    return h("input", { id, type: "password", autocomplete: "off", placeholder: t("web.secret_placeholder") });
  }
  if (field.kind === "choice") return h("select", { id }, field.options.map((o) => h("option", { value: o, selected: o === value }, o)));
  if (field.kind === "int") return h("input", { id, type: "number", min: field.min, max: field.max, value, inputmode: "numeric" });
  return h("input", { id, value: Array.isArray(value) ? value.join(", ") : value ?? "" });
}

function read(field, el) {
  if (field.kind === "bool") return el.checked;
  if (field.kind === "int") return Number(el.value);
  if (field.kind === "list") return el.values();
  return el.value;
}

// opts.groups limits the groups shown; opts.onSaved replaces the restart prompt (setup assistant).
export async function render(main, params = [], opts = {}) {
  const data = await api("/api/settings");
  const inputs = new Map();
  const fields = data.fields.filter((f) => !opts.groups || opts.groups.includes(f.group));
  const groups = [...new Set(fields.map((f) => f.group))];
  const save = h("button", { class: "primary big", onclick: async () => {
    const changed = {};
    for (const field of fields) {
      const value = read(field, inputs.get(field.key));
      if (JSON.stringify(value) !== JSON.stringify(data.values[field.key])) changed[field.key] = value;
    }
    if (!Object.keys(changed).length) { opts.onSaved?.({}); return; }
    if (Object.values(changed).every((v) => v === "")) { opts.onSaved?.({}); return; }
    if (await attempt(() => api("/api/settings", { method: "PUT", body: changed }), t("web.saved"))) {
      if (opts.onSaved) { opts.onSaved(changed); return; }
      if (changed.language && changed.language !== lang) await switchLanguage(changed.language);
      restartPrompt();
    }
  } }, icon("check"), t("web.save"));
  opts.register?.(() => save.click());
  fill(main,
    opts.embedded ? null : h("div", { class: "topbar" }, h("h1", {}, t("web.nav_settings")), save),
    data.overrides.length && !opts.embedded ? h("p", { class: "notice" }, t("web.overrides_note")) : null,
    h("div", { class: "grid wide" }, groups.map((group) => h("section", { class: "panel" },
      h("h2", {}, t(`web.group_${group}`)),
      fields.filter((f) => f.group === group).map((field) => {
        const el = control(field, data.values[field.key]);
        inputs.set(field.key, el);
        return h("div", { class: "switch-row" },
          h("label", { for: el.id, title: tip(`web.tip_${field.key}`) }, t(`web.setting_${field.key}`)), h("div", { class: "control" }, el));
      })))),
    opts.embedded ? null : h("section", { class: "panel" }, h("h2", {}, t("web.secrets")),
      h("p", { class: "muted" }, t("web.secrets_note")),
      h("dl", { class: "kv" }, Object.entries(data.secrets).map(([key, set]) =>
        [h("dt", { class: "mono" }, `PI_GUARD_${key.toUpperCase()}`), h("dd", {}, h("span", { class: `badge ${set ? "ok" : ""}` }, set ? t("web.set") : t("web.not_set")))]))));
}
