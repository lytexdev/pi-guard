// Settings: one place with sections, search and a save bar that appears when it is needed.
import { askDialog, api, attempt, fill, h, icon, infoTip, t, toast, when, whileShown, expertMode, setExpertMode } from "../api.js";
import { state } from "../app.js";
import { installFolds, unfold } from "../folds.js";
import { searchToggle, skeleton, supportNote } from "../widgets.js";
import { restartPrompt } from "./settings.js";

// Moved into other tabs; old links keep working.
// Detection and sound sit with the devices since 1.18, the voice with the assistant add-on, and
// updates under System.
const MOVED = { plan: "modes", api: "access", apps: "system", updates: "system", dashboard: "general", security: "data",
  detection: "devices", audio: "devices" };
// Sound settings shown with the devices; the voice ones (wake words, talk button) are the assistant's.
const DEVICE_KEYS = ["motion_enabled", "motion_sensitivity", "person_detection_enabled", "face_recognition_enabled",
  "audio_recording_enabled", "audio_playback_enabled", "audio_max_volume"];
// Data & backup holds what used to be four tabs; their old links open the right part.
const DATA_TABS = [["backup", "upload"], ["storage", "database"], ["security", "shield-check"], ["move", "map-pin"], ["export", "download"]];
const dataTabs = () => DATA_TABS.filter(([id]) => id !== "export" || expertMode());
// What is on the pages without settings fields, so the search finds it too.
const TOPICS = {
  modes: ["web.set_plan"],
  appearance: ["web.theme", "web.text_size", "web.ap_nav", "web.ap_color", "web.ap_font", "web.ap_animations", "web.ap_reset"],
  devices: ["web.cameras", "web.speaker", "web.microphone", "web.trusted_devices", "web.quiet_hours_sound", "web.set_detection", "web.set_audio"],
  network: ["web.set_network_pages", "web.addon_vpn", "web.addon_pihole"],
  access: ["web.users_title", "web.webauthn_title", "web.recovery_title", "web.codeword", "web.login_security", "web.account", "web.api_tokens", "web.api_docs"],
  messaging: ["web.discord_status", "web.signal_status"],
  addons: ["web.nav_addons"],
  "data/security": ["web.scan_firewall", "web.scan_updates", "web.scan_rootkits", "web.scan_ports", "web.scan_ssh"],
  "data/storage": ["web.st_recordings", "web.st_journal", "web.st_apt", "web.st_folders"],
  "data/export": ["web.ex_data", "web.ex_config", "web.ex_import"],
  "data/backup": ["web.bk_title", "web.bk_ssh_title"],
  system: ["web.up_title", "web.mob_devices", "web.mob_app", "web.apps_versions", "web.apps_invite", "web.sy_health_title"],
  logs: ["web.nav_logs", "web.audit", "web.sec_title"],
  "data/move": ["web.mv_title", "web.mv_before", "web.mv_after", "web.mv_package"],
  support: ["web.support_phone", "web.support_email", "web.support_address"],
};
const fold = (text) => text.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");

// [id, icon, settings groups, nav group]. Data & backup shows the backup group only on its backup tab.
const SECTIONS = [
  ["general", "settings", ["general", "dashboard"], "basics"], ["recording", "video", ["recording"], "basics"],
  ["modes", "home", ["modes"], "basics"],
  ["messaging", "message", ["telegram", "discord", "email", "whatsapp", "signal", "chatapps"], "alerts"], ["notifications", "bell-ringing", null, "alerts"],
  ["emergency", "alert-triangle", ["emergency"], "alerts"],
  ["devices", "camera", ["detection", "audio"], "home"], ["access", "shield-lock", null, "home"], ["appearance", "palette", null, "home"],
  ["federation", "link", ["federation"], "home"],
  ["system", "server", ["updates", "mobile"], "system"], ["network", "network", ["network"], "system"],
  ["data", "database", ["backup"], "system"], ["addons", "cpu", null, "system"], ["integrations", "router", ["integrations"], "system"],
  ["logs", "list-details", null, "system"], ["support", "headset", null, "system"],
];
const NAV_GROUPS = ["basics", "alerts", "home", "system"];
// Sections that open elsewhere: still found by the search, not listed in the settings menu.
const ELSEWHERE = {};

// Sections for people who know what they are looking for.
const EXPERT_SECTIONS = ["integrations"];
// A member sees the sections the owner granted (web/permissions.py); appearance is per device.
const MEMBER_NEEDS = { devices: "devices", notifications: "contacts", contacts: "contacts", system: "apps", logs: "logs", appearance: null };
function memberMay(id) {
  if (state.session?.role !== "member") return true;
  const granted = state.session.permissions || [];
  if (id === "addons") return granted.some((p) => p === "addons" || p.startsWith("addon:"));
  if (id in MEMBER_NEEDS) {
    const need = MEMBER_NEEDS[id];
    return need === null || granted.includes(need) || granted.includes(`settings:${id}`)
      || (id === "system" && ["settings:apps", "settings:updates"].some((p) => granted.includes(p)))
      || (id === "devices" && ["settings:detection", "settings:audio"].some((p) => granted.includes(p)));
  }
  return granted.includes(`settings:${id}`);
}
const shown = () => SECTIONS.filter(([id]) => (expertMode() || !EXPERT_SECTIONS.includes(id)) && memberMay(id));
const sectionIcon = (id) => SECTIONS.find(([section]) => section === id)?.[1] || "settings";

