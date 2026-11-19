// Notifications as a staircase: what arrives at once, who hears of an alarm nobody answered,
// when the emergency contacts are told, and what an SOS does. Each step is set up where it is.
import { api, attempt, confirmDialog, h, icon, t, fill } from "../api.js";
import { FITS, SHARE_ICON } from "./contacts.js";
import { restartPrompt } from "./settings.js";

const ORDER = ["motion_detected", "person_detected", "unknown_person", "known_person", "arrival_detected", "mode_changed", "presence_changed",
  "camera_disconnected", "camera_reconnected", "recording_failed", "storage_low", "service_down", "service_up", "system_started", "emergency_escalated"];
// The app and browser push first: alarms may come there only, and messengers carry just the
// second line, emergencies and SOS ("emergency").
const MESSENGERS = ["mobile", "push", "telegram", "signal", "discord", "whatsapp", "email"];
const WAY_ICON = { mobile: "device-mobile", push: "bell-ringing", email: "mail" };
const WAY_NAME = { mobile: "web.notif_via_mobile", push: "web.notif_via_push" };

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

function ruleList(rules, ways) {
  const put = (event, body) => attempt(() => api(`/api/notifications/${event}`, { method: "PUT", body }), t("web.saved"));
  return h("ul", { class: "list notif-list" }, rules.map((rule) => {
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
  }));
}

// A number setting of the emergency group, saved on change (needs a restart).
function minutesField(values, key, min, max, save) {
  if (!(key in values)) return null;
  const input = h("input", { type: "number", min, max, value: values[key], "aria-label": t(`web.setting_${key}`) });
  input.addEventListener("change", () => {
    const value = Math.round(Number(input.value));
    if (value >= min && value <= max) save({ [key]: value });
  });
  return h("label", { class: "stair-field" }, h("span", {}, t(`web.setting_${key}`)), input);
}

// One emergency contact on one occasion: takes part or not, and what comes with the message.
function contactRow(data, contact, occasion, redraw) {
  const takes = occasion in contact.share;
  const save = async (share) => {
    const done = await attempt(() => api(`/api/contacts/${contact.id}`, { method: "PUT", body: { contact: { ...contact, share }, person_id: contact.person_id } }), t("web.saved"));
    if (done) redraw();
  };
  const part = h("input", { type: "checkbox", class: "toggle", role: "switch", checked: takes, disabled: contact.legacy, "aria-label": `${contact.name} · ${t(`web.ec_occ_${occasion}`)}` });
  part.addEventListener("change", () => {
    const share = { ...contact.share };
    if (part.checked) share[occasion] = FITS[occasion].filter((s) => s !== "medical" && (occasion !== "sos" || s !== "events"));
    else delete share[occasion];
    save(share);
  });
  const offered = FITS[occasion].filter((s) => s !== "medical" || data.medical);
  const chips = takes ? h("div", { class: "row wrap stair-shares" }, offered.map((s) => {
    const box = h("input", { type: "checkbox", checked: (contact.share[occasion] || []).includes(s), disabled: contact.legacy });
    box.addEventListener("change", async () => {
      if (box.checked && s === "medical" && contact.medical_mode === "sheet" && !await confirmDialog(t("web.ec_sheet_confirm"), t("web.ec_sheet_yes"))) { box.checked = false; return; }
      const chosen = (contact.share[occasion] || []).filter((x) => x !== s);
      save({ ...contact.share, [occasion]: box.checked ? [...chosen, s] : chosen });
    });
    return h("label", { class: "check chip", title: t(`web.ec_share_${s}_hint`) }, box, icon(SHARE_ICON[s]), t(`web.ec_share_${s}`));
  })) : null;
  return h("li", { class: "stair-contact" }, h("div", { class: "row" }, part, icon("user"), h("span", { class: "grow" }, contact.name,
    contact.relation ? h("small", { class: "muted" }, ` · ${contact.relation}`) : null), contact.legacy ? h("small", { class: "muted" }, t("web.stair_legacy")) : null), chips);
}

function contactList(data, occasion, redraw) {
  if (!data) return h("p", { class: "muted" }, t("web.stair_no_contacts_right"));
  return [h("ul", { class: "list compact" }, data.contacts.length ? data.contacts.map((c) => contactRow(data, c, occasion, redraw))
    : h("li", { class: "empty" }, t("web.ec_none"))),
  h("p", {}, h("a", { href: "#/contacts" }, icon("users"), " ", t("web.stair_manage_contacts")))];
}

// Steps stay open across a redraw after saving.
const opened = new Set([1]);

// One step of the staircase. The summary says when it happens and who hears of it.
function step(level, tone, title, timing, who, body) {
  const details = h("details", { open: opened.has(level) },
      h("summary", {}, h("span", { class: "stair-number", "aria-hidden": "true" }, String(level)),
        h("span", { class: "stair-text" }, h("strong", {}, title), h("small", { class: "muted block" }, who)),
        h("span", { class: `badge ${tone}` }, timing)),
      h("div", { class: "stair-body stack" }, body));
  details.addEventListener("toggle", () => { if (details.open) opened.add(level); else opened.delete(level); });
  return h("li", { class: `stair stair-${level} ${tone}` }, details);
}

