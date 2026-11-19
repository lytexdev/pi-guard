// System: is everything healthy, what the device is, and updates of the Pi and the apps.
import { api, fill, h, icon, t, when, whileShown } from "../api.js";
import { state } from "../app.js";

const TABS = [["health", "circle-check"], ["device", "cpu"], ["updates", "refresh"]];
const LEVEL = { ok: ["ok", "circle-check"], warn: ["warn", "alert-triangle"], fail: ["danger", "x"], skip: ["", "circle"] };
const ORDER = { fail: 0, warn: 1, ok: 2, skip: 3 };

// Members see updates when they may manage apps; health and hardware stay with admins.
export function systemTabs(session = state.session) {
  if (session?.role === "admin") return TABS;
  const granted = session?.permissions || [];
  return TABS.filter(([id]) => id === "updates" && ["apps", "settings:updates", "settings:apps"].some((p) => granted.includes(p)));
}

const duration = (seconds) => {
  if (seconds === null || seconds === undefined) return "–";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return days ? t("web.sy_days", { days, hours }) : hours ? t("web.sy_hours", { hours, minutes }) : t("web.sy_minutes", { minutes });
};

// One check in words: the title by its id, the detail from its numbers.
function describe(check) {
  const p = check.params || {};
  switch (check.id) {
    case "service": return [t("web.sy_c_service"), [t("web.sy_c_service_up", { time: duration(p.uptime) }),
      p.failing?.length ? t("web.sy_c_service_failing", { names: p.failing.join(", ") }) : null,
      p.restarts ? t("web.sy_c_restarts", { count: p.restarts }) : null].filter(Boolean).join(" · ")];
    case "camera": return [p.name ? t("web.sy_c_camera", { name: p.name }) : t("web.sy_c_camera_none"),
      p.state === "none" ? t("web.sy_c_camera_none_hint") : [t(`web.sy_cam_${p.state}`), p.age !== null && p.age !== undefined ? t("web.sy_c_frame", { seconds: p.age }) : null,
        p.recording ? t("web.sy_c_recording") : null].filter(Boolean).join(" · ")];
    case "storage": return [t("web.sy_c_storage"), t("web.sy_c_storage_detail", { used: p.used, free: p.free_gb })];
    case "memory": return [t("web.sy_c_memory"), t("web.sy_c_memory_detail", { free: p.available_mb, total: p.total_mb })];
    case "cpu": return [t("web.sy_c_cpu"), t("web.sy_c_cpu_detail", { load: p.load, cores: p.cores })];
    case "temperature": return [t("web.sy_c_temperature"), `${p.celsius} °C${check.level !== "ok" ? ` · ${t("web.sy_c_hot")}` : ""}`];
    case "power": return [t("web.sy_c_power"), p.now?.length ? t("web.sy_c_power_now", { what: p.now.map((w) => t(`web.sy_pw_${w}`)).join(", ") })
      : p.since_boot?.length ? t("web.sy_c_power_before", { what: p.since_boot.map((w) => t(`web.sy_pw_${w}`)).join(", ") }) : t("web.sy_c_power_ok")];
    case "database": return [t("web.sy_c_database"), p.result === "ok" ? t("web.sy_c_database_ok") : String(p.result || p.error || "")];
    case "messenger": return [t("web.sy_c_messenger", { name: p.name[0].toUpperCase() + p.name.slice(1) }), check.level === "ok" ? t("web.sy_c_connected") : t("web.sy_c_disconnected")];
    case "models": return [t("web.sy_c_models"), p.missing?.length ? t("web.sy_c_models_missing", { names: p.missing.join(", ") }) : t("web.sy_c_models_ok")];
    case "backup": return [t("web.sy_c_backup"), check.level === "skip" ? t("web.sy_c_backup_off") : !p.time ? t("web.bk_never")
      : `${p.ok ? t("web.bk_ok") : t("web.bk_failed")} · ${when(p.time)}`];
    case "clock": return [t("web.sy_c_clock"), p.synced ? t("web.sy_c_clock_ok") : t("web.sy_c_clock_not")];
    default: return [check.id, p.error || ""];
  }
}

// Where a problem is fixed.
const FIX = { camera: "#/settings/devices", storage: "#/settings/data/storage", backup: "#/settings/data/backup", messenger: "#/settings/messaging", service: "#/settings/logs" };

async function healthTab(box) {
  const draw = async () => {
    const data = await api("/api/system/health");
    const checks = [...data.checks].sort((a, b) => ORDER[a.level] - ORDER[b.level]);
    const bad = checks.filter((c) => c.level === "fail").length;
    const warn = checks.filter((c) => c.level === "warn").length;
    const tone = bad ? "danger" : warn ? "warn" : "ok";
    fill(box,
      h("section", { class: `panel health-summary ${tone}` }, h("span", { class: `health-dot ${tone}` }, icon(LEVEL[bad ? "fail" : warn ? "warn" : "ok"][1])),
        h("div", { class: "grow" }, h("h2", {}, bad ? t("web.sy_bad", { count: bad }) : warn ? t("web.sy_warn", { count: warn }) : t("web.sy_ok")),
          h("small", { class: "muted" }, t("web.sy_checked", { time: when(new Date(data.time * 1000).toISOString()) })))),
      h("section", { class: "panel" }, h("ul", { class: "list health-list" }, checks.map((c) => {
        const [title, detail] = describe(c);
        const [cls, ic] = LEVEL[c.level] || LEVEL.skip;
        return h("li", {}, h("span", { class: `dot ${cls}`, role: "img", "aria-label": t(`web.sy_level_${c.level}`) }), icon(ic),
          h("div", { class: "grow" }, h("div", {}, title), h("small", { class: "muted" }, detail)),
          c.level !== "ok" && c.level !== "skip" && FIX[c.id] ? h("a", { class: "button small", href: FIX[c.id] }, t("web.sy_fix")) : null);
      }))),
      h("p", { class: "muted" }, h("small", {}, t("web.sy_doctor_hint"))));
    whileShown(box, draw, 30000);
  };
  await draw();
}

