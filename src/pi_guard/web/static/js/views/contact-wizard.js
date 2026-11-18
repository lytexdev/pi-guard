// Full contact setup: who they are, roles, messengers, emergency share, access, face.
import { api, attempt, confirmDialog, fill, h, icon, t, toast } from "../api.js";

const ROLE_ICONS = {
  household: "home",
  trusted: "user-check",
  backup: "users",
  emergency: "alert-triangle",
  alert: "bell",
  viewer: "eye",
};
const BRAND = {
  telegram: "brand-telegram",
  signal: "brand-signal",
  discord: "brand-discord",
  whatsapp: "brand-whatsapp",
  email: "mail",
};
const FITS = {
  sos: ["location", "phone_photo", "phone_audio", "pi_photo", "events", "home_audio", "medical"],
  escalation: ["events", "pi_photo"],
};
const SHARE_ICON = {
  location: "map-pin",
  phone_photo: "device-mobile",
  phone_audio: "microphone-off",
  pi_photo: "camera",
  events: "list-details",
  home_audio: "volume",
  medical: "user-check",
};
const DEFAULT_SHARE = {
  sos: FITS.sos.filter((s) => !["events", "medical"].includes(s)),
  escalation: ["events", "pi_photo"],
};

function modal(...children) {
  const dialog = h("dialog", { class: "editor wide cw-dialog" }, ...children);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

function stepBar(steps, index) {
  return h("div", { class: "steps", "aria-hidden": "true" },
    steps.map((_, i) => h("span", { class: i < index ? "done" : i === index ? "now" : "" })));
}

function rolePicker(roles, selected) {
  const boxes = Object.fromEntries(roles.map((r) => [
    r, h("input", { type: "checkbox", checked: selected.includes(r) }),
  ]));
  const node = h("div", { class: "choice cw-roles" }, roles.map((r) => {
    const card = h("button", { type: "button", class: boxes[r].checked ? "on" : "" },
      icon(ROLE_ICONS[r] || "user"),
      h("strong", {}, t(`web.person_role_${r}`)),
      h("small", {}, t(`web.person_role_${r}_hint`)));
    card.addEventListener("click", () => {
      boxes[r].checked = !boxes[r].checked;
      card.classList.toggle("on", boxes[r].checked);
    });
    return card;
  }));
  return { node, value: () => roles.filter((r) => boxes[r].checked), boxes };
}

function messengerPicker(contactsData, existing = []) {
  // Only providers that are running get a clear on/off; others stay folded as optional.
  const byProvider = Object.fromEntries(existing.map((c) => [c.provider, c.address]));
  const rows = {};
  const card = (provider, active) => {
    const on = h("input", { type: "checkbox", checked: Boolean(byProvider[provider]) || active });
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
    return h("label", { class: `cw-channel${active ? " ready" : " muted-card"}` },
      h("div", { class: "row between" },
        h("span", { class: "row" }, icon(BRAND[provider] || "message"), h("strong", {}, t(`web.ec_p_${provider}`)),
          active ? null : h("span", { class: "badge" }, t("web.ec_not_running"))),
        on),
      body);
  };
  const running = contactsData.providers || [];
  const rest = (contactsData.all_providers || []).filter((p) => !running.includes(p));
  const list = h("div", { class: "stack cw-channels" },
    running.length ? null : h("p", { class: "notice" }, t("web.cw_no_messenger")),
    running.map((p) => card(p, true)),
    rest.length ? h("details", { class: "cw-more" },
      h("summary", {}, t("web.cw_more_messengers")),
      h("div", { class: "stack" }, rest.map((p) => card(p, false)))) : null);
  return {
    node: list,
    value: () => Object.entries(rows)
      .filter(([, r]) => r.on.checked && r.address.value.trim())
      .map(([provider, r]) => ({ provider, address: r.address.value.trim() })),
  };
}

function shareStep(contactsData, contact) {
  const share = contact.share || { ...DEFAULT_SHARE };
  const takes = {};
  const boxes = {};
  const head = h("tr", {}, h("th", {}, t("web.ec_gets")), (contactsData.occasions || ["sos", "escalation"]).map((o) => {
    takes[o] = h("input", { type: "checkbox", checked: o in share, "aria-label": t(`web.ec_occ_${o}`) });
    return h("th", {}, h("label", { class: "check" }, takes[o], t(`web.ec_occ_${o}`)));
  }));
  const shares = (contactsData.shares || Object.keys(SHARE_ICON)).filter((s) => s !== "medical" || contactsData.medical);
  const body = shares.map((s) => h("tr", {},
    h("td", {}, icon(SHARE_ICON[s]), " ", t(`web.ec_share_${s}`),
      h("small", { class: "muted block" }, t(`web.ec_share_${s}_hint`))),
    (contactsData.occasions || ["sos", "escalation"]).map((o) => {
      if (!(FITS[o] || []).includes(s)) return h("td", { class: "muted" }, "–");
      const box = h("input", {
        type: "checkbox",
        checked: (share[o] || []).includes(s),
        "aria-label": `${t(`web.ec_share_${s}`)} · ${t(`web.ec_occ_${o}`)}`,
      });
      (boxes[o] ||= {})[s] = box;
      return h("td", {}, box);
    })));
  const sync = () => Object.keys(takes).forEach((o) => Object.values(boxes[o] || {}).forEach((b) => { b.disabled = !takes[o].checked; }));
  Object.values(takes).forEach((b) => b.addEventListener("change", sync));
  sync();
  const mode = h("select", {}, (contactsData.medical_modes || ["note", "sheet"]).map((m) =>
    h("option", { value: m, selected: m === (contact.medical_mode || "note") }, t(`web.ec_mode_${m}`))));
  const modeHint = h("p", { class: "muted" }, h("small", {}, t(`web.ec_mode_${mode.value}_hint`)));
  mode.addEventListener("change", () => fill(modeHint, h("small", {}, t(`web.ec_mode_${mode.value}_hint`))));
  return {
    node: h("div", { class: "stack" },
      h("p", { class: "muted" }, t("web.ec_share_intro")),
      h("div", { class: "table-wrap" }, h("table", { class: "share-matrix" }, h("thead", {}, head), h("tbody", {}, body))),
      contactsData.medical ? h("div", { class: "field" }, h("label", {}, t("web.ec_f_mode")), mode, modeHint) : null),
    value: () => Object.fromEntries(Object.keys(takes).filter((o) => takes[o].checked)
      .map((o) => [o, Object.entries(boxes[o] || {}).filter(([, b]) => b.checked).map(([s]) => s)])),
    medicalSheet: () => Boolean(Object.values(boxes).some((o) => o.medical?.checked) && mode.value === "sheet"),
    medicalMode: () => mode.value,
  };
}

function accessStep(personsData, person) {
  const linked = (person.links || []).find((l) => l.kind === "web_account")?.ref || "";
  const mode = h("select", {},
    h("option", { value: "none" }, t("web.cw_access_none")),
    h("option", { value: "link", selected: Boolean(linked) }, t("web.cw_access_link")),
    h("option", { value: "create" }, t("web.cw_access_create")));
  const account = h("select", {}, h("option", { value: "" }, t("web.none")),
    (personsData.accounts || []).map((a) => h("option", { value: a, selected: a === linked }, a)));
  const username = h("input", { maxlength: 32, autocomplete: "off" });
  const made = h("input", { type: "radio", name: "cw_pw", value: "made", checked: true });
  const own = h("input", { type: "radio", name: "cw_pw", value: "own" });
  const password = h("input", { type: "password", maxlength: 128, autocomplete: "new-password", minlength: 10 });
  const passwordBox = h("div", { class: "field hidden" }, h("label", {}, t("web.password")), password);
  [made, own].forEach((r) => r.addEventListener("change", () => passwordBox.classList.toggle("hidden", made.checked)));
  const role = h("select", {}, ["viewer", "member", "admin"].map((r) =>
    h("option", { value: r, selected: r === "viewer" }, t(`web.role_${r}`))));
  const linkBox = h("div", { class: linked ? "stack" : "stack hidden" },
    h("div", { class: "field" }, h("label", {}, t("web.person_account")), account));
  const createBox = h("div", { class: "stack hidden" },
    h("div", { class: "field" }, h("label", {}, t("web.username")), username),
    h("div", { class: "stack" }, h("label", { class: "check" }, made, t("web.cw_pw_made")), h("label", { class: "check" }, own, t("web.cw_pw_own"))),
    passwordBox,
    h("div", { class: "field" }, h("label", {}, t("web.role")), role),
    h("p", { class: "muted" }, h("small", {}, t("web.cw_access_create_hint"))));
  const sync = () => {
    linkBox.classList.toggle("hidden", mode.value !== "link");
    createBox.classList.toggle("hidden", mode.value !== "create");
  };
  mode.addEventListener("change", sync);
  sync();
  return {
    node: h("div", { class: "stack" },
      h("p", { class: "muted" }, t("web.cw_access_hint")),
      h("div", { class: "field" }, h("label", {}, t("web.cw_access_mode")), mode),
      linkBox, createBox),
    value: () => ({
      mode: mode.value,
      account: account.value,
      username: username.value.trim(),
      password: made.checked ? "" : password.value,
      role: role.value,
    }),
  };
}

function faceEntries(personsData) {
  // API used to return plain names; now { name, samples }.
  return (personsData.faces || []).map((f) => (typeof f === "string" ? { name: f, samples: 0 } : f));
}

function faceStep(personsData, person, nameInput) {
  const linked = (person.links || []).find((l) => l.kind === "face")?.ref || "";
  let chosen = linked;
  const enroll = h("input", { type: "checkbox", checked: !linked });
  const photo = h("input", { type: "file", accept: "image/*,video/*", multiple: true, "aria-label": t("web.cw_photo") });
  const preview = h("div", { class: "cw-photo-preview muted" }, t("web.cw_photo_optional"));
  const faces = faceEntries(personsData);
  const grid = h("div", { class: "cw-faces" });
  const paintFaces = () => fill(grid,
    h("button", { type: "button", class: `cw-face${!chosen ? " on" : ""}`, onclick: () => { chosen = ""; paintFaces(); } },
      h("strong", {}, t("web.cw_face_later")), h("small", { class: "muted" }, t("web.cw_face_later_hint"))),
    faces.map((f) => h("button", {
      type: "button",
      class: `cw-face${chosen === f.name ? " on" : ""}`,
      onclick: () => { chosen = f.name; enroll.checked = false; paintFaces(); },
    }, h("strong", {}, f.name),
      h("small", { class: "muted" }, t("web.cw_face_samples", { n: f.samples || 0 })))));
  paintFaces();
  photo.addEventListener("change", () => {
    const file = photo.files?.[0];
    if (!file) return;
    enroll.checked = true;
    chosen = "";
    paintFaces();
    if (file.type.startsWith("image/")) {
      fill(preview, h("img", { src: URL.createObjectURL(file), alt: "" }));
    } else {
      fill(preview, h("span", {}, t("web.cw_photo_files", { n: photo.files.length })));
    }
  });
  return {
    node: h("div", { class: "stack" },
      h("p", { class: "muted" }, t("web.cw_face_hint")),
      faces.length ? h("div", { class: "stack" }, h("strong", {}, t("web.person_face")), grid)
        : h("p", { class: "notice" }, t("web.cw_face_none")),
      h("label", { class: "check" }, enroll, t("web.cw_face_enroll", { name: nameInput.value.trim() || "…" })),
      h("div", { class: "field" }, h("label", {}, t("web.cw_photo")), photo),
      preview,
      h("p", { class: "muted" }, h("small", {}, t("web.cw_face_swipe")))),
    value: () => ({
      face: chosen,
      enroll: enroll.checked && !chosen,
      photo: photo.files?.[0] || null,
      files: [...(photo.files || [])],
    }),
  };
}

function medicalStep(personsData, contactsData, person, nameInput) {
  const linked = (person.links || []).find((l) => l.kind === "medical")?.ref || "";
  const existing = (personsData.medical || []).find((m) => String(m.id) === String(linked));
  const want = h("input", { type: "checkbox", checked: Boolean(linked || existing) });
  const link = h("select", {},
    h("option", { value: "" }, t("web.cw_medical_new")),
    (personsData.medical || []).map((m) => h("option", { value: String(m.id), selected: String(m.id) === String(linked) }, m.name)));
  const blood = h("select", {}, h("option", { value: "" }, "–"),
    ["A+", "A-", "B+", "B-", "AB+", "AB-", "0+", "0-"].map((b) => h("option", { value: b }, b)));
  const allergies = h("textarea", { rows: 2, maxlength: 2000, placeholder: t("web.cw_medical_allergies_ph") });
  const body = h("div", { class: want.checked ? "stack" : "stack hidden" },
    h("div", { class: "field" }, h("label", {}, t("web.cw_medical_link")), link),
    h("div", { class: "field" }, h("label", {}, t("web.md_f_blood_type")), blood),
    h("div", { class: "field" }, h("label", {}, t("web.md_f_allergies")), allergies),
    h("p", { class: "muted" }, h("small", {}, t("web.cw_medical_hint"))));
  want.addEventListener("change", () => body.classList.toggle("hidden", !want.checked));
  link.addEventListener("change", () => {
    const locked = Boolean(link.value);
    blood.disabled = locked;
    allergies.disabled = locked;
  });
  return {
    available: Boolean(personsData.medical_available || contactsData?.medical),
    node: h("div", { class: "stack" },
      h("label", { class: "check" }, want, t("web.cw_medical_want")),
      body),
    value: () => (!want.checked ? null : {
      linkId: link.value ? Number(link.value) : null,
      blood_type: blood.value,
      allergies: allergies.value.trim(),
      name: nameInput.value.trim(),
    }),
  };
}

async function saveEmergency(contactsData, draft, channels, share, medicalMode, personId) {
  if (!channels.length) {
    toast(t("web.ec_needs_channel"), true);
    return null;
  }
  const contact = {
    name: draft.name,
    relation: draft.relation,
    phone: draft.phone,
    email: draft.email,
    note: draft.note,
    account: draft.account || "",
    channels,
    share,
    medical_mode: medicalMode,
    person_id: personId || undefined,
  };
  const existing = draft.emergencyId
    || (contactsData.contacts || []).find((c) => !c.legacy && c.name === draft.name)?.id;
  const url = existing ? `/api/contacts/${existing}` : "/api/contacts";
  const method = existing ? "PUT" : "POST";
  const result = await attempt(() => api(url, { method, body: { contact, person_id: personId || undefined } }));
  return result ? (existing || result.id) : null;
}

// A new login always starts with a temporary password: made here, or typed by the admin. The
// person chooses an own one at the first login.
async function maybeCreateAccount(access) {
  if (access.mode !== "create") return { name: access.mode === "link" ? access.account : "" };
  if (!access.username) {
    toast(t("web.cw_access_need_creds"), true);
    return null;
  }
  const ok = await attempt(() => api("/api/dashboard-users", {
    method: "POST",
    body: { username: access.username, password: access.password, role: access.role || "viewer", temporary: true },
  }), t("web.saved"));
  return ok ? { name: access.username, password: ok.password || access.password } : null;
}

function accountMade(account, personId) {
  const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, icon("key"), " ", t("web.cw_made_title")),
    h("p", {}, t("web.cw_made_text", { user: account.name })),
    h("p", { class: "row wrap" }, h("code", { class: "big" }, account.password),
      h("button", { class: "small", onclick: () => navigator.clipboard?.writeText(account.password).then(() => toast(t("web.copied"))) }, icon("copy"), t("web.copy"))),
    h("p", { class: "muted" }, h("small", {}, t("web.cw_made_hint"))),
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.done")),
      h("button", { class: "primary", onclick: async () => { dialog.close(); (await import("./persons.js")).inviteById(personId, ["dashboard", "android", "desktop", "login"]); } },
        icon("send"), t("web.cw_made_invite"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

async function maybeEnrollFace(name, faceOpt) {
  if (faceOpt.face) return faceOpt.face;
  const files = (faceOpt.files || []).length ? faceOpt.files : (faceOpt.photo ? [faceOpt.photo] : []);
  if (!faceOpt.enroll && !files.length) return "";
  const faceName = name.slice(0, 64);
  if (files.length) {
    const form = new FormData();
    form.append("consent", "yes");
    files.forEach((f) => form.append("files", f, f.name || "photo.jpg"));
    const result = await attempt(() => api(`/api/people/${encodeURIComponent(faceName)}/samples`, {
      method: "POST", body: form,
    }));
    if (!result) return "";
  }
  return faceName;
}

async function maybeSaveMedical(medicalVal, accountName) {
  if (!medicalVal) return "";
  if (medicalVal.linkId) return String(medicalVal.linkId);
  if (!medicalVal.name) return "";
  const record = {
    name: medicalVal.name,
    blood_type: medicalVal.blood_type || "",
    allergies: medicalVal.allergies || "",
    account: accountName || "",
  };
  const saved = await attempt(() => api("/api/medical", { method: "POST", body: { record } }));
  return saved ? String(saved.id) : "";
}

/**
 * @param {object} personsData from /api/persons
 * @param {object} contactsData from /api/contacts (may be null)
 * @param {object} person existing person or {}
 * @param {() => void} onDone
 */
export async function openContactWizard(personsData, contactsData, person = {}, onDone = () => {}) {
  contactsData ||= await attempt(() => api("/api/contacts")) || {
    providers: [], all_providers: ["telegram", "signal", "discord", "whatsapp", "email"],
    occasions: ["sos", "escalation"], shares: Object.keys(SHARE_ICON), medical: false,
    medical_modes: ["note", "sheet"], contacts: [], accounts: personsData.accounts || [],
  };

  const linkedEmergency = (person.links || []).find((l) => l.kind === "emergency_contact");
  const emergencyContact = linkedEmergency
    ? (contactsData.contacts || []).find((c) => String(c.id) === String(linkedEmergency.ref))
    : (contactsData.contacts || []).find((c) => !c.legacy && c.name === person.display_name);
  const messengerLinks = (person.links || []).filter((l) => l.kind === "messenger").map((l) => {
    const [provider, ...rest] = l.ref.split(":");
    return { provider, address: rest.join(":") };
  });
  const fromEmergency = emergencyContact?.channels || [];
  const channelsSeed = messengerLinks.length ? messengerLinks : fromEmergency;

  const name = h("input", { value: person.display_name || "", maxlength: 60, required: true });
  const note = h("input", { value: person.note || "", maxlength: 300 });
  const relation = h("input", { value: emergencyContact?.relation || "", maxlength: 40 });
  const phone = h("input", { type: "tel", value: emergencyContact?.phone || "", maxlength: 40 });
  const email = h("input", { type: "email", value: emergencyContact?.email || "", maxlength: 200 });

  const roles = rolePicker(personsData.roles || [], person.roles || []);
  const messengers = messengerPicker(contactsData, channelsSeed);
  const share = shareStep(contactsData, emergencyContact || { share: { ...DEFAULT_SHARE }, medical_mode: "note" });
  const access = accessStep(personsData, person);
  const face = faceStep(personsData, person, name);
  const medical = medicalStep(personsData, contactsData, person, name);

  const STEPS = [
    { id: "who", title: "web.cw_step_who", need: () => true },
    { id: "roles", title: "web.cw_step_roles", need: () => true },
    { id: "reach", title: "web.cw_step_reach", need: () => true },
    {
      id: "notify",
      title: "web.cw_step_notify",
      need: () => roles.value().includes("emergency") || roles.value().includes("alert"),
    },
    {
      id: "access",
      title: "web.cw_step_access",
      need: () => roles.value().includes("viewer") || roles.value().includes("household"),
    },
    {
      id: "face",
      title: "web.cw_step_face",
      need: () => roles.value().includes("trusted") || roles.value().includes("household"),
    },
    {
      id: "medical",
      title: "web.cw_step_medical",
      need: () => medical.available,
    },
    { id: "done", title: "web.cw_step_done", need: () => true },
  ];

  let index = 0;
  const pane = h("div", { class: "cw-pane stack" });
  const nav = h("div", { class: "wizard-nav" });
  const badge = h("span", { class: "badge" });
  const title = h("h2", {});
  const intro = h("p", { class: "muted" });
  const stepsEl = h("div");

  const dialog = modal(
    h("div", { class: "row between wrap" }, title, badge,
      h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))),
    intro, stepsEl, pane, nav);

  const activeSteps = () => STEPS.filter((s) => s.need());

  function paint() {
    const steps = activeSteps();
    index = Math.min(index, steps.length - 1);
    const step = steps[index];
    fill(badge, t("web.setup_step", { n: index + 1, total: steps.length }));
    fill(title, t(step.title));
    fill(intro, t(`${step.title}_text`));
    fill(stepsEl, stepBar(steps, index));

    if (step.id === "who") {
      fill(pane,
        h("div", { class: "field" }, h("label", {}, t("web.name")), name),
        h("div", { class: "grid two" },
          h("div", { class: "field" }, h("label", {}, t("web.ec_f_relation")), relation),
          h("div", { class: "field" }, h("label", {}, t("web.person_note")), note)),
        h("div", { class: "grid two" },
          h("div", { class: "field" }, h("label", {}, t("web.ec_f_phone")), phone),
          h("div", { class: "field" }, h("label", {}, t("web.ec_f_email")), email)));
    } else if (step.id === "roles") {
      fill(pane, h("p", { class: "muted" }, t("web.person_roles_hint")), roles.node);
    } else if (step.id === "reach") {
      fill(pane, h("p", { class: "muted" }, t("web.cw_reach_hint")), messengers.node);
    } else if (step.id === "notify") {
      fill(pane, share.node);
    } else if (step.id === "access") {
      fill(pane, access.node);
    } else if (step.id === "face") {
      fill(pane, face.node);
    } else if (step.id === "medical") {
      fill(pane, medical.node);
    } else {
      const picked = roles.value();
      fill(pane, h("div", { class: "stack cw-review" },
        h("p", {}, h("strong", {}, name.value.trim() || "—")),
        h("div", { class: "row wrap" }, picked.map((r) =>
          h("span", { class: `badge person-role-${r}` }, icon(ROLE_ICONS[r]), " ", t(`web.person_role_${r}`)))),
        messengers.value().length
          ? h("p", { class: "muted" }, messengers.value().map((c) => `${t(`web.ec_p_${c.provider}`)}: ${c.address}`).join(" · "))
          : h("p", { class: "muted" }, t("web.cw_review_no_reach")),
        medical.value() ? h("p", { class: "muted" }, t("web.cw_review_medical")) : null,
        h("p", { class: "muted" }, t("web.cw_review_save"))));
    }

    const back = index > 0
      ? h("button", { type: "button", onclick: () => { index -= 1; paint(); } }, icon("chevron-left"), t("web.back"))
      : h("button", { type: "button", onclick: () => dialog.close() }, t("web.cancel"));
    const next = step.id === "done"
      ? h("button", { class: "primary", type: "button", onclick: finish }, icon("check"), t("web.save"))
      : h("button", { class: "primary", type: "button", onclick: () => {
        if (step.id === "who" && !name.value.trim()) { toast(t("web.person_needs_name"), true); name.focus(); return; }
        index += 1;
        paint();
      } }, t("web.next"), icon("chevron-right"));
    fill(nav, back, h("span", { class: "grow" }), next);
  }

  async function finish() {
    const displayName = name.value.trim();
    if (!displayName) { toast(t("web.person_needs_name"), true); return; }
    const picked = roles.value();
    const channels = messengers.value();
    if ((picked.includes("emergency") || picked.includes("alert") || picked.includes("backup")) && !channels.length) {
      toast(t("web.ec_needs_channel"), true);
      return;
    }
    if (share.medicalSheet && share.medicalSheet()
      && !await confirmDialog(t("web.ec_sheet_confirm"), t("web.ec_sheet_yes"))) return;

    const accessVal = access.value();
    const created = await maybeCreateAccount(accessVal);
    if (created === null) return;
    const accountName = created.name;

    const faceOpt = face.value();
    let faceRef = faceOpt.face;
    if (picked.includes("trusted") || picked.includes("household")) {
      const enrolled = await maybeEnrollFace(displayName, faceOpt);
      if (enrolled) faceRef = enrolled;
    }
    const medicalRef = await maybeSaveMedical(medical.value(), accountName);

    // Person first so emergency save can attach person_id (avoids a duplicate by name).
    // Links this wizard does not edit (VPN devices from an invitation) stay.
    const links = (person.links || []).filter((l) => l.kind === "vpn");
    if (faceRef) links.push({ kind: "face", ref: faceRef });
    if (accountName) links.push({ kind: "web_account", ref: accountName });
    if (medicalRef) links.push({ kind: "medical", ref: medicalRef });
    if (emergencyContact?.id) links.push({ kind: "emergency_contact", ref: String(emergencyContact.id) });
    channels.forEach((c) => links.push({ kind: "messenger", ref: `${c.provider}:${c.address}` }));

    const personBody = {
      person: {
        display_name: displayName,
        note: note.value.trim(),
        roles: picked,
        links,
      },
    };
    const url = person.id ? `/api/persons/${person.id}` : "/api/persons";
    const method = person.id ? "PUT" : "POST";
    const saved = await attempt(() => api(url, { method, body: personBody }), t("web.saved"));
    if (!saved) return;
    const personId = person.id || saved.id;

    let emergencyId = emergencyContact?.id || null;
    if (picked.includes("emergency") || picked.includes("alert") || channels.length) {
      emergencyId = await saveEmergency(contactsData, {
        name: displayName, relation: relation.value.trim(), phone: phone.value.trim(),
        email: email.value.trim(), note: note.value.trim(), account: accountName || "",
        emergencyId: emergencyContact?.id,
      }, channels, share.value(), share.medicalMode(), personId);
      if (!emergencyId && (picked.includes("emergency") || picked.includes("alert"))) return;
      if (emergencyId && String(emergencyId) !== String(emergencyContact?.id || "")) {
        links.push({ kind: "emergency_contact", ref: String(emergencyId) });
        await attempt(() => api(`/api/persons/${personId}`, {
          method: "PUT",
          body: { person: { ...personBody.person, links } },
        }));
      }
    }

    dialog.close();
    onDone();
    if (created.password) accountMade(created, personId);
    if (faceOpt.enroll && (picked.includes("trusted") || picked.includes("household"))) {
      toast(t("web.cw_face_next"));
    }
  }

  paint();
  name.focus();
}
