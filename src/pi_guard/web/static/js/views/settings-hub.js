// Settings: one place with sections, search and a save bar that appears when it is needed.
import { api, attempt, fill, h, icon, t, toast, when } from "../api.js";
import { state } from "../app.js";
import { installFolds, unfold } from "../folds.js";
import { searchToggle, supportNote } from "../widgets.js";
import { restartPrompt } from "./settings.js";

// Moved into other tabs; old links keep working.
const MOVED = { plan: "modes", api: "access" };
// What is on the pages without settings fields, so the search finds it too.
const TOPICS = {
  modes: ["web.set_plan"],
  appearance: ["web.theme", "web.text_size", "web.ap_nav", "web.ap_color", "web.ap_font", "web.ap_animations", "web.ap_logo"],
  devices: ["web.cameras", "web.speaker", "web.microphone", "web.trusted_devices", "web.quiet_hours_sound"],
  access: ["web.users_title", "web.webauthn_title", "web.recovery_title", "web.codeword", "web.login_security", "web.account", "web.api_tokens", "web.api_docs"],
  messaging: ["web.discord_status", "web.signal_status"],
  security: ["web.scan_firewall", "web.scan_updates", "web.scan_rootkits", "web.scan_ports", "web.scan_ssh"],
  storage: ["web.st_recordings", "web.st_journal", "web.st_apt", "web.st_folders"],
  data: ["web.ex_data", "web.ex_config", "web.ex_import"],
  updates: ["web.up_title"],
  support: ["web.support_phone", "web.support_email", "web.support_address"],
};
const fold = (text) => text.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");

const SECTIONS = [
  ["general", "settings", ["general"]], ["recording", "video", ["recording"]], ["detection", "users", ["detection"]],
  ["modes", "home", ["modes"]], ["audio", "volume", ["audio"]], ["messaging", "device-mobile", ["messaging", "discord", "email", "whatsapp", "signal"]],
  ["emergency", "alert-triangle", ["emergency"]], ["integrations", "router", ["integrations", "network"]], ["dashboard", "world", ["dashboard"]], ["appearance", "palette", null],
  ["devices", "camera", null], ["notifications", "bell-ringing", null], ["access", "shield-lock", null],
  ["security", "shield-check", null], ["storage", "database", null], ["data", "download", null], ["updates", "refresh", null], ["support", "headset", null],
];

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
        const r = await attempt(() => api("/api/tokens", { method: "POST", body: { name: name.value, scope: scope.value } }));
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
  const password = prompt(t("web.confirm_password"));
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
        const password = prompt(t("web.confirm_password"));
        if (password && await attempt(() => api("/api/import/config", { method: "POST", body: { config, password } }), t("web.saved"))) restartPrompt();
      } }, icon("upload"), t("web.ex_import_button")))));
}

async function updateSection(box) {
  const draw = async () => {
    const u = await api("/api/system/update");
    const state = u.state?.state;
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, icon("brand-github"), " ", t("web.up_title")),
      h("dl", { class: "kv" }, h("dt", {}, t("web.up_version")), h("dd", {}, `${u.version}${u.commit ? ` (${u.commit})` : ""}`),
        u.remote?.commit ? [h("dt", {}, t("web.up_latest")), h("dd", {}, `${u.remote.commit} · ${when(u.remote.date)}`)] : null),
      u.remote?.message ? h("p", { class: "muted" }, u.remote.message) : null,
      u.available ? h("p", { class: "notice" }, t("web.up_available")) : h("p", {}, icon("circle-check"), " ", t("web.up_current")),
      u.state?.message ? h("p", { class: `notice ${state === "failed" ? "danger" : ""}` }, `${u.state.message} (${when(u.state.time)})`) : null,
      h("div", { class: "row" },
        h("button", { class: "primary", disabled: u.pending || state === "running" || !u.installed_by_script, onclick: async () => {
          if (await attempt(() => api("/api/system/update", { method: "POST" }), t("web.up_started"))) setTimeout(draw, 3000);
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
  const section = (group) => SECTIONS.find(([, , groups]) => groups?.includes(group))?.[0];
  const entry = (id, target, label, extra = "") => ({ id, target, label, text: fold(`${label} ${extra}`), name: fold(label) });
  return [
    ...SECTIONS.map(([id]) => entry(id, "", t(`web.set_${id}`))),
    ...fields.filter((f) => section(f.group)).map((f) => entry(section(f.group), `s_${f.key}`, t(`web.setting_${f.key}`), `${t(`web.group_${f.group}`)} ${t(`web.tip_${f.key}`)} ${f.key.replace(/_/g, " ")}`)),
    ...Object.entries(TOPICS).flatMap(([id, keys]) => keys.map((key) => entry(id, key, t(key), t(`${key.replace("_title", "")}_hint`)))),
  ];
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

export async function render(main, params = []) {
  const wanted = MOVED[params[0]] || params[0];
  const current = SECTIONS.some(([id]) => id === wanted) ? wanted : "general";
  const body = h("div", { class: "hub-body stack" });
  const extra = h("div", { class: "hub-extra stack" });
  const saveBar = h("div", { class: "save-bar hidden" });
  const tabs = h("nav", { class: "hub-nav", "aria-label": t("web.nav_settings") }, SECTIONS.map(([id, ic]) =>
    h("a", { href: `#/settings/${id}`, class: id === current ? "active" : "" }, icon(ic), h("span", {}, t(`web.set_${id}`)))));
  const results = h("section", { class: "panel search-results hidden", "aria-live": "polite" });
  const hub = h("div", { class: "hub" }, h("div", { class: "hub-side" }, tabs), h("div", {}, body, extra, saveBar, supportNote()));
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
      h("h3", {}, icon(SECTIONS.find(([section]) => section === id)[1]), " ", t(`web.set_${id}`)),
      h("ul", { class: "list" }, matches.filter((item) => item.id === id).map((item) => h("li", {},
        h("a", { class: "grow", href: `#/settings/${id}${item.target ? `/${item.target}` : ""}` }, item.label), icon("chevron-right"))))])
      : h("p", { class: "muted" }, t("web.set_no_results")));
  });
  fill(main, h("div", { class: "topbar" }, h("div", { class: "title-row" }, h("h1", {}, t("web.nav_settings")), search)), results, hub);
  tabs.querySelector(".active")?.scrollIntoView({ inline: "center", block: "nearest" });
  installFolds(hub);
  let save = null;
  let stop = null;
  const showSave = () => {
    fill(saveBar, h("span", { class: "grow" }, t("web.set_unsaved")), h("button", { class: "primary", onclick: () => save?.() }, icon("check"), t("web.save")));
    saveBar.classList.remove("hidden");
  };
  const section = SECTIONS.find(([id]) => id === current);
  if (section[2]) {
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
      extra.append(await signalPanel());
    }
    if (current === "modes") await (await import("./plan.js")).render(extra);
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
  } else if (current === "storage") {
    await (await import("./storage.js")).render(body);
  } else if (current === "support") {
    await supportSection(body);
  } else if (current === "access") {
    await (await import("./access.js")).render(body);
    await apiSection(extra);
  } else if (current === "data") {
    dataSection(body);
  } else {
    await updateSection(body);
  }
  if (params[1]) jumpTo(params[1]);
  return stop;
}
