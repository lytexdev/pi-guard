// Setup assistant: introduction, login, devices, Telegram, alerts, emergency
// contacts, trusted people and design. Only the login step is mandatory (while
// the default password is in use); every other step can be skipped and the
// assistant can be opened again under More.
import { api, attempt, fill, h, icon, lang, t, toast } from "../api.js";
import { state, switchLanguage } from "../app.js";
import { setDesign, SIZES, THEMES } from "../theme.js";

const STEPS = ["welcome", "account", "devices", "telegram", "alerts", "emergency", "people", "design", "finish"];
const restartNeeded = () => sessionStorage.getItem("pg.restart") === "1";
let status = {};  // what the installer already set up (GET /api/setup/status)
const needRestart = () => sessionStorage.setItem("pg.restart", "1");

function go(step) { location.hash = `#/setup/${step}`; }
function next(step) { go(STEPS[Math.min(STEPS.indexOf(step) + 1, STEPS.length - 1)]); }

function frame(root, step, body, { canSkip = true, nextLabel = null, onNext = null } = {}) {
  const index = STEPS.indexOf(step);
  const back = index > 0 && !state.session.setup_required
    ? h("button", { class: "ghost", onclick: () => go(STEPS[index - 1]) }, t("web.back")) : h("span");
  const actions = h("div", { class: "row" },
    canSkip && step !== "finish" ? h("button", { class: "ghost", onclick: () => next(step) }, t("web.skip")) : null,
    onNext ? h("button", { class: "primary big", onclick: onNext }, nextLabel || t("web.next"), icon("chevron-right")) : null);
  fill(root, h("div", { class: "wizard" },
    h("div", { class: "brand" }, h("img", { src: "/static/img/icon-192.png", alt: "" }), "pi-guard",
      h("span", { class: "badge" }, t("web.setup_step", { n: index + 1, total: STEPS.length }))),
    h("div", { class: "steps", "aria-hidden": "true" }, STEPS.map((s, i) =>
      h("span", { class: i < index ? "done" : i === index ? "now" : "" }))),
    h("h1", {}, t(`web.setup_${step}_title`)),
    h("p", { class: "muted" }, t(`web.setup_${step}_text`)),
    body,
    h("div", { class: "wizard-nav" }, back, actions)));
}

// --- steps -------------------------------------------------------------------------------

function welcome(root) {
  const later = state.session.setup_required ? null : h("button", { class: "ghost", onclick: () => {
    sessionStorage.setItem("pg.setup_later", "1");
    location.hash = "#/overview";
  } }, t("web.setup_later"));
  frame(root, "welcome", h("div", { class: "stack" }, later,
    h("section", { class: "panel" }, h("ul", { class: "list" },
      [["video", "web.setup_intro_record"], ["bell", "web.setup_intro_alerts"],
       ["device-mobile", "web.setup_intro_phone"], ["shield-lock", "web.setup_intro_private"]]
        .map(([i, key]) => h("li", {}, icon(i, "lg"), h("span", { class: "grow" }, t(key)))))),
    status.language ? null : h("section", { class: "panel" }, h("h2", {}, t("web.language")),
      h("div", { class: "choice" }, [["de", "Deutsch"], ["en", "English"]].map(([code, name]) =>
        h("button", { class: lang === code ? "on" : "", onclick: async () => {
          await switchLanguage(code);
          await attempt(() => api("/api/settings", { method: "PUT", body: { language: code } }));
        } }, name)))),
    installed()),
  { canSkip: false, onNext: () => next("welcome") });
}

