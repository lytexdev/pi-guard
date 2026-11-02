// Trusted people: add, add more pictures, rename, delete.
import { api, attempt, confirmDialog, h, icon, t, toast, when, fill } from "../api.js";

export async function render(main, params = [], opts = {}) {
  const overview = await api("/api/overview");
  fill(main, opts.embedded ? null : h("div", { class: "topbar" }, h("h1", {}, t("web.nav_people"))));
  if (!overview.features.faces) {
    main.append(h("section", { class: "panel" }, h("p", {}, t("web.faces_disabled")), h("p", { class: "muted" }, t("web.faces_enable_hint"))));
    return;
  }
  const listPanel = h("section", { class: "panel" });
  const formPanel = h("section", { class: "panel" });

  const enrollForm = (preset = "") => {
    const name = h("input", { id: "pname", required: true, maxlength: 64, value: preset });
    const files = h("input", { id: "pfiles", type: "file", multiple: true, accept: "image/*,video/*" });
    const consent = h("input", { id: "pconsent", type: "checkbox" });
    const camSel = h("select", { id: "pcam" }, overview.cameras.map((c) => h("option", { value: c.id }, c.name)));
    const status = h("p", { class: "muted", "aria-live": "polite" });
    const check = () => {
      if (!name.value.trim()) { toast(t("web.name_required"), true); return false; }
      if (!consent.checked) { toast(t("web.consent_required"), true); return false; }
      return true;
    };
    const upload = h("button", { class: "primary", type: "button", onclick: async () => {
      if (!check() || !files.files.length) return;
      const form = new FormData();
      form.append("consent", "yes");
      [...files.files].forEach((f) => form.append("files", f, f.name));
      upload.disabled = true; status.textContent = t("web.processing");
      const result = await attempt(() => api(`/api/people/${encodeURIComponent(name.value.trim())}/samples`, { method: "POST", body: form }));
      upload.disabled = false;
      status.textContent = result ? t("web.enroll_result", { stored: result.stored, skipped: result.skipped, total: result.total }) : "";
      if (result) { files.value = ""; drawList(); }
    } }, icon("upload"), t("web.upload_pictures"));
    const fromCamera = h("button", { type: "button", onclick: async () => {
      if (!check()) return;
      const result = await attempt(() => api(`/api/people/${encodeURIComponent(name.value.trim())}/camera`,
        { method: "POST", body: { camera: camSel.value, consent: true } }));
      if (result) { status.textContent = t("web.enroll_camera_ok", { total: result.total }); drawList(); }
    } }, icon("camera"), t("faces.camera_button"));
    fill(formPanel,
      h("h2", {}, preset ? t("faces.more_button", { name: preset }) : t("faces.add_button")),
      h("div", { class: "field" }, h("label", { for: "pname" }, t("web.name")), name),
      h("div", { class: "field" }, h("label", { for: "pfiles" }, t("web.pictures_or_videos")), files,
        h("div", { class: "hint" }, t("web.enroll_hint"))),
      h("label", { class: "check", for: "pconsent" }, consent, t("web.consent_label")),
      h("div", { class: "row" }, upload,
        overview.cameras.length ? [overview.cameras.length > 1 ? camSel : null, fromCamera] : null),
      status);
  };

  const drawList = async () => {
    const people = await api("/api/people");
    fill(listPanel, h("h2", {}, t("faces.list_button")),
      h("ul", { class: "list" }, people.length ? people.map((p) => h("li", {},
        icon("user-check", "lg"),
        h("div", { class: "grow" }, h("div", {}, p.name), h("small", {}, t("web.samples_since", { samples: p.samples, date: when(p.enrolled) }))),
        h("button", { class: "icon-only", title: t("faces.more_button", { name: p.name }), onclick: () => { enrollForm(p.name); formPanel.scrollIntoView({ behavior: "smooth" }); } }, icon("plus")),
        h("button", { class: "icon-only", title: t("web.rename"), onclick: async () => {
          const next = prompt(t("web.new_name"), p.name);
          if (next && next.trim() !== p.name && await attempt(() => api(`/api/people/${encodeURIComponent(p.name)}`, { method: "PATCH", body: { name: next.trim() } }), t("web.saved"))) drawList();
        } }, icon("pencil")),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await confirmDialog(t("web.delete_person_confirm", { name: p.name }), t("web.delete"), true)
              && await attempt(() => api(`/api/people/${encodeURIComponent(p.name)}`, { method: "DELETE" }), t("web.deleted"))) drawList();
        } }, icon("trash"))))
        : h("li", { class: "empty" }, t("faces.none")))),
      h("p", { class: "muted" }, h("small", {}, t("web.faces_privacy"))));
  };
  enrollForm();
  await drawList();
  main.append(h("div", { class: "grid wide" }, listPanel, formPanel));
}
