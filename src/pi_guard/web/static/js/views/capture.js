// Pictures and videos taken right here, with the person guided through the views that help most.
// Nothing leaves the page until "Upload"; the server keeps only the face numbers, not the pictures.
import { h, icon, svg, t, toast, fill } from "../api.js";

const STEPS = [["front", 4], ["left", 3], ["right", 3]];
const VIDEO_SECONDS = 8;
const TYPES = [["video/webm;codecs=vp9", "webm"], ["video/webm", "webm"], ["video/mp4", "mp4"]];

// A head that shows where to look: nose and eyes move to the side the person should turn to.
// The picture is mirrored like the preview, so "left" looks the way the person sees it.
function head(view) {
  const turn = view === "left" ? -9 : view === "right" ? 9 : 0;
  const box = svg("svg", { class: "capture-head", viewBox: "0 0 64 64", "aria-hidden": "true" });
  box.append(
    svg("ellipse", { cx: 32, cy: 32, rx: 20, ry: 25, class: "face" }),
    svg("circle", { cx: 24 + turn, cy: 28, r: 2.6, class: "feature" }),
    svg("circle", { cx: 40 + turn, cy: 28, r: 2.6, class: "feature" }),
    svg("path", { d: `M${32 + turn * 1.4} 31 l${turn ? Math.sign(turn) * 4 : 0} 8 h${turn ? 0 : -3}`, class: "line" }),
    svg("path", { d: `M${25 + turn} 46 q${7} 4 ${14} 0`, class: "line" }));
  if (turn) {
    const x = turn < 0 ? 6 : 58, dx = turn < 0 ? 6 : -6;
    box.append(svg("path", { d: `M${x + dx} 32 h${-dx} m${dx / 2} -4 l${-dx / 2} 4 l${dx / 2} 4`, class: "arrow" }));
  }
  return box;
}

