// Which messages arrive: one switch per kind of message, effective at once.
import { api, attempt, h, icon, t, fill } from "../api.js";
import { restartPrompt } from "./settings.js";

const ORDER = ["motion_detected", "person_detected", "unknown_person", "known_person", "arrival_detected", "mode_changed", "presence_changed",
  "camera_disconnected", "camera_reconnected", "recording_failed", "storage_low", "service_down", "service_up", "system_started", "emergency_escalated"];

const wayName = (way) => (["mobile", "push", "email"].includes(way) ? t(`web.notif_via_${way}`) : way[0].toUpperCase() + way.slice(1));

// Where one kind of message goes. What is not set up here stays as it was.
function channels(rule, ways, put) {
  const summary = h("summary", {});
  const label = () => {
    const chosen = ways.filter((w) => rule.providers.includes(w));
    fill(summary, !chosen.length ? t("web.notif_via_none") : chosen.length === ways.length ? t("web.notif_via_all") : chosen.map(wayName).join(", "));
  };
  label();
  return h("details", { class: "notif-ways" }, summary, h("div", { class: "row" }, ways.map((way) => {
    const box = h("input", { type: "checkbox", checked: rule.providers.includes(way) });
    box.addEventListener("change", () => {
      rule.providers = box.checked ? [...rule.providers, way] : rule.providers.filter((w) => w !== way);
      label();
      put(rule.event, { providers: rule.providers });
    });
    return h("label", { class: "check" }, box, wayName(way));
  })));
}

const MESSENGERS = ["telegram", "signal", "discord", "whatsapp", "email"];

// Per messenger what it sends by itself, and when the second line hears of an alarm. Settings
// need a restart; without the right to change them this part is not shown.
async function routingPanel() {
  const [settings, persons] = await Promise.all([api("/api/settings").catch(() => null), api("/api/persons").catch(() => null)]);
  if (!settings?.values) return null;
  const on = MESSENGERS.filter((m) => `${m}_delivery` in settings.values && (settings.values[`${m}_enabled`] ?? true));
  const save = async (body) => { if (await attempt(() => api("/api/settings", { method: "PUT", body }), t("web.saved"))) restartPrompt(); };
  const backup = (persons?.persons || []).filter((p) => (p.roles || []).includes("backup")).map((p) => p.display_name);
  const minutes = h("input", { type: "number", min: 1, max: 1440, value: settings.values.emergency_backup_minutes ?? 10, "aria-label": t("web.setting_emergency_backup_minutes") });
  minutes.addEventListener("change", () => save({ emergency_backup_minutes: Number(minutes.value) }));
  return h("section", { class: "panel stack" }, h("h2", {}, icon("send"), " ", t("web.notif_routing")),
    h("p", { class: "muted" }, t("web.notif_routing_intro")),
    on.length ? h("ul", { class: "list" }, on.map((m) => {
      const pick = h("select", { "aria-label": t(`web.setting_${m}_delivery`) }, ["both", "alerts", "emergency", "commands"].map((o) =>
        h("option", { value: o, selected: settings.values[`${m}_delivery`] === o }, t(`web.opt_${m}_delivery_${o}`))));
      pick.addEventListener("change", () => save({ [`${m}_delivery`]: pick.value }));
      return h("li", {}, icon(m === "email" ? "mail" : `brand-${m}`), h("span", { class: "grow" }, t(`web.ec_p_${m}`)), pick);
    })) : h("p", { class: "muted" }, t("web.notif_no_messenger")),
    h("div", { class: "row wrap" }, h("label", { class: "grow" }, t("web.setting_emergency_backup_minutes"),
      h("small", { class: "muted block" }, backup.length ? t("web.notif_backup_people", { names: backup.join(", ") }) : t("web.notif_backup_none"))), minutes),
    h("p", { class: "notice" }, icon("alert-triangle"), " ", t("web.notif_sos_everywhere")));
}

export async function render(main) {
  const { rules, ways } = await api("/api/notifications");
  rules.sort((a, b) => ORDER.indexOf(a.event) - ORDER.indexOf(b.event));
  const put = (event, body) => attempt(() => api(`/api/notifications/${event}`, { method: "PUT", body }), t("web.saved"));
  fill(main,
    await routingPanel(),
    h("p", { class: "muted" }, t("web.notif_intro")),
    h("section", { class: "panel settings-panel" }, h("ul", { class: "list notif-list" }, rules.map((rule) => {
      const on = h("input", { type: "checkbox", class: "toggle", role: "switch", checked: rule.enabled, "aria-label": t(`event.${rule.event}`) });
      on.addEventListener("change", () => put(rule.event, { enabled: on.checked }));
      const when = h("select", { "aria-label": t("web.notif_when") }, [["both", "HOME,AWAY"], ["away", "AWAY"], ["home", "HOME"]].map(([k, v]) =>
        h("option", { value: v, selected: [...rule.modes].sort().join() === v }, t(`web.notif_when_${k}`))));
      when.addEventListener("change", () => put(rule.event, { modes: when.value.split(",") }));
      const who = h("select", { "aria-label": t("web.notif_who") }, ["all", "admins"].map((v) => h("option", { value: v, selected: v === rule.recipients }, t(`web.notif_who_${v}`))));
      who.addEventListener("change", () => put(rule.event, { recipients: who.value }));
      return h("li", { class: "notif" },
        h("div", { class: "notif-main" }, on, h("span", { class: "grow" }, t(`event.${rule.event}`))),
        h("div", { class: "notif-meta row wrap" }, when, who, channels(rule, ways, put)));
    }))));
}