async function account(root) {
  const info = await api("/api/account");
  const forced = state.session.setup_required;
  const body = h("div", { class: "stack" });
  const totpPanel = h("section", { class: "panel" });

  const drawTotp = async () => {
    const current = await api("/api/account");
    if (current.totp) {
      fill(totpPanel, h("h2", {}, t("web.totp_title")), h("p", {}, h("span", { class: "badge ok" }, icon("shield-check"), t("web.totp_on"))));
      return;
    }
    const startButton = h("button", { class: "primary", onclick: async () => {
      const setup = await attempt(() => api("/api/account/totp/begin", { method: "POST" }));
      if (!setup) return;
      const code = h("input", { id: "totpcode", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 7 });
      const confirm = h("button", { class: "primary", onclick: async () => {
        if (await attempt(() => api("/api/account/totp/confirm", { method: "POST", body: { code: code.value } }), t("web.totp_enabled"))) drawTotp();
      } }, icon("check"), t("web.totp_confirm"));
      fill(totpPanel, h("h2", {}, t("web.totp_title")),
        h("p", {}, t("web.totp_scan")),
        h("img", { class: "qr", src: setup.qr, alt: "QR" }),
        h("p", { class: "muted" }, t("web.totp_manual"), " ", h("code", {}, setup.secret)),
        h("div", { class: "field" }, h("label", { for: "totpcode" }, t("web.totp_code")), code), confirm);
      code.focus();
    } }, icon("shield-lock"), t("web.totp_setup"));
    fill(totpPanel, h("h2", {}, t("web.totp_title")), h("p", { class: "muted" }, t("web.totp_explain")), startButton);
  };

  const current = h("input", { id: "cur", type: "password", autocomplete: "current-password", required: true });
  const user = h("input", { id: "nuser", autocomplete: "username", value: info.username, required: true, maxlength: 32 });
  const pass = h("input", { id: "npass", type: "password", autocomplete: "new-password", minlength: 10, required: true });
  const repeat = h("input", { id: "npass2", type: "password", autocomplete: "new-password", required: true });
  const form = h("form", { class: "panel" },
    h("h2", {}, forced ? t("web.account_new") : t("web.account_change")),
    forced ? h("p", { class: "notice" }, t("web.account_default_note")) : null,
    h("div", { class: "field" }, h("label", { for: "cur" }, t("web.current_password")), current),
    h("div", { class: "field" }, h("label", { for: "nuser" }, t("web.username")), user),
    h("div", { class: "field" }, h("label", { for: "npass" }, t("web.new_password")), pass,
      h("div", { class: "hint" }, t("web.password_hint"))),
    h("div", { class: "field" }, h("label", { for: "npass2" }, t("web.repeat_password")), repeat),
    h("button", { class: "primary", type: "submit" }, icon("check"), t("web.save")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pass.value !== repeat.value) { toast(t("web.passwords_differ"), true); return; }
    try {
      await api("/api/account/credentials", { method: "POST", body: { current: current.value, username: user.value, password: pass.value } });
    } catch (error) { toast(t(error.message), true); return; }
    toast(t("web.saved"));
    state.session = await api("/api/session");
    form.replaceWith(h("section", { class: "panel" }, h("p", {}, icon("circle-check"), " ", t("web.account_saved", { name: user.value }))));
    totpPanel.classList.remove("hidden");
    await drawTotp();
    fill(root.querySelector(".wizard-nav"), h("span"), h("button", { class: "primary big", onclick: () => next("account") }, t("web.next"), icon("chevron-right")));
  });
  body.append(form, totpPanel);
  if (forced) totpPanel.classList.add("hidden"); else await drawTotp();
  frame(root, "account", body, { canSkip: !forced, onNext: forced ? null : () => next("account") });
}

function installed() {
  // Summary of what the installer already asked, so nobody types it twice.
  const items = [];
  if (status.language) items.push(t("web.setup_have_language", { language: status.language === "de" ? "Deutsch" : "English" }));
  if (status.telegram) items.push(t("web.setup_have_telegram", { bot: status.telegram.bot ? `@${status.telegram.bot}` : "Telegram", admins: status.telegram.admins }));
  if (status.devices) items.push(t("web.setup_have_devices", { cameras: status.devices.cameras.join(", ") }));
  if (!items.length) return null;
  return h("section", { class: "panel" }, h("h2", {}, t("web.setup_have_title")),
    h("ul", { class: "list" }, items.map((text) => h("li", {}, icon("circle-check"), h("span", { class: "grow" }, text)))),
    h("p", { class: "muted" }, h("small", {}, t("web.setup_have_hint"))));
}