function modeSwitch(redraw) {
  return h("div", { class: "segmented mode-switch", role: "group", "aria-label": t("web.mode_view") }, [[false, "web.mode_normal"], [true, "web.mode_expert"]].map(([on, label]) =>
    h("button", { class: expertMode() === on ? "on" : "", title: t(`${label}_tip`), onclick: () => { setExpertMode(on); redraw(); } }, t(label))));
}

async function apiSection(box) {
  const draw = async () => {
    const [tokens, docs] = await Promise.all([api("/api/tokens"), api("/api/docs")]);
    const name = h("input", { placeholder: t("web.api_name"), maxlength: 40, "aria-label": t("web.api_name") });
    const scope = h("select", { "aria-label": t("web.api_scope") }, ["read", "control"].map((s) => h("option", { value: s }, t(`web.api_scope_${s}`))));
    const out = h("div");
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("web.api_tokens")), h("p", { class: "muted" }, t("web.api_intro")),
      h("ul", { class: "list" }, tokens.length ? tokens.map((k) => h("li", {}, icon("key"),
        h("div", { class: "grow" }, h("div", {}, k.name), h("small", {}, `${t(`web.api_scope_${k.scope}`)} · ${k.last_used ? t("web.last_used", { time: when(k.last_used) }) : t("web.never_used")}`)),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => { if (await attempt(() => api(`/api/tokens/${k.id}`, { method: "DELETE" }))) draw(); } }, icon("trash")))) : h("li", { class: "empty" }, t("web.nothing"))),
      h("div", { class: "row" }, name, scope, h("button", { class: "primary", onclick: async () => {
        if (!name.value.trim()) return;
        const password = await askDialog(t("web.confirm_password"), { type: "password" });
        if (password === null) return;
        const r = await attempt(() => api("/api/tokens", { method: "POST", body: { name: name.value, scope: scope.value, password } }));
        if (r) { await draw(); box.querySelector(".token-out").replaceChildren(h("p", { class: "notice" }, t("web.api_shown_once")), h("pre", { class: "mono wrap" }, r.token)); }
      } }, icon("plus"), t("web.api_create"))), h("div", { class: "token-out" }), out),
    h("section", { class: "panel stack" }, h("h2", {}, t("web.api_docs")),
      h("pre", { class: "mono wrap" }, `curl -H "Authorization: Bearer pg_…" https://${location.host}/api/v1/status`),
      h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, docs.endpoints.map((e) => h("tr", {},
        h("td", {}, h("span", { class: "badge" }, e.method)), h("td", { class: "mono" }, e.path), h("td", {}, e.summary)))))),
      docs.plugins.length ? h("p", { class: "muted" }, t("web.api_plugins"), " ", docs.plugins.map((p) => p.base).join(", ")) : null,
      h("p", {}, h("a", { href: "/api/v1/openapi.json", target: "_blank", rel: "noopener" }, "OpenAPI (JSON)"))));
  };
  await draw();
}

async function download(path, body) {
  const password = await askDialog(t("web.confirm_password"), { type: "password" });
  if (!password) return;
  const response = await attempt(() => api(path, { method: "POST", body: { ...body, password }, raw: true }));
  if (!response) return;
  const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "pi-guard-export";
  const blob = await response.blob();
  if (!blob.size) { toast(t("web.ex_empty"), true); return; }
  const url = URL.createObjectURL(blob);
  h("a", { href: url, download: name }).click();
  URL.revokeObjectURL(url);
  toast(t("web.ex_downloaded", { name }));
}

function dataSection(box) {
  const secrets = h("input", { type: "checkbox" });
  const file = h("input", { type: "file", accept: ".json,application/json", "aria-label": t("web.ex_file") });
  fill(box,
    h("section", { class: "panel stack" }, h("h2", {}, t("web.ex_data")), h("p", { class: "muted" }, t("web.ex_data_hint")),
      h("div", { class: "row" }, ["json", "txt", "csv"].map((format) => h("button", { onclick: () => download("/api/export/data", { format }) }, icon("download"), format.toUpperCase())))),
    h("section", { class: "panel stack" }, h("h2", {}, t("web.ex_config")), h("p", { class: "muted" }, t("web.ex_config_hint")),
      h("label", { class: "check" }, secrets, t("web.ex_secrets")),
      h("button", { onclick: () => download("/api/export/config", { secrets: secrets.checked }) }, icon("download"), "config.json"),
      h("h3", {}, t("web.ex_import")),
      h("div", { class: "row" }, file, h("button", { onclick: async () => {
        if (!file.files.length) return;
        let config;
        try { config = JSON.parse(await file.files[0].text()); } catch { toast(t("web.ex_bad_file"), true); return; }
        const password = await askDialog(t("web.confirm_password"), { type: "password" });
        if (password && await attempt(() => api("/api/import/config", { method: "POST", body: { config, password } }), t("web.saved"))) restartPrompt();
      } }, icon("upload"), t("web.ex_import_button")))));
}

