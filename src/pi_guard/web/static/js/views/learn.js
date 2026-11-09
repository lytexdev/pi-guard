// "Who is this?" on a person event: say who a face in the picture is, or is not.
import { api, attempt, h, t, toast, fill } from "../api.js";

function seen(face) {
  if (face.verdict === "known") return t("web.lf_seen_known", { name: face.name, score: Math.round(face.score * 100) / 100 });
  return t(face.verdict === "uncertain" ? "web.lf_seen_uncertain" : "web.lf_seen_unknown");
}

function marker(face, data, number) {
  const [x, y, w, h_] = face.box;
  const box = h("span", { class: "face-box", "aria-hidden": "true" }, String(number));
  Object.assign(box.style, { left: `${(x / data.width) * 100}%`, top: `${(y / data.height) * 100}%`,
    width: `${(w / data.width) * 100}%`, height: `${(h_ / data.height) * 100}%` });
  return box;
}

function faceItem(event, face, index, data, reload) {
  const send = async (body, message) => {
    const result = await attempt(() => api(`/api/events/${event.id}/faces/${index}`, { method: "POST", body }));
    if (!result) return;
    toast(t(message, { name: body.name, total: result.total }));
    reload();
  };
  const choose = h("select", { "aria-label": t("web.lf_choose") },
    h("option", { value: "" }, t("web.lf_choose")),
    data.people.map((name) => h("option", { value: name, selected: name === face.name }, name)));
  const learn = h("button", { class: "primary", disabled: Boolean(face.learn_problem) || !face.name,
    onclick: () => choose.value && send({ name: choose.value, is: true }, "web.lf_learned") }, t("web.lf_learn"));
  choose.addEventListener("change", () => { learn.disabled = Boolean(face.learn_problem) || !choose.value; });
  return h("li", { class: "stack" },
    h("div", {}, h("strong", {}, t("web.lf_face", { number: index + 1 })), " · ", seen(face)),
    data.people.length ? h("div", { class: "row" }, choose, learn) : null,
    face.learn_problem ? h("small", { class: "muted" }, t(face.learn_problem)) : null,
    face.verdict === "known" ? h("div", {}, h("button", { class: "danger",
      onclick: () => send({ name: face.name, is: false }, "web.lf_rejected") }, t("web.lf_not", { name: face.name }))) : null);
}

export async function learnDialog(event) {
  const body = h("div", { class: "stack" });
  const dialog = h("dialog", { class: "wg-dialog wide" }, h("h2", {}, t("web.lf_title")),
    h("p", { class: "muted" }, t("web.lf_intro")), body,
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  const load = async () => {
    const data = await attempt(() => api(`/api/events/${event.id}/faces`));
    if (!data) { if (!dialog.open) dialog.remove(); else dialog.close(); return false; }
    const picture = h("div", { class: "face-picture" },
      h("img", { src: `/api/media/snapshots/${encodeURIComponent(event.data.snapshot)}`, alt: "" }),
      data.faces.map((face, i) => marker(face, data, i + 1)));
    fill(body, picture,
      data.faces.length ? null : h("p", { class: "notice" }, t("web.lf_none")),
      data.faces.length && !data.people.length ? h("p", { class: "notice" }, t("web.lf_no_people")) : null,
      h("ul", { class: "list" }, data.faces.map((face, i) => faceItem(event, face, i, data, load))));
    return true;
  };
  if (!(await load())) return;
  document.body.append(dialog);
  dialog.showModal();
}