function doneStep(root, step, lines, change) {
  // A step the installer already covered: show what is set, offer to change it anyway.
  frame(root, step, h("div", { class: "stack" },
    h("section", { class: "panel" },
      h("p", {}, h("span", { class: "badge ok" }, icon("circle-check"), t("web.setup_already_done"))),
      h("ul", { class: "list" }, lines.filter(Boolean).map((text) => h("li", {}, h("span", { class: "grow" }, text)))),
      h("button", { onclick: change }, icon("pencil"), t("web.setup_change_anyway")))),
  { canSkip: false, onNext: () => next(step) });
}

async function embedded(root, step, loader, opts = {}) {
  // "Save and continue" runs the embedded view's own save; views without one just continue.
  const container = h("div");
  let save = null;
  frame(root, step, container, { onNext: () => (save ? save() : next(step)), nextLabel: t("web.save_and_next") });
  const view = await loader();
  await view.render(container, [], {
    embedded: true,
    register: (fn) => { save = fn; },
    onSaved: (changed) => { if (!changed || Object.keys(changed).length) needRestart(); next(step); },
    ...opts,
  });
}

async function alerts(root) {
  const config = await api("/api/config");
  const motion = config.notifications.rules.motion_detected || {};
  const modes = motion.modes || ["AWAY"];
  const quiet = h("input", { id: "quiet", value: config.notifications.quiet_hours || "", placeholder: "23:00-06:00" });
  const atHome = h("input", { id: "athome", type: "checkbox", checked: modes.includes("HOME") });
  const withClip = h("input", { id: "clip", type: "checkbox", checked: motion.attach === "clip" });
  const settingsBox = h("div");
  let saveModes = null;
  const save = async () => {
    if (saveModes) await saveModes();
    const body = structuredClone(config);
    body.notifications.quiet_hours = quiet.value.trim() || null;
    body.notifications.rules.motion_detected = { ...motion, modes: atHome.checked ? ["HOME", "AWAY"] : ["AWAY"],
      attach: withClip.checked ? "clip" : "snapshot" };
    if (await attempt(() => api("/api/config", { method: "PUT", body }), t("web.saved"))) { needRestart(); next("alerts"); }
  };
  frame(root, "alerts", h("div", { class: "stack" },
    h("section", { class: "panel" },
      h("label", { class: "check", for: "athome" }, atHome, t("web.alert_at_home")),
      h("label", { class: "check", for: "clip" }, withClip, t("web.alert_with_clip")),
      h("div", { class: "field" }, h("label", { for: "quiet" }, t("web.quiet_hours_alerts")), quiet,
        h("div", { class: "hint" }, t("web.quiet_hours_hint")))),
    settingsBox), { onNext: save, nextLabel: t("web.save_and_next") });
  const view = await import("./settings.js");
  await view.render(settingsBox, [], { embedded: true, groups: ["modes"],
    register: (fn) => { saveModes = fn; },
    onSaved: (changed) => { if (Object.keys(changed || {}).length) needRestart(); } });
}

async function people(root) {
  const overview = await api("/api/overview");
  if (overview.features.faces) {
    await embedded(root, "people", () => import("./people.js"));
    return;
  }
  const models = await api("/api/models");
  const missing = models.filter((m) => !m.installed);
  const size = missing.reduce((sum, m) => sum + m.size_mb, 0).toFixed(1);
  const enable = h("button", { class: "primary", onclick: async () => {
    if (missing.length) {
      toast(t("web.models_downloading"));
      if (!(await attempt(() => api("/api/models/download", { method: "POST", body: { confirmed: true } })))) return;
    }
    if (await attempt(() => api("/api/settings", { method: "PUT", body: { person_detection_enabled: true, face_recognition_enabled: true } }), t("web.saved"))) {
      needRestart();
      fill(enable.parentElement, h("p", { class: "notice" }, t("web.faces_after_restart")));
    }
  } }, icon("user-check"), missing.length ? t("web.faces_enable_download", { size }) : t("web.faces_enable"));
  frame(root, "people", h("div", { class: "stack" },
    h("section", { class: "panel" },
      h("p", {}, t("web.faces_intro")),
      h("p", { class: "muted" }, t("web.faces_privacy_setup")),
      missing.length ? h("ul", { class: "list" }, missing.map((m) => h("li", {}, icon("download"),
        h("span", { class: "grow" }, `${m.file} · ${m.size_mb} MB · ${m.license}`, h("br"), h("small", { class: "muted" }, m.source))))) : null,
      h("div", {}, enable))), { onNext: () => next("people") });
}