// Backups: what happened last, a button for now, and the Pi's SSH key for rsync and Borg.
async function backupPanel(box) {
  const draw = async () => {
    const b = await api("/api/backup");
    const last = b.last || {};
    const panel = h("section", { class: "panel stack" }, h("h2", {}, icon("upload"), " ", t("web.bk_title")),
      b.passphrase ? null : h("div", { class: "notice stack" }, h("span", {}, t("web.bk_no_passphrase")), h("div", {}, h("button", { class: "small", onclick: () => {
        // The settings are below, folded while backups are off.
        const field = document.getElementById("s_backup_passphrase") || document.getElementById("s_backup_enabled");
        if (!field) return;
        unfold(field);
        field.scrollIntoView({ block: "center", behavior: "smooth" });
        field.focus();
      } }, icon("settings"), t("web.bk_set_up")))),
      h("p", {}, last.time ? h("span", { class: `badge ${last.ok ? "ok" : "danger"}` }, last.ok ? t("web.bk_ok") : t("web.bk_failed")) : null, " ",
        last.time ? t("web.bk_last", { time: when(last.time), target: last.target }) : t("web.bk_never")),
      last.ok === false ? h("p", { class: "notice danger mono wrap" }, last.error) : null,
      last.ok && last.size ? h("p", { class: "muted" }, `${last.name} · ${(last.size / 1e6).toFixed(1)} MB · ${last.files} ${t("web.bk_files")}`) : null,
      h("div", { class: "row" }, h("button", { class: "primary", disabled: b.running || !b.passphrase, onclick: async () => {
        const password = await askDialog(t("web.confirm_password"), { type: "password" });
        if (password && await attempt(() => api("/api/backup/run", { method: "POST", body: { password } }), t("web.bk_started"))) whileShown(box, draw, 3000);
      } }, icon("upload"), b.running ? t("web.bk_running") : t("web.bk_now"))),
      b.target !== "nextcloud" ? [h("h3", {}, t("web.bk_ssh_title")), h("p", { class: "muted" }, t("web.bk_ssh_hint")),
        b.public_key ? h("pre", { class: "mono wrap" }, b.public_key)
          : h("button", { onclick: async () => { if (await attempt(() => api("/api/backup/key", { method: "POST" }))) draw(); } }, icon("key"), t("web.bk_ssh_make"))] : null,
      h("p", { class: "muted" }, h("small", {}, t("web.bk_restore_hint"))));
    fill(box, panel);
    if (b.running) whileShown(box, draw, 3000);
  };
  await draw();
}

// Talking to the Pi: what it does right now, and push to talk.
export async function voicePanel(box) {
  const panel = h("div");
  box.append(panel);
  const draw = async () => {
    const v = await api("/api/voice").catch(() => null);
    if (!panel.isConnected || !v) return;
    const last = v.last;
    fill(panel, h("section", { class: "panel stack" }, h("h2", {}, icon("microphone"), " ", t("web.vo_title")),
      h("p", {}, h("span", { class: `badge ${v.state === "waiting" ? "warn" : v.state === "off" ? "" : "ok"}` }, t(`web.vo_state_${v.state}`)),
        v.wake && v.phrases.length ? h("span", { class: "muted" }, " ", t("web.vo_phrases", { phrases: v.phrases.join(" · ") })) : null),
      v.problem ? h("p", { class: "notice" }, t(v.problem)) : null,
      !v.enabled ? h("p", { class: "muted" }, t("web.vo_off_hint")) : !v.model ? h("p", { class: "notice" }, t("voice.no_model")) : null,
      h("div", { class: "row" }, h("button", { class: "primary", disabled: !v.enabled || !v.model || !["waiting", "idle"].includes(v.state), onclick: async () => {
        if (await attempt(() => api("/api/voice/listen", { method: "POST" }), t("web.vo_speak_now"))) whileShown(panel, draw, 1000);
      } }, icon("microphone"), t("web.vo_talk"))),
      last ? h("div", { class: "stack tight" }, h("small", { class: "muted" }, when(new Date(last.time * 1000).toISOString())),
        h("p", {}, h("strong", {}, t("web.vo_heard"), " "), last.question), h("p", {}, h("strong", {}, t("web.vo_said"), " "), last.answer)) : null,
      h("p", { class: "muted" }, h("small", {}, t("web.vo_privacy")))));
    if (v.enabled) whileShown(panel, draw, ["hearing", "thinking", "speaking"].includes(v.state) ? 1000 : 5000);
  };
  await draw();
}

