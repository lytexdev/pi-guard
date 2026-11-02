// Live: picture, sound, push-to-talk, image tools, sounds and alarm.
import { api, attempt, confirmDialog, h, icon, t, toast, fill } from "../api.js";

const REGIONS = ["top_left", "top", "top_right", "left", "center", "right", "bottom_left", "bottom", "bottom_right"];
const ARROWS = ["↖", "↑", "↗", "←", "·", "→", "↙", "↓", "↘"];

function pannable(stage, img) {
  // Pinch, wheel and drag zoom on the live picture; purely client-side, costs the Pi nothing.
  let scale = 1, x = 0, y = 0;
  const pointers = new Map();
  let pinch = null;
  const apply = () => {
    const w = stage.clientWidth, hgt = stage.clientHeight;
    x = Math.min(0, Math.max(w - w * scale, x));
    y = Math.min(0, Math.max(hgt - hgt * scale, y));
    img.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
  };
  const zoomAt = (factor, cx, cy) => {
    const next = Math.min(6, Math.max(1, scale * factor));
    x = cx - (cx - x) * (next / scale);
    y = cy - (cy - y) * (next / scale);
    scale = next;
    apply();
  };
  stage.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = stage.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  stage.addEventListener("pointerdown", (e) => { pointers.set(e.pointerId, e); stage.setPointerCapture(e.pointerId); });
  stage.addEventListener("pointermove", (e) => {
    const prev = pointers.get(e.pointerId);
    if (!prev) return;
    pointers.set(e.pointerId, e);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
      const r = stage.getBoundingClientRect();
      if (pinch) zoomAt(dist / pinch, (a.clientX + b.clientX) / 2 - r.left, (a.clientY + b.clientY) / 2 - r.top);
      pinch = dist;
    } else if (scale > 1) {
      x += e.clientX - prev.clientX; y += e.clientY - prev.clientY; apply();
    }
  });
  const up = (e) => { pointers.delete(e.pointerId); if (pointers.size < 2) pinch = null; };
  stage.addEventListener("pointerup", up);
  stage.addEventListener("pointercancel", up);
  stage.addEventListener("dblclick", () => { scale = 1; x = 0; y = 0; apply(); });
  return {
    zoom: (f) => zoomAt(f, stage.clientWidth / 2, stage.clientHeight / 2),
    reset: () => { scale = 1; x = 0; y = 0; apply(); },
  };
}

function resultPanel() {
  const panel = h("section", { class: "panel result hidden" });
  const show = (data, caption) => {
    panel.classList.remove("hidden");
    const grid = h("div", { class: "zoomgrid" }, REGIONS.map((region, i) =>
      h("button", { class: "icon-only", title: t("image.zoom_button"), onclick: async () => {
        const next = await attempt(() => api("/api/image/zoom", { method: "POST", body: { source: data.name, region } }));
        if (next) show(next, t("image.zoomed"));
      } }, ARROWS[i])));
    fill(panel,
      h("h2", {}, caption),
      h("a", { href: data.url, target: "_blank", rel: "noopener" }, h("img", { src: data.url, alt: caption })),
      h("div", { class: "row between" },
        h("div", {}, h("h3", {}, t("image.zoom_choose")), grid),
        h("div", { class: "row" },
          h("button", { onclick: async () => {
            const next = await attempt(() => api("/api/image/brighter", { method: "POST", body: { source: data.name } }));
            if (next) show(next, t("image.brightened"));
          } }, icon("sun"), t("image.brighter_button")),
          h("a", { class: "button", href: data.url, download: data.name }, icon("download"), t("web.download")))));
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  return { panel, show };
}

const TALK_LIMIT_SECONDS = 60;

function talkButton(camera) {
  // Tap to start, tap again to send. Holding a button is unreliable on phones: after about
  // a second the browser takes the touch for a long-press and cancels it.
  const label = h("span", {}, t("web.talk_start"));
  const button = h("button", { class: "talk big", type: "button" }, icon("microphone"), label);
  const cancel = h("button", { class: "hidden", type: "button" }, icon("x"), t("web.cancel"));
  const wrap = h("div", { class: "row" }, button, cancel);
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    button.disabled = true;
    button.title = t("web.talk_needs_https");
    return wrap;
  }
  let recorder = null, stream = null, chunks = [], timer = null, started = 0, discard = false;
  const reset = () => {
    clearInterval(timer);
    stream?.getTracks().forEach((track) => track.stop());
    recorder = null; stream = null;
    button.classList.remove("active");
    cancel.classList.add("hidden");
    label.textContent = t("web.talk_start");
  };
  const stop = (send) => {
    if (!recorder || recorder.state !== "recording") return;
    discard = !send;
    recorder.stop();
  };
  button.addEventListener("click", async () => {
    if (recorder) { stop(true); return; }
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
    catch { toast(t("web.mic_denied"), true); return; }
    chunks = []; discard = false;
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
    recorder.onstop = async () => {
      const blob = new Blob(chunks, { type: recorder?.mimeType || "audio/webm" });
      reset();
      if (discard || blob.size < 2000) return;
      button.disabled = true;
      label.textContent = t("web.talk_sending");
      await attempt(() => api("/api/talk", { method: "POST", body: blob }), t("web.talk_sent"));
      button.disabled = false;
      label.textContent = t("web.talk_start");
    };
    recorder.start(250);  // collect data regularly, so nothing is lost at the end
    started = Date.now();
    button.classList.add("active");
    cancel.classList.remove("hidden");
    const tick = () => {
      const seconds = Math.floor((Date.now() - started) / 1000);
      label.textContent = t("web.talk_recording", { seconds });
      if (seconds >= TALK_LIMIT_SECONDS) stop(true);
    };
    tick();
    timer = setInterval(tick, 500);
  });
  cancel.addEventListener("click", () => stop(false));
  return wrap;
}

