// Kid Protect: filter profiles for children's devices, times without internet, pause and extra time.
import { api, attempt, confirmDialog, fill, h, icon, t, toast, when, whileShown } from "../api.js";

const CATEGORY_ICON = { adult: "eye-off", gambling: "ban", malware: "shield-lock", scam: "alert-triangle", drugs: "ban", piracy: "download", social: "users", tiktok: "video" };
const DAYS = [0, 1, 2, 3, 4, 5, 6];
// Templates for times without internet: bedtime on school nights, a weekend night, homework.
const TEMPLATES = {
  bedtime: { from: "21:00", to: "06:30", days: [6, 0, 1, 2, 3] },
  weekend: { from: "22:30", to: "08:00", days: [4, 5] },
  homework: { from: "14:00", to: "16:00", days: [0, 1, 2, 3, 4] },
};

const dayName = (d) => new Date(2024, 0, 1 + d).toLocaleDateString(document.documentElement.lang || "de", { weekday: "short" });

function windowRow(w, rows) {
  const from = h("input", { type: "time", value: w.from || "21:00", "aria-label": t("web.kp_from") });
  const to = h("input", { type: "time", value: w.to || "06:30", "aria-label": t("web.kp_to") });
  const days = DAYS.map((d) => {
    const box = h("input", { type: "checkbox", checked: (w.days || []).includes(d) });
    return [d, box, h("label", { class: "day-chip" }, box, dayName(d))];
  });
  const row = h("div", { class: "window-row" }, h("div", { class: "row" }, from, h("span", {}, "–"), to,
    h("button", { type: "button", class: "icon-only danger", title: t("web.delete"), onclick: () => { rows.splice(rows.indexOf(entry), 1); row.remove(); } }, icon("trash"))),
    h("div", { class: "row wrap days" }, days.map(([, , label]) => label)));
  const entry = { row, value: () => ({ from: from.value, to: to.value, days: days.filter(([, b]) => b.checked).map(([d]) => d) }) };
  rows.push(entry);
  return row;
}