// Anonymous statistics: off until the owner switches them on, with the exact report shown first.
const MESSENGER_NAMES = { telegram: "Telegram", signal: "Signal", discord: "Discord", whatsapp: "WhatsApp", email: "E-Mail",
  slack: "Slack", rocketchat: "Rocket.Chat", matrix: "Matrix", mastodon: "Mastodon", mobile: "App" };

async function messengerTests() {
  const list = await api("/api/messengers").catch(() => []);
  return h("section", { class: "panel stack" }, h("h2", {}, icon("message"), " ", t("web.messengers_title")),
    h("p", { class: "muted" }, t("web.messengers_intro")),
    list.length ? h("ul", { class: "list" }, list.map((m) => h("li", {},
      h("span", { class: `dot ${m.connected ? "ok" : "danger"}` }),
      h("div", { class: "grow" }, h("div", {}, MESSENGER_NAMES[m.name] || m.name),
        m.admins ? null : h("small", { class: "muted" }, t("web.messenger_no_admin"))),
      h("button", { onclick: async () => {
        const r = await attempt(() => api(`/api/messengers/${encodeURIComponent(m.name)}/test`, { method: "POST" }));
        if (r) toast(t("web.messenger_test_sent", { count: r.sent }));
      } }, icon("bell"), t("web.test"))))) : h("p", { class: "empty" }, t("web.nothing")));
}

// System: is everything fine (a short summary, the details on the System page), then updates
// of the Pi and the apps with their settings.
async function systemSection(root) {
  const health = h("div");
  const updates = h("div");
  fill(root, health, updates);
  if (state.session?.role === "admin") {
    const data = await api("/api/system/health").catch(() => null);
    if (data) {
      const bad = data.checks.filter((c) => c.level === "fail").length;
      const warn = data.checks.filter((c) => c.level === "warn").length;
      const tone = bad ? "danger" : warn ? "warn" : "ok";
      fill(health, h("section", { class: `panel health-summary ${tone}`, id: "web.sy_health_title" },
        h("span", { class: `health-dot ${tone}` }, icon(bad || warn ? "alert-triangle" : "circle-check")),
        h("div", { class: "grow" }, h("h2", {}, bad ? t("web.sy_bad", { count: bad }) : warn ? t("web.sy_warn", { count: warn }) : t("web.sy_ok")),
          h("small", { class: "muted" }, t("web.sy_checked", { time: when(new Date(data.time * 1000).toISOString()) }))),
        h("div", { class: "row wrap" }, h("a", { class: "button", href: "#/system/health" }, icon("activity"), t("web.sy_tab_health")),
          h("a", { class: "button ghost", href: "#/system/device" }, icon("cpu"), t("web.sy_tab_device")))));
    }
  }
  await updatesPage(updates);
  return null;
}

// Network settings: the fields here, the pages with live state one tap away.
function networkLinks(body) {
  const links = [["#/network", "network", "web.nav_network"], ["#/network/vpn", "brand-wireguard", "web.addon_vpn"],
    ["#/addons/ddns", "cloud", "addons.ddns"], ["#/addons/tailscale", "world-www", "addons.tailscale"]];
  body.before(h("section", { class: "panel stack", id: "web.set_network_pages" }, h("h2", {}, icon("network"), " ", t("web.set_network_pages")),
    h("p", { class: "muted" }, t("web.set_network_pages_hint")),
    h("div", { class: "quick-links" }, links.map(([href, ic, label]) => h("a", { class: "button", href }, icon(ic), t(label))))));
}

