// Cameras, microphones and speakers as selection fields, written to dashboard.yaml.
import { api, attempt, h, icon, t, fill } from "../api.js";
import { restartPrompt } from "./settings.js";

const NEW_CAMERA = { source: "usb", room: "", enabled: true, resolution: "1280x720", fps: 15, input_format: "auto", codec: "auto", bitrate: "2M",
  recording: true, retention_days: null, motion_detection: true, motion: { sensitivity: null, min_area_percent: 0.4, min_duration_seconds: 1,
    analysis_fps: 4, analysis_width: 320, zones: [], schedule: [] }, snapshot_interval_seconds: 1, microphone: null, speaker: null };
const field = (label, el) => h("div", { class: "field" }, h("label", { for: el.id }, label), el);
let uid = 0;
const id = () => `d${++uid}`;

function select(options, value, allowNone) {
  return h("select", { id: id() }, allowNone ? h("option", { value: "" }, t("web.none")) : null,
    options.map(([v, label]) => h("option", { value: v, selected: v === value }, label)),
    value && !options.some(([v]) => v === value) ? h("option", { value, selected: true }, `${value} (${t("web.not_found")})`) : null);
}

function speakerTest(chosen) {
  const out = h("small", { class: "muted", "aria-live": "polite" });
  const button = h("button", { type: "button", title: t("web.dev_test_speaker_tip"), onclick: async (event) => {
    event.stopPropagation();  // not a change to the settings
    if (!chosen.value) { out.textContent = t("web.dev_test_choose"); return; }
    button.disabled = true;
    out.textContent = t("web.dev_test_playing");
    const done = await attempt(() => api("/api/devices/test/speaker", { method: "POST", body: { device: chosen.value } }));
    button.disabled = false;
    out.textContent = done ? t("web.dev_test_heard") : "";
  } }, icon("volume"), t("web.dev_test_speaker"));
  return h("div", { class: "device-test" }, button, out);
}

function microphoneTest(chosen) {
  const out = h("div", { class: "stack tight", "aria-live": "polite" });
  const button = h("button", { type: "button", title: t("web.dev_test_mic_tip"), onclick: async (event) => {
    event.stopPropagation();
    if (!chosen.value) { fill(out, h("small", { class: "muted" }, t("web.dev_test_choose"))); return; }
    button.disabled = true;
    fill(out, h("small", { class: "muted" }, t("web.dev_test_recording")));
    const response = await attempt(() => api("/api/devices/test/microphone", { method: "POST", body: { device: chosen.value }, raw: true }));
    button.disabled = false;
    if (!response) { fill(out); return; }
    const silent = response.headers.get("X-Silent") === "1";
    const peak = response.headers.get("X-Peak-Db");
    const player = h("audio", { controls: true, src: URL.createObjectURL(await response.blob()) });
    fill(out, h("small", { class: silent ? "warn-text" : "muted" }, t(silent ? "web.dev_test_silent" : "web.dev_test_sound", { peak })), player);
  } }, icon("microphone"), t("web.dev_test_mic"));
  return h("div", { class: "device-test" }, button, out);
}

function cameraEditor(camera, devices, mics, remove) {
  const usb = (camera.source || "usb") === "usb";
  const dev = usb ? select(devices.cameras.map((c) => [c.path, `${c.name} – ${c.path.split("/").pop()}`]), camera.device)
    : h("input", { id: id(), value: camera.device, placeholder: { rtsp: "rtsp://user:password@192.168.178.50:554/stream1", http: "http://192.168.178.50:81/stream", picamera: "0" }[camera.source] });
  const sizes = () => {
    const found = devices.cameras.find((c) => c.path === dev.value);
    return [...new Set((found?.formats || []).flatMap((f) => f.sizes))];
  };
  const res = usb ? select(sizes().map((s) => [s, s]), camera.resolution) : h("input", { id: id(), value: camera.resolution, pattern: "[0-9]+x[0-9]+" });
  if (usb) dev.addEventListener("change", () => fill(res, ...sizes().map((s) => h("option", { value: s }, s))));
  const room = h("input", { id: id(), value: camera.room || "", maxlength: 40, placeholder: t("web.room_placeholder"), list: "rooms" });
  const name = h("input", { id: id(), value: camera.name, maxlength: 40 });
  const fps = h("input", { id: id(), type: "number", min: 1, max: 60, value: camera.fps });
  const rate = select(["500k", "1M", "1500k", "2M", "3M", "4M", camera.bitrate].filter((v, i, all) => all.indexOf(v) === i).map((v) => [v, t("web.bitrate_value", { rate: v.endsWith("k") ? parseInt(v, 10) / 1000 : parseFloat(v), day: Math.round((v.endsWith("k") ? parseInt(v, 10) / 1000 : parseFloat(v)) * 10.8) })]), camera.bitrate);
  const enabled = h("input", { id: id(), type: "checkbox", checked: camera.enabled });
  const recording = h("input", { id: id(), type: "checkbox", checked: camera.recording });
  const motion = h("input", { id: id(), type: "checkbox", checked: camera.motion_detection });
  const sens = h("input", { id: id(), type: "range", min: 0, max: 100, value: camera.motion.sensitivity ?? 50 });
  const mic = select(mics, camera.microphone, true);
  const panel = h("section", { class: "panel" },
    h("div", { class: "row between" }, h("h2", {}, camera.name || camera.id),
      h("button", { class: "icon-only danger", title: t("web.delete"), onclick: remove }, icon("trash"))),
    field(t("web.name"), name), field(t("web.room"), room),
    h("p", {}, h("span", { class: "badge" }, t(`web.source_${camera.source || "usb"}`))),
    field(usb ? t("web.device") : t("web.source_address"), dev),
    usb ? null : h("div", { class: "hint" }, t(`web.source_${camera.source}_hint`)),
    field(t("web.resolution"), res), field(t("web.fps"), fps), field(t("web.bitrate"), rate),
    h("label", { class: "check" }, enabled, t("web.enabled")),
    h("label", { class: "check" }, recording, t("web.recording")),
    h("label", { class: "check" }, motion, t("web.motion_detection")),
    field(t("web.sensitivity"), sens), field(t("web.microphone_in_video"), mic), microphoneTest(mic));
  panel.read = () => ({
    ...camera, name: name.value, room: room.value.trim(), device: dev.value.trim(), resolution: res.value, fps: Number(fps.value), bitrate: rate.value,
    enabled: enabled.checked, recording: recording.checked, motion_detection: motion.checked,
    motion: { ...camera.motion, sensitivity: Number(sens.value) }, microphone: mic.value || null,
  });
  return panel;
}

