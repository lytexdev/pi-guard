// Emergency medical information. Every look, change, file and link asks for the password and
// goes into the audit log. While one sheet is open, the password typed to open it is kept in
// this dialog only, for its PDF, picture, BMP text and sending; closing the sheet forgets it.
import { askDialog, api, attempt, confirmDialog, fill, h, icon, t, toast, when } from "../api.js";
import { markdown } from "../markdown.js";

const BLOOD = ["", "0+", "0-", "A+", "A-", "B+", "B-", "AB+", "AB-"];
const DONOR = ["", "yes", "no", "some", "except", "person", "undecided"];
const DOSES = ["morning", "noon", "evening", "night"];
const ALERTS = ["allergies", "conditions", "implants"];
const MORE = ["medication_plan", "contacts", "doctor", "insurance", "notes"];

const password = () => askDialog(t("web.md_password"), { type: "password" });

function modal(cls, ...children) {
  const dialog = h("dialog", { class: cls }, ...children);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

async function saveFile(response, fallback) {
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || fallback;
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  h("a", { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// --- the form -----------------------------------------------------------------------------

function markdownField(key, value, rows) {
  const area = h("textarea", { id: `md_${key}`, rows }, value || "");
  const preview = h("div", { class: "md-preview hidden" });
  const toggle = h("button", { type: "button", class: "ghost small", onclick: () => {
    const show = preview.classList.contains("hidden");
    if (show) fill(preview, markdown(area.value));
    preview.classList.toggle("hidden", !show);
    area.classList.toggle("hidden", show);
    toggle.textContent = t(show ? "web.md_edit_text" : "web.md_preview");
  } }, t("web.md_preview"));
  const field = h("div", { class: "field" }, h("div", { class: "row between" }, h("label", { for: `md_${key}` }, t(`web.md_f_${key}`)), toggle), area, preview);
  return { field, value: () => area.value };
}

function choice(id, values, current, label) {
  return h("select", { id }, values.map((v) => h("option", { value: v, selected: v === (current || "") }, label(v))));
}

function rowEditor(kind, items, fields) {
  // A list of rows, one card each; meds and over-the-counter products share it.
  const list = h("div", { class: "med-list" });
  const rows = [];
  const add = (item = {}) => {
    const inputs = {};
    const remove = h("button", { type: "button", class: "icon-only danger", title: t("web.delete") }, icon("trash"));
    const card = h("div", { class: "med-row" },
      fields.map(([key, type, extra]) => {
        const id = `${kind}_${rows.length}_${key}`;
        inputs[key] = type === "select" ? choice(id, extra, item[key], (v) => t(`web.md_${key}_${v}`))
          : h("input", { id, value: item[key] || "", maxlength: extra || 80, inputmode: DOSES.includes(key) ? "decimal" : null, placeholder: DOSES.includes(key) ? "0" : "" });
        return h("div", { class: `field f-${key}` }, h("label", { for: id }, t(`web.md_m_${key}`)), inputs[key]);
      }), remove);
    const row = { card, inputs };
    remove.addEventListener("click", () => { rows.splice(rows.indexOf(row), 1); card.remove(); });
    rows.push(row);
    list.append(card);
  };
  (items || []).forEach(add);
  const values = () => rows.map((r) => Object.fromEntries(Object.entries(r.inputs).map(([k, el]) => [k, el.value.trim()]))).filter((r) => r.name || r.ingredient);
  return { list, add, values, clear: () => { rows.splice(0); list.replaceChildren(); } };
}

const MED_COLUMNS = [["name", "text", 80], ["strength", "text", 40], ["ingredient", "text", 80], ["form", "text", 40],
  ["morning", "text", 8], ["noon", "text", 8], ["evening", "text", 8], ["night", "text", 8], ["schedule", "text", 60], ["unit", "text", 30],
  ["kind", "select", ["regular", "as_needed"]], ["source", "select", ["doctor", "self"]], ["reason", "text", 120], ["note", "text", 200]];
const SUPPLEMENT_COLUMNS = [["name", "text", 80], ["dose", "text", 60], ["note", "text", 200]];

async function bmpDialog(onRead) {
  const text = h("textarea", { rows: 6, placeholder: "<MP v=\"026\" …>", "aria-label": t("web.md_bmp_text") });
  const file = h("input", { type: "file", accept: ".xml,.txt,text/xml,text/plain", "aria-label": t("web.md_bmp_file") });
  file.addEventListener("change", async () => { if (file.files[0]) text.value = await file.files[0].text(); });
  const dialog = modal("wg-dialog", h("h2", {}, t("web.md_bmp_title")), h("p", { class: "muted" }, t("web.md_bmp_intro")),
    h("div", { class: "stack" }, text, file),
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const plan = await attempt(() => api("/api/medical/bmp", { method: "POST", body: { text: text.value } }));
        if (plan) { dialog.close(); onRead(plan); }
      } }, icon("check"), t("web.md_bmp_read"))));
}