async function updateSection(root) {
  const box = h("div");
  const apps = h("div");
  fill(root, box, apps);
  const draw = async () => {
    const u = await api("/api/system/update");
    const state = u.state?.state;
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, icon("brand-raspberrypi"), " ", t("web.up_title")),
      h("dl", { class: "kv" }, h("dt", {}, t("web.up_version")), h("dd", {}, `${u.version}${u.commit ? ` (${u.commit})` : ""}`),
        u.remote?.commit ? [h("dt", {}, t("web.up_latest")), h("dd", {}, `${u.remote.version ? `${u.remote.version} · ` : ""}${u.remote.commit} · ${when(u.remote.date)}`)] : null,
        u.source ? [h("dt", {}, t("web.up_source")), h("dd", { class: "mono" }, `${u.source.repository} · ${t(`web.opt_update_channel_${u.source.channel}`)}`)] : null,
        u.instance ? [h("dt", {}, t("web.up_instance"), " ", infoTip(t("web.up_instance_hint"))), h("dd", { class: "mono" }, u.instance)] : null),
      u.remote?.message ? h("p", { class: "muted" }, u.remote.message) : null,
      u.available ? h("p", { class: "notice" }, t("web.up_available")) : h("p", {}, icon("circle-check"), " ", t("web.up_current")),
      u.state?.message ? h("p", { class: `notice ${state === "failed" ? "danger" : ""}` }, `${u.state.message} (${when(u.state.time)})`) : null,
      h("div", { class: "row" },
        h("button", { class: "primary", disabled: u.pending || state === "running" || !u.installed_by_script, onclick: async () => {
          if (await attempt(() => api("/api/system/update", { method: "POST" }), t("web.up_started"))) whileShown(box, draw, 3000);
        } }, icon("refresh"), u.pending || state === "running" ? t("web.up_running") : t("web.up_now")),
        h("button", { onclick: draw }, t("web.up_check"))),
      h("p", { class: "muted" }, h("small", {}, t("web.up_hint"))),
      (u.changelog || []).length ? h("details", { class: "guide", open: Boolean(u.available) },
        h("summary", {}, icon("list-details"), " ", t("web.up_changelog")),
        h("div", { class: "stack changelog" }, u.changelog.map((entry) => h("article", { class: "changelog-entry" },
          h("h3", {}, entry.title || entry.version),
          h("pre", { class: "changelog-body mono wrap" }, entry.body))))) : null));
  };
  const drawApps = (info) => {
    const row = (iconName, label, value, note) => h("li", {}, icon(iconName), h("div", { class: "grow" }, h("div", {}, label), note ? h("small", { class: "muted" }, note) : null), h("strong", {}, value || "–"));
    const desktop = Object.entries(info.desktop || {});
    const last = info.last || {};
    fill(apps, h("section", { class: "panel stack" }, h("h2", {}, icon("device-mobile"), " ", t("web.up_apps")),
      h("ul", { class: "list compact" },
        row("brand-android", t("web.apps_android"), info.android, last["android-apk"]),
        desktop.map(([platform, version]) => row(platform.startsWith("win") ? "brand-windows" : "terminal-2", t("web.apps_desktop", { platform: platform.startsWith("win") ? "Windows" : "Linux" }), version, last[`desktop-${platform}`])),
        Object.entries(info.agent || {}).map(([platform, version]) => row("server", `Agent ${platform}`, version, last[`agent-${platform}`]))),
      !info.android && !desktop.length ? h("p", { class: "muted" }, t("web.up_apps_none")) : null,
      h("p", { class: "muted" }, h("small", {}, t("web.apps_versions_hint"))),
      h("div", { class: "row" }, h("button", { onclick: async () => {
        const next = await attempt(() => api("/api/apps/check", { method: "POST", body: {} }), t("web.up_apps_checked"));
        if (next) drawApps(next);
      } }, icon("refresh"), t("web.up_apps_check")))));
  };
  await Promise.all([draw(), api("/api/apps").then(drawApps).catch(() => fill(apps))]);
}

// Updates of the Pi and the apps (System page): versions first, then the settings with their
// own save bar, then invitations and phones.
export async function updatesPage(root) {
  const status = h("div");
  const body = h("div", { class: "hub-body stack" });
  const extra = h("div", { class: "stack" });
  const saveBar = h("div", { class: "save-bar hidden", role: "status" });
  fill(root, status, body, saveBar, extra);
  let save = null;
  await updateSection(status);
  const view = await import("./settings.js");
  await view.render(body, [], { embedded: true, groups: ["updates", "mobile"], register: (fn) => { save = fn; },
    onSaved: (changed) => { saveBar.classList.add("hidden"); if (Object.keys(changed || {}).length) restartPrompt(); else toast(t("web.saved")); } });
  const showSave = () => {
    fill(saveBar, h("span", { class: "grow" }, t("web.set_unsaved")), h("button", { class: "primary", onclick: () => save?.() }, icon("check"), t("web.save")));
    saveBar.classList.remove("hidden");
  };
  body.addEventListener("input", showSave);
  body.addEventListener("change", showSave);
  await (await import("./mobile.js")).render(extra, { embedded: true, skipVersions: true });
}

// What an emergency would use, on one page: who is told, and whose emergency data exist.
async function emergencyOverview(body) {
  const contacts = h("div");
  const data = h("section", { class: "panel stack" });
  body.before(contacts, data);
  const admin = state.session?.role === "admin";
  const granted = state.session?.permissions || [];
  if (admin || granted.includes("contacts")) await (await import("./contacts.js")).contactsPanel(contacts);
  const people = admin || granted.includes("addon:medical") ? await api("/api/medical").catch(() => null) : null;
  fill(data, h("div", { class: "row between wrap" }, h("h2", {}, icon("user-check"), " ", t("web.md_title")),
    h("a", { class: "button", href: "#/addons/medical" }, icon("pencil"), t("web.em_open_medical"))),
  people === null ? h("p", { class: "muted" }, t("web.em_medical_off"))
    : h("ul", { class: "list" }, people.length ? people.map((p) => h("li", {}, icon("user"),
      h("div", { class: "grow" }, h("div", {}, p.name), h("small", { class: "muted" }, t("web.md_updated", { time: when(p.updated) })))))
      : h("li", { class: "empty" }, t("web.md_none"))),
  h("p", { class: "muted" }, h("small", {}, t("web.em_medical_hint"))));
}