function design(root) {
  const draw = () => frame(root, "design", h("div", { class: "stack" },
    h("section", { class: "panel" }, h("h2", {}, t("web.theme")),
      h("div", { class: "choice" }, THEMES.map((theme) => h("button", {
        class: (localStorage.getItem("theme") || "dark") === theme ? "on" : "",
        onclick: () => { setDesign(theme); draw(); },
      }, t(`web.theme_${theme}`))))),
    h("section", { class: "panel" }, h("h2", {}, t("web.text_size")),
      h("div", { class: "choice" }, SIZES.map((size) => h("button", {
        class: (localStorage.getItem("text") || "normal") === size ? "on" : "",
        onclick: () => { setDesign(null, size); draw(); },
      }, t(`web.text_${size}`)))),
      h("p", { class: "muted" }, t("web.design_per_device")))), { onNext: () => next("design") });
  draw();
}

function finish(root) {
  const done = async (restart) => {
    await attempt(() => api("/api/setup/done", { method: "POST" }));
    if (restart) {
      sessionStorage.removeItem("pg.restart");
      await attempt(() => api("/api/system/restart", { method: "POST" }), t("web.restarting"));
      setTimeout(() => { location.hash = "#/overview"; location.reload(); }, 9000);
    } else {
      location.hash = "#/overview";
    }
  };
  frame(root, "finish", h("div", { class: "stack" },
    h("section", { class: "panel" },
      h("p", {}, icon("circle-check", "lg"), " ", t("web.setup_finish_ready")),
      restartNeeded() ? h("p", { class: "notice" }, t("web.setup_restart_note")) : null,
      h("div", { class: "row" },
        restartNeeded()
          ? h("button", { class: "primary big", onclick: () => done(true) }, icon("power"), t("web.restart_and_finish"))
          : h("button", { class: "primary big", onclick: () => done(false) }, icon("home"), t("web.to_overview"))))),
  { canSkip: false });
}

export async function render(root, params) {
  if (state.session.setup_required && params[0] !== "account" && params[0] !== "welcome") { go("welcome"); return; }
  try { status = await api("/api/setup/status"); } catch { status = {}; }
  const step = STEPS.includes(params[0]) ? params[0] : "welcome";
  if (step === "welcome") welcome(root);
  else if (step === "account") await account(root);
  else if (step === "devices" && status.devices && !params[1]) {
    const d = status.devices;
    doneStep(root, "devices", [
      t("web.setup_have_devices", { cameras: d.cameras.join(", ") }),
      d.speaker ? t("web.setup_have_speaker", { device: d.speaker }) : null,
      d.microphone ? t("web.setup_have_microphone", { device: d.microphone }) : null,
    ], () => go("devices/change"));
  }
  else if (step === "devices") await embedded(root, "devices", () => import("./devices.js"));
  else if (step === "telegram" && status.telegram && !params[1]) {
    doneStep(root, "telegram", [
      t("web.setup_have_telegram", { bot: status.telegram.bot ? `@${status.telegram.bot}` : "Telegram", admins: status.telegram.admins }),
    ], () => go("telegram/change"));
  }
  else if (step === "telegram") await embedded(root, "telegram", () => import("./settings.js"), { groups: ["messaging"] });
  else if (step === "alerts") await alerts(root);
  else if (step === "emergency") await embedded(root, "emergency", () => import("./settings.js"), { groups: ["emergency"] });
  else if (step === "people") await people(root);
  else if (step === "design") design(root);
  else finish(root);
}
