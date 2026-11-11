// Accessibility: everything is remembered in this browser and applied before anything renders.
import { api, h, icon, t, fill } from "./api.js";

const KEY = "a11y";
const DEFAULTS = { scale: 100, letter: 0, line: 150, invert: false, gray: false, vision: "", cursor: false,
  ruler: false, lens: false, links: false, read: false, motion: false, flash: false };
const VISION = {
  protanopia: "0.567 0.433 0 0 0  0.558 0.442 0 0 0  0 0.242 0.758 0 0  0 0 0 1 0",
  deuteranopia: "0.625 0.375 0 0 0  0.7 0.3 0 0 0  0 0.3 0.7 0 0  0 0 0 1 0",
  tritanopia: "0.95 0.05 0 0 0  0 0.433 0.567 0 0  0 0.475 0.525 0 0  0 0 0 1 0",
};

const load = () => ({ ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") });
let state = load();
let ruler = null, lens = null;

function filters() {
  if (document.getElementById("a11y-filters")) return;
  const ns = "http://www.w3.org/2000/svg";
  const root = document.createElementNS(ns, "svg");
  root.id = "a11y-filters";
  root.setAttribute("aria-hidden", "true");
  root.setAttribute("width", "0");
  root.setAttribute("height", "0");
  for (const [name, values] of Object.entries(VISION)) {
    const filter = document.createElementNS(ns, "filter");
    filter.id = `a11y-${name}`;
    const matrix = document.createElementNS(ns, "feColorMatrix");
    matrix.setAttribute("type", "matrix");
    matrix.setAttribute("values", values);
    filter.append(matrix);
    root.append(filter);
  }
  document.body.append(root);
}

function follow(event) {
  if (ruler) ruler.style.top = `${event.clientY - 24}px`;
  if (lens) {
    const target = document.elementFromPoint(event.clientX, event.clientY);
    const text = target && !lens.contains(target) ? (target.innerText || target.alt || target.title || "").trim().slice(0, 200) : "";
    lens.textContent = text;
    lens.hidden = !text;
    lens.style.left = `${Math.min(event.clientX + 16, innerWidth - 340)}px`;
    lens.style.top = `${Math.min(event.clientY + 20, innerHeight - 130)}px`;
  }
}

function readTarget(event) {
  if (!state.read || event.target.closest(".a11y-panel")) return;
  const text = (event.target.innerText || event.target.alt || "").trim();
  if (text) speak(text.slice(0, 800));
}

export const readsAloud = () => state.read;

export function speak(text) {
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = document.documentElement.lang || "de";
  speechSynthesis.speak(utterance);
}

export function applyAccessibility() {
  const root = document.documentElement;
  root.style.setProperty("--scale", state.scale / 100);
  root.style.setProperty("--letter", `${state.letter / 100}em`);
  root.style.setProperty("--leading", state.line / 100);
  const parts = [];
  if (state.invert) parts.push("invert(1) hue-rotate(180deg)");
  if (state.gray) parts.push("grayscale(1)");
  if (state.vision) { filters(); parts.push(`url(#a11y-${state.vision})`); }
  root.style.filter = parts.join(" ");
  root.classList.toggle("a11y-cursor", state.cursor);
  root.classList.toggle("a11y-links", state.links);
  root.classList.toggle("a11y-still", state.motion);
  if (state.ruler && !ruler) { ruler = h("div", { class: "a11y-ruler", "aria-hidden": "true" }); document.body.append(ruler); }
  if (!state.ruler && ruler) { ruler.remove(); ruler = null; }
  if (state.lens && !lens) { lens = h("div", { class: "a11y-lens", hidden: true, "aria-hidden": "true" }); document.body.append(lens); }
  if (!state.lens && lens) { lens.remove(); lens = null; }
  watchAlerts(state.flash);
}

// Seen instead of heard: a new alert makes the page glow and the device vibrate. A slow pulse,
// never a strobe, so nobody gets a seizure from it.
let watching = null;
let known = null;
async function checkAlerts() {
  let open;
  try { open = await api("/api/alerts"); } catch { return; }
  const ids = new Set(open.map((a) => a.id));
  const fresh = known && open.some((a) => !known.has(a.id));
  known = ids;
  if (!fresh) return;
  document.documentElement.classList.remove("a11y-alert");
  void document.documentElement.offsetWidth;
  document.documentElement.classList.add("a11y-alert");
  navigator.vibrate?.([400, 200, 400]);
  if (state.read) speak(t("web.a11y_alert_spoken"));
}
function watchAlerts(on) {
  if (on && !watching) { known = null; checkAlerts(); watching = setInterval(checkAlerts, 15000); }
  if (!on && watching) { clearInterval(watching); watching = null; }
}

// Spoken or typed: "Schrift größer", "Vorlesen an", "Lupe aus", "Rot-Grün", "zurücksetzen".
// Runs in this browser only; returns what was done, or null if the words were no such request.
const fold = (text) => text.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");
export function accessibilityCommand(text) {
  const s = fold(text);
  const off = /\b(aus|ausschalten|off|stop|beenden)\b/.test(s);
  const flag = (key) => { save({ [key]: !off }); return t(off ? "web.a11y_cmd_off" : "web.a11y_cmd_on", { what: t(`web.a11y_${key}`) }); };
  if (/(schrift|text|font|zoom).*(groesser|bigger|larger|in\b)|(groesser|bigger|larger).*(schrift|text|font)|vergroesser/.test(s)) { save({ scale: Math.min(180, state.scale + 20) }); return t("web.a11y_cmd_scale", { value: state.scale }); }
  if (/(schrift|text|font|zoom).*(kleiner|smaller|out\b)|(kleiner|smaller).*(schrift|text|font)|verkleiner/.test(s)) { save({ scale: Math.max(80, state.scale - 20) }); return t("web.a11y_cmd_scale", { value: state.scale }); }
  if (/(lies|read).*(seite|page)|seite vorlesen/.test(s)) { speak(document.getElementById("main")?.innerText.slice(0, 4000) || ""); return t("web.a11y_read_page"); }
  if (/vorlesen|read aloud/.test(s)) return flag("read");
  if (/kontrast|contrast|invert/.test(s)) return flag("invert");
  if (/graustuf|grayscale|greyscale/.test(s)) return flag("gray");
  if (/lupe|magnif/.test(s)) return flag("lens");
  if (/leselinie|lineal|ruler/.test(s)) return flag("ruler");
  if (/animation|bewegung|motion/.test(s)) { save({ motion: !/\b(an|ein|on)\b/.test(s) }); return t("web.a11y_motion"); }
  if (/(sichtbar|visuell|visual|blink|flash).*(alarm|alert|meldung)|(alarm|alert|meldung).*(sichtbar|blink|flash)/.test(s)) return flag("flash");
  if (/farb|color|colour|rot.?gruen|red.?green|protan|deutan|tritan/.test(s)) {
    const vision = off ? "" : /protan|rot\b|red\b/.test(s) && !/gruen|green/.test(s) ? "protanopia" : /tritan|blau|blue/.test(s) ? "tritanopia" : "deuteranopia";
    save({ vision });
    return t("web.a11y_vision") + ": " + t(`web.a11y_vision_${vision || "off"}`);
  }
  if (/(barrierefrei|accessib|bedienungshilfe).*(zuruecksetzen|reset)|^(zuruecksetzen|reset)$/.test(s.trim())) {
    state = { ...DEFAULTS }; localStorage.removeItem(KEY); applyAccessibility(); return t("web.a11y_reset");
  }
  return null;
}

function save(change) {
  state = { ...state, ...change };
  localStorage.setItem(KEY, JSON.stringify(state));
  applyAccessibility();
}

const TOUR = ["overview", "live", "monitor", "events", "people", "network", "addons", "settings"];

function tutorial(step = 0) {
  const dialog = h("dialog", { class: "tour" },
    h("small", { class: "muted" }, t("web.tour_step", { n: step + 1, total: TOUR.length })),
    h("h2", {}, t(`web.nav_${TOUR[step]}`)), h("p", {}, t(`web.tour_${TOUR[step]}`)),
    h("div", { class: "row end" },
      h("button", { onclick: () => dialog.close() }, t("web.tour_end")),
      h("button", { class: "primary", onclick: () => {
        dialog.close();
        if (step + 1 < TOUR.length) tutorial(step + 1); else location.hash = "#/overview";
      } }, step + 1 < TOUR.length ? t("web.next") : t("web.done"))));
  location.hash = `#/${TOUR[step]}`;
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function slider(key, label, min, max, step, unit) {
  const value = h("output", {}, `${state[key]}${unit}`);
  const input = h("input", { type: "range", min, max, step, value: state[key], "aria-label": label });
  input.addEventListener("input", () => { value.textContent = `${input.value}${unit}`; save({ [key]: Number(input.value) }); });
  return h("label", { class: "a11y-row" }, h("span", { class: "grow" }, label), value, input);
}

function toggle(key, label) {
  const input = h("input", { type: "checkbox", checked: state[key] });
  input.addEventListener("change", () => save({ [key]: input.checked }));
  return h("label", { class: "check" }, input, label);
}

function panel() {
  const vision = h("select", { "aria-label": t("web.a11y_vision") },
    ["", "protanopia", "deuteranopia", "tritanopia"].map((v) => h("option", { value: v, selected: v === state.vision }, t(`web.a11y_vision_${v || "off"}`))));
  vision.addEventListener("change", () => save({ vision: vision.value }));
  const dialog = h("dialog", { class: "a11y-panel", "aria-label": t("web.a11y_title") },
    h("div", { class: "row between" }, h("h2", {}, t("web.a11y_title")), h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))),
    h("h3", {}, t("web.a11y_text")),
    slider("scale", t("web.a11y_scale"), 80, 180, 10, " %"), slider("letter", t("web.a11y_letter"), 0, 20, 2, " %"), slider("line", t("web.a11y_line"), 130, 240, 10, " %"),
    h("h3", {}, t("web.a11y_colors")),
    toggle("invert", t("web.a11y_invert")), toggle("gray", t("web.a11y_gray")),
    h("label", { class: "a11y-row" }, h("span", { class: "grow" }, t("web.a11y_vision")), vision),
    h("h3", {}, t("web.a11y_help")),
    toggle("read", t("web.a11y_read")), toggle("cursor", t("web.a11y_cursor")), toggle("ruler", t("web.a11y_ruler")),
    toggle("lens", t("web.a11y_lens")), toggle("links", t("web.a11y_links")), toggle("motion", t("web.a11y_motion")),
    toggle("flash", t("web.a11y_flash")),
    h("p", { class: "muted" }, h("small", {}, t("web.a11y_voice_hint"))),
    h("div", { class: "row" },
      h("button", { onclick: () => speak(document.getElementById("main")?.innerText.slice(0, 4000) || "") }, icon("volume"), t("web.a11y_read_page")),
      h("button", { onclick: () => speechSynthesis.cancel() }, t("web.a11y_read_stop")),
      h("button", { onclick: () => { dialog.close(); tutorial(); } }, t("web.a11y_tour"))),
    h("button", { class: "ghost", onclick: () => { state = { ...DEFAULTS }; localStorage.removeItem(KEY); applyAccessibility(); dialog.close(); } }, t("web.a11y_reset")));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

export function accessibilityButton() {
  return h("button", { class: "a11y-open", title: t("web.a11y_title"), "aria-label": t("web.a11y_title"), onclick: panel }, icon("accessible"));
}

addEventListener("mousemove", follow, { passive: true });
addEventListener("click", readTarget);