// Who to ask: set in the env file or during installation, shown here as it is.
async function supportSection(box) {
  const s = state.session.support || {};
  const rows = ["name", "phone", "email", "address"].filter((key) => s[key]).map((key) => [h("dt", {}, t(`web.support_${key}`)), h("dd", {}, s[key])]);
  fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("web.set_support")), h("p", { class: "muted" }, t("web.support_hint")),
    // The example for the env file only while nobody is set: next to real data it looked like data too.
    rows.length ? h("dl", { class: "kv" }, rows) : [h("p", { class: "notice" }, t("web.support_none")),
      h("p", { class: "muted" }, h("small", {}, t("web.support_where"))),
      h("pre", { class: "mono wrap" }, "PI_GUARD_SUPPORT_NAME=\"Max Mustermann\"\nPI_GUARD_SUPPORT_PHONE=+49 170 1234567\nPI_GUARD_SUPPORT_EMAIL=hilfe@example.org\nPI_GUARD_SUPPORT_ADDRESS=\"Musterstraße 1, 12345 Musterstadt\"")]),
    await donationPanel());
}

async function donationPanel() {
  // Entirely optional, and only here.
  const coins = await api("/api/support/donate");
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.donate_title")), h("p", { class: "muted" }, t("web.donate_text")),
    h("ul", { class: "list" }, coins.map((coin) => {
      const qr = h("img", { class: "qr light hidden", src: coin.qr, alt: t("web.donate_qr", { name: coin.name }) });
      return h("li", { class: "donate-row" }, icon(`brand-${coin.id}`),
        h("div", { class: "grow clip" }, h("div", {}, coin.name), h("small", { class: "mono wrap" }, coin.address)),
        h("button", { class: "icon-only", title: t("web.donate_copy"), onclick: () => navigator.clipboard?.writeText(coin.address).then(() => toast(t("web.copied"))) }, icon("copy")),
        h("button", { class: "icon-only", title: t("web.donate_show_qr"), onclick: () => qr.classList.toggle("hidden") }, icon("qrcode")), qr);
    })));
}

function searchIndex(fields) {
  // path is where the result opens: a section, or a section and its tab (data/move).
  const section = (group) => (group === "backup" ? "data/backup" : SECTIONS.find(([, , groups]) => groups?.includes(group))?.[0]);
  const allowed = (path) => shown().some(([id]) => id === path.split("/")[0]) && (!path.startsWith("data/export") || expertMode());
  const entry = (path, target, label, extra = "") => ({ id: path.split("/")[0], path, target, label, text: fold(`${label} ${extra}`), name: fold(label) });
  return [
    ...shown().map(([id]) => entry(id, "", t(`web.set_${id}`), t(`web.set_${id}_hint`))),
    ...dataTabs().map(([id]) => entry(`data/${id}`, "", t(`web.set_${id}`), t("web.set_data"))),
    ...fields.filter((f) => section(f.group) && (!f.expert || expertMode()) && (f.group !== "audio" || DEVICE_KEYS.includes(f.key))).map((f) => entry(section(f.group), `s_${f.key}`, t(`web.setting_${f.key}`), `${t(`web.group_${f.group}`)} ${t(`web.tip_${f.key}`)} ${f.key.replace(/_/g, " ")}`)),
    ...Object.entries(TOPICS).flatMap(([path, keys]) => keys.map((key) => entry(path, key, t(key), t(`${key.replace("_title", "")}_hint`)))),
  ].filter((item) => allowed(item.path));
}

export function jumpTo(target) {
  // A field by its id, a topic by its heading.
  const found = document.getElementById(target)?.closest(".switch-row, .field") || document.getElementById(target)
    || [...document.querySelectorAll(".hub-body h2, .hub-body h3, .hub-extra h2, .hub-extra h3, .hub-body label")].find((el) => el.textContent.trim() === t(target));
  if (!found) return;
  unfold(found);
  found.scrollIntoView({ block: "center" });
  found.classList.add("flash");
  setTimeout(() => found.classList.remove("flash"), 2200);
}