function medicalForm(record, accounts, onSave) {
  if (record.organ_donor && !DONOR.includes(record.organ_donor)) {  // free text from before the choice
    record = { ...record, organ_donor: "", organ_donor_note: [record.organ_donor, record.organ_donor_note].filter(Boolean).join(" ") };
  }
  const line = (key, type = "text", attrs = {}) => {
    const input = h("input", { id: `md_${key}`, type, value: record[key] || "", ...attrs });
    return { field: h("div", { class: "field" }, h("label", { for: `md_${key}` }, t(`web.md_f_${key}`)), input), value: () => input.value };
  };
  const select = (key, values, label) => {
    const input = choice(`md_${key}`, values, record[key], label);
    return { field: h("div", { class: "field" }, h("label", { for: `md_${key}` }, t(`web.md_f_${key}`)), input), value: () => input.value };
  };
  const f = {
    name: line("name", "text", { maxlength: 80, required: true }), birthday: line("birthday", "date"),
    phone: line("phone", "tel", { maxlength: 40 }), email: line("email", "email", { maxlength: 120 }),
    account: select("account", ["", ...accounts], (v) => v || t("web.md_no_account")),
    blood_type: select("blood_type", BLOOD, (v) => v || "–"),
    height_cm: line("height_cm", "text", { inputmode: "numeric", maxlength: 5 }), weight_kg: line("weight_kg", "text", { inputmode: "numeric", maxlength: 5 }),
    allergies: markdownField("allergies", record.allergies, 3), conditions: markdownField("conditions", record.conditions, 4),
    implants: markdownField("implants", record.implants, 2),
    medications: markdownField("medications", record.medications, 3), medication_plan: markdownField("medication_plan", record.medication_plan, 3),
    organ_donor: select("organ_donor", DONOR, (v) => (v ? t(`web.md_od_${v}`) : "–")), organ_donor_note: line("organ_donor_note", "text", { maxlength: 300 }),
    contacts: markdownField("contacts", record.contacts, 3), doctor: markdownField("doctor", record.doctor, 2),
    insurance: line("insurance", "text", { maxlength: 200 }), notes: markdownField("notes", record.notes, 4),
  };
  const meds = rowEditor("med", record.meds, MED_COLUMNS);
  const supplements = rowEditor("sup", record.supplements, SUPPLEMENT_COLUMNS);
  const readPlan = () => bmpDialog(async (plan) => {
    if (meds.values().length && await confirmDialog(t("web.md_bmp_replace"), t("web.md_bmp_replace_yes"))) meds.clear();
    plan.meds.forEach((m) => meds.add(m));
    (plan.supplements || []).forEach((s) => supplements.add(s));
    toast(t("web.md_bmp_done", { count: plan.meds.length + (plan.supplements || []).length }));
  });
  const section = (title, hint, ...fields) => h("fieldset", { class: "md-section" }, h("legend", {}, title), hint ? h("p", { class: "muted" }, h("small", {}, hint)) : null, ...fields);
  const dialog = modal("editor wide", h("h2", {}, record.name || t("web.md_new")),
    h("p", { class: "muted" }, t("web.md_form_intro")),
    h("p", { class: "muted" }, h("small", {}, t("web.md_markdown_hint"))),
    h("div", { class: "stack" },
      section(t("web.md_sec_person"), t("web.md_account_hint"), h("div", { class: "grid two" }, f.name.field, f.birthday.field, f.phone.field, f.email.field), f.account.field),
      section(t("web.md_sec_danger"), null, h("div", { class: "grid three" }, f.blood_type.field, f.height_cm.field, f.weight_kg.field), f.allergies.field, f.conditions.field, f.implants.field),
      section(t("web.md_f_meds"), t("web.md_meds_hint"), meds.list,
        h("div", { class: "row wrap" }, h("button", { type: "button", onclick: () => meds.add({ source: "doctor", kind: "regular" }) }, icon("plus"), t("web.md_add_med")),
          h("button", { type: "button", onclick: readPlan }, icon("qrcode"), t("web.md_bmp_title"))),
        f.medications.field, f.medication_plan.field),
      section(t("web.md_f_supplements"), t("web.md_supplements_hint"), supplements.list,
        h("div", {}, h("button", { type: "button", onclick: () => supplements.add() }, icon("plus"), t("web.md_add_supplement")))),
      section(t("web.md_f_organ_donor"), null, f.organ_donor.field, f.organ_donor_note.field),
      section(t("web.md_sec_more"), null, f.contacts.field, f.doctor.field, f.insurance.field, f.notes.field)),
    h("div", { class: "row end sticky-actions" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const values = Object.fromEntries(Object.entries(f).map(([key, field]) => [key, field.value()]));
        values.meds = meds.values();
        values.supplements = supplements.values();
        if (await onSave(values)) dialog.close();
      } }, icon("check"), t("web.save"))));
  return dialog;
}

