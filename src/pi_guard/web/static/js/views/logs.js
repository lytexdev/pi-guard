// Logs: a security overview, the service log and the audit trail, each in its own tab.
import { api, fill, h, icon, lang, t, toast, when } from "../api.js";
import { supportNote } from "../widgets.js";

const TABS = [["overview", "shield-check"], ["service", "list-details"], ["audit", "history"]];
const LEVELS = ["", "INFO", "WARNING", "ERROR"];
const LEVEL_CLASS = { DEBUG: "", INFO: "info", WARNING: "warn", ERROR: "danger", CRITICAL: "danger" };
// What an audit action is about, for its icon.
const AUDIT_ICONS = [["login", "lock"], ["logout", "logout"], ["password", "key"], ["totp", "key"], ["passkey", "fingerprint"],
  ["settings", "settings"], ["module", "plug-connected"], ["addon", "plug-connected"], ["security", "shield-check"],
  ["wireguard", "brand-wireguard"], ["vpn", "brand-wireguard"], ["mode", "home"], ["alarm", "alarm"], ["system", "refresh"],
  ["mobile", "device-mobile"], ["emergency", "alert-triangle"], ["apps", "share"], ["assistant", "sparkles"], ["backup", "database"]];
const auditIcon = (action) => AUDIT_ICONS.find(([word]) => action.includes(word))?.[1] || "file-text";

let tab = (() => { try { return localStorage.getItem("logs.tab") || "overview"; } catch { return "overview"; } })();

function tile(key, value, iconName, bad) {
  return h("div", { class: `log-tile${bad && value ? " bad" : ""}` }, icon(iconName),
    h("div", {}, h("strong", {}, String(value)), h("small", {}, t(`web.sec_${key}`))));
}

function listOrEmpty(items, row) {
  return h("ul", { class: "list compact" }, items.length ? items.map(row) : h("li", { class: "empty" }, t("web.nothing")));
}

function overview(s) {
  if (!s) return h("p", { class: "muted" }, t("web.nothing"));
  const c = s.counts;
  return [
    h("section", { class: "panel stack" }, h("h2", {}, icon("shield-check"), " ", t("web.sec_title", { days: s.days })),
      h("div", { class: "log-tiles" },
        tile("logins", c.logins, "lock"), tile("failed_logins", c.failed_logins, "alert-triangle", true),
        tile("honeypot_hits", c.honeypot_hits, "eye", true), tile("bans", c.bans, "ban", true),
        tile("bad_hosts", c.bad_hosts, "world", true), tile("rejected_messages", c.rejected_messages, "message", true),
        tile("denied_commands", c.denied_commands, "hand-stop", true)),
      s.recommendations.length ? h("ul", { class: "log-advice" }, s.recommendations.map((r) => h("li", {}, icon("bulb"), h("span", { class: "grow" }, t(r.key)),
        h("a", { class: "button small", href: r.href }, t("web.sec_fix"))))) : h("p", {}, icon("circle-check"), " ", t("web.sec_all_good"))),
    h("div", { class: "grid wide" },
      h("section", { class: "panel" }, h("h2", {}, icon("world"), " ", t("web.sec_sources")),
        listOrEmpty(s.top_sources, (x) => h("li", {}, h("span", { class: "grow mono" }, x.ip), h("span", { class: "badge" }, x.count)))),
      h("section", { class: "panel" }, h("h2", {}, icon("eye"), " ", t("web.sec_honeypots")),
        listOrEmpty(s.honeypot_paths, (x) => h("li", {}, h("span", { class: "grow mono clip" }, x.path), h("span", { class: "badge" }, x.count)))),
      h("section", { class: "panel" }, h("h2", {}, icon("router"), " ", t("web.sec_new_devices")),
        listOrEmpty(s.new_devices, (x) => h("li", {}, h("div", { class: "grow" }, h("div", { class: "mono" }, x.ip || x.mac), h("small", { class: "muted" }, `${x.mac} · ${when(x.first_seen)}`))))),
      h("section", { class: "panel" }, h("h2", {}, icon("network"), " ", t("web.sec_network")), h("dl", { class: "kv" },
        h("dt", {}, t("web.sec_blocked")), h("dd", {}, s.percent_blocked == null ? "–" : `${s.percent_blocked} %`),
        h("dt", {}, t("web.sec_sessions")), h("dd", {}, s.sessions),
        h("dt", {}, t("web.sec_active_bans")), h("dd", {}, Object.keys(s.active_bans).length)))),
    h("section", { class: "panel" }, h("h2", {}, icon("history"), " ", t("web.sec_timeline")),
      listOrEmpty(s.timeline.slice(0, 10), (r) => h("li", {}, icon(auditIcon(r.action)),
        h("div", { class: "grow" }, h("div", {}, r.action), h("small", { class: "mono muted" }, `${r.actor} ${r.detail}`)), h("small", { class: "muted" }, when(r.ts))))),
  ];
}

