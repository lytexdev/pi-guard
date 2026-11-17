// Contacts, in one place: every person with their face, account, messengers, emergency role,
// emergency card and VPN devices. Cards to see it at a glance; the wizard to change it; invite,
// photos and videos straight from the card. What is not linked to anyone yet is offered below.
import { api, attempt, confirmDialog, fill, h, icon, t, toast } from "../api.js";
import { openContactWizard } from "./contact-wizard.js";
import { captureDialog } from "./capture.js";

const ROLE_ICONS = { household: "home", trusted: "user-check", emergency: "alert-triangle", alert: "bell", viewer: "eye" };
const BRAND = { telegram: "brand-telegram", signal: "brand-signal", discord: "brand-discord", whatsapp: "brand-whatsapp", email: "mail" };

const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "?";
const hue = (name) => [...name].reduce((sum, c) => sum + c.charCodeAt(0), 0) % 360;
const linksOf = (p, kind) => (p.links || []).filter((l) => l.kind === kind).map((l) => l.ref);

function modal(cls, ...children) {
  const dialog = h("dialog", { class: cls }, ...children);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

// Where a person can be written to: their messengers, and their emergency contact's ways and email.
function channelsOf(p, contacts) {
  const found = linksOf(p, "messenger").map((ref) => { const [provider, ...rest] = ref.split(":"); return [provider, rest.join(":")]; });
  for (const id of linksOf(p, "emergency_contact")) {
    const c = (contacts?.contacts || []).find((x) => String(x.id) === String(id));
    if (!c) continue;
    (c.channels || []).forEach((ch) => found.push([ch.provider, ch.address]));
    if (c.email) found.push(["email", c.email]);
  }
  const seen = new Set();
  return found.filter(([p, a]) => { const key = `${p}:${a}`; if (seen.has(key)) return false; seen.add(key); return true; });
}

function inviteDialog(person, contacts, redraw) {
  const channels = channelsOf(person, contacts);
  const running = new Set(contacts?.providers || []);
  if (!channels.length) { toast(t("web.inv_no_channel"), true); return; }
  const pick = h("select", { id: "inv_to" }, channels.map(([p, a], i) =>
    h("option", { value: i, disabled: !running.has(p) }, `${t(`web.ec_p_${p}`)}: ${a}${running.has(p) ? "" : ` (${t("web.inv_off")})`}`)));
  const app = h("input", { type: "checkbox", checked: true });
  const vpn = h("input", { type: "checkbox" });
  const password = h("input", { type: "password", autocomplete: "current-password", placeholder: t("web.password") });
  const vpnBox = h("div", { class: "stack hidden" }, h("p", { class: "notice" }, t("web.inv_vpn_warning")), password);
  vpn.addEventListener("change", () => vpnBox.classList.toggle("hidden", !vpn.checked));
  const dialog = modal("wg-dialog", h("h2", {}, icon("share"), " ", t("web.inv_title", { name: person.display_name })),
    h("p", { class: "muted" }, t("web.inv_intro")),
    h("div", { class: "field" }, h("label", { for: "inv_to" }, t("web.inv_to")), pick),
    h("label", { class: "check" }, app, t("web.inv_app")),
    h("label", { class: "check" }, vpn, t("web.inv_vpn")), vpnBox,
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const [provider, address] = channels[Number(pick.value)];
        const body = { provider, address, app: app.checked, vpn: vpn.checked, password: password.value };
        if (await attempt(() => api(`/api/persons/${person.id}/invite`, { method: "POST", body }), t("web.inv_sent"))) { dialog.close(); redraw(); }
      } }, icon("send"), t("web.inv_send"))));
  return dialog;
}

