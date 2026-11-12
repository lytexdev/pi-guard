// Things that float above every page: back to top, and the assistant.
import { api, fill, h, icon, t, toast } from "./api.js";
import { state } from "./app.js";
import { accessibilityCommand, readsAloud, speak } from "./accessibility.js";
import { markdown } from "./markdown.js";

// A small search icon beside the headline; the field only opens when it is needed.
export function searchToggle(label, onInput) {
  const input = h("input", { type: "search", class: "hidden", placeholder: label, "aria-label": label });
  const button = h("button", { class: "icon-only", title: label, "aria-expanded": "false", onclick: () => {
    const open = input.classList.toggle("hidden") === false;
    button.setAttribute("aria-expanded", String(open));
    if (open) input.focus();
    else { input.value = ""; onInput(""); }
  } }, icon("search"));
  input.addEventListener("input", () => onInput(input.value.trim().toLowerCase()));
  input.addEventListener("keydown", (event) => { if (event.key === "Escape") button.click(); });
  return h("div", { class: "search-toggle" }, button, input);
}

// Stands in for what is still loading. The CSS waits a moment, so fast answers show no flash.
export function skeleton(blocks = 3) {
  return h("div", { class: "skeleton", role: "status", "aria-label": t("web.loading") },
    [...Array(blocks)].map(() => h("div", { class: "panel" })));
}

// Who to ask, if the admin entered it. Shown small on the pages where people get stuck.
export function supportNote() {
  const s = state.session?.support || {};
  if (!s.name && !s.phone && !s.email && !s.address) return null;
  return h("p", { class: "support-note" }, icon("headset"), " ", t("web.support_note"), " ", s.name || null,
    s.phone ? [" · ", h("a", { href: `tel:${s.phone.replace(/[^+\d]/g, "")}` }, s.phone)] : null,
    s.email ? [" · ", h("a", { href: `mailto:${s.email}` }, s.email)] : null,
    s.address ? ` · ${s.address}` : null);
}

export const brandIcon = (provider) => icon({ telegram: "brand-telegram", signal: "brand-signal", whatsapp: "brand-whatsapp", discord: "brand-discord" }[provider] || "user");

// Live and the camera room are one place with two views.
export const liveSwitch = (current) => h("div", { class: "segmented" }, [["live", "web.nav_live"], ["monitor", "web.nav_monitor"]].map(([route, label]) =>
  h("a", { class: `button${route === current ? " on" : ""}`, href: `#/${route}` }, t(label))));

export function scrollTopButton() {
  const button = h("button", { class: "to-top hidden", title: t("web.to_top"), "aria-label": t("web.to_top"), onclick: () => scrollTo({ top: 0, behavior: "smooth" }) }, icon("chevron-right"));
  addEventListener("scroll", () => button.classList.toggle("hidden", scrollY < 500), { passive: true });
  return button;
}

export const assistantIcon = () => h("img", { class: "ai-icon", src: "/static/img/assistant.svg", alt: "" });

// The assistant's examples: a tap sends them as they are.
const EXAMPLES = ["web.ai_example_light", "web.ai_example_record", "web.ai_example_help"];