function kv(rows) {
  return h("dl", { class: "kv" }, rows.filter(([, v]) => v !== null && v !== undefined && v !== "").map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

async function deviceTab(box) {
  const { hardware: hw } = await api("/api/system/health");
  const gb = (mb) => `${(mb / 1024).toFixed(1)} GB`;
  fill(box,
    h("section", { class: "panel stack" }, h("h2", {}, icon("brand-raspberrypi"), " ", t("web.sy_device")),
      kv([[t("web.sy_model"), hw.model], [t("web.sy_hostname"), hw.hostname], [t("web.sy_cpu"), `${hw.cpu || hw.arch} · ${t("web.sy_cores", { count: hw.cores })}`],
        [t("web.sy_memory"), hw.memory_mb ? `${gb(hw.memory_mb)}${hw.swap_mb ? ` · ${t("web.sy_swap", { size: gb(hw.swap_mb) })}` : ""}` : null],
        [t("web.sy_os"), hw.os], [t("web.sy_kernel"), `${hw.kernel} (${hw.arch})`], [t("web.sy_boot_uptime"), duration(hw.boot_uptime)],
        [t("web.sy_version"), `pi-guard ${hw.version} · Python ${hw.python}`]])),
    h("section", { class: "panel stack" }, h("h2", {}, icon("database"), " ", t("web.sy_disks")),
      h("ul", { class: "list compact" }, hw.disks.map((d) => {
        const used = d.total_gb ? Math.round(100 * (1 - d.free_gb / d.total_gb)) : 0;
        return h("li", {}, h("div", { class: "grow stack tight" }, h("div", { class: "row" }, h("span", { class: "grow" }, t(`web.sy_disk_${d.label}`), " ", h("small", { class: "muted mono" }, d.path)),
          h("small", {}, t("web.sy_disk_free", { free: d.free_gb, total: d.total_gb }))),
        h("div", { class: `meter ${used > 90 ? "danger" : used > 80 ? "warn" : ""}`, role: "meter", "aria-valuenow": used, "aria-valuemin": 0, "aria-valuemax": 100, "aria-label": t(`web.sy_disk_${d.label}`) }, h("i", { style: `width: ${used}%` })),
        h("small", { class: "muted mono" }, [d.device, d.type, d.mount].filter(Boolean).join(" · "))));
      }))),
    h("section", { class: "panel stack" }, h("h2", {}, icon("camera"), " ", t("web.sy_cameras")),
      hw.cameras.length ? h("ul", { class: "list compact" }, hw.cameras.map((c) => h("li", {}, icon("camera"), h("div", { class: "grow" }, h("div", {}, c.name),
        h("small", { class: "muted mono" }, [c.source, c.resolution, c.fps ? `${c.fps} fps` : null, c.input, c.encoder].filter(Boolean).join(" · "))))))
        : h("p", { class: "muted" }, t("web.sy_c_camera_none")),
      kv([[t("web.speaker"), hw.audio.speaker || "–"], [t("web.microphone"), hw.audio.microphone || "–"]])),
    hw.usb.length ? h("section", { class: "panel stack" }, h("h2", {}, icon("plug-connected"), " ", t("web.sy_usb")),
      h("ul", { class: "list compact" }, hw.usb.map((u) => h("li", {}, icon("plug-connected"), h("div", { class: "grow" }, h("div", {}, u.name),
        h("small", { class: "muted" }, [u.maker, u.speed ? `${u.speed} Mbit/s` : null].filter(Boolean).join(" · "))))))) : null,
    hw.addresses.length ? h("section", { class: "panel stack" }, h("h2", {}, icon("network"), " ", t("web.sy_network")),
      kv(hw.addresses.map((a) => [a.interface, h("span", { class: "mono" }, a.address)]))) : null);
}

export async function render(main, params = []) {
  const tabs = systemTabs();
  if (!tabs.length) { location.hash = "#/overview"; return null; }
  const tab = tabs.some(([id]) => id === params[0]) ? params[0] : tabs[0][0];
  const body = h("div", { class: "stack" });
  fill(main, h("div", { class: "topbar" }, h("div", { class: "title-row" }, h("h1", {}, t("web.nav_system_page")))),
    h("p", { class: "muted" }, t("web.sy_intro")),
    tabs.length > 1 ? h("nav", { class: "tabs", "aria-label": t("web.nav_system_page") }, tabs.map(([id, ic]) =>
      h("a", { href: `#/system/${id}`, class: id === tab ? "active" : "", "aria-current": id === tab ? "page" : null }, icon(ic), " ", t(`web.sy_tab_${id}`)))) : null,
    body);
  if (tab === "health") await healthTab(body);
  else if (tab === "device") await deviceTab(body);
  else {
    const hub = await import("./settings-hub.js");
    await hub.updatesPage(body);
    if (params[1]) hub.jumpTo(params[1]);
  }
  return null;
}
