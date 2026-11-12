// Settings: one place with sections, search and a save bar that appears when it is needed.
import { askDialog, api, attempt, fill, h, icon, t, toast, when, whileShown, expertMode, setExpertMode } from "../api.js";
import { state } from "../app.js";
import { installFolds, unfold } from "../folds.js";
import { searchToggle, supportNote } from "../widgets.js";
import { restartPrompt } from "./settings.js";

// Moved into other tabs; old links keep working.
const MOVED = { plan: "modes", api: "access" };
// Data & backup holds what used to be four tabs; their old links open the right part.
const DATA_TABS = [["backup", "upload"], ["storage", "database"], ["move", "map-pin"], ["export", "download"]];
const dataTabs = () => DATA_TABS.filter(([id]) => id !== "export" || expertMode());
// What is on the pages without settings fields, so the search finds it too.
const TOPICS = {
  modes: ["web.set_plan"],
  appearance: ["web.theme", "web.text_size", "web.ap_nav", "web.ap_color", "web.ap_font", "web.ap_animations", "web.ap_reset"],
  devices: ["web.cameras", "web.speaker", "web.microphone", "web.trusted_devices", "web.quiet_hours_sound"],
  access: ["web.users_title", "web.webauthn_title", "web.recovery_title", "web.codeword", "web.login_security", "web.account", "web.api_tokens", "web.api_docs"],
  messaging: ["web.discord_status", "web.signal_status"],
  apps: ["web.mob_devices", "web.mob_app", "web.apps_versions", "web.apps_invite"],
  security: ["web.scan_firewall", "web.scan_updates", "web.scan_rootkits", "web.scan_ports", "web.scan_ssh"],
  "data/storage": ["web.st_recordings", "web.st_journal", "web.st_apt", "web.st_folders"],
  "data/export": ["web.ex_data", "web.ex_config", "web.ex_import"],
  "data/backup": ["web.bk_title", "web.bk_ssh_title"],
  updates: ["web.up_title"],
  "data/move": ["web.mv_title", "web.mv_before", "web.mv_after", "web.mv_package"],
  support: ["web.support_phone", "web.support_email", "web.support_address"],
};
const fold = (text) => text.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");

// [id, icon, settings groups, nav group]. Data & backup shows the backup group only on its backup tab.
const SECTIONS = [
  ["general", "settings", ["general"], "basics"], ["recording", "video", ["recording"], "basics"], ["detection", "users", ["detection"], "basics"],
  ["modes", "home", ["modes"], "basics"], ["audio", "volume", ["audio"], "basics"],
  ["messaging", "message", ["telegram", "discord", "email", "whatsapp", "signal", "chatapps"], "alerts"], ["notifications", "bell-ringing", null, "alerts"],
  ["emergency", "alert-triangle", ["emergency"], "alerts"],
  ["devices", "camera", null, "home"], ["apps", "device-mobile", ["mobile"], "home"], ["access", "shield-lock", null, "home"], ["appearance", "palette", null, "home"],
  ["data", "database", ["backup"], "system"], ["security", "shield-check", null, "system"], ["integrations", "router", ["integrations", "network"], "system"],
  ["dashboard", "world", ["dashboard"], "system"], ["updates", "refresh", null, "system"], ["support", "headset", null, "system"],
];
const NAV_GROUPS = ["basics", "alerts", "home", "system"];

