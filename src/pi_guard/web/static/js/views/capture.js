// Pictures and videos taken right here, with the person guided through the views that help most.
// Nothing leaves the page until "Upload"; the server keeps only the face numbers, not the pictures.
import { h, icon, t, toast, fill } from "../api.js";

const STEPS = [["front", 4], ["left", 3], ["right", 3]];
const VIDEO_SECONDS = 8;
const TYPES = [["video/webm;codecs=vp9", "webm"], ["video/webm", "webm"], ["video/mp4", "mp4"]];

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
  const prompt = h("p", { class: "capture-prompt", "aria-live": "polite" });
  const strip = h("div", { class: "capture-strip" });
  const open = async () => {
    stream?.getTracks().forEach((track) => track.stop());
    stream = await navigator.mediaDevices.getUserMedia({ audio: false,
      video: { facingMode: facing, width: { ideal: 1280 }, height: { ideal: 720 } } });
    video.srcObject = stream;
    video.classList.toggle("mirror", facing === "user");
    await video.play().catch(() => {});
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
    if (!recording) prompt.textContent = now ? t(`web.cap_view_${now.view}`, { count: now.left }) : t("web.cap_enough");
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
    h("div", { class: "capture-stage" }, video, h("div", { class: "capture-oval", "aria-hidden": "true" })),
    prompt,
    h("div", { class: "capture-buttons" }, shutter, film),
    strip,
    h("div", { class: "row end" }, h("button", { type: "button", onclick: () => dialog.close() }, t("web.cancel")), finish));
  dialog.addEventListener("close", () => {
    stream?.getTracks().forEach((track) => track.stop());
    taken.forEach((item) => URL.revokeObjectURL(item.url));
    dialog.remove();
  });
  document.body.append(dialog);
  dialog.showModal();
  draw();
}