async function soundsPanel(features) {
  if (!features.audio_playback) return null;
  const panel = h("section", { class: "panel" });
  const draw = async () => {
    const s = await api("/api/sounds");
    const volume = h("input", { type: "range", min: 0, max: s.max_volume, value: s.volume, id: "volume" });
    volume.addEventListener("change", () => attempt(() => api("/api/volume", { method: "PUT", body: { volume: Number(volume.value) } })));
    const upload = h("form", { class: "row" },
      h("input", { name: "name", placeholder: t("web.sound_name"), pattern: "[a-z0-9_]{1,32}", required: true }),
      h("input", { name: "file", type: "file", accept: "audio/*", required: true }),
      h("button", { type: "submit" }, icon("upload"), t("web.upload")));
    upload.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (await attempt(() => api("/api/sounds", { method: "POST", body: new FormData(upload) }), t("web.saved"))) draw();
    });
    fill(panel,
      h("div", { class: "row between" }, h("h2", {}, t("web.sounds")),
        s.alarm_active
          ? h("button", { class: "danger solid big", onclick: async () => { await attempt(() => api("/api/alarm", { method: "POST", body: { on: false } })); draw(); } }, icon("hand-stop"), t("web.alarm_stop"))
          : h("button", { class: "danger big", onclick: async () => {
              if (await confirmDialog(t("web.alarm_confirm"), t("web.alarm_start"), true)) {
                await attempt(() => api("/api/alarm", { method: "POST", body: { on: true } })); draw();
              } } }, icon("alarm"), t("web.alarm_start"))),
      h("div", { class: "field" }, h("label", { for: "volume" }, t("web.volume")), volume),
      h("ul", { class: "list" }, s.sounds.map((name) => h("li", {},
        h("span", { class: "grow" }, name, s.alarm_sound === name ? h("span", { class: "badge danger" }, t("web.alarm_sound")) : null),
        h("button", { class: "icon-only", title: t("web.play"), onclick: () => attempt(() => api(`/api/sounds/${name}/play`, { method: "POST" })) }, icon("player-play")),
        h("button", { class: "icon-only", title: t("web.use_as_alarm"), onclick: async () => { await attempt(() => api("/api/sounds/alarm", { method: "PUT", body: { name } }), t("web.saved")); draw(); } }, icon("alarm")),
        ["doorbell", "chime", "warning"].includes(name) ? null
          : h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => { await attempt(() => api(`/api/sounds/${name}`, { method: "DELETE" })); draw(); } }, icon("trash"))))),
      h("h3", {}, t("web.sound_upload")), upload);
  };
  await draw();
  return panel;
}