// --- the sheet, read only -----------------------------------------------------------------

function medLine(m) {
  const name = [m.name, m.strength, m.form].filter(Boolean).join(" ") + (m.ingredient && m.ingredient !== m.name ? ` (${m.ingredient})` : "");
  const dose = m.schedule || (DOSES.some((k) => m[k]) ? DOSES.map((k) => m[k] || "0").join("-") : "");
  return h("tr", {}, h("td", {}, h("strong", {}, name), m.note ? h("small", { class: "muted block" }, m.note) : null),
    h("td", { class: "mono" }, [dose, m.unit].filter(Boolean).join(" ")),
    h("td", {}, m.reason || ""),
    h("td", {}, h("span", { class: `badge ${m.source === "self" ? "" : "ok"}` }, t(`web.md_source_${m.source || "doctor"}`)),
      m.kind === "as_needed" ? h("span", { class: "badge" }, t("web.md_kind_as_needed")) : null));
}

export function sheetView(record) {
  const age = record.birthday ? Math.floor((Date.now() - new Date(record.birthday)) / 31557600000) : null;
  const fact = (key, value) => (value ? [h("dt", {}, t(`web.md_f_${key}`)), h("dd", {}, value)] : null);
  const donor = [DONOR.includes(record.organ_donor || "") ? (record.organ_donor ? t(`web.md_od_${record.organ_donor}`) : "") : record.organ_donor, record.organ_donor_note || ""].filter(Boolean).join(" – ");
  return h("div", { class: "md-sheet" },
    ALERTS.filter((k) => record[k]).map((k) => h("div", { class: "md-alert" }, h("h3", {}, t(`web.md_f_${k}`)), markdown(record[k]))),
    record.meds?.length || record.medications ? h("div", { class: "md-alert" }, h("h3", {}, t("web.md_f_meds")),
      record.meds?.length ? h("div", { class: "table-wrap" }, h("table", { class: "med-table" },
        h("thead", {}, h("tr", {}, ["web.md_m_name", "web.md_dose", "web.md_m_reason", "web.md_m_source"].map((k) => h("th", {}, t(k))))),
        h("tbody", {}, record.meds.map(medLine)))) : null,
      record.medications ? markdown(record.medications) : null) : null,
    h("dl", { class: "kv" }, fact("birthday", record.birthday ? `${record.birthday}${age != null ? ` (${age})` : ""}` : ""), fact("blood_type", record.blood_type),
      fact("height_cm", record.height_cm), fact("weight_kg", record.weight_kg), fact("organ_donor", donor),
      fact("phone", record.phone), fact("email", record.email), fact("account", record.account)),
    record.supplements?.length ? h("section", {}, h("h3", {}, t("web.md_f_supplements")),
      h("ul", {}, record.supplements.map((s) => h("li", {}, h("strong", {}, s.name), s.dose ? ` · ${s.dose}` : "", s.note ? ` · ${s.note}` : "")))) : null,
    MORE.filter((k) => record[k]).map((k) => h("section", {}, h("h3", {}, t(`web.md_f_${k}`)), k === "insurance" ? h("p", {}, record[k]) : markdown(record[k]))));
}