// Sections for people who know what they are looking for.
const EXPERT_SECTIONS = ["integrations", "dashboard", "security"];
const shown = () => SECTIONS.filter(([id]) => expertMode() || !EXPERT_SECTIONS.includes(id));
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
  const url = URL.createObjectURL(await response.blob());
  h("a", { href: url, download: name }).click();
  URL.revokeObjectURL(url);
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
async function voicePanel(box) {
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
async function contributionPanel(box) {
  const draw = async () => {
    const c = await api("/api/contribution");
    const toggle = h("input", { type: "checkbox", id: "contribution", checked: c.enabled, disabled: !c.url && !c.enabled });
    toggle.addEventListener("change", async () => {
      const done = await attempt(() => api("/api/contribution", { method: "PUT", body: { enabled: toggle.checked } }), t("web.saved"));
      if (!done) toggle.checked = !toggle.checked;
      draw();
    });
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, icon("chart-bar"), " ", t("web.co_title")),
      h("p", {}, t("web.co_intro")),
      h("ul", {}, ["web.co_sent", "web.co_never", "web.co_when"].map((key) => h("li", {}, t(key)))),
      h("div", { class: "switch-row" }, h("label", { for: "contribution" }, t("web.co_switch")), h("div", { class: "control" }, toggle)),
      c.url ? h("p", { class: "muted" }, h("small", {}, t("web.co_url", { url: c.url }), c.last_sent ? ` · ${t("web.co_last", { time: when(c.last_sent) })}` : ""))
        : h("p", { class: "notice" }, t("web.co_no_url")),
      h("details", {}, h("summary", {}, t("web.co_preview")), h("pre", { class: "mono wrap" }, JSON.stringify(c.report, null, 2)))));
  };
  await draw();
}

const MESSENGER_NAMES = { telegram: "Telegram", signal: "Signal", discord: "Discord", whatsapp: "WhatsApp", email: "E-Mail",
  slack: "Slack", rocketchat: "Rocket.Chat", matrix: "Matrix", mastodon: "Mastodon", mobile: "App" };

// Expert view: a test message through every messenger that is set up.
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

async function updateSection(root) {
  const box = h("div");
  const helping = h("div");
  fill(root, box, helping);
  contributionPanel(helping).catch(() => helping.remove());
  const draw = async () => {
    const u = await api("/api/system/update");
    const state = u.state?.state;
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, icon("brand-github"), " ", t("web.up_title")),
      h("dl", { class: "kv" }, h("dt", {}, t("web.up_version")), h("dd", {}, `${u.version}${u.commit ? ` (${u.commit})` : ""}`),
        u.remote?.commit ? [h("dt", {}, t("web.up_latest")), h("dd", {}, `${u.remote.version ? `${u.remote.version} · ` : ""}${u.remote.commit} · ${when(u.remote.date)}`)] : null),
      u.remote?.message ? h("p", { class: "muted" }, u.remote.message) : null,
      u.available ? h("p", { class: "notice" }, t("web.up_available")) : h("p", {}, icon("circle-check"), " ", t("web.up_current")),
      u.state?.message ? h("p", { class: `notice ${state === "failed" ? "danger" : ""}` }, `${u.state.message} (${when(u.state.time)})`) : null,
      h("div", { class: "row" },
        h("button", { class: "primary", disabled: u.pending || state === "running" || !u.installed_by_script, onclick: async () => {
          if (await attempt(() => api("/api/system/update", { method: "POST" }), t("web.up_started"))) whileShown(box, draw, 3000);
        } }, icon("refresh"), u.pending || state === "running" ? t("web.up_running") : t("web.up_now")),
        h("button", { onclick: draw }, t("web.up_check"))),
      h("p", { class: "muted" }, h("small", {}, t("web.up_hint")))));
  };
  await draw();
}

async function supportSection(box) {
  const saved = state.session.support || {};
  const input = (key, type = "text") => h("input", { id: `sp_${key}`, type, value: saved[key] || "" });
  const fields = { name: input("name"), phone: input("phone", "tel"), email: input("email", "email"), address: h("textarea", { id: "sp_address", rows: 3 }, saved.address || "") };
  fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("web.set_support")), h("p", { class: "muted" }, t("web.support_hint")),
    Object.entries(fields).map(([key, el]) => h("div", { class: "field" }, h("label", { for: el.id }, t(`web.support_${key}`)), el)),
    h("button", { class: "primary", onclick: async () => {
      const values = Object.fromEntries(Object.entries(fields).map(([key, el]) => [key, el.value]));
      const result = await attempt(() => api("/api/support", { method: "PUT", body: values }), t("web.saved"));
      if (result) state.session.support = result;
    } }, icon("check"), t("web.save"))),
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
    ...fields.filter((f) => section(f.group) && (!f.expert || expertMode())).map((f) => entry(section(f.group), `s_${f.key}`, t(`web.setting_${f.key}`), `${t(`web.group_${f.group}`)} ${t(`web.tip_${f.key}`)} ${f.key.replace(/_/g, " ")}`)),
    ...Object.entries(TOPICS).flatMap(([path, keys]) => keys.map((key) => entry(path, key, t(key), t(`${key.replace("_title", "")}_hint`)))),
  ].filter((item) => allowed(item.path));
}