function download(name, text) {
  const link = h("a", { href: URL.createObjectURL(new Blob([text], { type: "text/plain" })), download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function serviceLog(box) {
  let level = "", query = "", live = true, records = [];
  const list = h("ol", { class: "log-lines", "aria-live": "off" });
  const count = h("small", { class: "muted" });
  const chips = h("div", { class: "segmented", role: "group", "aria-label": t("web.log_level") }, LEVELS.map((value) =>
    h("button", { class: value === level ? "on" : "", onclick: (e) => { level = value; [...chips.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); load(); } },
      value ? t(`web.log_${value.toLowerCase()}`) : t("web.all"))));
  const search = h("input", { type: "search", placeholder: t("web.log_search"), "aria-label": t("web.log_search") });
  const pause = h("button", { class: "ghost", onclick: () => { live = !live; drawPause(); if (live) load(); } });
  const drawPause = () => fill(pause, icon(live ? "player-stop" : "player-play"), t(live ? "web.log_pause" : "web.log_live"));
  const text = () => records.map((r) => `${r.ts} ${r.level} ${r.logger}: ${r.message}`).join("\n");
  const show = () => {
    const shown = query ? records.filter((r) => `${r.logger} ${r.message}`.toLowerCase().includes(query)) : records;
    fill(count, t("web.log_count", { shown: shown.length, total: records.length }));
    fill(list, shown.length ? shown.map((r) => h("li", { class: `log-line ${LEVEL_CLASS[r.level] || ""}` },
      h("time", {}, when(r.ts)), h("span", { class: `badge ${LEVEL_CLASS[r.level] || ""}` }, r.level),
      h("span", { class: "log-logger" }, r.logger.replace(/^pi_guard\./, "")), h("span", { class: "log-message" }, r.message)))
      : h("li", { class: "empty" }, t("web.nothing")));
  };
  const load = async () => { records = await api(`/api/logs?level=${level}&limit=500`); show(); };
  search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); show(); });
  drawPause();
  fill(box, h("section", { class: "panel stack" },
    h("div", { class: "row between wrap" }, h("h2", {}, icon("list-details"), " ", t("web.service_log")), count),
    h("div", { class: "row wrap log-tools" }, chips, h("div", { class: "grow" }, search), pause,
      h("button", { class: "ghost", title: t("web.copy"), onclick: async () => { try { await navigator.clipboard.writeText(text()); toast(t("web.copied")); } catch { toast(t("web.error"), true); } } }, icon("copy"), t("web.copy")),
      h("button", { class: "ghost", onclick: () => download("pi-guard.log", text()) }, icon("download"), t("web.log_download"))),
    list));
  return { load, live: () => live };
}

function auditLog(box) {
  let rows = [], query = "";
  const list = h("div", { class: "audit-days" });
  const search = h("input", { type: "search", placeholder: t("web.log_search_audit"), "aria-label": t("web.log_search_audit") });
  const show = () => {
    const shown = query ? rows.filter((a) => `${a.actor} ${a.action} ${a.detail}`.toLowerCase().includes(query)) : rows;
    const days = new Map();
    for (const a of shown) {
      const day = new Date(a.ts).toLocaleDateString(lang, { weekday: "long", day: "numeric", month: "long" });
      if (!days.has(day)) days.set(day, []);
      days.get(day).push(a);
    }
    fill(list, days.size ? [...days].map(([day, items]) => h("section", { class: "audit-day" }, h("h3", {}, day),
      h("ul", { class: "list compact" }, items.map((a) => h("li", {}, icon(auditIcon(a.action)),
        h("div", { class: "grow" }, h("div", {}, h("strong", {}, a.action), " ", h("span", { class: "badge" }, a.actor)),
          a.detail ? h("small", { class: "mono muted block clip" }, a.detail) : null),
        h("small", { class: "muted" }, new Date(a.ts).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" }))))))) : h("p", { class: "empty" }, t("web.nothing")));
  };
  const load = async () => { rows = await api("/api/audit"); show(); };
  search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); show(); });
  fill(box, h("section", { class: "panel stack" }, h("h2", {}, icon("history"), " ", t("web.audit")), h("p", { class: "muted" }, t("web.audit_hint")), search, list));
  return { load };
}

export async function render(main) {
  const body = h("div", { class: "stack" });
  const tabs = h("nav", { class: "tabs", "aria-label": t("web.nav_logs") });
  let timer = null;
  const open = async (name) => {
    tab = name;
    try { localStorage.setItem("logs.tab", name); } catch { /* private window */ }
    clearInterval(timer);
    fill(tabs, TABS.map(([id, ic]) => h("a", { href: "#/logs", class: id === tab ? "active" : "", "aria-current": id === tab ? "page" : null,
      onclick: (e) => { e.preventDefault(); open(id); } }, icon(ic), " ", t(`web.logs_tab_${id}`))));
    if (tab === "overview") {
      fill(body, overview(await api("/api/security").catch(() => null)));
    } else if (tab === "service") {
      const view = serviceLog(body);
      await view.load();
      timer = setInterval(() => { if (view.live() && body.isConnected) view.load().catch(() => {}); }, 10000);
    } else {
      const view = auditLog(body);
      await view.load();
    }
  };
  fill(main, h("div", { class: "topbar" }, h("h1", {}, t("web.nav_logs"))), tabs, body, supportNote());
  await open(TABS.some(([id]) => id === tab) ? tab : "overview");
  return () => clearInterval(timer);
}