// The sheet as a picture: for the phone's lock screen, a messenger, or the fridge.
async function picture(lines, name) {
  const width = 1080;
  const pad = 64;
  const style = { title: ["700 54px", "#111"], alert: ["700 36px", "#b42318"], h: ["700 34px", "#111"], kv: ["400 30px", "#222"], p: ["400 30px", "#222"], li: ["400 30px", "#222"] };
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  const layout = [];
  let y = pad;
  for (const [kind, text] of lines) {
    const base = kind.startsWith("li") ? "li" : kind;
    const indent = base === "li" ? 28 * (kind.length - 1) : 0;
    const [font, color] = style[base];
    ctx.font = `${font} system-ui, sans-serif`;
    const size = parseInt(font.split(" ")[1], 10);
    y += base === "title" ? 0 : base === "alert" || base === "h" ? 26 : 6;
    let current = base === "li" ? "• " : "";
    const words = text.split(/\s+/);
    for (const word of words) {
      const next = current ? `${current}${current.endsWith(" ") ? "" : " "}${word}` : word;
      if (ctx.measureText(next).width > width - 2 * pad - indent && current.trim()) {
        layout.push([font, color, pad + indent, y + size, current]);
        y += size * 1.3;
        current = base === "li" ? `  ${word}` : word;
      } else current = next;
    }
    layout.push([font, color, pad + indent, y + size, current]);
    y += size * 1.3;
  }
  canvas.width = width;
  canvas.height = Math.ceil(y + pad);
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = "#b42318";
  ctx.fillRect(0, 0, 16, canvas.height);
  for (const [font, color, x, top, text] of layout) {
    ctx.font = `${font} system-ui, sans-serif`;
    ctx.fillStyle = color;
    ctx.fillText(text, x, top);
  }
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
  const file = new File([blob], `notfall-${name.toLowerCase().replace(/[^a-z0-9]+/g, "-")}.png`, { type: "image/png" });
  if (navigator.canShare?.({ files: [file] }) && await confirmDialog(t("web.md_picture_share"), t("web.share"))) {
    await navigator.share({ files: [file], title: t("web.md_title") }).catch(() => {});
    return;
  }
  const url = URL.createObjectURL(file);
  h("a", { href: url, download: file.name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

async function sendDialog(person, pass) {
  const data = await attempt(() => api("/api/contacts"));
  if (!data) return;
  const contacts = data.contacts.filter((c) => !c.legacy);
  const pick = h("select", { id: "md_contact" }, contacts.map((c) => h("option", { value: c.id }, `${c.name} – ${t(`web.ec_mode_${c.medical_mode}`)}`)));
  const dialog = modal("wg-dialog", h("h2", {}, t("web.md_send_title", { name: person.name })), h("p", { class: "muted" }, t("web.md_send_intro")),
    contacts.length ? h("div", { class: "field" }, h("label", { for: "md_contact" }, t("web.md_send_to")), pick)
      : h("p", { class: "notice" }, t("web.md_send_none"), " ", h("a", { href: "#/settings/emergency", onclick: () => dialog.close() }, t("web.ec_title"))),
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      contacts.length ? h("button", { class: "primary", onclick: async () => {
        const sent = await attempt(() => api(`/api/medical/${person.id}/send`, { method: "POST", body: { contact: Number(pick.value), password: pass } }));
        if (sent) { dialog.close(); toast(t(`web.md_sent_${sent.mode}`)); }
      } }, icon("message"), t("web.md_send")) : null));
}

function sheetDialog(person, record, pass, edit) {
  const post = (path, body = {}) => api(path, { method: "POST", body: { ...body, password: pass }, raw: true });
  const dialog = modal("editor wide", h("div", { class: "row between" }, h("h2", {}, record.name), h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))),
    h("div", { class: "row wrap md-actions" },
      h("button", { class: "primary", onclick: () => { dialog.close(); edit(record); } }, icon("pencil"), t("web.md_edit")),
      h("button", { title: t("web.md_pdf_tip"), onclick: async () => { const r = await attempt(() => post(`/api/medical/${person.id}/pdf`)); if (r) saveFile(r, "notfall.pdf"); } }, icon("file-text"), "PDF"),
      h("button", { title: t("web.md_picture_tip"), onclick: async () => {
        const r = await attempt(() => post(`/api/medical/${person.id}/pdf`, { format: "lines" }));
        if (r) await picture((await r.json()).lines, record.name);
      } }, icon("photo"), t("web.md_picture")),
      h("button", { title: t("web.md_bmp_tip"), onclick: async () => { const r = await attempt(() => post(`/api/medical/${person.id}/bmp`)); if (r) saveFile(r, "bmp.xml"); } }, icon("qrcode"), "BMP"),
      h("button", { title: t("web.md_send_tip"), onclick: () => sendDialog(person, pass) }, icon("message"), t("web.md_send"))),
    sheetView(record),
    h("p", { class: "muted" }, h("small", {}, t("web.md_sheet_password"))));
  dialog.addEventListener("close", () => { pass = ""; });
}

