// Central contacts: one person with face, account, messenger and emergency links.
import { api, attempt, confirmDialog, fill, h, icon, t } from "../api.js";
import { openContactWizard } from "./contact-wizard.js";

const ROLE_ICONS = {
  household: "home",
  trusted: "user-check",
  emergency: "alert-triangle",
  alert: "bell",
  viewer: "eye",
};

function roleBadges(roles) {
  if (!(roles || []).length) return h("small", { class: "muted" }, t("web.person_no_roles"));
  return h("div", { class: "row wrap person-roles" }, roles.map((r) =>
    h("span", { class: `badge person-role-${r}`, title: t(`web.person_role_${r}_hint`) },
      icon(ROLE_ICONS[r] || "user"), " ", t(`web.person_role_${r}`))));
}

export async function personsPanel(box) {
  const draw = async () => {
    const [data, contacts] = await Promise.all([
      attempt(() => api("/api/persons")),
      attempt(() => api("/api/contacts")),
    ]);
    if (!data) { fill(box, h("p", { class: "empty" }, t("web.error"))); return; }
    const open = (person = {}) => openContactWizard(data, contacts, person, draw);
    fill(box, h("section", { class: "panel stack" },
      h("div", { class: "row between wrap" },
        h("h2", {}, t("web.set_contacts")),
        h("div", { class: "row wrap" },
          h("button", { title: t("web.person_migrate_tip"), onclick: async () => {
            if (!await confirmDialog(t("web.person_migrate_confirm"), t("web.person_migrate"))) return;
            if (await attempt(() => api("/api/persons/migrate", { method: "POST", body: {} }), t("web.saved"))) draw();
          } }, icon("link"), t("web.person_migrate")),
          h("button", { class: "primary", onclick: () => open() }, icon("plus"), t("web.person_new")))),
      h("p", { class: "muted" }, t("web.set_contacts_hint")),
      h("ul", { class: "list" }, data.persons.length ? data.persons.map((p) => h("li", { class: "person-row" },
        icon("user"),
        h("div", { class: "grow" }, h("div", {}, p.display_name),
          roleBadges(p.roles),
          h("small", { class: "muted block" }, (p.links || []).map((l) => `${l.kind}:${l.ref}`).join(" · ") || t("web.person_no_links"))),
        h("button", { class: "icon-only", title: t("web.edit"), onclick: () => open(p) }, icon("pencil")),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (!await confirmDialog(t("web.person_delete_confirm", { name: p.display_name }), t("web.delete"), true)) return;
          if (await attempt(() => api(`/api/persons/${p.id}`, { method: "DELETE" }))) draw();
        } }, icon("trash")))) : h("li", { class: "empty" }, t("web.person_empty"))),
      h("p", { class: "muted" }, h("a", { href: "#/settings/emergency" }, t("web.person_emergency_link")))));
  };
  await draw();
}
