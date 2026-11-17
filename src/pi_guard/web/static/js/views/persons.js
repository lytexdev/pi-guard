// Central contacts: one person with face, account, messenger and emergency links.
import { askDialog, api, attempt, confirmDialog, fill, h, icon, t } from "../api.js";

const ROLE_ICONS = {
  household: "home",
  trusted: "user-check",
  emergency: "alert-triangle",
  alert: "bell",
  viewer: "eye",
};

function modal(...children) {
  const dialog = h("dialog", { class: "editor wide" }, ...children);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

function roleBadges(roles) {
  if (!(roles || []).length) return h("small", { class: "muted" }, t("web.person_no_roles"));
  return h("div", { class: "row wrap person-roles" }, roles.map((r) =>
    h("span", { class: `badge person-role-${r}`, title: t(`web.person_role_${r}_hint`) },
      icon(ROLE_ICONS[r] || "user"), " ", t(`web.person_role_${r}`))));
}

function editor(data, person, onSave) {
  const name = h("input", { value: person.display_name || "", maxlength: 60 });
  const note = h("input", { value: person.note || "", maxlength: 300 });
  const roleBoxes = Object.fromEntries(data.roles.map((r) => [
    r, h("input", { type: "checkbox", checked: (person.roles || []).includes(r) }),
  ]));
  const face = h("select", {}, h("option", { value: "" }, t("web.none")),
    data.faces.map((n) => h("option", { value: n, selected: (person.links || []).some((l) => l.kind === "face" && l.ref === n) }, n)));
  const account = h("select", {}, h("option", { value: "" }, t("web.none")),
    data.accounts.map((a) => h("option", { value: a, selected: (person.links || []).some((l) => l.kind === "web_account" && l.ref === a) }, a)));
  const emergency = h("select", {}, h("option", { value: "" }, t("web.none")),
    data.contacts.map((c) => h("option", { value: String(c.id), selected: (person.links || []).some((l) => l.kind === "emergency_contact" && l.ref === String(c.id)) }, c.name)));
  const messengers = (person.links || []).filter((l) => l.kind === "messenger").map((l) => l.ref).join(", ");
  const messenger = h("input", { value: messengers, placeholder: "telegram:123, signal:+49…" });
  // Linking a face usually means household + trusted for the cameras.
  face.addEventListener("change", () => {
    if (!face.value) return;
    roleBoxes.household.checked = true;
    roleBoxes.trusted.checked = true;
  });
  emergency.addEventListener("change", () => {
    if (emergency.value) roleBoxes.emergency.checked = true;
  });
  const dialog = modal(h("h2", {}, person.display_name || t("web.person_new")),
    h("p", { class: "muted" }, t("web.person_form_hint")),
    h("div", { class: "stack" },
      h("div", { class: "field" }, h("label", {}, t("web.name")), name),
      h("div", { class: "field" }, h("label", {}, t("web.person_note")), note),
      h("section", { class: "panel stack tight" },
        h("h3", {}, t("web.person_roles")),
        h("p", { class: "muted" }, t("web.person_roles_hint")),
        h("div", { class: "stack person-role-picks" }, data.roles.map((r) =>
          h("label", { class: "check person-role-pick" }, roleBoxes[r],
            h("span", {}, h("strong", {}, t(`web.person_role_${r}`)),
              h("small", { class: "muted block" }, t(`web.person_role_${r}_hint`))))))),
      h("div", { class: "field" }, h("label", {}, t("web.person_face")), face),
      h("div", { class: "field" }, h("label", {}, t("web.person_account")), account),
      h("div", { class: "field" }, h("label", {}, t("web.person_emergency")), emergency),
      h("div", { class: "field" }, h("label", {}, t("web.person_messengers")), messenger, h("small", { class: "muted" }, t("web.person_messengers_hint"))),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: async () => {
          const links = [];
          if (face.value) links.push({ kind: "face", ref: face.value });
          if (account.value) links.push({ kind: "web_account", ref: account.value });
          if (emergency.value) links.push({ kind: "emergency_contact", ref: emergency.value });
          messenger.value.split(",").map((x) => x.trim()).filter(Boolean).forEach((ref) => links.push({ kind: "messenger", ref }));
          const password = await askDialog(t("web.confirm_password"), { type: "password" });
          if (!password) return;
          const body = { password, person: {
            display_name: name.value.trim(), note: note.value.trim(),
            roles: data.roles.filter((r) => roleBoxes[r].checked), links,
          } };
          const url = person.id ? `/api/persons/${person.id}` : "/api/persons";
          if (await attempt(() => api(url, { method: person.id ? "PUT" : "POST", body }), t("web.saved"))) {
            dialog.close();
            onSave();
          }
        } }, icon("check"), t("web.save")),
        h("button", { onclick: () => dialog.close() }, t("web.cancel")))));
}

export async function personsPanel(box) {
  const draw = async () => {
    const data = await attempt(() => api("/api/persons"));
    if (!data) { fill(box, h("p", { class: "empty" }, t("web.error"))); return; }
    fill(box, h("section", { class: "panel stack" },
      h("div", { class: "row between" }, h("h2", {}, t("web.set_contacts")),
        h("div", { class: "row" },
          h("button", { title: t("web.person_migrate_tip"), onclick: async () => {
            if (!await confirmDialog(t("web.person_migrate_confirm"), t("web.person_migrate"))) return;
            const password = await askDialog(t("web.confirm_password"), { type: "password" });
            if (!password) return;
            if (await attempt(() => api("/api/persons/migrate", { method: "POST", body: { password } }), t("web.saved"))) draw();
          } }, icon("link"), t("web.person_migrate")),
          h("button", { class: "primary", onclick: () => editor(data, {}, draw) }, icon("plus"), t("web.person_new")))),
      h("p", { class: "muted" }, t("web.set_contacts_hint")),
      h("ul", { class: "list" }, data.persons.length ? data.persons.map((p) => h("li", { class: "person-row" },
        icon("user"),
        h("div", { class: "grow" }, h("div", {}, p.display_name),
          roleBadges(p.roles),
          h("small", { class: "muted block" }, (p.links || []).map((l) => `${l.kind}:${l.ref}`).join(" · ") || t("web.person_no_links"))),
        h("button", { class: "icon-only", title: t("web.edit"), onclick: () => editor(data, p, draw) }, icon("edit")),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (!await confirmDialog(t("web.person_delete_confirm", { name: p.display_name }), t("web.delete"), true)) return;
          if (await attempt(() => api(`/api/persons/${p.id}`, { method: "DELETE" }))) draw();
        } }, icon("trash")))) : h("li", { class: "empty" }, t("web.person_empty"))),
      h("p", { class: "muted" }, h("a", { href: "#/settings/contacts" }, t("web.person_faces_link")), " · ",
        h("a", { href: "#/settings/emergency" }, t("web.person_emergency_link")))));
  };
  await draw();
}
