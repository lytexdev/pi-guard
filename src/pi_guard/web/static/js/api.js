// Fetch wrapper (session cookie + CSRF header), translations and small DOM helpers.

let csrf = null;
let texts = {};
export let lang = localStorage.getItem("lang") || "de";

// Everyday settings by default; the rest appears on request, per device.
export const expertMode = () => localStorage.getItem("expert") === "1";
export const setExpertMode = (on) => localStorage.setItem("expert", on ? "1" : "0");

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function setCsrf(token) { csrf = token; }

export async function api(path, { method = "GET", body, raw } = {}) {
  const headers = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf || "";
  let payload = body;
  if (body !== undefined && !(body instanceof FormData) && !(body instanceof Blob)) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  if (response.status === 401 && !path.startsWith("/api/login")) {
    location.hash = "#/login";
    throw new ApiError(401, t("web.login_required"));
  }
  if (!response.ok) {
    const message = (await response.text()) || response.statusText;
    if (response.status === 403 && message === "setup_required") location.hash = "#/setup/account";
    throw new ApiError(response.status, message);
  }
  if (raw) return response;
  const type = response.headers.get("Content-Type") || "";
  return type.includes("json") ? response.json() : response.text();
}

export async function loadTexts(language) {
  lang = language;
  localStorage.setItem("lang", language);
  document.documentElement.lang = language;
  texts = await (await fetch(`/api/i18n/${language}`)).json();
}

export function t(key, values = {}) {
  const template = texts[key] ?? key;
  return template.replace(/\{(\w+)\}/g, (_, name) => values[name] ?? "");
}

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === false || value == null) continue;
    if (name.startsWith("on")) el.addEventListener(name.slice(2), value);
    else if (name === "class") el.className = value;
    else if (name === "dataset") Object.assign(el.dataset, value);
    // Through the CSSOM: the Content Security Policy blocks style attributes, not this.
    else if (name === "style") el.style.cssText = value;
    else el.setAttribute(name, value === true ? "" : value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

// A small (i) next to a label: its text shows on hover, keyboard focus and a tap (tooltip.js).
export function infoTip(text) {
  return h("button", { type: "button", class: "info-tip", title: text, "data-tap-tip": "" }, icon("info-circle"));
}

const SVG = "http://www.w3.org/2000/svg";
export function icon(name, cls = "") {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", `icon ${cls}`.trim());
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG, "use");
  use.setAttribute("href", `#${name}`);
  svg.append(use);
  return svg;
}

// The icons live in the page itself, so they appear wherever the page does: an external <use>
// is one more request, and a WebView or an old cache must not be able to lose every icon at once.
export async function loadIcons() {
  const sprite = new DOMParser().parseFromString(await (await fetch("/static/icons/sprite.svg")).text(), "image/svg+xml").documentElement;
  sprite.setAttribute("class", "sprite");
  sprite.setAttribute("aria-hidden", "true");
  document.body.prepend(document.importNode(sprite, true));
}

export function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

export function toast(message, error = false) {
  const el = h("div", { class: `toast${error ? " error" : ""}`, role: "status" }, message);
  document.body.append(el);
  setTimeout(() => el.remove(), error ? 6000 : 3000);
}

export async function attempt(action, success) {
  try {
    const result = await action();
    if (success) toast(success);
    return result;
  } catch (error) {
    // Server messages are often translation keys ("web.wrong_password").
    if (error.status !== 401) toast(error.message ? t(error.message) : t("web.error"), true);
    return undefined;
  }
}

export function confirmDialog(message, confirmLabel, danger = false) {
  return new Promise((resolve) => {
    const dialog = h("dialog", {},
      h("p", {}, message),
      h("div", { class: "row end" },
        h("button", { onclick: () => { dialog.close(); resolve(false); } }, t("web.cancel")),
        h("button", { class: danger ? "danger solid" : "primary",
                      onclick: () => { dialog.close(); resolve(true); } }, confirmLabel)));
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
  });
}

// A question with an answer. window.prompt does not exist in the desktop app, and it shows
// passwords in plain text everywhere else. Resolves to null on cancel.
// `multiline` keeps line breaks, for pasted keys and certificates.
export function askDialog(message, { value = "", type = "text", confirmLabel, multiline = false } = {}) {
  return new Promise((resolve) => {
    const input = multiline
      ? h("textarea", { rows: 10, class: "mono", spellcheck: false, "aria-label": message }, value)
      : h("input", { type, value, "aria-label": message, autocomplete: type === "password" ? "current-password" : "off" });
    let answer = null;
    const dialog = h("dialog", {},
      h("form", { method: "dialog", class: "stack", onsubmit: () => { answer = input.value; } },
        h("label", {}, message), input,
        h("div", { class: "row end" },
          h("button", { type: "button", onclick: () => dialog.close() }, t("web.cancel")),
          h("button", { class: "primary", type: "submit" }, confirmLabel || t("web.ok")))));
    dialog.addEventListener("close", () => { dialog.remove(); resolve(answer); });
    document.body.append(dialog);
    dialog.showModal();
    input.select();
  });
}

export function when(iso) {
  const date = new Date(iso);
  const today = new Date().toDateString() === date.toDateString();
  return date.toLocaleString(lang, today
    ? { hour: "2-digit", minute: "2-digit" }
    : { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function duration(seconds) {
  const s = Math.round(seconds);
  if (s < 90) return `${s} s`;
  if (s < 5400) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

// Runs fn later, unless the page holding el has been left by then.
export function whileShown(el, fn, ms) {
  return setTimeout(() => { if (el.isConnected) fn(); }, ms);
}

// Like el.replaceChildren(), but skips null/false like h() does.
export function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c != null && c !== false)
    .map((c) => (c instanceof Node ? c : document.createTextNode(String(c)))));
  return el;
}
