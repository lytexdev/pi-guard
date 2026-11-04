// Which messages arrive: one switch per kind of message, effective at once.
import { api, attempt, h, t, fill } from "../api.js";

const ORDER = ["motion_detected", "person_detected", "unknown_person", "known_person", "arrival_detected", "mode_changed", "presence_changed",
  "camera_disconnected", "camera_reconnected", "recording_failed", "storage_low", "service_down", "service_up", "system_started", "emergency_escalated"];

export async function render(main) {
  const rules = await api("/api/notifications");
  rules.sort((a, b) => ORDER.indexOf(a.event) - ORDER.indexOf(b.event));
  const put = (event, body) => attempt(() => api(`/api/notifications/${event}`, { method: "PUT", body }), t("web.saved"));
  fill(main, h("div", { class: "topbar" }, h("h1", {}, t("web.nav_notifications"))),
    h("p", { class: "muted" }, t("web.notif_intro")),
    h("section", { class: "panel" }, h("ul", { class: "list" }, rules.map((rule) => {
      const on = h("input", { type: "checkbox", checked: rule.enabled, "aria-label": t(`event.${rule.event}`) });
      on.addEventListener("change", () => put(rule.event, { enabled: on.checked }));
      const when = h("select", { "aria-label": t("web.notif_when") }, [["both", "HOME,AWAY"], ["away", "AWAY"], ["home", "HOME"]].map(([k, v]) =>
        h("option", { value: v, selected: [...rule.modes].sort().join() === v }, t(`web.notif_when_${k}`))));
      when.addEventListener("change", () => put(rule.event, { modes: when.value.split(",") }));
      const who = h("select", { "aria-label": t("web.notif_who") }, ["all", "admins"].map((v) => h("option", { value: v, selected: v === rule.recipients }, t(`web.notif_who_${v}`))));
      who.addEventListener("change", () => put(rule.event, { recipients: who.value }));
      return h("li", { class: "notif" }, on, h("span", { class: "grow" }, t(`event.${rule.event}`)), when, who);
    }))));
}
