// Emergency contacts: who they are, how to reach them, and what each gets on which occasion.
import { api, attempt, confirmDialog, fill, h, icon, t, toast } from "../api.js";
import { openContactWizard } from "./contact-wizard.js";

const BRAND = { telegram: "brand-telegram", signal: "brand-signal", discord: "brand-discord", whatsapp: "brand-whatsapp", email: "mail" };
const SHARE_ICON = { location: "map-pin", phone_photo: "device-mobile", phone_audio: "microphone-off", pi_photo: "camera", events: "list-details", home_audio: "volume", medical: "user-check" };
// What makes sense on which occasion: no phone and no place when alerts go unanswered at home.
const FITS = { sos: ["location", "phone_photo", "phone_audio", "pi_photo", "events", "home_audio", "medical"], escalation: ["events", "pi_photo"] };

function modal(...children) {
  const dialog = h("dialog", { class: "editor wide" }, ...children);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

function channelCards(data, channels) {
  // Running messengers first as toggles; others stay folded so the form stays short.
  const byProvider = Object.fromEntries((channels || []).map((c) => [c.provider, c.address]));
  const rows = {};
  const card = (provider, ready) => {
    const on = h("input", { type: "checkbox", checked: Boolean(byProvider[provider]) });
    const address = h("input", {
      value: byProvider[provider] || "",
      maxlength: 200,
      placeholder: t(`web.ec_hint_${provider}`),
      "aria-label": t("web.ec_address"),
    });
    const body = h("div", { class: on.checked ? "cw-channel-body" : "cw-channel-body hidden" },
      address, h("small", { class: "muted" }, t(`web.ec_hint_${provider}`)));
    on.addEventListener("change", () => {
      body.classList.toggle("hidden", !on.checked);
      if (on.checked) address.focus();
    });
    rows[provider] = { on, address };
    return h("label", { class: `cw-channel${ready ? " ready" : " muted-card"}` },
      h("div", { class: "row between" },
        h("span", { class: "row" }, icon(BRAND[provider] || "message"), h("strong", {}, t(`web.ec_p_${provider}`)),
          ready ? null : h("span", { class: "badge" }, t("web.ec_not_running"))),
        on),
      body);
  };
  const running = data.providers || [];
  const rest = (data.all_providers || []).filter((p) => !running.includes(p));
  const list = h("div", { class: "stack cw-channels" },
    running.length ? null : h("p", { class: "notice" }, t("web.cw_no_messenger")),
    running.map((p) => card(p, true)),
    rest.length ? h("details", { class: "cw-more" },
      h("summary", {}, t("web.cw_more_messengers")),
      h("div", { class: "stack" }, rest.map((p) => card(p, false)))) : null);
  return {
    list,
    values: () => Object.entries(rows)
      .filter(([, r]) => r.on.checked && r.address.value.trim())
      .map(([provider, r]) => ({ provider, address: r.address.value.trim() })),
  };
}

function shareMatrix(data, contact) {
  const boxes = {};
  const takes = {};
  const head = h("tr", {}, h("th", {}, t("web.ec_gets")), data.occasions.map((o) => {
    takes[o] = h("input", { type: "checkbox", checked: o in contact.share, "aria-label": t(`web.ec_occ_${o}`) });
    return h("th", {}, h("label", { class: "check" }, takes[o], t(`web.ec_occ_${o}`)));
  }));
  const body = data.shares.filter((s) => s !== "medical" || data.medical).map((s) => h("tr", {}, h("td", {}, icon(SHARE_ICON[s]), " ", t(`web.ec_share_${s}`),
    h("small", { class: "muted block" }, t(`web.ec_share_${s}_hint`))),
    data.occasions.map((o) => {
      if (!FITS[o].includes(s)) return h("td", { class: "muted" }, "–");
      const box = h("input", { type: "checkbox", checked: (contact.share[o] || []).includes(s), "aria-label": `${t(`web.ec_share_${s}`)} · ${t(`web.ec_occ_${o}`)}` });
      (boxes[o] ||= {})[s] = box;
      return h("td", {}, box);
    })));
  const sync = () => data.occasions.forEach((o) => Object.values(boxes[o] || {}).forEach((b) => { b.disabled = !takes[o].checked; }));
  Object.values(takes).forEach((b) => b.addEventListener("change", sync));
  sync();
  const value = () => Object.fromEntries(data.occasions.filter((o) => takes[o].checked)
    .map((o) => [o, Object.entries(boxes[o] || {}).filter(([, b]) => b.checked).map(([s]) => s)]));
  return { table: h("div", { class: "table-wrap" }, h("table", { class: "share-matrix" }, h("thead", {}, head), h("tbody", {}, body))), value, medical: () => Object.values(boxes).some((o) => o.medical?.checked) };
}

function editor(data, contact, onSave) {
  const line = (key, type = "text", max = 60) => {
    const input = h("input", { id: `ec_${key}`, type, value: contact[key] || "", maxlength: max });
    return [h("div", { class: "field" }, h("label", { for: `ec_${key}` }, t(`web.ec_f_${key}`)), input), input];
  };
  const [nameField, name] = line("name");
  const [relationField, relation] = line("relation", "text", 40);
  const [phoneField, phone] = line("phone", "tel", 40);
  const [emailField, email] = line("email", "email", 200);
  const [noteField, note] = line("note", "text", 300);
  const account = h("select", { id: "ec_account" }, h("option", { value: "" }, t("web.md_no_account")),
    data.accounts.map((a) => h("option", { value: a, selected: a === contact.account }, a)));
  const channels = channelCards(data, contact.channels || []);
  const matrix = shareMatrix(data, contact);
  const mode = h("select", { id: "ec_mode" }, data.medical_modes.map((m) => h("option", { value: m, selected: m === contact.medical_mode }, t(`web.ec_mode_${m}`))));
  const modeHint = h("p", { class: "muted" }, h("small", {}));
  const showHint = () => fill(modeHint, h("small", {}, t(`web.ec_mode_${mode.value}_hint`)));
  mode.addEventListener("change", showHint);
  showHint();
  const dialog = modal(h("h2", {}, contact.name || t("web.ec_new")), h("p", { class: "muted" }, t("web.ec_form_intro")),
    h("div", { class: "stack" },
      h("fieldset", { class: "md-section" }, h("legend", {}, t("web.ec_sec_person")),
        h("div", { class: "grid two" }, nameField, relationField, phoneField, emailField),
        h("div", { class: "field" }, h("label", { for: "ec_account" }, t("web.ec_f_account")), account, h("small", { class: "muted" }, t("web.ec_account_hint"))), noteField),
      h("fieldset", { class: "md-section" }, h("legend", {}, t("web.ec_sec_reach")), h("p", { class: "muted" }, h("small", {}, t("web.cw_reach_hint"))),
        channels.list),
      h("fieldset", { class: "md-section" }, h("legend", {}, t("web.ec_sec_share")), h("p", { class: "muted" }, h("small", {}, t("web.ec_share_intro"))), matrix.table,
        data.medical ? h("div", { class: "field" }, h("label", { for: "ec_mode" }, t("web.ec_f_mode")), mode, modeHint) : null)),
    h("div", { class: "row end sticky-actions" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        if (matrix.medical() && mode.value === "sheet" && !await confirmDialog(t("web.ec_sheet_confirm"), t("web.ec_sheet_yes"))) return;
        const values = { name: name.value, relation: relation.value, phone: phone.value, email: email.value, account: account.value, note: note.value,
          channels: channels.values(), share: matrix.value(), medical_mode: mode.value };
        if (await onSave(values)) dialog.close();
      } }, icon("check"), t("web.save"))));
}

