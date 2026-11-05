// Weekly plan: when nobody is usually at home. Drag in a day to add time, tap a block to change it.
import { api, attempt, fill, h, icon, t } from "../api.js";

const STEP = 30;
const HEIGHT = 576;  // px for 24 hours, 12 px per half hour
const DAY = 1440;
const LABELS = ["web.plan_work", "web.plan_school", "web.plan_sport", "web.plan_trip"];

const px = (minutes) => (minutes / DAY) * HEIGHT;
const snap = (y) => Math.min(DAY, Math.max(0, Math.round((y / HEIGHT) * DAY / STEP) * STEP));
const clock = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const minutes = (text) => { const [a, b] = text.split(":").map(Number); return a * 60 + b; };

export async function render(box) {
  const plan = await api("/api/schedule");
  const grid = h("div", { class: "plan-grid" });
  const save = async () => {
    const saved = await attempt(() => api("/api/schedule", { method: "PUT", body: plan }));
    if (saved) Object.assign(plan, saved);
    drawGrid();
  };

  const edit = (preset, existing = null) => {
    const start = h("input", { id: "pstart", type: "time", step: 1800, value: clock(preset.start) });
    const end = h("input", { id: "pend", type: "time", step: 1800, value: preset.end === DAY ? "23:59" : clock(preset.end) });
    const label = h("input", { id: "plabel", maxlength: 40, value: preset.label || "", list: "plabels" });
    const days = [0, 1, 2, 3, 4, 5, 6].map((day) => h("input", { type: "checkbox", value: day, checked: day === preset.day || (preset.days || []).includes(day) }));
    const dialog = h("dialog", { class: "wg-dialog" }, h("h2", {}, t(existing ? "web.plan_edit" : "web.plan_add")),
      h("div", { class: "field" }, h("label", { for: "plabel" }, t("web.plan_label")), label,
        h("datalist", { id: "plabels" }, LABELS.map((key) => h("option", { value: t(key) })))),
      h("div", { class: "row" }, h("div", { class: "field" }, h("label", { for: "pstart" }, t("web.plan_from")), start),
        h("div", { class: "field" }, h("label", { for: "pend" }, t("web.plan_to")), end)),
      h("div", { class: "day-picks" }, days.map((input, day) => h("label", {}, input, t(`web.wd_${day}`)))),
      h("div", { class: "row end" },
        existing ? h("button", { class: "danger", onclick: () => { plan.blocks.splice(plan.blocks.indexOf(existing), 1); dialog.close(); save(); } }, icon("trash"), t("web.delete")) : null,
        h("button", { onclick: () => dialog.close() }, t("web.cancel")),
        h("button", { class: "primary", onclick: () => {
          const from = minutes(start.value), raw = minutes(end.value);
          const to = raw === 1439 ? DAY : raw;
          const chosen = days.filter((d) => d.checked).map((d) => Number(d.value));
          if (!chosen.length || to <= from) return;
          if (existing) plan.blocks.splice(plan.blocks.indexOf(existing), 1);
          chosen.forEach((day) => plan.blocks.push({ day, start: from, end: to, label: label.value }));
          dialog.close();
          save();
        } }, icon("check"), t("web.save"))));
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
  };

  const column = (day, today, now) => {
    const col = h("div", { class: "plan-col", role: "group", "aria-label": t(`web.wd_${day}`) });
    plan.blocks.filter((b) => b.day === day).forEach((block) => {
      const el = h("button", { class: "plan-block", title: `${block.label || t("web.plan_away")} ${clock(block.start)}–${clock(block.end)}`,
        onclick: () => edit(block, block) }, block.label || clock(block.start));
      el.style.top = `${px(block.start)}px`;
      el.style.height = `${px(block.end - block.start)}px`;
      col.append(el);
    });
    if (day === today) {
      const line = h("div", { class: "plan-now" });
      line.style.top = `${px(now)}px`;
      col.append(line);
    }
    col.addEventListener("pointerdown", (event) => {
      if (event.target !== col) return;
      const top = col.getBoundingClientRect().top;
      const first = snap(event.clientY - top);
      if (event.pointerType === "touch") {  // a finger scrolls; a tap opens the form
        col.addEventListener("click", () => edit({ day, start: first, end: Math.min(DAY, first + 60) }), { once: true });
        return;
      }
      event.preventDefault();
      const ghost = h("div", { class: "plan-block ghost" });
      col.append(ghost);
      let last = first;
      const follow = (e) => {
        last = snap(e.clientY - top);
        ghost.style.top = `${px(Math.min(first, last))}px`;
        ghost.style.height = `${px(Math.abs(last - first))}px`;
      };
      const drop = () => {
        removeEventListener("pointermove", follow);
        removeEventListener("pointerup", drop);
        ghost.remove();
        const start = Math.min(first, last);
        const end = Math.max(first, last);
        edit({ day, start, end: end > start ? end : Math.min(DAY, start + 60) });
      };
      addEventListener("pointermove", follow);
      addEventListener("pointerup", drop);
    });
    return col;
  };

  const drawGrid = () => {
    const date = new Date();
    const today = (date.getDay() + 6) % 7;
    const now = date.getHours() * 60 + date.getMinutes();
    const hours = h("div", { class: "plan-hours" }, [0, 6, 12, 18, 24].map((hour) => {
      const label = h("span", {}, `${hour}`);
      label.style.top = `${Math.min(px(hour * 60), HEIGHT - 8)}px`;
      return label;
    }));
    fill(grid, hours, [0, 1, 2, 3, 4, 5, 6].map((day) => column(day, today, now)));
    head.querySelectorAll("span[data-day]").forEach((el) => el.classList.toggle("today", Number(el.dataset.day) === today));
  };

  const head = h("div", { class: "plan-head" }, h("span"), [0, 1, 2, 3, 4, 5, 6].map((day) => h("span", { dataset: { day } }, t(`web.wd_${day}`))));
  const enabled = h("input", { type: "checkbox", checked: plan.enabled });
  const presence = h("input", { type: "checkbox", checked: plan.respect_presence });
  enabled.addEventListener("change", () => { plan.enabled = enabled.checked; save(); });
  presence.addEventListener("change", () => { plan.respect_presence = presence.checked; save(); });
  fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("web.set_plan")), h("p", { class: "muted" }, t("web.plan_intro")),
    h("label", { class: "check", title: t("web.plan_on_tip") }, enabled, t("web.plan_on")),
    plan.presence ? h("label", { class: "check", title: t("web.plan_presence_tip") }, presence, t("web.plan_presence")) : null,
    h("button", { class: "primary", onclick: () => edit({ days: [0, 1, 2, 3, 4], start: 480, end: 1020, label: t("web.plan_work") }) }, icon("plus"), t("web.plan_add")),
    h("div", {}, head, grid), h("p", { class: "muted" }, h("small", {}, t("web.plan_hint")))), await calendarsPanel());
  drawGrid();
}

