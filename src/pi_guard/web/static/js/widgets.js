// Things that float above every page: back to top, and the assistant.
import { api, attempt, fill, h, icon, t, toast } from "./api.js";

export function scrollTopButton() {
  const button = h("button", { class: "to-top hidden", title: t("web.to_top"), "aria-label": t("web.to_top"), onclick: () => scrollTo({ top: 0, behavior: "smooth" }) }, icon("chevron-right"));
  addEventListener("scroll", () => button.classList.toggle("hidden", scrollY < 500), { passive: true });
  return button;
}

export const assistantIcon = () => h("img", { class: "ai-icon", src: "/static/img/assistant.svg", alt: "" });

function panel() {
  const log = h("div", { class: "chat-log", "aria-live": "polite" }, h("p", { class: "muted" }, t("web.ai_hello")));
  const say = (cls, ...content) => { const el = h("div", { class: `bubble ${cls}` }, ...content); log.append(el); log.scrollTop = log.scrollHeight; return el; };
  const show = (r) => {
    const box = say("bot", r.reply);
    if (r.photo) box.append(h("img", { src: r.photo, alt: "" }));
    if (r.documents?.length) box.append(h("small", { class: "muted block" }, r.documents.join(", ")));
    if (!r.pending) return;
    const actions = h("div", { class: "row" });
    const answer = async (yes) => {
      actions.remove();
      const next = await attempt(() => api("/api/assistant/confirm", { method: "POST", body: { token: r.pending, yes } }));
      if (next) show(next);
    };
    actions.append(h("button", { class: "primary", onclick: () => answer(true) }, t("cmd.yes")), h("button", { onclick: () => answer(false) }, t("cmd.no")));
    box.append(actions);
  };
  const input = h("input", { placeholder: t("web.ai_placeholder"), maxlength: 1000, "aria-label": t("web.ai_placeholder") });
  const send = async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    say("me", text);
    const r = await attempt(() => api("/api/assistant/chat", { method: "POST", body: { text } }));
    if (r) show(r);
  };
  let recorder = null;
  const mic = h("button", { type: "button", class: "icon-only talk", title: t("web.talk_start") }, icon("microphone"));
  mic.addEventListener("click", async () => {
    if (recorder) { recorder.stop(); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { toast(t("web.mic_denied"), true); return; }
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType });
      recorder = null;
      mic.classList.remove("active");
      const r = await attempt(() => api("/api/assistant/chat/voice", { method: "POST", body: blob }));
      if (r) { say("me", r.question); show(r); }
    };
    recorder.start(250);
    mic.classList.add("active");
  });
  const form = h("form", { class: "row" }, input, window.MediaRecorder && window.isSecureContext ? mic : null, h("button", { class: "primary", type: "submit" }, t("web.ai_send")));
  form.addEventListener("submit", send);
  const dialog = h("dialog", { class: "chat", "aria-label": t("addons.assistant") },
    h("div", { class: "row between" }, h("h2", { class: "ai-title" }, assistantIcon(), t("addons.assistant")), h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))), log, form);
  document.body.append(dialog);
  return dialog;
}

export function assistantButton() {
  let dialog = null;
  return h("button", { class: "ai-open", title: t("addons.assistant"), "aria-label": t("addons.assistant"), onclick: () => {
    dialog ||= panel();
    dialog.showModal();
  } }, assistantIcon());
}

// Any picture marked .thumb or .zoomable opens large on click; Escape or a click closes it.
export function installLightbox() {
  document.addEventListener("click", (event) => {
    const picture = event.target.closest?.("img.thumb, img.zoomable, .bubble img, .result img");
    if (!picture || picture.closest("dialog.lightbox")) return;
    event.preventDefault();
    const dialog = h("dialog", { class: "lightbox", "aria-label": picture.alt || t("web.picture") },
      h("img", { src: picture.src, alt: picture.alt || "" }),
      h("div", { class: "row end" }, h("a", { class: "button", href: picture.src, download: "" }, icon("download"), t("web.download")),
        h("button", { class: "primary", onclick: () => dialog.close() }, t("web.close"))));
    dialog.addEventListener("close", () => dialog.remove());
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
    document.body.append(dialog);
    dialog.showModal();
  });
}
