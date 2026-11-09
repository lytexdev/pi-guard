// The rescuer's page: a link (and maybe a PIN) opens one person's emergency sheet, read only.
const token = location.pathname.split("/").pop();
const sheet = document.getElementById("sheet");
let texts = {};
const t = (key) => texts[key] ?? key;
const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  node.append(...children.flat(Infinity).filter((c) => c != null && c !== ""));
  return node;
};
// Danger first: what can harm the person if nobody knows it.
const ALERTS = ["allergies", "conditions", "medications", "implants"];
const FACTS = ["birthday", "blood_type", "height_cm", "weight_kg", "organ_donor"];
const MORE = ["medication_plan", "contacts", "doctor", "insurance", "notes"];

function show(data, expires) {
  const age = data.birthday ? Math.floor((Date.now() - new Date(data.birthday)) / 31557600000) : null;
  sheet.replaceChildren(...[
    el("h1", {}, data.name),
    el("p", { class: "muted" }, t("web.md_sheet_intro")),
    ALERTS.filter((k) => data[k]).map((k) => el("div", { class: "alert" }, el("h2", {}, t(`web.md_f_${k}`)), el("p", {}, data[k]))),
    el("dl", {}, FACTS.filter((k) => data[k]).map((k) => [el("dt", {}, t(`web.md_f_${k}`)),
      el("dd", {}, k === "birthday" && age != null ? `${data[k]} (${age})` : data[k])])),
    MORE.filter((k) => data[k]).map((k) => el("section", {}, el("h2", {}, t(`web.md_f_${k}`)), el("p", {}, data[k]))),
    el("footer", { class: "muted" }, t("web.md_sheet_until").replace("{time}", new Date(expires).toLocaleTimeString(document.documentElement.lang, { hour: "2-digit", minute: "2-digit" })),
      " ", el("button", { type: "button", class: "noprint", id: "print" }, t("web.md_print"))),
  ].flat(Infinity).filter(Boolean));
  document.getElementById("print").addEventListener("click", () => window.print());
}

function ask(state) {
  const pin = el("input", { inputmode: "numeric", autocomplete: "off", maxlength: "6", "aria-label": "PIN", required: "" });
  const message = el("p", { class: "muted" }, t("web.md_pin_ask"));
  const form = el("form", {}, pin, el("button", { type: "submit" }, t("web.md_open")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await open(state, pin.value, message);
  });
  sheet.replaceChildren(el("h1", {}, t("web.md_title")), message, form);
  pin.focus();
}

async function open(state, pin = "", message = null) {
  const response = await fetch(`/api/emergency/${token}`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pin }), credentials: "omit" });
  if (response.ok) { show(await response.json(), state.expires); return; }
  if (response.status === 403 && message) { message.textContent = t("web.md_pin_wrong"); return; }
  gone();
}

function gone() {
  sheet.replaceChildren(el("h1", {}, t("web.md_title")), el("p", {}, t("web.md_link_gone")));
}

(async () => {
  const response = await fetch(`/api/emergency/${token}`, { credentials: "omit" });
  const state = response.ok ? await response.json() : null;
  const language = state?.language || (navigator.language || "de").slice(0, 2);
  texts = await (await fetch(`/api/i18n/${language === "en" ? "en" : "de"}`)).json().catch(() => ({}));
  document.documentElement.lang = language;
  if (!state) { gone(); return; }
  if (state.pin) ask(state); else open(state);
})();