// Photos and videos for face recognition, with the person's consent; or the live camera.
function faceDialog(person, data, redraw) {
  const faceName = linksOf(person, "face")[0] || person.display_name;
  const consent = h("input", { type: "checkbox" });
  const files = h("input", { class: "hidden", type: "file", multiple: true, accept: "image/*,video/*" });
  const status = h("p", { class: "muted", "aria-live": "polite" });
  const linkFace = async () => {
    if (linksOf(person, "face").includes(faceName)) return;
    const links = [...(person.links || []), { kind: "face", ref: faceName }];
    await attempt(() => api(`/api/persons/${person.id}`, { method: "PUT", body: { person: { ...person, links } } }));
  };
  const send = async (chosen) => {
    const form = new FormData();
    form.append("consent", "yes");
    chosen.forEach((f) => form.append("files", f, f.name));
    fill(status, h("span", { class: "spinner", "aria-hidden": "true" }), " ", t("web.processing"));
    const result = await attempt(() => api(`/api/people/${encodeURIComponent(faceName)}/samples`, { method: "POST", body: form }));
    if (!result) { fill(status); return; }
    await linkFace();
    fill(status, t("web.pf_face_done", { stored: result.stored, total: result.total }));
    redraw();
  };
  files.addEventListener("change", () => { if (files.files.length) send([...files.files]); files.value = ""; });
  const ready = () => { if (!consent.checked) { toast(t("web.consent_required"), true); return false; } return true; };
  const samples = (data.faces.find((f) => f.name === faceName) || {}).samples || 0;
  const dialog = modal("wg-dialog", h("h2", {}, icon("user-check"), " ", t("web.pf_face_title", { name: person.display_name })),
    h("p", { class: "muted" }, t("web.pf_face_intro", { samples })),
    data.faces_on === false ? h("p", { class: "notice" }, t("web.pf_face_off")) : null,
    h("label", { class: "check" }, consent, t("web.consent_label")),
    h("div", { class: "row wrap" },
      h("button", { class: "primary", onclick: () => { if (ready()) files.click(); } }, icon("upload"), t("web.pf_face_upload")),
      h("button", { onclick: () => { if (ready()) captureDialog(faceName, send); } }, icon("camera"), t("web.cap_open")), files),
    h("p", { class: "muted" }, h("small", {}, t("web.pf_face_hint"))), status,
    h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  return dialog;
}

function chip(iconName, text, title, tone = "") {
  return h("span", { class: `person-chip ${tone}`, title: title || text }, icon(iconName), text ? h("span", {}, text) : null);
}

function personCard(p, data, contacts, open, redraw) {
  const faces = linksOf(p, "face");
  const samples = faces.reduce((n, f) => n + ((data.faces.find((x) => x.name === f) || {}).samples || 0), 0);
  const accounts = linksOf(p, "web_account");
  const messengers = linksOf(p, "messenger").map((ref) => ref.split(":")[0]);
  const emergency = linksOf(p, "emergency_contact").length > 0;
  const medical = linksOf(p, "medical").length > 0;
  const vpn = linksOf(p, "vpn").length;
  const avatar = h("span", { class: "person-avatar", "aria-hidden": "true" }, initials(p.display_name));
  avatar.style.setProperty("--hue", hue(p.display_name));
  const menu = h("details", { class: "addon-menu" }, h("summary", { title: t("web.ad_more"), "aria-label": `${p.display_name}: ${t("web.ad_more")}` }, icon("dots-vertical")),
    h("div", { class: "addon-menu-body", role: "menu" },
      h("button", { role: "menuitem", onclick: () => { menu.open = false; open(p); } }, icon("pencil"), t("web.edit")),
      h("button", { role: "menuitem", class: "danger", onclick: async () => {
        menu.open = false;
        if (!await confirmDialog(t("web.person_delete_confirm", { name: p.display_name }), t("web.delete"), true)) return;
        if (await attempt(() => api(`/api/persons/${p.id}`, { method: "DELETE" }))) redraw();
      } }, icon("trash"), t("web.delete"))));
  return h("article", { class: "person-card", dataset: { text: `${p.display_name} ${p.note} ${(p.links || []).map((l) => l.ref).join(" ")}`.toLowerCase(), roles: (p.roles || []).join(" ") } },
    h("div", { class: "person-head" }, avatar,
      h("div", { class: "grow" }, h("h3", {}, p.display_name), p.note ? h("small", { class: "muted block" }, p.note) : null,
        h("div", { class: "row wrap person-roles" }, (p.roles || []).length ? p.roles.map((r) =>
          h("span", { class: `badge person-role-${r}`, title: t(`web.person_role_${r}_hint`) }, icon(ROLE_ICONS[r] || "user"), " ", t(`web.person_role_${r}`)))
          : h("small", { class: "muted" }, t("web.person_no_roles")))),
      menu),
    h("div", { class: "person-chips" },
      faces.length ? chip("user-check", t("web.pc_face", { samples }), t("web.pc_face_tip"), samples ? "ok" : "warn") : null,
      accounts.map((a) => chip("key", a, t("web.pc_account"))),
      messengers.map((m) => chip(BRAND[m] || "message", "", t(`web.ec_p_${m}`))),
      emergency ? chip("alert-triangle", t("web.pc_emergency"), t("web.pc_emergency_tip"), "warn") : null,
      medical ? chip("file-text", t("web.pc_medical"), t("web.pc_medical_tip")) : null,
      vpn ? chip("brand-wireguard", t("web.pc_vpn", { count: vpn }), t("web.pc_vpn_tip")) : null,
      !(p.links || []).length ? h("small", { class: "muted" }, t("web.person_no_links")) : null),
    h("div", { class: "person-actions" },
      h("button", { class: "small", onclick: () => open(p) }, icon("pencil"), t("web.pc_edit")),
      h("button", { class: "small", onclick: () => faceDialog(p, data, redraw) }, icon("photo"), t("web.pc_photos")),
      h("button", { class: "small", disabled: !channelsOf(p, contacts).length, title: channelsOf(p, contacts).length ? t("web.inv_intro") : t("web.inv_no_channel"),
        onclick: () => inviteDialog(p, contacts, redraw) }, icon("share"), t("web.pc_invite"))));
}

// Faces, accounts and emergency contacts nobody is linked to yet: one tap makes a person of each.
function loosePanel(data, contacts, redraw) {
  const linked = (kind) => new Set(data.persons.flatMap((p) => linksOf(p, kind)));
  const faces = data.faces.filter((f) => !linked("face").has(f.name));
  const accounts = data.accounts.filter((a) => !linked("web_account").has(a));
  const emergency = (contacts?.contacts || []).filter((c) => !c.legacy && !linked("emergency_contact").has(String(c.id)));
  const items = [
    ...faces.map((f) => ({ icon: "user-check", name: f.name, what: t("web.pc_loose_face", { samples: f.samples }), links: [{ kind: "face", ref: f.name }], roles: ["trusted"] })),
    ...accounts.map((a) => ({ icon: "key", name: a, what: t("web.pc_loose_account"), links: [{ kind: "web_account", ref: a }], roles: [] })),
    ...emergency.map((c) => ({ icon: "alert-triangle", name: c.name, what: t("web.pc_loose_emergency"), links: [{ kind: "emergency_contact", ref: String(c.id) }], roles: ["emergency"] })),
  ];
  if (!items.length) return null;
  return h("section", { class: "panel stack" }, h("h2", {}, icon("link"), " ", t("web.pc_loose_title")), h("p", { class: "muted" }, t("web.pc_loose_hint")),
    h("ul", { class: "list compact" }, items.map((item) => h("li", {}, icon(item.icon),
      h("div", { class: "grow" }, h("div", {}, item.name), h("small", { class: "muted" }, item.what)),
      h("button", { class: "small", onclick: async () => {
        const same = data.persons.find((p) => p.display_name.toLowerCase() === item.name.toLowerCase());
        const body = same ? { person: { ...same, links: [...(same.links || []), ...item.links] } }
          : { person: { display_name: item.name, roles: item.roles, links: item.links } };
        const url = same ? `/api/persons/${same.id}` : "/api/persons";
        if (await attempt(() => api(url, { method: same ? "PUT" : "POST", body }), t("web.saved"))) redraw();
      } }, icon(data.persons.some((p) => p.display_name.toLowerCase() === item.name.toLowerCase()) ? "link" : "plus"),
      t(data.persons.some((p) => p.display_name.toLowerCase() === item.name.toLowerCase()) ? "web.pc_loose_link" : "web.pc_loose_make"))))));
}

export async function personsPanel(box) {
  let query = "", role = "";
  const draw = async () => {
    const [data, contacts] = await Promise.all([attempt(() => api("/api/persons")), api("/api/contacts").catch(() => null)]);
    if (!data) { fill(box, h("p", { class: "empty" }, t("web.error"))); return; }
    const open = (person = {}) => openContactWizard(data, contacts, person, draw);
    const grid = h("div", { class: "person-grid" }, data.persons.map((p) => personCard(p, data, contacts, open, draw)));
    const apply = () => grid.querySelectorAll(".person-card").forEach((card) => {
      card.hidden = (query && !card.dataset.text.includes(query)) || (role && !card.dataset.roles.split(" ").includes(role));
    });
    const search = h("input", { type: "search", placeholder: t("web.pc_search"), "aria-label": t("web.pc_search"), value: query });
    search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); apply(); });
    const filters = h("div", { class: "chip-filter", role: "group", "aria-label": t("web.person_roles") },
      ["", ...(data.roles || [])].map((r) => h("button", { class: r === role ? "on" : "", onclick: (e) => {
        role = r;
        [...filters.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget));
        apply();
      } }, r ? [icon(ROLE_ICONS[r] || "user"), " ", t(`web.person_role_${r}`)] : t("web.all"))));
    fill(box,
      h("section", { class: "panel stack" },
        h("div", { class: "row between wrap" }, h("h2", {}, icon("users"), " ", t("web.pc_title", { count: data.persons.length })),
          h("div", { class: "row wrap" },
            h("button", { title: t("web.sw_teaser"), onclick: async () => (await import("./swipe.js")).openSwipe() }, icon("photo"), t("web.sw_title")),
            h("button", { class: "primary", onclick: () => open() }, icon("plus"), t("web.person_new")))),
        data.persons.length ? h("div", { class: "stack person-tools" }, search, filters) : null,
        data.persons.length ? grid : h("div", { class: "empty-state stack" }, icon("users", "lg"), h("p", {}, t("web.person_empty")),
          h("div", { class: "row" }, h("button", { class: "primary", onclick: () => open() }, icon("plus"), t("web.person_new"))))),
      loosePanel(data, contacts, draw));
    apply();
  };
  await draw();
}