function jumpTo(target) {
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
  if (DATA_TABS.some(([id]) => id === wanted) || wanted === "data") {
    const tab = wanted === "data" ? rest.shift() : wanted;
    const tabs = dataTabs();
    return { current: "data", tab: tabs.some(([id]) => id === tab) ? tab : tabs[0][0], target: rest[0] };
  }
  wanted = MOVED[wanted] || wanted;
  return { current: SECTIONS.some(([id]) => id === wanted) ? wanted : "general", target: rest[0] };
}

function navigation(current) {
  const items = shown();
  return h("nav", { class: "hub-nav", "aria-label": t("web.nav_settings") }, NAV_GROUPS.map((group) => {
    const own = items.filter(([, , , g]) => g === group);
    return own.length ? h("div", { class: "hub-group" }, h("small", { class: "hub-group-title" }, t(`web.set_group_${group}`)),
      own.map(([id, ic]) => h("a", { href: `#/settings/${id}`, class: id === current ? "active" : "", "aria-current": id === current ? "page" : null },
        icon(ic), h("span", {}, t(`web.set_${id}`)), icon("chevron-right", "hub-chevron")))) : null;
  }));
}

export async function render(main, params = []) {
  const { current, tab, target } = place(params);
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
  const section = SECTIONS.find(([id]) => id === current);
  if (current === "data" && tab !== "backup") {
    if (tab === "storage") await (await import("./storage.js")).render(body);
    else if (tab === "move") await (await import("./move.js")).render(body);
    else dataSection(body);
  } else if (section[2]) {
    const view = await import("./settings.js");
    await view.render(body, [], { embedded: true, groups: section[2], register: (fn) => { save = fn; },
      onSaved: (changed) => { saveBar.classList.add("hidden"); if (Object.keys(changed || {}).length) { if (changed.language) location.reload(); restartPrompt(); } else toast(t("web.saved")); } });
    body.addEventListener("input", showSave);
    body.addEventListener("change", showSave);
    if (current === "messaging") {
      const { discordPanel, emailPanel, signalPanel } = await import("./signal.js");
      const modules = state.session.modules || {};
      if (modules.discord) extra.append(await discordPanel());
      if (modules.email) extra.append(emailPanel());
      if (modules.signal) extra.append(await signalPanel());
      if (expertMode()) extra.append(await messengerTests());
    }
    if (current === "modes") await (await import("./plan.js")).render(extra);
    if (current === "apps") await (await import("./mobile.js")).render(extra, { embedded: true });
    if (current === "emergency") {  // the contacts first: the fields below are the old way
      const contacts = h("div");
      body.before(contacts);
      await (await import("./contacts.js")).contactsPanel(contacts);
    }
    if (current === "data") {  // what happened last first, the settings below
      const status = h("div");
      body.before(status);
      await backupPanel(status);
    }
    if (current === "audio") await voicePanel(extra);
    if (current === "general") {
      const { helpPanel, historyPanel } = await import("./settings-help.js");
      const help = h("div");
      const history = h("div");
      extra.append(help, history);
      const redraw = await historyPanel(history);
      helpPanel(help, redraw);
    }
  } else if (current === "devices") {
    const view = await import("./devices.js");
    await view.render(body, [], { embedded: true, register: (fn) => { save = fn; }, onSaved: () => { saveBar.classList.add("hidden"); restartPrompt(); } });
    body.addEventListener("input", showSave);
    body.addEventListener("change", showSave);
    body.addEventListener("click", (e) => { if (e.target.closest("button")) showSave(); });
  } else if (current === "notifications") {
    await (await import("./notifications.js")).render(body);
  } else if (current === "appearance") {
    (await import("./appearance.js")).render(body);
  } else if (current === "security") {
    stop = await (await import("./scan.js")).render(body);
  } else if (current === "support") {
    await supportSection(body);
  } else if (current === "access") {
    await (await import("./access.js")).render(body);
    await apiSection(extra);
  } else {
    await updateSection(body);
  }
  if (target) jumpTo(target);
  return stop;
}
