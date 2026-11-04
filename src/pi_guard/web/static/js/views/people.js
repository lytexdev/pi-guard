// Trusted people: add, add more pictures, rename, delete.
import { api, attempt, confirmDialog, h, icon, t, toast, when, fill } from "../api.js";
import { captureDialog } from "./capture.js";

// What makes recognition work, in the order it matters when adding someone.
const GUIDE = [["user-check", "ask"], ["sun", "light"], ["eye", "face"], ["refresh", "angles"], ["circle-check", "test"]];

function guide(open) {
  return h("details", { class: "guide", open }, h("summary", {}, icon("bulb"), t("web.pt_title")),
    h("ol", {}, GUIDE.map(([name, id]) => h("li", {}, icon(name), h("div", {}, h("strong", {}, t(`web.pt_${id}`)), h("small", {}, t(`web.pt_${id}_text`)))))),
    h("p", { class: "muted" }, h("small", {}, t("web.pt_privacy"))));
}

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
    const files = h("input", { class: "hidden", type: "file", multiple: true, accept: "image/*,video/*" });
    const consent = h("input", { id: "pconsent", type: "checkbox" });
    const camSel = h("select", { id: "pcam", "aria-label": t("web.camera") }, overview.cameras.map((c) => h("option", { value: c.id }, c.name)));
    const status = h("p", { class: "muted", "aria-live": "polite" });
    const check = () => {
      if (!name.value.trim()) { toast(t("web.name_required"), true); name.focus(); return false; }
      if (!consent.checked) { toast(t("web.consent_required"), true); return false; }
      return true;
    };
    const send = async (chosen) => {
      const form = new FormData();
      form.append("consent", "yes");
      chosen.forEach((f) => form.append("files", f, f.name));
      fill(status, t("web.processing"));
      const result = await attempt(() => api(`/api/people/${encodeURIComponent(name.value.trim())}/samples`, { method: "POST", body: form }));
      fill(status, result ? enrollSummary(result) : "");
      if (result) drawList();
    };
    files.addEventListener("change", () => { if (files.files.length) send([...files.files]); files.value = ""; });
    const live = h("button", { class: "primary big", type: "button", onclick: () => { if (check()) captureDialog(name.value.trim(), send); } }, icon("camera"), t("web.cap_open"));
    const upload = h("button", { class: "big", type: "button", onclick: () => { if (check()) files.click(); } }, icon("upload"), t("web.upload_pictures"));
    const fromCamera = h("button", { type: "button", onclick: async () => {
      if (!check()) return;
      const result = await attempt(() => api(`/api/people/${encodeURIComponent(name.value.trim())}/camera`,
        { method: "POST", body: { camera: camSel.value, consent: true } }));
      if (result) { fill(status, t("web.enroll_camera_ok", { total: result.total }), qualityView(result.quality)); drawList(); }
    } }, icon("video"), t("faces.camera_button"));
    fill(formPanel,
      h("h2", {}, preset ? t("faces.more_button", { name: preset }) : t("faces.add_button")),
      guide(!preset),
      h("div", { class: "field" }, h("label", { for: "pname" }, t("web.name")), name),
      h("label", { class: "check", for: "pconsent" }, consent, t("web.consent_label")),
      h("div", { class: "enroll-ways" }, live, upload, files),
      h("div", { class: "hint" }, t("web.enroll_hint")),
      overview.cameras.length ? h("div", { class: "row enroll-home" }, overview.cameras.length > 1 ? camSel : null, fromCamera) : null,
      status);
  };

  const drawList = async () => {
    const people = await api("/api/people");
    fill(listPanel, h("h2", {}, t("faces.list_button")),
      h("ul", { class: "list" }, people.length ? people.map((p) => h("li", { class: "person-row" },
        icon("user-check", "lg"),
        h("div", { class: "grow" }, h("div", {}, p.name), h("small", {}, t("web.samples_since", { samples: p.samples, date: when(p.enrolled) })),
          p.similar_to ? h("small", { class: "warn-text" }, h("br"), t("web.similar_warning", { other: p.similar_to })) : null,
        ),
        h("button", { class: "icon-only", title: t("faces.more_button", { name: p.name }), onclick: () => { enrollForm(p.name); formPanel.scrollIntoView({ behavior: "smooth" }); } }, icon("plus")),
        h("button", { class: "icon-only", title: t("web.pp_details"), onclick: () => detailsDialog(p, drawList) }, icon("list-details")),
        h("button", { class: "icon-only", title: t("web.rename"), onclick: async () => {
          const next = prompt(t("web.new_name"), p.name);
          if (next && next.trim() !== p.name && await attempt(() => api(`/api/people/${encodeURIComponent(p.name)}`, { method: "PATCH", body: { name: next.trim() } }), t("web.saved"))) drawList();
        } }, icon("pencil")),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await confirmDialog(t("web.delete_person_confirm", { name: p.name }), t("web.delete"), true)
              && await attempt(() => api(`/api/people/${encodeURIComponent(p.name)}`, { method: "DELETE" }), t("web.deleted"))) drawList();
        } }, icon("trash")), qualityView(p.quality)))
        : h("li", { class: "empty" }, t("faces.none"))),
      h("p", { class: "muted" }, h("small", {}, t("web.faces_privacy"))));
  };
  enrollForm();
  await drawList();
  main.append(h("div", { class: "grid wide" }, listPanel, formPanel), testPanel());
}

