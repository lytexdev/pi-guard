// The rescuer's page: a link (and maybe a PIN) opens one person's emergency sheet, read only.
import { markdown } from "./markdown.js";

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
const ALERTS = ["allergies", "conditions", "implants"];
const FACTS = ["birthday", "blood_type", "height_cm", "weight_kg", "organ_donor", "phone"];
const MORE = ["medication_plan", "contacts", "doctor", "insurance", "notes"];
const DONOR = ["yes", "no", "some", "except", "person", "undecided"];
const DOSES = ["morning", "noon", "evening", "night"];

function medicine(m) {
  const name = [m.name, m.strength, m.form].filter(Boolean).join(" ") + (m.ingredient && m.ingredient !== m.name ? ` (${m.ingredient})` : "");
  const dose = m.schedule || (DOSES.some((k) => m[k]) ? DOSES.map((k) => m[k] || "0").join("-") : "");
  const tags = [m.kind === "as_needed" ? t("web.md_kind_as_needed") : "", t(`web.md_source_${m.source || "doctor"}`), m.reason ? `${t("web.md_m_reason")}: ${m.reason}` : "", m.note || ""].filter(Boolean);
  return el("li", {}, el("strong", {}, name), dose ? el("span", { class: "dose" }, ` ${[dose, m.unit].filter(Boolean).join(" ")}`) : "", el("br"), el("small", {}, tags.join(" · ")));
}

function value(data, key, age) {
  if (key === "birthday" && age != null) return `${data[key]} (${age})`;
  if (key === "organ_donor") return [DONOR.includes(data[key]) ? t(`web.md_od_${data[key]}`) : data[key], data.organ_donor_note].filter(Boolean).join(" – ");
  return data[key];
}

function show(data, expires) {
  const age = data.birthday ? Math.floor((Date.now() - new Date(data.birthday)) / 31557600000) : null;
  const meds = data.meds || [];
  sheet.replaceChildren(...[
    el("h1", {}, data.name),
    el("p", { class: "muted" }, t("web.md_sheet_intro")),
    ALERTS.filter((k) => data[k]).map((k) => el("div", { class: "alert" }, el("h2", {}, t(`web.md_f_${k}`)), markdown(data[k]))),
    meds.length || data.medications ? el("div", { class: "alert" }, el("h2", {}, t("web.md_f_meds")),
      meds.length ? el("ul", { class: "meds" }, meds.map(medicine)) : null, data.medications ? markdown(data.medications) : null) : null,
    el("dl", {}, FACTS.filter((k) => data[k] || (k === "organ_donor" && data.organ_donor_note)).map((k) => [el("dt", {}, t(`web.md_f_${k}`)), el("dd", {}, value(data, k, age))])),
    data.supplements?.length ? el("section", {}, el("h2", {}, t("web.md_f_supplements")),
      el("ul", {}, data.supplements.map((s) => el("li", {}, [s.name, s.dose, s.note].filter(Boolean).join(" · "))))) : null,
    MORE.filter((k) => data[k]).map((k) => el("section", {}, el("h2", {}, t(`web.md_f_${k}`)), markdown(data[k]))),
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