function panel() {
  const log = h("div", { class: "chat-log", "aria-live": "polite" });
  const welcome = () => h("div", { class: "chat-welcome" }, assistantIcon(), h("p", {}, t("web.ai_hello")),
    h("div", { class: "chat-chips" }, EXAMPLES.map((key) => h("button", { type: "button", class: "chip", onclick: () => ask(t(key)) }, t(key)))));
  const reset = () => fill(log, welcome());
  const say = (cls, ...content) => {
    log.querySelector(".chat-welcome")?.remove();
    const el = h("div", { class: `bubble ${cls}` }, ...content);
    log.append(el);
    el.scrollIntoView({ block: "end", behavior: "smooth" });
    return el;
  };
  // The answer is Markdown from a language model; markdown() builds nodes, never HTML text.
  const text = (reply) => (reply && /[*_`#\n[]/.test(reply) ? markdown(reply) : reply);
  const show = (r, spokenAnswer = false) => {
    const box = say("bot", text(r.reply));
    if (spokenAnswer || readsAloud()) speak(r.reply);
    if (r.photo) box.append(h("img", { src: r.photo, alt: "" }));
    if (r.documents?.length) box.append(h("small", { class: "muted block chat-sources" }, icon("file-text"), " ", r.documents.join(", ")));
    if (!r.pending) return;
    const actions = h("div", { class: "row chat-confirm" });
    const answer = async (yes) => {
      actions.remove();
      say("me", t(yes ? "cmd.yes" : "cmd.no"));
      const next = await waiting(() => api("/api/assistant/confirm", { method: "POST", body: { token: r.pending, yes } }));
      if (next) show(next);
    };
    actions.append(h("button", { class: "primary small", onclick: () => answer(true) }, icon("check"), t("cmd.yes")),
      h("button", { class: "small", onclick: () => answer(false) }, icon("x"), t("cmd.no")));
    box.append(actions);
  };
  const input = h("input", { placeholder: t("web.ai_placeholder"), maxlength: 1000, enterkeyhint: "send", autocomplete: "off", "aria-label": t("web.ai_placeholder") });
  const sendButton = h("button", { class: "icon-only primary chat-send", type: "submit", title: t("web.ai_send"), "aria-label": t("web.ai_send") }, icon("send"));
  // While the Pi thinks: dots in the log, and nothing to send twice.
  const waiting = async (call) => {
    const dots = say("bot typing", h("span"), h("span"), h("span"));
    dots.setAttribute("aria-label", t("web.ai_thinking"));
    input.disabled = sendButton.disabled = true;
    // Errors are answered in the chat: a toast would sit under the open dialog.
    try { return await call(); } catch (error) {
      if (error.status !== 401) say("bot error", icon("alert-triangle"), " ", error.message ? t(error.message) : t("web.error"));
      return undefined;
    } finally {
      dots.remove();
      input.disabled = sendButton.disabled = false;
      input.focus();
    }
  };
  const ask = async (question) => {
    say("me", question);
    const done = accessibilityCommand(question);
    if (done) { say("bot", done); if (readsAloud()) speak(done); return; }
    const r = await waiting(() => api("/api/assistant/chat", { method: "POST", body: { text: question } }));
    if (r) show(r);
  };
  let recorder = null;
  const mic = h("button", { type: "button", class: "icon-only ghost talk", title: t("web.talk_start"), "aria-label": t("web.talk_start") }, icon("microphone"));
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
      input.placeholder = t("web.ai_placeholder");
      const r = await waiting(() => api("/api/assistant/chat/voice", { method: "POST", body: blob }));
      if (!r) return;
      say("me", r.question);
      // Settings of this browser are done here, not by the server's answer.
      const done = accessibilityCommand(r.question || "");
      if (done) { say("bot", done); speak(done); } else show(r, true);
    };
    recorder.start(250);
    mic.classList.add("active");
    input.placeholder = t("web.ai_listening");
  });
  const form = h("form", { class: "chat-input" }, input, window.MediaRecorder && window.isSecureContext ? mic : null, sendButton);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    ask(question);
  });
  const dialog = h("dialog", { class: "chat", "aria-label": t("addons.assistant") },
    h("header", { class: "chat-head" }, assistantIcon(),
      h("div", { class: "grow" }, h("h2", {}, t("addons.assistant")), h("small", { class: "muted" }, t("web.ai_subtitle"))),
      h("button", { class: "icon-only ghost", title: t("web.ai_clear"), "aria-label": t("web.ai_clear"), onclick: reset }, icon("refresh")),
      h("button", { class: "icon-only ghost", title: t("web.close"), "aria-label": t("web.close"), onclick: () => dialog.close() }, icon("x"))),
    log, h("div", { class: "chat-foot" }, form, supportNote()));
  // A click beside the panel closes it, as Escape does.
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  dialog.addEventListener("close", () => document.querySelector(".ai-open")?.classList.remove("hidden"));
  reset();
  document.body.append(dialog);
  dialog.focusInput = () => input.focus();
  return dialog;
}

export function assistantButton() {
  let dialog = null;
  const button = h("button", { class: "ai-open", title: t("addons.assistant"), "aria-label": t("addons.assistant"), onclick: () => {
    dialog ||= panel();
    dialog.showModal();
    button.classList.add("hidden");
    dialog.focusInput();
  } }, assistantIcon());
  return button;
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