function summary(data, c) {
  const parts = data.occasions.filter((o) => o in c.share).map((o) => `${t(`web.ec_occ_${o}`)}: ${(c.share[o] || []).map((s) => t(`web.ec_share_${s}`)).join(", ") || t("web.ec_only_words")}`);
  return parts.join(" · ") || t("web.ec_never");
}

export async function contactsPanel(box) {
  const draw = async () => {
    const data = await attempt(() => api("/api/contacts"));
    if (!data) return;
    const save = async (id, contact) => {
      const done = await attempt(() => api(id ? `/api/contacts/${id}` : "/api/contacts", { method: id ? "PUT" : "POST", body: { contact } }), t("web.saved"));
      if (done) draw();
      return Boolean(done);
    };
    const test = async (c) => {
      const r = await attempt(() => api(`/api/contacts/${c.id}/test`, { method: "POST" }));
      if (r) toast(r.results.map((x) => `${t(`web.ec_p_${x.provider}`)}: ${x.ok ? "✓" : "✗"}`).join(" · "), r.results.some((x) => !x.ok));
    };
    const fullSetup = async () => {
      const persons = await attempt(() => api("/api/persons"));
      if (!persons) return;
      await openContactWizard(persons, data, { roles: ["emergency"] }, draw);
    };
    fill(box, h("section", { class: "panel stack" },
      h("div", { class: "row between wrap" }, h("h2", {}, icon("alert-triangle"), " ", t("web.ec_title")),
        h("div", { class: "row wrap" },
          h("button", { onclick: fullSetup }, icon("user-plus"), t("web.cw_full_setup")),
          h("button", { class: "primary", onclick: () => editor(data, { share: { sos: FITS.sos.filter((s) => !["events", "medical"].includes(s)), escalation: ["events", "pi_photo"] }, medical_mode: "note", channels: [] }, (v) => save(null, v)) }, icon("plus"), t("web.ec_add")))),
      h("p", { class: "muted" }, t("web.ec_intro", { alerts: data.after_alerts, minutes: data.after_minutes })),
      h("ul", { class: "list" }, data.contacts.length ? data.contacts.map((c) => h("li", {},
        h("div", { class: "row" }, c.channels.map((ch) => icon(BRAND[ch.provider] || "message"))),
        h("div", { class: "grow" }, h("div", {}, c.name, c.relation ? h("span", { class: "badge" }, c.relation) : null, c.legacy ? h("span", { class: "badge" }, t("web.ec_legacy")) : null,
          c.account ? h("span", { class: "badge accent" }, icon("user"), " ", c.account) : null),
          h("small", { class: "muted block" }, [c.phone, c.email].filter(Boolean).join(" · ")),
          h("small", { class: "block" }, summary(data, c), (c.share.sos || []).includes("medical") ? ` · ${t(`web.ec_mode_${c.medical_mode}`)}` : "")),
        h("button", { class: "icon-only", title: t("web.ec_test"), onclick: () => test(c) }, icon("message")),
        c.legacy ? null : [
          h("button", { class: "icon-only", title: t("web.edit"), onclick: () => editor(data, c, (v) => save(c.id, v)) }, icon("pencil")),
          h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
            if (await confirmDialog(t("web.ec_delete", { name: c.name }), t("web.delete"), true) && await attempt(() => api(`/api/contacts/${c.id}`, { method: "DELETE" }), t("web.deleted"))) draw();
          } }, icon("trash"))]))
        : h("li", { class: "empty" }, t("web.ec_none"))),
      data.contacts.some((c) => c.legacy) ? h("p", { class: "muted" }, h("small", {}, t("web.ec_legacy_hint"))) : null));
  };
  await draw();
}