export async function render(main, params = [], opts = {}) {
  const [config, devices] = await Promise.all([api("/api/config"), api("/api/devices")]);
  const mics = devices.audio.filter((a) => a.capture).map((a) => [a.device, a.name]);
  const speakers = devices.audio.filter((a) => a.playback).map((a) => [a.device, a.name]);
  const cameraPanels = h("div", { class: "grid wide" });
  const addCamera = (camera) => {
    const panel = cameraEditor(camera, devices, mics, () => panel.remove());
    cameraPanels.append(panel);
  };
  config.cameras.forEach(addCamera);
  const speaker = select(speakers, config.audio.speaker, true);
  const microphone = select(mics, config.audio.microphone, true);
  const quiet = h("input", { id: id(), value: config.audio.quiet_hours || "", placeholder: "22:00-07:00" });
  const notifyQuiet = h("input", { id: id(), value: config.notifications.quiet_hours || "", placeholder: "23:00-06:00" });
  const presence = h("ul", { class: "list" });
  const deviceRow = (d) => {
    const name = h("input", { value: d.name, "aria-label": t("web.name") });
    const mac = h("input", { value: d.mac_address, "aria-label": "MAC", class: "mono" });
    const li = h("li", {}, name, mac, h("button", { class: "icon-only danger", onclick: () => li.remove() }, icon("trash")));
    li.read = () => ({ ...d, name: name.value, mac_address: mac.value, id: d.id || name.value.toLowerCase().replace(/[^a-z0-9]+/g, "_").slice(0, 32) || "device" });
    presence.append(li);
  };
  config.presence.devices.forEach(deviceRow);

  const save = h("button", { class: "primary big", onclick: async () => {
    const body = structuredClone(config);
    body.cameras = [...cameraPanels.children].map((p) => p.read());
    body.audio = { ...body.audio, speaker: speaker.value || null, microphone: microphone.value || null, quiet_hours: quiet.value || null };
    body.notifications = { ...body.notifications, quiet_hours: notifyQuiet.value || null };
    body.presence = { ...body.presence, devices: [...presence.children].map((li) => li.read()) };
    if (await attempt(() => api("/api/config", { method: "PUT", body }), t("web.saved"))) {
      if (opts.onSaved) opts.onSaved(); else restartPrompt();
    }
  } }, icon("check"), t("web.save"));

  opts.register?.(() => save.click());
  fill(main,
    opts.embedded ? null : h("div", { class: "topbar" }, h("h1", {}, t("web.nav_devices")), save),
    h("h3", {}, t("web.cameras")), h("datalist", { id: "rooms" }, [...new Set(config.cameras.map((c) => c.room).filter(Boolean))].map((r) => h("option", { value: r }))), cameraPanels,
    h("div", { class: "row wrap" }, ["rtsp", "http", "picamera"].map((source) => h("button", { onclick: () => addCamera({ ...NEW_CAMERA, id: `${source}_${Date.now() % 100000}`, name: t(`web.source_${source}`), source, device: source === "picamera" ? "0" : "" }) }, icon("plus"), t(`web.source_add_${source}`)))),
    h("div", { class: "row" }, devices.cameras.filter((c) => !config.cameras.some((x) => x.device === c.path)).map((c) =>
      h("button", { onclick: () => addCamera({ ...NEW_CAMERA, id: c.slug, name: c.name, device: c.path }) },
        icon("plus"), `${t("web.add")}: ${c.name}`))),
    h("div", { class: "grid wide" },
      h("section", { class: "panel" }, h("h2", {}, t("web.audio")),
        field(t("web.speaker"), speaker), speakerTest(speaker), field(t("web.microphone"), microphone), microphoneTest(microphone), field(t("web.quiet_hours_sound"), quiet)),
      h("section", { class: "panel" }, h("h2", {}, t("web.notifications")), field(t("web.quiet_hours_alerts"), notifyQuiet)),
      h("section", { class: "panel" }, h("h2", {}, t("web.trusted_devices")), h("p", { class: "muted" }, t("web.trusted_devices_hint")),
        presence, h("button", { onclick: () => deviceRow({ name: "", mac_address: "", enabled: true, ip_address: null }) }, icon("plus"), t("web.add")))));
}