function enrollSummary(result) {
  const reasons = Object.entries(result.reasons || {});
  const notes = [];
  if (result.outliers) notes.push(t("faces.outliers", { count: result.outliers }));
  Object.entries(result.conflicts || {}).forEach(([other, count]) => notes.push(t("faces.conflict", { count, other })));
  if (!result.stored) notes.push(t("faces.nothing_stored"));
  return [
    h("span", {}, t("web.enroll_result", { stored: result.stored, skipped: result.skipped, pictures: result.pictures ?? result.skipped + result.stored, total: result.total })),
    reasons.length ? h("small", { class: "muted" }, h("br"), t("web.enroll_reasons"), " ",
      reasons.map(([key, count]) => `${t(key)} (${count}×)`).join(" · ")) : null,
    notes.map((note) => h("small", { class: "warn-text" }, h("br"), note)),
    result.total ? qualityView(result.quality) : null,
  ];
}

// How well someone is learned and what would help most. An estimate, the test below is the proof.
function qualityView(q) {
  if (!q) return null;
  const bar = h("i");
  bar.style.width = `${q.score}%`;
  const meter = h("div", { class: `meter${{ weak: " danger", ok: " warn", good: "" }[q.level]}`, role: "img", "aria-label": t("web.pq_score", { score: q.score }) }, bar);
  return h("div", { class: "quality", title: t("web.pq_tip") },
    h("div", { class: "row between" }, h("small", {}, t(`web.pq_${q.level}`)), h("small", {}, t("web.pq_score", { score: q.score }))), meter,
    h("small", { class: "muted" }, t("web.pq_views", q.views)),
    q.tips.length ? h("ul", { class: "quality-tips" }, q.tips.map((tip) => h("li", {}, h("small", {}, t(tip.key, tip))))) : null);
}

function testPanel() {
  // Upload a photo and see who pi-guard would recognise; nothing is stored.
  const file = h("input", { id: "ptest", type: "file", accept: "image/*" });
  const out = h("div", { "aria-live": "polite" });
  const button = h("button", { type: "button", onclick: async () => {
    if (!file.files.length) { toast(t("web.test_needs_photo"), true); return; }
    const form = new FormData();
    form.append("file", file.files[0], file.files[0].name);
    button.disabled = true;
    const result = await attempt(() => api("/api/people-test", { method: "POST", body: form }));
    button.disabled = false;
    if (!result) return;
    fill(out, result.faces.length ? h("ul", { class: "list" }, result.faces.map((f) => h("li", {},
      icon(f.verdict === "known" ? "user-check" : f.verdict === "unknown" ? "user-x" : "user"),
      h("div", { class: "grow" },
        h("div", {}, f.verdict === "known" ? t("web.test_known", { name: f.name, score: f.score })
          : f.verdict === "unknown" ? t("web.test_unknown", { score: f.score }) : t("web.test_uncertain")),
        f.reason ? h("small", { class: "muted" }, t(f.reason)) : null,
        f.runner_up ? h("small", { class: "muted" }, f.reason ? " · " : "", t("web.test_runner_up", { name: f.runner_up, score: f.runner_up_score })) : null))))
      : h("p", { class: "muted" }, t("web.test_no_face")));
  } }, icon("user-check"), t("web.test_button"));
  return h("section", { class: "panel" },
    h("h2", {}, t("web.test_title")),
    h("p", { class: "muted" }, t("web.test_hint")),
    h("div", { class: "row" }, file, button),
    out);
}

async function detailsDialog(person, redraw) {
  const rows = await attempt(() => api(`/api/people/${encodeURIComponent(person.name)}`));
  if (!rows) return;
  const list = h("ul", { class: "list" });
  const draw = (items) => fill(list, items.map((r) => h("li", {}, icon("photo"),
    h("div", { class: "grow" }, h("div", {}, when(r.created)), h("small", {}, t("web.pp_sample", { numbers: r.numbers, bytes: r.bytes, match: Math.round(r.match * 100) }))),
    r.pose ? h("span", { class: "badge" }, t(`web.pq_view_${r.pose}`)) : null,
    r.match < 0.45 ? h("span", { class: "badge danger" }, t("web.pp_odd")) : null,
    h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
      if (await attempt(() => api(`/api/people-samples/${r.id}`, { method: "DELETE" }))) { items = items.filter((x) => x.id !== r.id); draw(items); redraw(); }
    } }, icon("trash")))));
  draw(rows);
  const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, person.name),
    h("p", { class: "muted" }, t("web.pp_intro", { count: rows.length, by: rows[0]?.by || "–" })), list,
    h("div", { class: "row end" }, h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}
