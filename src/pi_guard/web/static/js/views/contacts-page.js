// Contacts as their own page: one card per person, with faces and emergency contacts below.
import { h, icon, t } from "../api.js";

export async function render(main) {
  const persons = h("div");
  const faces = h("div");
  const emergency = h("div");
  const more = (titleKey, iconName, inner) => h("details", { class: "panel hub-more" },
    h("summary", {}, icon(iconName), " ", t(titleKey)), inner);
  main.append(h("h1", {}, t("web.nav_contacts")), persons,
    more("web.pc_more_faces", "user-check", faces), more("web.pc_more_emergency", "alert-triangle", emergency));
  await (await import("./persons.js")).personsPanel(persons);
  await (await import("./people.js")).render(faces, [], { embedded: true });
  await (await import("./contacts.js")).contactsPanel(emergency);
}