// Without HTTPS or with the camera refused, the phone's own camera app still works.
function fallback(onDone, reason) {
  const pick = (accept, label, iconName) => {
    const input = h("input", { class: "hidden", type: "file", accept, capture: "user" });
    input.addEventListener("change", () => { if (input.files.length) { dialog.close(); onDone([...input.files]); } });
    return [input, h("button", { type: "button", class: "big", onclick: () => input.click() }, icon(iconName), t(label))];
  };
  const dialog = h("dialog", { class: "capture" }, h("h2", {}, t("web.cap_title")), h("p", { class: "notice" }, t(reason)),
    h("div", { class: "stack" }, pick("image/*", "web.cap_photo", "camera"), pick("video/*", "web.cap_video", "video")),
    h("div", { class: "row end" }, h("button", { type: "button", onclick: () => dialog.close() }, t("web.cancel"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

export async function captureDialog(person, onDone) {
  if (!navigator.mediaDevices?.getUserMedia) return fallback(onDone, "web.cap_no_camera");
  let facing = "user";
  let stream = null;
  let taken = [];
  let recording = false;
  const video = h("video", { autoplay: true, muted: true, playsinline: true });
  const stage = h("div", { class: "capture-stage" });
  const guide = h("div", { class: "capture-guide" });
  const prompt = h("p", { class: "capture-prompt", "aria-live": "polite" });
  const strip = h("div", { class: "capture-strip" });
  const open = async () => {
    stream?.getTracks().forEach((track) => track.stop());
    stream = await navigator.mediaDevices.getUserMedia({ audio: false,
      video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } } });
    video.srcObject = stream;
    video.classList.toggle("mirror", facing === "user");
    // Phones zoom some front cameras in; the widest view makes the face fit the oval.
    const [track] = stream.getVideoTracks();
    const zoom = track?.getCapabilities?.().zoom;
    if (zoom?.min != null) await track.applyConstraints({ advanced: [{ zoom: zoom.min }] }).catch(() => {});
    await video.play().catch(() => {});
    // The stage takes the camera's own shape, upright on a phone, so nothing is cut off.
    if (video.videoWidth && video.videoHeight) stage.style.aspectRatio = `${video.videoWidth} / ${video.videoHeight}`;
  };
  try { await open(); } catch { return fallback(onDone, "web.cap_refused"); }

  const photos = () => taken.filter((item) => !item.video).length;
  const step = () => {
    let count = photos();
    for (const [view, wanted] of STEPS) {
      if (count < wanted) return { view, left: wanted - count };
      count -= wanted;
    }
    return null;
  };
  const draw = () => {
    const now = step();
    if (!recording) {
      prompt.textContent = now ? t(`web.cap_view_${now.view}`, { count: now.left }) : t("web.cap_enough");
      fill(guide, now ? head(now.view) : icon("circle-check", "lg"));
    }
    fill(strip, taken.map((item) => h("button", { type: "button", class: "capture-thumb", title: t("web.delete"),
      onclick: () => { URL.revokeObjectURL(item.url); taken = taken.filter((x) => x !== item); draw(); } },
    item.video ? icon("video", "lg") : h("img", { src: item.url, alt: "" }))));
    finish.disabled = !taken.length || recording;
    fill(finish, icon("upload"), t("web.cap_upload", { count: taken.length }));
  };
  const add = (file, isVideo) => { taken.push({ file, video: isVideo, url: URL.createObjectURL(file) }); draw(); };

  const shutter = h("button", { type: "button", class: "primary big", onclick: () => {
    const canvas = h("canvas", { width: video.videoWidth, height: video.videoHeight });
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob((blob) => add(new File([blob], `live-${taken.length + 1}.jpg`, { type: "image/jpeg" }), false), "image/jpeg", 0.92);
  } }, icon("camera"), t("web.cap_photo"));

  // About eight seconds, turning slowly: one video gives many views at once.
  const film = h("button", { type: "button", class: "big", onclick: () => {
    const [mime, suffix] = TYPES.find(([type]) => window.MediaRecorder?.isTypeSupported(type)) || [];
    if (!mime) { toast(t("web.cap_no_video"), true); return; }
    const parts = [];
    const recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 2500000 });
    recorder.ondataavailable = (event) => parts.push(event.data);
    recorder.onstop = () => {
      recording = false; shutter.disabled = film.disabled = false;
      add(new File(parts, `live-${taken.length + 1}.${suffix}`, { type: mime }), true);
    };
    recording = true; shutter.disabled = film.disabled = true; finish.disabled = true;
    recorder.start();
    let second = 0;
    const tick = () => {
      if (dialog.open === false || recorder.state !== "recording") return;
      const view = second < 3 ? "front" : second < 6 ? "left" : "right";
      fill(guide, head(view));
      prompt.textContent = t("web.cap_filming", { seconds: VIDEO_SECONDS - second, view: t(`web.cap_turn_${view}`) });
      if (second++ >= VIDEO_SECONDS) recorder.stop(); else setTimeout(tick, 1000);
    };
    tick();
  } }, icon("video"), t("web.cap_video"));

  const flip = h("button", { type: "button", class: "icon-only", title: t("web.cap_flip"), onclick: async () => {
    facing = facing === "user" ? "environment" : "user";
    try { await open(); } catch { toast(t("web.cap_refused"), true); }
  } }, icon("refresh"));
  const finish = h("button", { type: "button", class: "primary", disabled: true, onclick: () => {
    const files = taken.map((item) => item.file);
    dialog.close();
    onDone(files);
  } });
  const dialog = h("dialog", { class: "capture" },
    h("div", { class: "row between" }, h("h2", {}, person), flip),
    h("p", { class: "muted capture-intro" }, t("web.cap_intro")),
    stage,
    h("div", { class: "capture-hint" }, guide, prompt),
    h("div", { class: "capture-buttons" }, shutter, film),
    strip,
    h("div", { class: "row end" }, h("button", { type: "button", onclick: () => dialog.close() }, t("web.cancel")), finish));
  stage.append(video, h("div", { class: "capture-oval", "aria-hidden": "true" }));
  dialog.addEventListener("close", () => {
    stream?.getTracks().forEach((track) => track.stop());
    taken.forEach((item) => URL.revokeObjectURL(item.url));
    dialog.remove();
  });
  document.body.append(dialog);
  dialog.showModal();
  draw();
}