function linkDialog(person, redraw) {
  const minutes = h("select", { id: "md_min" }, [15, 30, 60].map((m) => h("option", { value: m, selected: m === 30 }, t("web.md_minutes", { minutes: m }))));
  const pin = h("input", { type: "checkbox", id: "md_pin", checked: true });
  const pass = h("input", { type: "password", id: "md_pass", autocomplete: "current-password" });
  const dialog = modal("wg-dialog", h("h2", {}, t("web.md_link_for", { name: person.name })), h("p", { class: "muted" }, t("web.md_link_hint")),
    h("div", { class: "stack" }, h("div", { class: "field" }, h("label", { for: "md_min" }, t("web.md_valid")), minutes),
      h("label", { class: "check", for: "md_pin" }, pin, t("web.md_with_pin")),
      h("div", { class: "field" }, h("label", { for: "md_pass" }, t("web.md_password")), pass)),
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const made = await attempt(() => api(`/api/medical/${person.id}/link`, { method: "POST", body: { minutes: Number(minutes.value), pin: pin.checked, password: pass.value } }));
        if (made) { dialog.close(); showLink(made); redraw(); }
      } }, icon("plus"), t("web.md_make_link"))));
}

function showLink(link) {
  const dialog = modal("wg-dialog", h("h2", {}, t("web.md_link_title")),
    h("p", {}, t("web.md_link_intro", { time: when(link.expires) })),
    h("pre", { class: "mono wrap" }, link.url),
    link.pin ? h("p", {}, t("web.md_link_pin"), " ", h("strong", { class: "mono big-pin" }, link.pin)) : h("p", { class: "notice" }, t("web.md_link_no_pin")),
    h("div", { class: "row end" },
      h("button", { onclick: () => navigator.clipboard?.writeText(link.url).then(() => toast(t("web.copied"))) }, icon("copy"), t("web.copy")),
      navigator.share ? h("button", { onclick: () => navigator.share({ url: link.url }).catch(() => {}) }, icon("upload"), t("web.share")) : null,
      h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
}

export async function medical(box, status) {
  const people = await status("/api/medical");
  if (!people) { fill(box, h("p", { class: "notice" }, t("web.addon_restart"))); return; }
  const [links, accounts] = await Promise.all([status("/api/medical/links"), status("/api/medical/accounts")]);
  const redraw = () => medical(box, status);
  const save = async (id, record, pass) => {
    pass ||= await password();
    if (!pass) return false;
    const url = id ? `/api/medical/${id}` : "/api/medical";
    const done = await attempt(() => api(url, { method: id ? "PUT" : "POST", body: { record, password: pass } }), t("web.saved"));
    if (done) redraw();
    return Boolean(done);
  };
  const open = async (person) => {
    const pass = await password();
    if (!pass) return;
    const record = await attempt(() => api("/api/medical/view", { method: "POST", body: { id: person.id, password: pass } }));
    if (record) sheetDialog(person, record, pass, (r) => medicalForm(r, accounts || [], (values) => save(person.id, values, pass)));
  };
  fill(box,
    h("section", { class: "panel stack" }, h("h2", {}, t("web.md_people")), h("p", { class: "muted" }, t("web.md_intro")),
      h("ul", { class: "list" }, people.length ? people.map((p) => h("li", {}, icon("user"),
        h("div", { class: "grow" }, h("div", {}, p.name), h("small", { class: "muted" }, t("web.md_updated", { time: when(p.updated) }))),
        h("button", { onclick: () => open(p) }, icon("eye"), t("web.md_open_edit")),
        h("button", { class: "primary", onclick: () => linkDialog(p, redraw) }, icon("world"), t("web.md_link_button")),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (!await confirmDialog(t("web.md_delete", { name: p.name }), t("web.delete"), true)) return;
          const pass = await password();
          if (pass && await attempt(() => api(`/api/medical/${p.id}`, { method: "DELETE", body: { password: pass } }), t("web.deleted"))) redraw();
        } }, icon("trash")))) : h("li", { class: "empty" }, t("web.md_none"))),
      h("div", {}, h("button", { onclick: () => medicalForm({}, accounts || [], (values) => save(null, values)) }, icon("plus"), t("web.md_add")))),
    h("section", { class: "panel stack" }, h("h2", {}, t("web.md_links")),
      h("ul", { class: "list" }, links?.length ? links.map((l) => h("li", {}, icon("clock"),
        h("div", { class: "grow" }, h("div", {}, l.name), h("small", { class: "muted" }, t("web.md_link_state", { time: when(l.expires), opened: l.opened }), l.pin ? " · PIN" : "")),
        h("button", { class: "danger", onclick: async () => { if (await attempt(() => api(`/api/medical/links/${l.id}`, { method: "DELETE" }), t("web.saved"))) redraw(); } }, t("web.md_end")))) : h("li", { class: "empty" }, t("web.md_no_links")))),
    h("p", { class: "muted" }, h("small", {}, t("web.md_privacy"))));
}
