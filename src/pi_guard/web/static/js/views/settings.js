// .env settings the dashboard may change, grouped, with a restart prompt.
import { api, attempt, confirmDialog, h, icon, lang, t, fill } from "../api.js";
import { switchLanguage } from "../app.js";

export async function restartPrompt() {
  if (await confirmDialog(t("web.restart_confirm"), t("web.restart"))) {
    await attempt(() => api("/api/system/restart", { method: "POST" }), t("web.restarting"));
    setTimeout(() => location.reload(), 8000);
  }
}

function control(field, value) {
  const id = `s_${field.key}`;
  if (field.kind === "bool") return h("input", { id, type: "checkbox", checked: Boolean(value) });
  if (field.kind === "choice") return h("select", { id }, field.options.map((o) => h("option", { value: o, selected: o === value }, o)));
  if (field.kind === "int") return h("input", { id, type: "number", min: field.min, max: field.max, value, inputmode: "numeric" });
  return h("input", { id, value: Array.isArray(value) ? value.join(", ") : value ?? "" });
}

function read(field, el) {
  if (field.kind === "bool") return el.checked;
  if (field.kind === "int") return Number(el.value);
  if (field.kind === "list") return el.value.split(",").map((s) => s.trim()).filter(Boolean);
  return el.value;
}

export async function render(main) {
  const data = await api("/api/settings");
  const inputs = new Map();
  const groups = [...new Set(data.fields.map((f) => f.group))];
  const save = h("button", { class: "primary big", onclick: async () => {
    const changed = {};
    for (const field of data.fields) {
      const value = read(field, inputs.get(field.key));
      if (JSON.stringify(value) !== JSON.stringify(data.values[field.key])) changed[field.key] = value;
    }
    if (!Object.keys(changed).length) return;
    if (await attempt(() => api("/api/settings", { method: "PUT", body: changed }), t("web.saved"))) {
      if (changed.language && changed.language !== lang) await switchLanguage(changed.language);
      restartPrompt();
    }
  } }, icon("check"), t("web.save"));
  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_settings")), save),
    data.overrides.length ? h("p", { class: "notice" }, t("web.overrides_note")) : null,
    h("div", { class: "grid wide" }, groups.map((group) => h("section", { class: "panel" },
      h("h2", {}, t(`web.group_${group}`)),
      data.fields.filter((f) => f.group === group).map((field) => {
        const el = control(field, data.values[field.key]);
        inputs.set(field.key, el);
        return h("div", { class: "switch-row" },
          h("label", { for: el.id }, t(`web.setting_${field.key}`)), h("div", { class: "control" }, el));
      })))),
    h("section", { class: "panel" }, h("h2", {}, t("web.secrets")),
      h("p", { class: "muted" }, t("web.secrets_note")),
      h("dl", { class: "kv" }, Object.entries(data.secrets).map(([key, set]) =>
        [h("dt", { class: "mono" }, `PI_GUARD_${key.toUpperCase()}`), h("dd", {}, h("span", { class: `badge ${set ? "ok" : ""}` }, set ? t("web.set") : t("web.not_set")))]))));
}