// Calendars count as "not at home" during busy times. Only start and end ever reach the Pi.
async function calendarsPanel() {
  const panel = h("section", { class: "panel stack" });
  const draw = async () => {
    const { feeds, phone } = await api("/api/calendars");
    const name = h("input", { id: "calname", maxlength: 40, placeholder: t("web.cal_name_placeholder") });
    const link = h("input", { id: "callink", type: "url", autocomplete: "off", placeholder: "https://…" });
    const add = h("form", { class: "stack", onsubmit: async (event) => {
      event.preventDefault();
      const done = await attempt(() => api("/api/calendars", { method: "POST", body: { name: name.value, link: link.value } }), t("web.saved"));
      if (done) draw();
    } }, h("div", { class: "field" }, h("label", { for: "calname" }, t("web.name")), name),
      h("div", { class: "field" }, h("label", { for: "callink" }, t("web.cal_link")), link),
      h("button", { class: "primary", type: "submit" }, icon("plus"), t("web.cal_add")));
    fill(panel, h("h2", {}, icon("calendar"), " ", t("web.cal_title")),
      h("p", { class: "muted" }, t("web.cal_intro")),
      h("p", {}, icon("device-mobile"), " ", phone ? t("web.cal_phone_on", { count: phone }) : t("web.cal_phone_off")),
      feeds.length ? h("ul", { class: "list" }, feeds.map((f) => h("li", {}, icon("calendar"),
        h("div", { class: "grow" }, h("div", {}, f.name), h("small", { class: f.error ? "warn-text" : "muted" }, f.error ? t(f.error) : t("web.cal_feed_state", { host: f.host, count: f.events }))),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await attempt(() => api(`/api/calendars/${f.id}`, { method: "DELETE" }), t("web.deleted"))) draw();
        } }, icon("trash"))))) : null,
      feeds.length ? h("button", { onclick: async () => { await attempt(() => api("/api/calendars/refresh", { method: "POST", body: {} }), t("web.saved")); draw(); } }, icon("refresh"), t("web.cal_refresh")) : null,
      ["google", "proton", "other"].map((id) => h("details", { class: "guide" }, h("summary", {}, t(`web.cal_how_${id}`)), h("p", { class: "muted" }, t(`web.cal_how_${id}_text`)))),
      add, h("p", { class: "muted" }, h("small", {}, t("web.cal_privacy"))));
  };
  await draw();
  return panel;
}