function editor(data, profile, onSave) {
  const name = h("input", { id: "kp_name", value: profile.name || "", maxlength: 40, placeholder: t("web.kp_name_hint") });
  const chosen = new Set(profile.categories || data.defaults);
  const categories = data.categories.map((c) => {
    const box = h("input", { type: "checkbox", checked: chosen.has(c), disabled: !data.filters });
    return [c, box, h("label", { class: "kp-category" }, box, icon(CATEGORY_ICON[c] || "ban"),
      h("span", { class: "grow" }, h("strong", {}, t(`web.kp_cat_${c}`)), h("small", { class: "muted block" }, t(`web.kp_cat_${c}_hint`))))];
  });
  const rows = [];
  const windows = h("div", { class: "stack" }, (profile.offline || []).map((w) => windowRow(w, rows)));
  const dialog = h("dialog", { class: "editor wide" }, h("h2", {}, profile.name || t("web.kp_new")),
    h("div", { class: "stack" },
      h("div", { class: "field" }, h("label", { for: "kp_name" }, t("web.kp_name")), name),
      h("fieldset", { class: "md-section" }, h("legend", {}, t("web.kp_filters")),
        data.filters ? h("p", { class: "muted" }, h("small", {}, t("web.kp_filters_hint"))) : h("p", { class: "notice" }, t("web.kp_no_pihole")),
        h("div", { class: "kp-categories" }, categories.map(([, , label]) => label))),
      h("fieldset", { class: "md-section" }, h("legend", {}, t("web.kp_offline")),
        h("p", { class: "muted" }, h("small", {}, t(data.router ? "web.kp_offline_router" : "web.kp_offline_dns"))),
        windows,
        h("div", { class: "row wrap" }, Object.entries(TEMPLATES).map(([id, w]) =>
          h("button", { type: "button", onclick: () => windows.append(windowRow(w, rows)) }, icon("plus"), t(`web.kp_tpl_${id}`)))))),
    h("div", { class: "row end sticky-actions" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const values = { name: name.value, categories: categories.filter(([, b]) => b.checked).map(([c]) => c), offline: rows.map((r) => r.value()) };
        if (await onSave(values)) dialog.close();
      } }, icon("check"), t("web.save"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  name.focus();
}

function stateOf(p) {
  const now = Date.now();
  if (p.paused_until && Date.parse(p.paused_until) > now) return ["danger", t("web.kp_paused_until", { time: when(p.paused_until) })];
  if (p.extra_until && Date.parse(p.extra_until) > now) return ["ok", t("web.kp_extra_until", { time: when(p.extra_until) })];
  return p.offline_now ? ["danger", t("web.kp_offline_now")] : ["ok", t("web.kp_online_now")];
}

function windowsText(windows) {
  if (!windows.length) return t("web.kp_no_offline");
  return windows.map((w) => `${w.days.map(dayName).join(" ")} ${w.from}–${w.to}`).join(" · ");
}

export async function kidsPanel(box) {
  let tick = 0;  // only the newest drawing keeps the minute refresh going
  const later = (mine) => whileShown(box, () => {
    if (mine !== tick) return;
    if (document.querySelector("dialog[open]") || box.querySelector("details[open]") || box.contains(document.activeElement)) later(mine);
    else draw();
  }, 60000);
  const draw = async () => {
    const mine = ++tick;
    const data = await attempt(() => api("/api/kids"));
    if (!data) { fill(box, h("p", { class: "notice" }, t("web.addon_restart"))); return; }
    const after = (result) => { if (result?.problem) toast(t("web.kp_problem", { problem: result.problem }), true); draw(); };
    const save = async (id, values) => {
      const done = await attempt(() => api(id ? `/api/kids/profiles/${id}` : "/api/kids/profiles", { method: id ? "PUT" : "POST", body: values }), t("web.saved"));
      if (done) after(done);
      return Boolean(done);
    };
    const timed = async (p, what, minutes) => after(await attempt(() => api(`/api/kids/profiles/${p.id}/${what}`, { method: "POST", body: { minutes } }), t("web.saved")));
    const assign = async (mac, name, profile) => after(await attempt(() => api("/api/kids/devices", { method: "PUT", body: { mac, name, profile: profile ? Number(profile) : null } }), t("web.saved")));
    const assigned = new Map(data.profiles.flatMap((p) => p.devices.map((d) => [d.mac, { ...d, profile: p.id }])));
    const network = [...data.network];
    for (const [mac, d] of assigned) if (!network.some((n) => n.mac === mac)) network.push({ mac, ip: "", name: d.name });
    const profileCard = (p) => {
      const [tone, label] = stateOf(p);
      const paused = p.paused_until && Date.parse(p.paused_until) > Date.now();
      const extra = p.extra_until && Date.parse(p.extra_until) > Date.now();
      return h("article", { class: `kp-card ${tone}` },
        h("div", { class: "row between" }, h("h3", {}, icon("mood-happy"), " ", p.name), h("span", { class: `badge ${tone}` }, label)),
        h("div", { class: "row wrap" }, p.categories.length ? p.categories.map((c) => h("span", { class: "badge" }, icon(CATEGORY_ICON[c] || "ban"), " ", t(`web.kp_cat_${c}`)))
          : h("small", { class: "muted" }, t("web.kp_no_filters"))),
        h("p", { class: "muted" }, icon("clock"), " ", windowsText(p.offline)),
        h("p", {}, h("small", { class: "muted" }, t("web.kp_devices_count", { count: p.devices.length })), " ",
          p.devices.map((d) => h("span", { class: "badge accent" }, d.name || d.mac))),
        h("div", { class: "row wrap kp-actions" },
          paused ? h("button", { onclick: () => timed(p, "pause", 0) }, icon("player-play"), t("web.kp_resume"))
            : h("details", { class: "kp-menu" }, h("summary", { class: "button" }, icon("player-stop"), t("web.kp_pause")),
              h("div", { class: "row wrap" }, [15, 30, 60, 120].map((m) => h("button", { onclick: () => timed(p, "pause", m) }, t("web.kp_minutes", { minutes: m }))))),
          p.offline_now || extra ? (extra ? h("button", { onclick: () => timed(p, "extra", 0) }, t("web.kp_end_extra"))
            : h("details", { class: "kp-menu" }, h("summary", { class: "button" }, icon("plus"), t("web.kp_extra")),
              h("div", { class: "row wrap" }, [15, 30, 60].map((m) => h("button", { onclick: () => timed(p, "extra", m) }, `+${t("web.kp_minutes", { minutes: m })}`))))) : null,
          h("span", { class: "grow" }),
          h("button", { class: "icon-only", title: t("web.edit"), onclick: () => editor(data, p, (v) => save(p.id, v)) }, icon("pencil")),
          h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
            if (await confirmDialog(t("web.kp_delete", { name: p.name }), t("web.delete"), true)) after(await attempt(() => api(`/api/kids/profiles/${p.id}`, { method: "DELETE" })));
          } }, icon("trash"))));
    };
    const search = h("input", { type: "search", placeholder: t("web.kp_search"), "aria-label": t("web.kp_search") });
    const rows = network.map((d) => {
      const current = assigned.get(d.mac)?.profile || "";
      const pick = h("select", { "aria-label": t("web.kp_profile_for", { name: d.name || d.mac }) }, h("option", { value: "" }, t("web.kp_unfiltered")),
        data.profiles.map((p) => h("option", { value: p.id, selected: p.id === current }, p.name)));
      pick.addEventListener("change", () => assign(d.mac, d.name, pick.value));
      return h("tr", { dataset: { text: `${d.name} ${d.ip} ${d.mac}`.toLowerCase() } },
        h("td", {}, d.name || "–", current ? [" ", h("span", { class: "badge accent" }, t("web.kp_filtered"))] : null),
        h("td", { class: "mono" }, d.ip || "–"), h("td", { class: "mono" }, d.mac), h("td", {}, pick));
    });
    search.addEventListener("input", () => rows.forEach((r) => { r.hidden = !r.dataset.text.includes(search.value.toLowerCase()); }));
    fill(box,
      h("section", { class: "panel stack" },
        h("div", { class: "row between" }, h("h2", {}, icon("mood-happy"), " ", t("web.kp_profiles")),
          h("button", { class: "primary", onclick: () => editor(data, { offline: [TEMPLATES.bedtime] }, (v) => save(null, v)) }, icon("plus"), t("web.kp_add"))),
        h("p", { class: "muted" }, t("web.kp_intro")),
        h("div", { class: "row wrap" },
          h("span", { class: `badge ${data.filters ? "ok" : ""}` }, icon("brand-pihole"), " ", t(data.filters ? "web.kp_has_pihole" : "web.kp_no_pihole_short")),
          h("span", { class: `badge ${data.router ? "ok" : ""}` }, icon("router"), " ", t(data.router ? "web.kp_has_router" : "web.kp_no_router"))),
        data.profiles.length ? h("div", { class: "kp-grid" }, data.profiles.map(profileCard)) : h("p", { class: "empty" }, t("web.kp_none"))),
      h("section", { class: "panel stack" }, h("h2", {}, icon("router"), " ", t("web.kp_devices")), h("p", { class: "muted" }, t("web.kp_devices_hint")),
        search, network.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["web.device_name", "web.address", "MAC", "web.kp_profile"].map((k) => h("th", {}, k.startsWith("web.") ? t(k) : k)))), h("tbody", {}, rows)))
          : h("p", { class: "notice" }, t("web.kp_no_network"))),
      h("p", { class: "muted" }, h("small", {}, t("web.kp_limits"))));
    later(mine);
  };
  await draw();
}