export async function render(main, params) {
  const overview = await api("/api/overview");
  const cameras = overview.cameras;
  if (!cameras.length) { main.append(h("p", { class: "empty" }, t("web.no_cameras"))); return; }
  const camera = cameras.find((c) => c.id === params[0]) || cameras[0];
  const features = overview.features;
  const img = h("img", { src: `/api/live/${camera.id}/video`, alt: camera.name });
  img.addEventListener("error", () => { if (!img.dataset.failed) { img.dataset.failed = "1"; toast(t("web.live_unavailable"), true); } });
  const stage = h("div", { class: "stage" }, img,
    h("div", { class: "overlay" }, h("span", { class: "live" }, "● LIVE"), h("span", {}, camera.name)));
  const pan = pannable(stage, img);
  const result = resultPanel();
  const audio = h("audio", { class: "hidden" });
  const listen = h("button", { class: "big" }, icon("volume"), t("web.listen"));
  listen.disabled = !features.audio_recording;
  let listening = false, retries = 0;
  const connect = () => {
    audio.src = `/api/live/${camera.id}/audio?ts=${Date.now()}`;
    audio.play().catch(() => { if (listening) toast(t("web.live_unavailable"), true); });
  };
  const stopListening = () => {
    listening = false;
    audio.pause(); audio.removeAttribute("src"); audio.load();
    listen.classList.remove("on");
  };
  // A live stream has no end: if the browser stops (network hiccup, buffer ran dry), reconnect.
  const recover = () => {
    if (!listening) return;
    if (retries++ > 5) { stopListening(); toast(t("web.live_unavailable"), true); return; }
    setTimeout(() => { if (listening) connect(); }, 800);
  };
  audio.addEventListener("ended", recover);
  audio.addEventListener("error", recover);
  audio.addEventListener("playing", () => { retries = 0; });
  listen.addEventListener("click", () => {
    if (listening) { stopListening(); return; }
    listening = true; retries = 0;
    listen.classList.add("on");
    connect();
  });
  const photo = async () => {
    const data = await attempt(() => api(`/api/live/${camera.id}/snapshot`, { method: "POST" }));
    if (data) result.show(data, t("web.photo"));
  };
  const sharper = async (button) => {
    button.disabled = true;
    const response = await attempt(() => api("/api/image/sharper", { method: "POST", body: { camera: camera.id }, raw: true }));
    button.disabled = false;
    if (response) result.show(await response.json(), response.headers.get("X-Person") === "1" ? t("web.sharper_person") : t("web.sharper"));
  };
  const sharperButton = h("button", {}, icon("focus-2"), t("image.sharper_button"));
  sharperButton.addEventListener("click", () => sharper(sharperButton));

  fill(main,
    h("div", { class: "topbar" }, h("h1", {}, t("web.nav_live")),
      cameras.length > 1 ? h("div", { class: "segmented" }, cameras.map((c) =>
        h("button", { class: c.id === camera.id ? "on" : "", onclick: () => { location.hash = `#/live/${c.id}`; } }, c.name))) : null),
    h("div", { class: "grid wide" },
      h("section", { class: "panel" }, stage,
        h("div", { class: "controls" },
          h("button", { onclick: () => pan.zoom(1.5) }, icon("zoom-in"), t("web.zoom_in")),
          h("button", { onclick: () => pan.zoom(1 / 1.5) }, icon("zoom-out"), t("web.zoom_out")),
          h("button", { onclick: () => stage.requestFullscreen?.() }, icon("maximize"), t("web.fullscreen")),
          h("button", { onclick: photo }, icon("photo"), t("web.photo")),
          sharperButton)),
      h("section", { class: "panel stack" },
        h("h2", {}, t("web.talk_and_listen")),
        listen, audio,
        features.audio_playback ? talkButton(camera) : h("p", { class: "muted" }, t("web.no_speaker")),
        h("p", { class: "muted" }, h("small", {}, t("web.listen_note"))))),
    result.panel);
  const sounds = await soundsPanel(features);
  if (sounds) main.append(sounds);
  return () => { listening = false; img.removeAttribute("src"); audio.pause(); audio.removeAttribute("src"); };
}
