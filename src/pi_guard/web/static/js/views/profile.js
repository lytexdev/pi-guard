// Contact details of a dashboard account, with vCard in and out. Everything is optional.
import { api, attempt, h, icon, t } from "../api.js";

const FIELDS = [["name", "text", "name"], ["email", "email", "email"], ["phone", "tel", "tel"]];

// `url` is /api/me/profile or /api/profiles/NAME; `title` names whose details these are.
export async function profileDialog(url, title) {
  const values = await attempt(() => api(url));
  if (!values) return;
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