// Which section, which tab of Data & backup, and what to jump to: from #/settings/SECTION[/TAB][/TARGET].
function place(params) {
  let [wanted, ...rest] = params;
  if ((DATA_TABS.some(([id]) => id === wanted) || wanted === "data") && memberMay("data")) {
    const tab = wanted === "data" ? rest.shift() : wanted;
    const tabs = dataTabs();
    return { current: "data", tab: tabs.some(([id]) => id === tab) ? tab : tabs[0][0], target: rest[0] };
  }
  wanted = MOVED[wanted] || wanted;
  const open = SECTIONS.filter(([id]) => memberMay(id));  // expert sections stay reachable by link
  const fallback = open.some(([id]) => id === "general") ? "general" : open[0]?.[0] || "appearance";
  return { current: open.some(([id]) => id === wanted) ? wanted : fallback, target: rest[0] };
}

function navigation(current) {
  const items = shown().filter(([id]) => !(id in ELSEWHERE));
  return h("nav", { class: "hub-nav", "aria-label": t("web.nav_settings") }, NAV_GROUPS.map((group) => {
    const own = items.filter(([, , , g]) => g === group);
    return own.length ? h("div", { class: "hub-group" }, h("small", { class: "hub-group-title" }, t(`web.set_group_${group}`)),
      own.map(([id, ic]) => h("a", { href: `#/settings/${id}`, class: id === current ? "active" : "", "aria-current": id === current ? "page" : null },
        icon(ic), h("span", {}, t(`web.set_${id}`)), icon("chevron-right", "hub-chevron")))) : null;
  }));
}

