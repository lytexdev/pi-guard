// Contact details of a dashboard account, with vCard in and out. Everything is optional.
import { api, attempt, h, icon, t } from "../api.js";

const FIELDS = [["name", "text", "name"], ["email", "email", "email"], ["phone", "tel", "tel"]];
// The same person elsewhere: messenger accounts, and (for admins) a trusted person and a card.
const MESSENGERS = [["telegram", "brand-telegram", "@name / 123456789"], ["discord", "brand-discord", "123456789012345678"],
  ["signal", "brand-signal", "+49…"], ["whatsapp", "brand-whatsapp", "+49…"]];

// `url` is /api/me/profile or /api/profiles/NAME; `title` names whose details these are.
// `username`: set when an admin edits someone, to link a trusted person and an emergency card.
export async function profileDialog(url, title, username = null) {
  const values = await attempt(() => api(url));
  if (!values) return;
  const [people, cards] = username ? await Promise.all([api("/api/people").catch(() => null), api("/api/medical/linked").catch(() => null)]) : [null, null];
  const inputs = {};
  const field = (key, type, autocomplete) => {
    inputs[key] = h("input", { id: `pf_${key}`, type, value: values[key] || "", autocomplete, maxlength: 120 });
    return h("div", { class: "field" }, h("label", { for: `pf_${key}` }, t(`web.pf_${key}`)), inputs[key]);
  };
  const area = (key, rows) => {
    inputs[key] = h("textarea", { id: `pf_${key}`, rows }, values[key] || "");
    return h("div", { class: "field" }, h("label", { for: `pf_${key}` }, t(`web.pf_${key}`)), inputs[key]);
  };
  const file = h("input", { type: "file", accept: ".vcf,text/vcard", class: "hidden" });
  file.addEventListener("change", async () => {
    const card = file.files[0];
    if (!card) return;
    const read = await attempt(() => api("/api/me/profile/vcard", { method: "POST", body: card }));
    if (read) for (const [key, input] of Object.entries(inputs)) if (read[key]) input.value = read[key];
    file.value = "";
  });
  const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, title),
    h("p", { class: "muted" }, t("web.pf_intro")),
    h("div", { class: "stack" }, FIELDS.map(([key, type, auto]) => field(key, type, auto)), area("address", 3), area("note", 3)),
    h("h3", {}, icon("link"), " ", t("web.pf_links")), h("p", { class: "muted" }, h("small", {}, t("web.pf_links_hint"))),
    h("div", { class: "grid two" }, MESSENGERS.map(([key, ic, hint]) => {
      inputs[key] = h("input", { id: `pf_${key}`, value: values[key] || "", placeholder: hint, maxlength: 64, autocomplete: "off" });
      return h("div", { class: "field" }, h("label", { for: `pf_${key}` }, icon(ic), " ", t(`web.pf_${key}`)), inputs[key]);
    })),
    people ? (() => {
      inputs.person = h("select", { id: "pf_person" }, h("option", { value: "" }, t("web.pf_person_none")),
        people.map((p) => h("option", { value: p.name, selected: p.name === values.person }, p.name)));
      return h("div", { class: "field" }, h("label", { for: "pf_person" }, icon("user-check"), " ", t("web.pf_person")), inputs.person);
    })() : null,
    cards ? (() => {
      const mine = cards.find((c) => (c.account || "").toLowerCase() === username.toLowerCase());
      const card = h("select", { id: "pf_card" }, h("option", { value: "" }, t("web.pf_card_none")),
        cards.map((c) => h("option", { value: c.id, selected: c === mine, disabled: c.account && c !== mine }, c.account && c !== mine ? `${c.name} (${c.account})` : c.name)));
      card.addEventListener("change", () => attempt(() => api("/api/medical/link-account", { method: "PUT", body: { account: username, id: Number(card.value) || 0 } }), t("web.saved")));
      return h("div", { class: "field" }, h("label", { for: "pf_card" }, icon("alert-triangle"), " ", t("web.pf_card")), card);
    })() : null,
    h("div", { class: "row" },
      file, h("button", { type: "button", onclick: () => file.click() }, icon("upload"), t("web.pf_vcard_in")),
      h("a", { class: "button", href: `${url}/vcard`, download: "" }, icon("download"), t("web.pf_vcard_out"))),
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const body = Object.fromEntries(Object.entries(inputs).map(([key, input]) => [key, input.value]));
        if (await attempt(() => api(url, { method: "PUT", body }), t("web.saved"))) dialog.close();
      } }, icon("check"), t("web.save"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}
