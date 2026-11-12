// .env settings the dashboard may change, grouped, with a restart prompt.
import { api, attempt, confirmDialog, h, icon, lang, t, fill, expertMode } from "../api.js";
import { state, switchLanguage } from "../app.js";

export async function restartPrompt() {
  if (await confirmDialog(t("web.restart_confirm"), t("web.restart"))) {
    await attempt(() => api("/api/system/restart", { method: "POST" }), t("web.restarting"));
    setTimeout(() => location.reload(), 8000);
  }
}

const tip = (key) => (t(key) === key ? null : t(key));
const GROUP_ICONS = { telegram: "brand-telegram", mobile: "device-mobile", discord: "brand-discord", signal: "brand-signal", whatsapp: "brand-whatsapp", email: "mail" };
const FIELD_ICONS = [["mqtt_", "brand-mqtt"], ["pihole_", "brand-pihole"], ["wireguard_", "brand-wireguard"], ["assistant_deepl", "brand-deepl"],
  ["assistant_paperless", "brand-paperless"], ["assistant_nextcloud", "brand-nextcloud"]];
const fieldIcon = (key) => FIELD_ICONS.find(([prefix]) => key.startsWith(prefix))?.[1];

function optionLabel(field, value) {
  if (field.key === "language") return new Intl.DisplayNames([lang], { type: "language" }).of(value) || value;
  const key = `web.opt_${field.key}_${value}`;
  return t(key) === key ? value : t(key);
}

function multiChoice(field, values, id) {
  // All possible options, ticked. "none" only shows up if it is in use: it switches off the login.
  const options = field.options.filter((o) => o !== "none" || values.includes("none"));
  const inputs = options.map((option) => h("input", { type: "checkbox", value: option, checked: values.includes(option) }));
  const box = h("div", { id, class: "multi-choice" }, inputs.map((input, index) => h("label", { class: "check", title: tip(`web.tip_${field.key}_${options[index]}`) }, input, optionLabel(field, options[index]))));
  box.values = () => inputs.filter((input) => input.checked).map((input) => input.value);
  return box;
}

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
  if (field.kind === "multi") return multiChoice(field, value || [], id);
  if (field.kind === "bool") return h("input", { id, type: "checkbox", class: "toggle", role: "switch", checked: Boolean(value) });
  if (field.kind === "secret") {
    return h("input", { id, type: "password", autocomplete: "off", placeholder: t("web.secret_placeholder") });
  }
  if (field.kind === "choice") return h("select", { id }, field.options.map((o) => h("option", { value: o, selected: o === value }, optionLabel(field, o))));
  if (field.kind === "int") return h("input", { id, type: "number", min: field.min, max: field.max, value, inputmode: "numeric" });
  return h("input", { id, value: Array.isArray(value) ? value.join(", ") : value ?? "" });
}

function read(field, el) {
  if (field.kind === "bool") return el.checked;
  if (field.kind === "int") return Number(el.value);
  if (field.kind === "list" || field.kind === "multi") return el.values();
  return el.value;
}

// opts.groups limits the groups shown; opts.onSaved replaces the restart prompt (setup assistant).
export async function render(main, params = [], opts = {}) {
  const data = await api("/api/settings");
  const inputs = new Map();
  const wanted = (group) => state.session?.modules?.[group] !== false;  // switched off under Add-ons
  const fields = data.fields.filter((f) => (!opts.groups || opts.groups.includes(f.group)) && wanted(f.group) && (!f.expert || expertMode()));
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
    h("div", { class: "grid wide" }, groups.map((group) => h("section", { class: "panel", dataset: { foldDefault: data.values[`${group}_enabled`] === false ? "closed" : "open" } },
      h("h2", {}, GROUP_ICONS[group] ? [icon(GROUP_ICONS[group]), " "] : null, t(`web.group_${group}`)),
      fields.filter((f) => f.group === group).map((field) => {
        const el = control(field, data.values[field.key]);
        inputs.set(field.key, el);
        // The explanation stands under the name, where it is read, not only in a tooltip.
        const hint = tip(`web.tip_${field.key}`);
        if (hint) el.setAttribute("aria-describedby", `${el.id}_hint`);
        return h("div", { class: `switch-row${field.kind === "bool" ? " is-bool" : ""}` },
          h("div", { class: "switch-label" }, h("label", { for: el.id }, fieldIcon(field.key) ? [icon(fieldIcon(field.key)), " "] : null, t(`web.setting_${field.key}`)),
            hint ? h("small", { class: "muted", id: `${el.id}_hint` }, hint) : null),
          h("div", { class: "control" }, el));
      })))),
    opts.embedded ? null : h("section", { class: "panel" }, h("h2", {}, t("web.secrets")),
      h("p", { class: "muted" }, t("web.secrets_note")),
      h("dl", { class: "kv" }, Object.entries(data.secrets).map(([key, set]) =>
        [h("dt", { class: "mono" }, `PI_GUARD_${key.toUpperCase()}`), h("dd", {}, h("span", { class: `badge ${set ? "ok" : ""}` }, set ? t("web.set") : t("web.not_set")))]))));
}