export async function render(main) {
  const draw = async () => {
    const [notes, settings, persons, contacts] = await Promise.all([api("/api/notifications"),
      api("/api/settings").catch(() => null), api("/api/persons").catch(() => null), api("/api/contacts").catch(() => null)]);
    const { rules, ways } = notes;
    rules.sort((a, b) => ORDER.indexOf(a.event) - ORDER.indexOf(b.event));
    const values = settings?.values || {};
    const save = async (body) => { if (await attempt(() => api("/api/settings", { method: "PUT", body }), t("web.saved"))) { restartPrompt(); draw(); } };
    const takes = (occasion) => (contacts?.contacts || []).filter((c) => occasion in c.share).map((c) => c.name);
    const names = (list, none) => (list.length ? list.join(", ") : t(none));
    const backup = (persons?.persons || []).filter((p) => (p.roles || []).includes("backup")).map((p) => p.display_name);
    const active = rules.filter((r) => r.enabled).length;
    const enabled = (m) => (m === "push" ? values.webpush_enabled : values[`${m}_enabled`]) ?? true;
    const on = MESSENGERS.filter((m) => `${m}_delivery` in values && enabled(m));
    const backupMin = values.emergency_backup_minutes;
    const afterMin = values.emergency_after_minutes;
    const afterAlerts = values.emergency_after_alerts;

    const routing = on.length ? h("ul", { class: "list compact" }, on.map((m) => {
      const pick = h("select", { "aria-label": t(`web.setting_${m}_delivery`) }, ["both", "alerts", "emergency", "commands"].map((o) =>
        h("option", { value: o, selected: values[`${m}_delivery`] === o }, t(`web.opt_${m}_delivery_${o}`))));
      pick.addEventListener("change", () => save({ [`${m}_delivery`]: pick.value }));
      return h("li", {}, icon(WAY_ICON[m] || `brand-${m}`), h("span", { class: "grow" }, t(WAY_NAME[m] || `web.ec_p_${m}`)), pick);
    })) : settings ? h("p", { class: "muted" }, t("web.notif_no_messenger")) : null;
    // Browser push is off until switched on; it needs no messenger at all.
    const pushOff = "webpush_enabled" in values && !values.webpush_enabled ? (() => {
      const box = h("input", { type: "checkbox", class: "toggle", role: "switch", "aria-label": t("web.setting_webpush_enabled") });
      box.addEventListener("change", () => save({ webpush_enabled: box.checked }));
      return h("label", { class: "row" }, box, icon("bell-ringing"), h("span", { class: "grow" }, t("web.notif_push_off")));
    })() : null;

    const order = backupMin !== undefined && afterMin !== undefined && backupMin >= afterMin
      ? h("p", { class: "notice" }, icon("alert-triangle"), " ", t("web.stair_order_warn")) : null;
    const sosAudio = "mobile_sos_home_audio" in values ? (() => {
      const box = h("input", { type: "checkbox", class: "toggle", role: "switch", checked: values.mobile_sos_home_audio, "aria-label": t("web.setting_mobile_sos_home_audio") });
      box.addEventListener("change", () => save({ mobile_sos_home_audio: box.checked }));
      return h("label", { class: "row" }, box, h("span", { class: "grow" }, t("web.setting_mobile_sos_home_audio")));
    })() : null;

    fill(main,
      h("p", { class: "muted" }, t("web.stair_intro")),
      h("ol", { class: "stairs" },
        step(1, "accent", t("web.stair_1"), t("web.stair_now"), t("web.stair_1_who", { count: active, total: rules.length }), [
          routing ? [h("h3", {}, t("web.notif_routing")), h("p", { class: "muted" }, h("small", {}, t("web.notif_routing_intro"))), routing, pushOff] : null,
          h("h3", {}, t("web.stair_1_events")), h("p", { class: "muted" }, h("small", {}, t("web.notif_intro"))), h("div", { class: "settings-panel" }, ruleList(rules, ways))]),
        step(2, "warn", t("web.stair_2"), backupMin !== undefined ? t("web.stair_after", { minutes: backupMin }) : t("web.stair_2_timing"),
          names(backup, "web.notif_backup_none"), [
            h("p", { class: "muted" }, t("web.stair_2_intro")),
            minutesField(values, "emergency_backup_minutes", 1, 1440, save),
            h("ul", { class: "list compact" }, backup.length ? backup.map((n) => h("li", {}, icon("user"), h("span", { class: "grow" }, n)))
              : h("li", { class: "empty" }, t("web.notif_backup_none"))),
            h("p", {}, h("a", { href: "#/contacts" }, icon("users"), " ", t("web.stair_2_roles")))]),
        step(3, "danger", t("web.stair_3"), afterMin !== undefined ? t("web.stair_after_alerts", { minutes: afterMin, alerts: afterAlerts }) : t("web.stair_3_timing"),
          names(takes("escalation"), "web.stair_nobody"), [
            h("p", { class: "muted" }, t("web.stair_3_intro")), order,
            h("div", { class: "row wrap" }, minutesField(values, "emergency_after_alerts", 1, 50, save), minutesField(values, "emergency_after_minutes", 1, 1440, save)),
            contactList(contacts, "escalation", draw)]),
        step(4, "danger sos", t("web.stair_4"), t("web.stair_always"), names(takes("sos"), "web.stair_4_admins"), [
          h("p", { class: "notice" }, icon("alert-triangle"), " ", t("web.stair_4_intro")),
          sosAudio,
          contactList(contacts, "sos", draw)])));
  };
  await draw();
}
