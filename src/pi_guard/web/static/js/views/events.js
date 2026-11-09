// Events with filters, plus statistics drawn as plain SVG.
import { api, h, svg, t, when, fill } from "../api.js";
import { eventRow } from "./overview.js";
import { state } from "../app.js";

const TYPES = ["motion_detected", "person_detected", "unknown_person", "known_person",
  "camera_disconnected", "recording_failed", "storage_low", "arrival_detected", "mode_changed"];

function barChart(values, labels, alt = false) {
  const width = 600, height = 180, pad = 22;
  const max = Math.max(1, ...values);
  const step = (width - pad) / Math.max(values.length, 1);
  const chart = svg("svg", { class: "chart", viewBox: `0 0 ${width} ${height}`, role: "img" });
  chart.append(svg("line", { x1: pad, x2: width, y1: height - pad, y2: height - pad }));
  values.forEach((value, i) => {
    const barH = (value / max) * (height - pad * 2);
    const rect = svg("rect", { class: `bar${alt ? " alt" : ""}`, x: pad + i * step + 2, y: height - pad - barH,
      width: Math.max(step - 4, 2), height: barH, rx: 2 });
    const title = svg("title");
    title.textContent = `${labels[i]}: ${value}`;
    rect.append(title);
    chart.append(rect);
    if (values.length <= 24 || i % 2 === 0) {
      const text = svg("text", { x: pad + i * step + step / 2, y: height - 6, "text-anchor": "middle" });
      text.textContent = labels[i];
      chart.append(text);
    }
  });
  const top = svg("text", { x: 2, y: pad }); top.textContent = String(max); chart.append(top);
  return chart;
}

async function evidencePanel() {
  const data = await api("/api/evidence").catch(() => null);
  if (!data || (!data.files.length && !data.running.length)) return null;
  return h("section", { class: "panel" }, h("h2", {}, t("web.ev_title")),
    data.running.length ? h("p", { class: "notice" }, t("web.ev_recording")) : null,
    h("ul", { class: "list" }, data.files.map((f) => h("li", {},
      h("div", { class: "grow clip" }, h("div", {}, `${f.camera} · ${when(f.start_utc)}`),
        h("small", { class: "mono" }, `SHA-256 ${f.sha256.slice(0, 24)}… · ${Math.round(f.duration_seconds / 60)} min · ${(f.size / 1e6).toFixed(1)} MB`)),
      h("a", { class: "button", href: `/api/media/evidence/${encodeURIComponent(f.file)}`, download: "" }, "MP4"),
      h("a", { class: "button", href: `/api/media/evidence/${encodeURIComponent(f.metadata)}`, download: "" }, "JSON"),
      f.report ? h("a", { class: "button", href: `/api/media/evidence/${encodeURIComponent(f.report)}`, download: "", title: t("web.ev_report_tip") }, icon("file-text"), t("web.ev_report")) : null))));
}

export async function render(main) {
  const [overview, stats, evidence, first] = await Promise.all([api("/api/overview"), api("/api/stats?days=14"),
    evidencePanel(), api("/api/events?limit=30")]);
  const cameras = Object.fromEntries(overview.cameras.map((c) => [c.id, c.name]));
  const learn = state.session.role !== "viewer" && overview.features.faces;
  const typeSel = h("select", { id: "type" }, h("option", { value: "" }, t("web.all_types")),
    TYPES.map((type) => h("option", { value: type }, t(`event.${type}`))));
  const camSel = h("select", { id: "camera" }, h("option", { value: "" }, t("web.all_cameras")),
    overview.cameras.map((c) => h("option", { value: c.id }, c.name)));
  const list = h("ul", { class: "list" });
  const more = h("button", {}, t("web.load_more"));
  let before = "";
  const show = (events, reset) => {
    if (reset && !events.length) list.append(h("li", { class: "empty" }, t("web.nothing")));
    events.forEach((e) => list.append(eventRow(e, cameras, learn)));
    before = events.length ? events[events.length - 1].ts : before;
    more.classList.toggle("hidden", events.length < 30);
  };
  const load = async (reset) => {
    if (reset) { list.replaceChildren(); before = ""; }
    const query = new URLSearchParams({ limit: 30, type: typeSel.value, camera: camSel.value, before });
    show(await api(`/api/events?${query}`), reset);
  };
  typeSel.addEventListener("change", () => load(true));
  camSel.addEventListener("change", () => load(true));
  more.addEventListener("click", () => load(false));

  const days = [...Array(14)].map((_, i) => {
    const d = new Date(Date.now() - (13 - i) * 86400000);
    return d.toISOString().slice(0, 10);
  });
  const motion = days.map((d) => stats.daily.filter((r) => r.day === d && r.type === "motion_detected").reduce((a, r) => a + r.n, 0));
  const people = days.map((d) => stats.daily.filter((r) => r.day === d && ["person_detected", "unknown_person"].includes(r.type)).reduce((a, r) => a + r.n, 0));
  const offset = -new Date().getTimezoneOffset() / 60;
  const hours = [...Array(24)].map((_, local) => stats.motion_by_hour_utc[((local - offset) % 24 + 24) % 24] || 0);

  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_events"))),
    evidence,
    h("div", { class: "grid wide" },
      h("section", { class: "panel" }, h("h2", {}, t("web.motion_per_day")), barChart(motion, days.map((d) => d.slice(8)))),
      h("section", { class: "panel" }, h("h2", {}, t("web.people_per_day")), barChart(people, days.map((d) => d.slice(8)), true)),
      h("section", { class: "panel" }, h("h2", {}, t("web.motion_by_hour")), barChart(hours, hours.map((_, i) => String(i))))),
    h("section", { class: "panel" },
      h("div", { class: "row" }, h("div", { class: "field grow" }, h("label", { for: "type" }, t("web.type")), typeSel),
        h("div", { class: "field grow" }, h("label", { for: "camera" }, t("web.camera")), camSel)),
      list, more));
  show(first, true);
}