export async function render(main, params = []) {
  const { current, tab, target } = place(params);
  if (current in ELSEWHERE) { location.hash = `#/${ELSEWHERE[current]}${target ? `/${target}` : ""}`; return null; }
  const body = h("div", { class: "hub-body stack" });
  const extra = h("div", { class: "hub-extra stack" });
  const saveBar = h("div", { class: "save-bar hidden", role: "status" });
  const tabs = navigation(current);
  const results = h("section", { class: "panel search-results hidden", "aria-live": "polite" });
  const back = h("a", { class: "hub-back", href: "#/settings" }, icon("chevron-left"), t("web.nav_settings"));
  const head = h("header", { class: "hub-head" }, h("span", { class: "hub-head-icon" }, icon(sectionIcon(current))),
    h("div", {}, h("h2", {}, t(`web.set_${current}`)), h("p", { class: "muted" }, t(`web.set_${current}_hint`))));
  const subtabs = current === "data" ? h("nav", { class: "tabs hub-tabs", "aria-label": t("web.set_data") }, dataTabs().map(([id, ic]) =>
    h("a", { href: `#/settings/data/${id}`, class: id === tab ? "active" : "", "aria-current": id === tab ? "page" : null }, icon(ic), " ", t(`web.set_${id}`)))) : null;
  const hub = h("div", { class: params[0] ? "hub" : "hub menu" }, h("div", { class: "hub-side" }, tabs),
    h("div", { class: "hub-main" }, back, head, subtabs, body, extra, saveBar, supportNote()));
  let index = null;
  const search = searchToggle(t("web.set_search"), async (query) => {
    // Searches all tabs, not this one: results replace the page until the search is closed.
    index ||= api("/api/settings").then((data) => searchIndex(data.fields));
    const words = fold(query).split(/\s+/).filter(Boolean);
    const rank = (item) => (item.name.startsWith(words[0]) ? 0 : item.name.includes(words[0]) ? 1 : 2);
    const matches = words.length ? (await index).filter((item) => words.every((word) => item.text.includes(word))).sort((a, b) => rank(a) - rank(b)).slice(0, 30) : [];
    hub.classList.toggle("hidden", words.length > 0);
    results.classList.toggle("hidden", !words.length);
    const groups = [...new Set(matches.map((item) => item.id))];
    fill(results, matches.length ? groups.map((id) => [
      h("h3", {}, icon(sectionIcon(id)), " ", t(`web.set_${id}`)),
      h("ul", { class: "list" }, matches.filter((item) => item.id === id).map((item) => h("li", { class: "link-row" },
        h("a", { class: "grow", href: `#/settings/${item.path}${item.target ? `/${item.target}` : ""}` }, item.label), icon("chevron-right"))))])
      : h("p", { class: "muted" }, t("web.set_no_results")));
  });
  // The view switch sits in the header with the title and the search, where it is seen first.
  fill(main, h("div", { class: "topbar" }, h("div", { class: "title-row" }, h("h1", {}, t("web.nav_settings")), search), modeSwitch(() => render(main, params))), results, hub);
  installFolds(hub);
  let save = null;
  let stop = null;
  const showSave = () => {
    fill(saveBar, h("span", { class: "grow" }, t("web.set_unsaved")), h("button", { class: "primary", onclick: () => save?.() }, icon("check"), t("web.save")));
    saveBar.classList.remove("hidden");
  };
  // Something to look at while a section loads; finding cameras and sound devices takes seconds.
  const waiting = h("div", { class: "hub-loading" },
    current === "devices" ? h("p", { class: "muted", role: "status" }, h("span", { class: "spinner", "aria-hidden": "true" }), " ", t("web.dev_searching")) : null,
    skeleton(current === "devices" ? 2 : 1));
  body.append(waiting);
  const section = SECTIONS.find(([id]) => id === current);
  if (current === "data" && tab !== "backup") {
    if (tab === "storage") await (await import("./storage.js")).render(body);
    else if (tab === "move") await (await import("./move.js")).render(body);
    else if (tab === "security") stop = await (await import("./scan.js")).render(body);
    else dataSection(body);
  } else if (current === "devices") {
    const view = await import("./devices.js");
    let saveDevices = null;
    let saveFields = null;
    save = () => { saveDevices?.(); saveFields?.(); };
    await view.render(body, [], { embedded: true, register: (fn) => { saveDevices = fn; }, onSaved: () => { saveBar.classList.add("hidden"); restartPrompt(); } });
    // Detection and sound below the devices they belong to, with the same save bar.
    const fields = h("div", { class: "stack" });
    extra.append(h("section", { class: "panel stack", id: "web.set_detection" }, h("h2", {}, icon("users"), " ", t("web.set_detection")),
      h("p", { class: "muted" }, t("web.set_detection_hint"))), fields,
    h("p", { class: "muted" }, icon("microphone"), " ", t("web.voice_moved"), " ", h("a", { href: "#/addons/assistant" }, t("addons.assistant"))));
    const settingsView = await import("./settings.js");
    await settingsView.render(fields, [], { embedded: true, groups: ["detection", "audio"], keys: DEVICE_KEYS, register: (fn) => { saveFields = fn; },
      onSaved: (changed) => { saveBar.classList.add("hidden"); if (Object.keys(changed || {}).length) restartPrompt(); else toast(t("web.saved")); } });
    for (const el of [body, extra]) {
      el.addEventListener("input", showSave);
      el.addEventListener("change", showSave);
    }
    body.addEventListener("click", (e) => { if (e.target.closest("button")) showSave(); });
  } else if (current === "system") {
    stop = await systemSection(body);
  } else if (section[2]) {
    const view = await import("./settings.js");
    await view.render(body, [], { embedded: true, groups: section[2], register: (fn) => { save = fn; },
      onSaved: (changed) => { saveBar.classList.add("hidden"); if (Object.keys(changed || {}).length) { if (changed.language) location.reload(); restartPrompt(); } else toast(t("web.saved")); } });
    body.addEventListener("input", showSave);
    body.addEventListener("change", showSave);
    if (current === "messaging") {
      body.before(h("p", { class: "notice" }, t("web.messaging_contacts_note"), " ",
        h("a", { href: "#/contacts" }, t("web.set_contacts"))));
      const { discordPanel, emailPanel, signalPanel } = await import("./signal.js");
      const modules = state.session.modules || {};
      if (modules.discord) extra.append(await discordPanel());
      if (modules.email) extra.append(emailPanel());
      if (modules.signal) extra.append(await signalPanel());
      if (expertMode()) extra.append(await messengerTests());
    }
    if (current === "modes") await (await import("./plan.js")).render(extra);
    if (current === "emergency") await emergencyOverview(body);
    if (current === "federation") await (await import("./federation.js")).render(extra);
    if (current === "data") {  // what happened last first, the settings below
      const status = h("div");
      body.before(status);
      await backupPanel(status);
    }
    if (current === "network") networkLinks(body);
    if (current === "integrations") {
      const creds = h("div");
      extra.append(h("section", { class: "panel stack" }, h("h2", {}, icon("key"), " ", t("web.int_credentials")),
        h("p", { class: "muted" }, t("web.int_credentials_hint")), creds));
      await view.render(creds, [], { embedded: true, secretsOnly: true, register: (fn) => { const prev = save; save = () => { prev?.(); fn(); }; },
        onSaved: (changed) => { saveBar.classList.add("hidden"); if (Object.keys(changed || {}).length) restartPrompt(); else toast(t("web.saved")); } });
    }
    if (current === "general") {
      const { helpPanel, historyPanel } = await import("./settings-help.js");
      const help = h("div");
      const history = h("div");
      extra.append(help, history);
      const redraw = await historyPanel(history);
      helpPanel(help, redraw);
    }
  } else if (current === "addons") {
    const view = await import("./addons.js");
    await view.render(body, [], { embedded: true });
  } else if (current === "notifications") {
    await (await import("./notifications.js")).render(body);
  } else if (current === "appearance") {
    (await import("./appearance.js")).render(body);
  } else if (current === "logs") {
    stop = await (await import("./logs.js")).render(body, { embedded: true });
  } else if (current === "support") {
    await supportSection(body);
  } else if (current === "access") {
    await (await import("./access.js")).render(body);
    await apiSection(extra);
  }
  waiting.remove();
  if (target) jumpTo(target);
  return stop;
}
