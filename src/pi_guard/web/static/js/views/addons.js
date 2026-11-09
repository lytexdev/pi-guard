// Add-ons: overview with on/off switches, and one detail page per add-on.
// renderDetail() is also used by the setup assistant.
import { askDialog, api, attempt, confirmDialog, fill, h, icon, t, toast, when, whileShown } from "../api.js";
import { searchToggle } from "../widgets.js";
import { state } from "../app.js";
import { restartPrompt } from "./settings.js";

export const ADDONS = { heartbeat: "world", analytics: "chart-bar", smarthome: "router", remote: "power", assistant: "speakerphone", agents: "device-desktop", languages: "world" };

const slug = (text) => text.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 28) || "item";
const uniqueId = (name, items) => {
  const base = slug(name);
  let id = base;
  for (let n = 2; items.some((i) => i.id === id); n += 1) id = `${base}_${n}`;
  return id;
};
const field = (label, input, hint) => h("div", { class: "field" }, h("label", {}, label, input), hint ? h("div", { class: "hint" }, hint) : null);
const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

async function save(id, section, opts) {
  const done = await attempt(() => api(`/api/addons/${id}`, { method: "PUT", body: section }), t("web.saved"));
  if (done && !opts.embedded) restartPrompt();
  if (done) opts.onSaved?.();
  return Boolean(done);
}

async function status(path) {
  try { return await api(path); } catch { return null; }  // not running until the next restart
}

// --- service watcher ---------------------------------------------------------------------

async function heartbeat(box, data, opts) {
  const section = data.config.heartbeat;
  const live = await status("/api/addons/heartbeat/status");
  const state = Object.fromEntries((live || []).map((r) => [r.id, r]));
  const redraw = () => renderDetail("heartbeat", box, opts);
  const name = h("input", { required: true, maxlength: 60, placeholder: t("web.hb_name_placeholder") });
  const kind = h("select", {}, ["http", "tcp", "ping"].map((k) => h("option", { value: k }, t(`web.hb_kind_${k}`))));
  const address = h("input", { required: true, placeholder: "https://example.org" });
  const interval = h("input", { type: "number", min: 10, max: 86400, value: 60 });
  const failures = h("input", { type: "number", min: 1, max: 20, value: 3 });
  const text = h("input", { placeholder: t("web.hb_text_placeholder") });
  const tls = h("input", { type: "checkbox", checked: true });
  kind.addEventListener("change", () => { address.placeholder = { http: "https://example.org", tcp: "192.168.178.20:22", ping: "192.168.178.1" }[kind.value]; });
  const form = h("form", { class: "stack" },
    field(t("web.name"), name), field(t("web.hb_kind"), kind), field(t("web.hb_address"), address),
    field(t("web.hb_interval"), interval), field(t("web.hb_failures"), failures, t("web.hb_failures_hint")),
    field(t("web.hb_text"), text),
    h("label", { class: "check" }, tls, t("web.hb_tls")),
    h("button", { class: "primary", type: "submit" }, icon("plus"), t("web.add")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const target = { id: uniqueId(name.value, section.targets), name: name.value.trim(), kind: kind.value, address: address.value.trim(),
      interval_seconds: Number(interval.value), failures_before_alert: Number(failures.value), verify_tls: tls.checked, expected_text: text.value.trim() || null };
    if (await save("heartbeat", { ...section, targets: [...section.targets, target] }, opts)) redraw();
  });
  fill(box,
    h("section", { class: "panel" }, h("h2", {}, t("web.hb_targets")),
      h("ul", { class: "list" }, section.targets.length ? section.targets.map((target) => {
        const s = state[target.id];
        const dot = !s || s.up === null ? "" : s.up ? "ok" : "danger";
        return h("li", {}, h("span", { class: `dot ${dot}` }),
          h("div", { class: "grow" }, h("div", {}, target.name),
            h("small", {}, s?.up === false ? `${t("web.hb_down")}: ${s.reason || ""}` : s?.up ? `${s.latency_ms} ms · ${when(s.last_check)}` : t(`web.hb_kind_${target.kind}`))),
          h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
            if (await save("heartbeat", { ...section, targets: section.targets.filter((x) => x.id !== target.id) }, opts)) redraw();
          } }, icon("trash")));
      }) : h("li", { class: "empty" }, t("web.hb_none")))),
    h("section", { class: "panel" }, h("h2", {}, t("web.add")), form));
}

// --- network analysis --------------------------------------------------------------------

function ranked(rows, label, onPick) {
  // Ranked list: one hue, the number always next to the bar.
  const top = Math.max(1, ...rows.map((r) => r.count));
  return h("ul", { class: "ranked" }, rows.map((row) => {
    const bar = h("i");
    bar.style.width = `${(row.count / top) * 100}%`;
    const name = label(row);
    return h("li", {},
      h("div", { class: "row between" }, onPick ? h("a", { href: "#", onclick: (e) => { e.preventDefault(); onPick(row); } }, name) : h("span", {}, name),
        h("span", { class: "mono" }, row.count.toLocaleString())),
      h("div", { class: "rank-bar" }, bar));
  }));
}

function history(points) {
  // Queries over the last 24 hours: one series, so the title names it and no legend is needed.
  const wrap = h("div", { class: "history", role: "img", "aria-label": t("web.an_history") });
  const top = Math.max(1, ...points.map((p) => p.total));
  points.forEach((point) => {
    const bar = h("i", { title: `${new Date(point.time * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}: ${point.total} (${point.blocked} ${t("web.an_blocked_short")})` });
    bar.style.height = `${Math.max(2, (point.total / top) * 100)}%`;
    wrap.append(bar);
  });
  return wrap;
}

async function analytics(box, data, opts) {
  const o = await status("/api/addons/analytics/overview");
  if (!o) { fill(box, h("section", { class: "panel" }, h("p", { class: "muted" }, t("web.an_after_restart")))); return; }
  const detail = h("section", { class: "panel hidden" });
  const pick = async (row) => {
    const d = await attempt(() => api(`/api/addons/analytics/device/${encodeURIComponent(row.ip)}`));
    if (!d) return;
    detail.classList.remove("hidden");
    fill(detail, h("h2", {}, row.name || row.ip),
      h("p", { class: "muted" }, `${row.ip}${row.vendor ? ` · ${row.vendor}` : ""} · ${t("web.an_last_queries", { count: d.queries })}`),
      h("div", { class: "grid wide" },
        h("div", {}, h("h3", {}, t("web.an_top_domains")), ranked(d.top_domains, (r) => r.domain)),
        h("div", {}, h("h3", {}, t("web.an_top_blocked")), ranked(d.top_blocked, (r) => r.domain))));
    detail.scrollIntoView({ behavior: "smooth" });
  };
  fill(box,
    h("div", { class: "grid" },
      h("section", { class: "panel" }, h("h2", {}, t("web.an_queries")), h("div", { class: "stat" }, o.summary.queries.toLocaleString())),
      h("section", { class: "panel" }, h("h2", {}, t("web.an_blocked")), h("div", { class: "stat" }, `${o.summary.percent_blocked} %`)),
      h("section", { class: "panel" }, h("h2", {}, t("web.an_devices")), h("div", { class: "stat" }, o.summary.clients))),
    h("section", { class: "panel" }, h("h2", {}, t("web.an_history")), history(o.history)),
    h("div", { class: "grid wide" },
      h("section", { class: "panel" }, h("h2", {}, t("web.an_by_device")), ranked(o.devices, (r) => r.name || r.ip, pick)),
      h("section", { class: "panel" }, h("h2", {}, t("web.an_top_domains")), ranked(o.top_domains, (r) => r.domain)),
      h("section", { class: "panel" }, h("h2", {}, t("web.an_top_blocked")), ranked(o.top_blocked, (r) => r.domain)),
      h("section", { class: "panel" }, h("h2", {}, t("web.an_upstreams")), ranked(o.upstreams, (r) => r.name),
        h("h3", {}, t("web.an_types")), ranked(Object.entries(o.query_types).map(([k, v]) => ({ type: k, count: v })).sort((a, b) => b.count - a.count), (r) => r.type))),
    detail,
    h("p", { class: "muted" }, h("small", {}, t("web.an_note"))));
}

// --- smart home --------------------------------------------------------------------------

async function smarthome(box, data, opts) {
  const section = data.config.smarthome;
  const live = await status("/api/addons/smarthome/status");
  const redraw = () => renderDetail("smarthome", box, opts);
  const name = h("input", { required: true, maxlength: 60, placeholder: t("web.sh_name_placeholder") });
  const kind = h("select", {}, ["switch", "light", "sensor"].map((k) => h("option", { value: k }, t(`web.sh_kind_${k}`))));
  const state = h("input", { placeholder: "tasmota/stat/plug1/POWER" });
  const command = h("input", { placeholder: "tasmota/cmnd/plug1/POWER" });
  const key = h("input", { placeholder: "temperature" });
  const unit = h("input", { maxlength: 10, placeholder: "°C" });
  const discovery = h("input", { type: "checkbox", checked: section.discovery });
  discovery.addEventListener("change", () => save("smarthome", { ...section, discovery: discovery.checked }, opts));
  const form = h("form", { class: "stack" },
    field(t("web.name"), name), field(t("web.sh_kind"), kind), field(t("web.sh_state_topic"), state), field(t("web.sh_command_topic"), command),
    field(t("web.sh_value_key"), key, t("web.sh_value_key_hint")), field(t("web.sh_unit"), unit),
    h("button", { class: "primary", type: "submit" }, icon("plus"), t("web.add")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const device = { id: uniqueId(name.value, section.devices), name: name.value.trim(), kind: kind.value, state_topic: state.value.trim() || null,
      command_topic: command.value.trim() || null, value_key: key.value.trim() || null, unit: unit.value.trim() };
    if (await save("smarthome", { ...section, devices: [...section.devices, device] }, opts)) redraw();
  });
  const rows = live?.devices || section.devices.map((d) => ({ ...d, on: null, value: null, switchable: false }));
  fill(box,
    live && !live.connected ? h("p", { class: "notice" }, t("smarthome.offline"), " ", h("a", { href: "#/settings" }, t("web.nav_settings"))) : null,
    h("section", { class: "panel" }, h("h2", {}, t("web.sh_devices")),
      h("ul", { class: "list" }, rows.length ? rows.map((d) => h("li", {},
        h("span", { class: `dot ${d.on ? "ok" : ""}` }),
        h("div", { class: "grow" }, h("div", {}, d.name),
          h("small", {}, d.kind === "sensor" ? `${d.value ?? "–"} ${d.unit || ""}` : d.on === null ? t("smarthome.unknown") : t(d.on ? "smarthome.on" : "smarthome.off"),
            d.discovered ? ` · ${t("web.sh_discovered")}` : "")),
        d.switchable ? h("button", { onclick: async () => {
          if (await attempt(() => api(`/api/addons/smarthome/${d.id}`, { method: "POST", body: {} }))) { await sleep(600); redraw(); }
        } }, icon("power"), t("web.sh_toggle")) : null,
        section.devices.some((x) => x.id === d.id) ? h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await save("smarthome", { ...section, devices: section.devices.filter((x) => x.id !== d.id) }, opts)) redraw();
        } }, icon("trash")) : null))
        : h("li", { class: "empty" }, t("web.sh_none")))),
    h("section", { class: "panel" }, h("label", { class: "check" }, discovery, t("web.sh_discovery")),
      h("p", { class: "muted" }, h("small", {}, t("web.sh_discovery_hint")))),
    h("section", { class: "panel" }, h("h2", {}, t("web.add")), form));
}

// --- remote commands ---------------------------------------------------------------------

function typedConfirm(name, host) {
  // Second confirmation: the name has to be typed, so it cannot happen by accident.
  return new Promise((resolve) => {
    const input = h("input", { autocomplete: "off", "aria-label": name });
    const go = h("button", { class: "danger solid", disabled: true, onclick: () => { dialog.close(); resolve(input.value); } }, t("web.rc_run_now"));
    input.addEventListener("input", () => { go.disabled = input.value.trim().toLowerCase() !== name.toLowerCase(); });
    const dialog = h("dialog", {},
      h("p", {}, t("remote.confirm_2", { name, host })),
      h("p", { class: "muted" }, t("web.rc_type_name", { name })), input,
      h("div", { class: "row end" }, h("button", { onclick: () => { dialog.close(); resolve(null); } }, t("web.cancel")), go));
    dialog.addEventListener("close", () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
  });
}

async function runAction(action) {
  const first = await attempt(() => api("/api/addons/remote/begin", { method: "POST", body: { id: action.id } }));
  if (!first) return;
  const started = Date.now();
  if (!await confirmDialog(t("remote.confirm_1", { name: action.name }), t("remote.yes_1"), true)) {
    await api("/api/addons/remote/cancel", { method: "POST", body: { token: first.token } });
    return;
  }
  await sleep(Math.max(0, 1100 - (Date.now() - started)));
  const second = await attempt(() => api("/api/addons/remote/confirm", { method: "POST", body: { token: first.token } }));
  if (!second) return;
  const typed = await typedConfirm(second.name, second.host);
  if (typed === null) {
    await api("/api/addons/remote/cancel", { method: "POST", body: { token: second.token } });
    toast(t("remote.cancelled"));
    return;
  }
  toast(t("remote.running", { name: action.name }));
  const result = await attempt(() => api("/api/addons/remote/confirm", { method: "POST", body: { token: second.token, typed } }));
  if (result) toast(t(result.ok ? "remote.done" : "remote.failed", { name: action.name, output: result.output || "–" }), !result.ok);
}

async function remote(box, data, opts) {
  const section = data.config.remote;
  const live = await status("/api/addons/remote/status");
  const redraw = () => renderDetail("remote", box, opts);
  const name = h("input", { required: true, maxlength: 60, placeholder: t("web.rc_name_placeholder") });
  const host = h("input", { required: true, placeholder: "192.168.178.20" });
  const user = h("input", { required: true, placeholder: "immanuel" });
  const port = h("input", { type: "number", min: 1, max: 65535, value: 22 });
  const command = h("input", { required: true, maxlength: 500, placeholder: "sudo systemctl poweroff" });
  const preset = (label, value) => h("button", { type: "button", onclick: () => { command.value = value; } }, label);
  const form = h("form", { class: "stack" },
    field(t("web.name"), name), field(t("web.rc_host"), host), field(t("web.rc_user"), user), field(t("web.rc_port"), port), field(t("web.rc_command"), command),
    h("div", { class: "row" }, preset("Linux: poweroff", "sudo systemctl poweroff"), preset("Windows: shutdown", "shutdown /s /t 0"), preset("macOS: shutdown", "sudo shutdown -h now")),
    h("button", { class: "primary", type: "submit" }, icon("plus"), t("web.add")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const action = { id: uniqueId(name.value, section.actions), name: name.value.trim(), host: host.value.trim(), user: user.value.trim(), port: Number(port.value), command: command.value.trim() };
    if (await save("remote", { ...section, actions: [...section.actions, action] }, opts)) redraw();
  });
  fill(box,
    h("section", { class: "panel" }, h("h2", {}, t("web.rc_actions")),
      h("ul", { class: "list" }, section.actions.length ? section.actions.map((action) => h("li", {},
        icon("power"),
        h("div", { class: "grow" }, h("div", {}, action.name), h("small", { class: "mono" }, `${action.user}@${action.host}: ${action.command}`)),
        live?.actions.some((a) => a.id === action.id) ? h("button", { class: "danger", onclick: () => runAction(action) }, t("web.rc_run")) : null,
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await save("remote", { ...section, actions: section.actions.filter((x) => x.id !== action.id) }, opts)) redraw();
        } }, icon("trash")))) : h("li", { class: "empty" }, t("web.rc_none")))),
    live ? h("section", { class: "panel" }, h("h2", {}, t("web.rc_key")), h("p", { class: "muted" }, t("web.rc_key_hint")),
      h("pre", { class: "mono wrap" }, live.public_key)) : null,
    h("section", { class: "panel" }, h("h2", {}, t("web.add")), form));
}

// --- assistant ---------------------------------------------------------------------------

function askPanel(info) {
  const question = h("input", { placeholder: t("web.as_placeholder"), maxlength: 1000, "aria-label": t("web.as_placeholder") });
  const speak = h("input", { type: "checkbox" });
  const out = h("div", { "aria-live": "polite" });
  const usage = h("small", { class: "muted" }, t("web.as_usage", { used: info.tokens_today, limit: info.daily_limit }));
  const show = (r) => {
    fill(out, h("p", { class: "muted" }, r.question), h("p", {}, r.answer), h("small", { class: "muted" }, t(`web.as_source_${r.source}`),
      r.documents?.length ? ` · ${r.documents.join(", ")}` : ""));
    fill(usage, t("web.as_usage", { used: r.tokens_today, limit: info.daily_limit }));
  };
  const form = h("form", { class: "row" }, question, h("button", { class: "primary", type: "submit" }, t("web.as_ask")));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!question.value.trim()) return;
    const result = await attempt(() => api("/api/addons/assistant/ask", { method: "POST", body: { question: question.value, speak: speak.checked } }));
    if (result) { show(result); question.value = ""; }
  });
  let recorder = null;
  const mic = h("button", { type: "button", class: "talk" }, icon("microphone"), t("web.talk_start"));
  mic.addEventListener("click", async () => {
    if (recorder) { recorder.stop(); return; }
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
    catch { toast(t("web.mic_denied"), true); return; }
    const chunks = [];
    const opus = "audio/webm;codecs=opus";
    recorder = new MediaRecorder(stream, MediaRecorder.isTypeSupported(opus) ? { mimeType: opus, audioBitsPerSecond: 64000 } : {});
    recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
    recorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop());
      const blob = new Blob(chunks, { type: recorder.mimeType });
      recorder = null;
      mic.classList.remove("active");
      const result = await attempt(() => api(`/api/addons/assistant/voice?speak=${speak.checked ? 1 : 0}`, { method: "POST", body: blob }));
      if (result) show(result);
    };
    recorder.start(250);
    mic.classList.add("active");
  });
  return h("section", { class: "panel stack" }, h("h2", {}, t("web.as_ask_title")), form,
    h("div", { class: "row" }, info.speech_input && window.isSecureContext && window.MediaRecorder ? mic : null,
      info.speech_output ? h("label", { class: "check" }, speak, t("web.as_speak")) : null),
    out, usage);
}

async function knowledgePanel(info) {
  const panel = h("section", { class: "panel stack" });
  const draw = async () => {
    const files = await status("/api/addons/assistant/knowledge");
    const input = h("input", { type: "file", multiple: true, accept: ".txt,.md,.json,.pdf", "aria-label": t("web.kn_choose") });
    const out = h("div", { "aria-live": "polite" });
    fill(panel, h("h2", {}, t("web.kn_title")), h("p", { class: "muted" }, t("web.kn_hint")),
      info.documents_allowed ? null : h("p", { class: "notice" }, t("web.kn_not_shared")),
      h("ul", { class: "list" }, files?.length ? files.map((f) => h("li", {}, icon("file-text"),
        h("div", { class: "grow clip" }, h("div", {}, f.name), h("small", {}, t("web.kn_info", { passages: f.passages, size: (f.size / 1024).toFixed(0) }))),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await attempt(() => api(`/api/addons/assistant/knowledge/${f.id}`, { method: "DELETE" }))) draw();
        } }, icon("trash")))) : h("li", { class: "empty" }, t("web.nothing"))),
      h("div", { class: "row" }, input, h("button", { onclick: async () => {
        if (!input.files.length) return;
        const form = new FormData();
        [...input.files].forEach((file) => form.append("files", file, file.name));
        const results = await attempt(() => api("/api/addons/assistant/knowledge", { method: "POST", body: form }));
        if (!results) return;
        await draw();
        const bad = results.filter((r) => !r.ok);
        toast(t("web.kn_done", { count: results.length - bad.length }), bad.length > 0);
        bad.forEach((r) => toast(`${r.name}: ${t(r.error)}`, true));
      } }, icon("upload"), t("web.upload"))), out,
      h("p", { class: "muted" }, h("small", {}, t("web.kn_cached", { count: info.answers_stored }))),
      h("button", { class: "ghost", onclick: () => attempt(() => api("/api/addons/assistant/answers", { method: "DELETE" }), t("web.saved")) }, t("web.kn_forget")));
  };
  await draw();
  return panel;
}

const PROVIDERS = { mistral: "brand-mistral", openai: "brand-openai", anthropic: "brand-anthropic", gemini: "brand-gemini", grok: "brand-x", ollama: "brand-ollama", custom: "settings" };

// Only the icon; "Verified" and what it means appear on hover, focus and tap.
const verified = () => h("button", { type: "button", class: "verified-mark", "data-tap-tip": "",
  title: `${t("web.verified")}: ${t("web.verified_tip")}` }, icon("rosette-check"));

function gitRow(p, redraw) {
  // Installed from a link: which branch, and how it stays current.
  const auto = h("input", { type: "checkbox", checked: p.git.auto });
  auto.addEventListener("change", async () => {
    const body = { enabled: auto.checked };
    if (auto.checked) {
      if (!await confirmDialog(t("web.pm_auto_confirm", { name: p.name }), t("web.pm_auto"), true)) { auto.checked = false; return; }
      body.password = await askDialog(t("web.confirm_password"), { type: "password" });
      if (!body.password) { auto.checked = false; return; }
    }
    if (!await attempt(() => api(`/api/plugin-manager/${p.id}/auto`, { method: "PUT", body }), t("web.saved"))) auto.checked = !auto.checked;
  });
  return h("div", { class: "row wrap git-row" },
    h("small", { class: "mono" }, `${p.git.branch || t("web.pm_default_branch")} · ${p.git.commit}`),
    p.git.update ? h("span", { class: "badge accent" }, t("web.pm_update_available")) : null,
    h("label", { class: "check", title: t("web.pm_auto_tip") }, auto, t("web.pm_auto")),
    h("button", { class: "ghost", onclick: async () => { if (await attempt(() => api(`/api/plugin-manager/${p.id}/check`, { method: "POST" }))) redraw(); } }, icon("refresh"), t("web.pm_check")),
    p.git.update ? h("button", { class: "primary", onclick: async () => {
      const password = await askDialog(t("web.confirm_password"), { type: "password" });
      if (password && await attempt(() => api(`/api/plugin-manager/${p.id}/update`, { method: "POST", body: { password } }), t("web.addon_restart"))) redraw();
    } }, t("web.pm_update")) : null);
}

function providerChoice(selected) {
  const box = h("div", { class: "choice provider-choice", role: "radiogroup" });
  box.value = selected;
  Object.entries(PROVIDERS).forEach(([id, name]) => box.append(h("button", { type: "button", role: "radio", class: id === selected ? "on" : "", "aria-checked": String(id === selected), dataset: { id }, onclick: (event) => {
    box.value = id;
    box.querySelectorAll("button").forEach((b) => { b.classList.toggle("on", b.dataset.id === id); b.setAttribute("aria-checked", String(b.dataset.id === id)); });
    box.dispatchEvent(new Event("change", { bubbles: true }));
    event.preventDefault();
  } }, icon(name), t(`web.as_provider_${id}`))));
  return box;
}

function usagePanel(info) {
  const u = info.usage;
  const money = (value) => (value === null ? "–" : new Intl.NumberFormat(undefined, { style: "currency", currency: u.currency, maximumFractionDigits: value < 1 ? 4 : 2 }).format(value));
  const tile = (label, value) => h("div", { class: "tile" }, h("div", {}, h("strong", {}, value), h("small", {}, label)));
  const tokens = u.month.input + u.month.output;
  return h("section", { class: "panel stack" }, h("h2", {}, icon("chart-bar"), " ", t("web.as_spend")),
    h("div", { class: "tiles" }, tile(t("web.as_usage_today"), info.tokens_today.toLocaleString()), tile(t("web.as_usage_month"), tokens.toLocaleString()),
      tile(t("web.as_usage_cost"), money(u.cost_month)), tile(t("web.as_usage_left"), money(u.left))),
    h("small", { class: "muted" }, t("web.as_usage_note", { model: info.model })));
}

async function assistant(box, data, opts) {
  const section = data.config.assistant;
  const info = await status("/api/addons/assistant/status");
  const provider = providerChoice(section.provider);
  const model = h("input", { value: section.model, placeholder: t("web.as_model_placeholder") });
  const url = h("input", { value: section.base_url || "", placeholder: "https://…/v1" });
  const location = h("input", { value: section.location, placeholder: "Berlin" });
  const limit = h("input", { type: "number", min: 1000, max: 10000000, value: section.daily_token_limit });
  const length = h("input", { type: "number", min: 50, max: 2000, value: section.max_answer_tokens });
  const turns = h("input", { type: "number", min: 0, max: 10, value: section.history_turns });
  const speech = h("input", { type: "checkbox", checked: section.speech_output });
  const share = h("input", { type: "checkbox", checked: section.share_documents });
  const actions = h("input", { type: "checkbox", checked: section.ai_actions });
  const vision = h("input", { type: "checkbox", checked: section.allow_vision });
  const paperless = h("input", { type: "url", value: section.paperless_url || "", placeholder: "http://192.168.178.20:8000" });
  const nextcloud = h("input", { type: "url", value: section.nextcloud_url || "", placeholder: "https://cloud.example.org" });
  const nextUser = h("input", { value: section.nextcloud_user || "" });
  const number = (value) => h("input", { type: "number", min: 0, step: "0.01", value: value ?? "" });
  const priceIn = number(section.price_input), priceOut = number(section.price_output), budget = number(section.budget);
  const currency = h("select", {}, ["USD", "EUR"].map((c) => h("option", { value: c, selected: c === section.currency }, c)));
  const amount = (input) => (input.value === "" ? null : Number(input.value));
  const keys = h("div");
  let saveKeys = null;
  const button = h("button", { class: "primary", onclick: async () => {
    if (saveKeys) saveKeys();
    await save("assistant", { ...section, provider: provider.value, model: model.value.trim(), base_url: url.value.trim() || null, location: location.value.trim(),
      price_input: amount(priceIn), price_output: amount(priceOut), budget: amount(budget), currency: currency.value,
      daily_token_limit: Number(limit.value), max_answer_tokens: Number(length.value), history_turns: Number(turns.value), speech_output: speech.checked,
      share_documents: share.checked, ai_actions: actions.checked, allow_vision: vision.checked, paperless_url: paperless.value.trim() || null, nextcloud_url: nextcloud.value.trim() || null, nextcloud_user: nextUser.value.trim() }, opts);
  } }, icon("check"), t("web.save"));
  fill(box,
    info ? askPanel(info) : null,
    info ? usagePanel(info) : null,
    h("section", { class: "panel stack" }, h("h2", {}, t("web.as_usage_prices")), h("p", { class: "muted" }, t("web.as_usage_prices_hint")),
      field(t("web.as_price_input"), priceIn), field(t("web.as_price_output"), priceOut), field(t("web.as_budget"), budget, t("web.as_budget_hint")), field(t("web.as_currency"), currency)),
    h("section", { class: "panel stack" }, h("h2", {}, t("web.nav_settings")),
      field(t("web.as_provider"), provider), field(t("web.as_model"), model, t("web.as_model_hint")), field(t("web.as_base_url"), url),
      field(t("web.as_location"), location, t("web.as_location_hint")), field(t("web.as_limit"), limit, t("web.as_limit_hint")),
      field(t("web.as_length"), length), field(t("web.as_turns"), turns),
      h("label", { class: "check" }, speech, t("web.as_speech_output")),
      h("h3", {}, t("web.as_documents")), h("p", { class: "muted" }, t("web.as_documents_hint")),
      h("label", { class: "check" }, share, t("web.as_share")),
      h("label", { class: "check" }, actions, t("web.as_actions")),
      h("label", { class: "check" }, vision, t("web.as_vision")),
      field("Paperless-ngx", paperless), field("Nextcloud", nextcloud), field(t("web.as_nextcloud_user"), nextUser),
      keys, button),
    info ? await knowledgePanel(info) : null,
    h("p", { class: "muted" }, h("small", {}, t("web.as_privacy"))));
  const view = await import("./settings.js");
  await view.render(keys, [], { embedded: true, groups: ["assistant"], register: (fn) => { saveKeys = fn; }, onSaved: () => {} });
}

function agentDialog(agent) {
  const body = h("div", { class: "stack" });
  const dialog = h("dialog", { class: "editor" }, h("div", { class: "row between" }, h("h2", {}, agent.name), h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))), body);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  const draw = async () => {
    const rows = await attempt(() => api(`/api/agents/${agent.id}/docker`));
    if (!rows) { fill(body, h("p", { class: "muted" }, t("web.ag_no_docker"))); return; }
    fill(body, h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, rows.map((c) => h("tr", {},
      h("td", {}, h("span", { class: `dot ${c.state === "running" ? "ok" : ""}` })),
      h("td", {}, c.name, h("small", { class: "muted block" }, c.image)), h("td", {}, h("small", {}, c.status)),
      h("td", { class: "actions" },
        c.state === "running" ? [act("stop", "player-stop", c), act("restart", "refresh", c)] : act("start", "player-play", c), act("remove", "trash", c),
        h("button", { class: "icon-only", title: t("web.ag_logs"), onclick: () => logs(c) }, icon("file-text")))))))));
  };
  const act = (action, ic, c) => h("button", { class: `icon-only${action === "remove" ? " danger" : ""}`, title: t(`web.ag_docker_${action}`), onclick: async () => {
    if (action === "remove" && !await confirmDialog(t("web.ag_docker_remove_confirm", { name: c.name }), t("web.delete"), true)) return;
    if (await attempt(() => api(`/api/agents/${agent.id}/docker`, { method: "POST", body: { action, container: c.name } }), t("web.saved"))) draw();
  } }, icon(ic));
  const logs = async (c) => {
    const text = await attempt(() => api(`/api/agents/${agent.id}/docker/${encodeURIComponent(c.name)}/logs`));
    if (text != null) fill(body, h("button", { onclick: draw }, t("web.back")), h("pre", { class: "log mono wrap" }, text));
  };
  draw();
}

// Looking after a computer: reading is always allowed; changes need the password, and the
// computer's own agent.conf decides whether it allows them at all.
function maintenanceDialog(agent) {
  const body = h("div", { class: "stack" });
  const TABS = ["info", "security", "updates", "compose", "git", "logs"];
  const tabs = h("div", { class: "segmented wrap" });
  const dialog = h("dialog", { class: "editor" }, h("div", { class: "row between" }, h("h2", {}, icon("settings"), " ", agent.name),
    h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))), tabs, body);
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  const kv = (rows) => h("dl", { class: "kv" }, rows.filter(([, v]) => v != null && v !== "").map(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))]));
  const change = async (what, target = "") => {
    const password = await askDialog(t("web.confirm_password"), { type: "password" });
    if (!password) return;
    if (await attempt(() => api(`/api/agents/${agent.id}/maintain`, { method: "POST", body: { what, target, password } }), t("web.sv_started"))) job();
  };
  const job = async () => {
    const s = await attempt(() => api(`/api/agents/${agent.id}/look/job`));
    if (!s) return;
    const box = h("section", { class: "panel stack" }, h("h3", {}, t("web.sv_job"), " ", s.name ? h("span", { class: `badge ${s.running ? "accent" : s.exit === 0 ? "ok" : "danger"}` },
      s.running ? t("web.sv_running") : s.exit === 0 ? t("web.sv_done") : t("web.sv_failed", { code: s.exit })) : null),
      s.name ? h("p", { class: "muted" }, s.name) : h("p", { class: "muted" }, t("web.sv_no_job")),
      s.log ? h("pre", { class: "log mono wrap" }, s.log) : null);
    fill(body, box);
    if (s.running) whileShown(box, job, 3000);
  };
  const views = {
    info: async () => {
      const [i, st] = [await api(`/api/agents/${agent.id}/look/info`), agent.info];
      return [kv([[t("web.sv_os"), st.os], [t("web.sv_kernel"), st.kernel], ["CPU", `${i.cpu} (${i.cores})`],
        [t("web.sv_temp"), i.temperatures?.length ? `${Math.max(...i.temperatures)} °C` : null], [t("web.memory"), `${st.memory_available_mb} / ${st.memory_total_mb} MB`],
        [t("web.storage"), st.disk ? `${st.disk.free_mb} / ${st.disk.total_mb} MB` : null], [t("web.sv_agent"), st.agent]]),
        i.failed_units?.length ? h("p", { class: "notice danger" }, t("web.sv_failed_units"), " ", i.failed_units.join(", ")) : h("p", {}, icon("circle-check"), " ", t("web.sv_units_ok")),
        h("h3", {}, t("web.sv_addresses")), h("ul", { class: "list mono" }, (i.addresses || []).map((a) => h("li", {}, a)))];
    },
    security: async () => {
      const s = await api(`/api/agents/${agent.id}/look/security`);
      return [s.warnings.length ? h("ul", { class: "list" }, s.warnings.map((w) => h("li", {}, icon("alert-triangle"), h("span", { class: "grow" }, t(`web.sv_warn_${w}`)))))
        : h("p", { class: "notice ok" }, icon("shield-check"), " ", t("web.sv_secure")),
      kv([[t("web.sv_firewall"), s.firewall.tool ? `${s.firewall.tool}: ${s.firewall.active ? t("web.sv_on") : t("web.sv_off")}` : t("web.sv_none")],
        ["SSH root", s.ssh.present ? s.ssh.root_login : null], [t("web.sv_ssh_passwords"), s.ssh.present ? s.ssh.passwords : null],
        [t("web.sv_failed_logins"), s.failed_logins_24h], [t("web.sv_updates"), s.updates]]),
      h("h3", {}, t("web.sv_ports")), h("p", { class: "mono" }, s.open_ports.join("  ") || "–")];
    },
    updates: async () => {
      const u = await api(`/api/agents/${agent.id}/look/updates`);
      return [h("p", {}, u.manager ? t("web.sv_waiting", { count: u.count, manager: u.manager }) : t("web.sv_no_manager")),
        u.packages?.length ? h("p", { class: "mono muted" }, u.packages.join(" ")) : null,
        agent.info.can_upgrade ? h("button", { class: "primary", disabled: !u.count, onclick: () => change("upgrade") }, icon("download"), t("web.sv_upgrade"))
          : h("p", { class: "muted" }, t("web.sv_needs", { key: "ALLOW_UPGRADE=true" }))];
    },
    compose: async () => {
      if (!agent.info.compose_dirs) return [h("p", { class: "muted" }, t("web.sv_needs", { key: "DOCKER_COMPOSE_DIRS=/srv/…" }))];
      const dirs = await api(`/api/agents/${agent.id}/compose`);
      return [h("ul", { class: "list" }, dirs.map((d) => h("li", {}, icon("brand-docker"), h("span", { class: "grow mono" }, d),
        h("button", { onclick: () => change("compose", d) }, icon("refresh"), t("web.sv_update")))))];
    },
    git: async () => {
      if (!agent.info.git_repos) return [h("p", { class: "muted" }, t("web.sv_needs", { key: "GIT_REPOS=/srv/…" }))];
      const repos = await api(`/api/agents/${agent.id}/git`);
      return [h("ul", { class: "list" }, repos.map((r) => h("li", {}, icon("brand-github"),
        h("div", { class: "grow" }, h("div", { class: "mono" }, r.repo), h("small", { class: "muted" }, `${r.branch} · ${r.last}${r.changed ? ` · ${t("web.sv_changed", { count: r.changed })}` : ""}`)),
        h("button", { onclick: () => change("git", r.repo) }, icon("download"), t("web.sv_pull")))))];
    },
    logs: async () => {
      if (!agent.info.can_logs) return [h("p", { class: "muted" }, t("web.sv_needs", { key: "ALLOW_LOGS=true" }))];
      const out = h("pre", { class: "log mono wrap" });
      const show = async (kind) => { fill(out, t("web.loading")); out.textContent = await attempt(() => api(`/api/agents/${agent.id}/logs/${kind}`)) || ""; };
      show("warnings");
      return [h("div", { class: "row" }, h("button", { onclick: () => show("warnings") }, t("web.sv_log_warnings")), h("button", { onclick: () => show("audit") }, t("web.sv_log_audit"))), out];
    },
  };
  const open = async (name) => {
    [...tabs.children].forEach((b) => b.classList.toggle("on", b.dataset.tab === name));
    fill(body, h("p", { class: "muted" }, t("web.loading")));
    try { fill(body, ...(await views[name]())); } catch (error) { fill(body, h("p", { class: "notice danger" }, t(error.message || "web.error"))); }
  };
  fill(tabs, TABS.map((name) => h("button", { dataset: { tab: name }, onclick: () => open(name) }, t(`web.sv_tab_${name}`))),
    h("button", { onclick: () => { [...tabs.children].forEach((b) => b.classList.remove("on")); job(); } }, t("web.sv_job")));
  open("info");
}

async function agentPower(agent, command) {
  const first = await attempt(() => api(`/api/agents/${agent.id}/power/begin`, { method: "POST", body: { command } }));
  if (!first) return;
  const started = Date.now();
  const what = t(`web.ag_cmd_${command}`);
  if (!await confirmDialog(t("web.ag_confirm", { name: agent.name, what }), t("remote.yes_1"), true)) return;
  await sleep(Math.max(0, 1100 - (Date.now() - started)));
  const second = await attempt(() => api("/api/agents/power/confirm", { method: "POST", body: { token: first.token } }));
  if (!second) return;
  const typed = await typedConfirm(second.name, what);
  if (typed === null) { toast(t("remote.cancelled")); return; }
  if (await attempt(() => api("/api/agents/power/confirm", { method: "POST", body: { token: second.token, typed } }))) toast(t("web.ag_sent", { what }));
}

async function agents(box, data, opts) {
  const list = await status("/api/agents");
  if (!list) { fill(box, h("p", { class: "notice" }, t("web.addon_restart"))); return; }
  const redraw = () => renderDetail("agents", box, opts);
  const name = h("input", { placeholder: t("web.ag_name_placeholder"), maxlength: 40, "aria-label": t("web.name") });
  const out = h("div", { class: "stack" });
  const watched = h("div", { class: "stack" });  // the services this Pi watches, part of "Server"
  fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("web.ag_title")), h("p", { class: "muted" }, t("web.ag_intro")),
    h("ul", { class: "list" }, list.length ? list.map((a) => h("li", { class: "agent" }, h("span", { class: `dot ${a.online ? "ok" : ""}` }), icon("device-desktop"),
      h("div", { class: "grow" }, h("div", {}, a.name), h("small", {}, a.online
        ? `${a.info.os || ""} · ${a.info.memory_available_mb ?? "?"} MB frei · Last ${a.info.load ?? "?"}`
        : a.last_seen ? t("web.last_used", { time: when(a.last_seen) }) : t("web.ag_waiting"))),
      a.online ? h("button", { onclick: () => maintenanceDialog(a) }, icon("settings"), t("web.sv_maintain")) : null,
      a.online && a.info.can_docker ? h("button", { onclick: () => agentDialog(a) }, "Docker") : null,
      a.online && a.info.can_power ? ["suspend", "reboot", "shutdown"].map((c) => h("button", { class: c === "shutdown" ? "danger" : "", onclick: () => agentPower(a, c) }, t(`web.ag_cmd_${c}`))) : null,
      h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
        if (await confirmDialog(t("web.ag_delete", { name: a.name }), t("web.delete"), true) && await attempt(() => api(`/api/agents/${a.id}`, { method: "DELETE" }))) redraw();
      } }, icon("trash")))) : h("li", { class: "empty" }, t("web.ag_none")))),
  h("section", { class: "panel stack" }, h("h2", {}, t("web.ag_add")),
    h("div", { class: "row" }, name, h("button", { class: "primary", onclick: async () => {
      const r = await attempt(() => api("/api/agents", { method: "POST", body: { name: name.value } }));
      if (!r) return;
      fill(out, h("p", { class: "notice" }, t("web.ag_token_once")), h("pre", { class: "mono wrap" }, `git clone https://github.com/lytexdev/pi-guard && cd pi-guard/agent\ncargo build --release\nsudo ./install.sh https://${location.host} ${r.token}`));
    } }, icon("plus"), t("web.ag_create"))), out),
    h("h2", { class: "section-title" }, icon("world"), " ", t("addons.heartbeat")), h("p", { class: "muted" }, t("web.addon_heartbeat_text")), watched);
  await heartbeat(watched, data, opts);
}

async function languages(box) {
  const draw = async () => {
    const s = await status("/api/addons/languages/status");
    if (!s) return;
    fill(box, h("section", { class: "panel stack" }, h("h2", {}, t("addons.languages")), h("p", { class: "muted" }, t("web.lg_intro")),
      s.key ? null : h("p", { class: "notice" }, t("web.lg_no_key"), " ", h("a", { href: "#/addons/assistant" }, t("addons.assistant"))),
      s.running.code ? h("p", { class: "notice" }, t("web.lg_progress", { name: s.available[s.running.code], done: s.running.done, total: s.running.total })) : null,
      h("ul", { class: "list" }, Object.entries(s.available).map(([code, label]) => h("li", {}, h("span", { class: "grow" }, label),
        s.installed.includes(code)
          ? [h("span", { class: "badge ok" }, t("web.lg_installed")), h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => { if (await attempt(() => api(`/api/addons/languages/${code}`, { method: "DELETE" }))) draw(); } }, icon("trash"))]
          : h("button", { disabled: !s.key || Boolean(s.running.code), onclick: async () => { if (await attempt(() => api(`/api/addons/languages/${code}`, { method: "POST" }))) whileShown(box, draw, 1500); } }, icon("plus"), t("web.add"))))),
      h("p", { class: "muted" }, h("small", {}, t("web.lg_hint")))));
    if (s.running.code) whileShown(box, draw, 3000);
  };
  await draw();
}

const DETAILS = { heartbeat, analytics, smarthome, remote, assistant, agents, languages };

export async function renderDetail(id, box, opts = {}) {
  const data = await api("/api/addons");
  await DETAILS[id](box, data, opts);
}

export async function setEnabled(id, enabled, opts = {}) {
  const data = await api("/api/addons");
  return save(id, { ...data.config[id], enabled }, opts);
}

export async function render(main, params = []) {
  const id = params[0];
  if (id === "plugin") { await pluginPage(main, params[1]); return; }
  if (id === "mobile") { await (await import("./mobile.js")).render(main); return; }
  if (id in DETAILS) {
    const box = h("div", { class: "stack" });
    fill(main, h("div", { class: "topbar" }, h("h1", {}, t(`addons.${id}`)), h("a", { class: "button", href: "#/addons" }, t("web.back"))), box);
    await renderDetail(id, box);
    return;
  }
  const data = await api("/api/addons");
  const pinned = new Set(state.session.pinned || []);
  const pin = async (id, button) => {
    if (pinned.has(id)) pinned.delete(id); else pinned.add(id);
    button.classList.toggle("on", pinned.has(id));
    const saved = await attempt(() => api("/api/navigation/pinned", { method: "PUT", body: { pinned: [...pinned] } }));
    if (saved) { state.session.pinned = saved.pinned; location.reload(); }
  };
  // Cards in groups, each with its state at a glance; removing sits in a menu, not next to "Open".
  const GROUPS = [["server", ["agents", "remote"]], ["network", ["analytics"]], ["home", ["smarthome"]],
    ["assistant", ["assistant", "languages"]], ["messages", ["discord", "whatsapp", "email", "mobile", "chatapps"]]];
  const MODULE_ICONS = { discord: "brand-discord", whatsapp: "brand-whatsapp", email: "mail", mobile: "device-mobile", chatapps: "message" };
  const stateBadge = (on, extra) => extra
    ? h("span", { class: "badge warn" }, extra)
    : h("span", { class: `badge ${on ? "ok" : ""}` }, t(on ? "web.ad_on" : "web.ad_off"));
  const why = (text) => h("p", { class: "addon-why" }, icon("alert-triangle"), " ", text);
  const card = ({ id, title, iconName, text, enabled, badge, reason, open, onToggle, remove }) => {
    const toggle = h("input", { type: "checkbox", checked: enabled, id: `ad_${id}` });
    toggle.addEventListener("change", () => onToggle(toggle));
    const star = h("button", { class: `icon-only pin${pinned.has(id) ? " on" : ""}`, title: t("web.pm_pin"), onclick: () => pin(id, star) }, icon("list-details"));
    const menu = remove ? h("details", { class: "addon-menu" }, h("summary", { title: t("web.ad_more"), "aria-label": t("web.ad_more") }, "⋯"),
      h("div", { class: "addon-menu-body" }, h("button", { class: "danger", title: t("web.addon_remove_tip"), onclick: remove }, icon("trash"), t("web.addon_remove")))) : null;
    return h("article", { class: `addon-card${enabled ? " on" : ""}`, dataset: { state: enabled ? "on" : "off" } },
      h("div", { class: "addon-head" }, h("span", { class: "addon-icon" }, icon(iconName)),
        h("div", { class: "grow" }, h("h3", {}, title, verified()), badge), star, menu),
      h("p", { class: "muted addon-text" }, text),
      reason ? why(reason) : null,
      h("div", { class: "addon-foot" }, h("label", { class: "addon-switch", for: `ad_${id}` }, toggle, t("web.ad_switch")),
        open ? h("a", { class: `button small${enabled ? "" : " ghost"}`, href: open, title: t("web.addon_open") }, icon("settings"), t("web.ad_open")) : null));
  };
  const modules = await api("/api/modules");
  const cardFor = {};
  for (const addon of data.addons) {
    const extra = addon.needs ? t("web.ad_needs_setup") : addon.enabled !== addon.running ? t("web.ad_restart") : null;
    cardFor[addon.id] = card({ id: addon.id, title: t(`addons.${addon.id}`), iconName: ADDONS[addon.id], text: t(`web.addon_${addon.id}_text`),
      enabled: addon.enabled, badge: stateBadge(addon.enabled, extra), reason: addon.needs ? t(addon.needs) : null, open: addon.id === "analytics" ? "#/network" : `#/addons/${addon.id}`,
      onToggle: (toggle) => setEnabled(addon.id, toggle.checked),
      remove: async () => {
        if (!await confirmDialog(t("web.addon_remove_confirm", { name: t(`addons.${addon.id}`) }), t("web.addon_remove"), true)) return;
        const done = await attempt(() => api(`/api/addons/${addon.id}`, { method: "DELETE" }));
        if (done) { toast(t("web.addon_removed", { size: `${(done.freed / 1e6).toFixed(1)} MB` })); restartPrompt(); }
      } });
  }
  for (const module of modules) {
    let c;
    c = cardFor[module.id] = card({ id: module.id, title: t(`web.addon_${module.id}`), iconName: MODULE_ICONS[module.id], text: t(`web.addon_${module.id}_text`),
      enabled: module.enabled, badge: stateBadge(module.enabled), open: module.id === "mobile" ? "#/addons/mobile" : "#/settings/messaging",
      onToggle: async (toggle) => {
        const done = await attempt(() => api(`/api/modules/${module.id}`, { method: "PUT", body: { enabled: toggle.checked } }), t("web.saved"));
        if (!done) { toggle.checked = !toggle.checked; return; }
        state.session.modules = { ...state.session.modules, [module.id]: toggle.checked };
        c.classList.toggle("on", toggle.checked);
        c.dataset.state = toggle.checked ? "on" : "off";
        if (done.restart) restartPrompt();
      } });
  }
  const cards = h("div", { class: "stack" }, GROUPS.map(([group, ids]) => {
    const inside = ids.map((id) => cardFor[id]).filter(Boolean);
    return inside.length ? h("section", { class: "addon-group", dataset: { group } }, h("h2", { class: "section-title" }, t(`web.ad_group_${group}`)),
      h("div", { class: "addon-grid" }, inside)) : null;
  }));
  let only = "all";
  const filters = h("div", { class: "segmented", role: "group", "aria-label": t("web.ad_filter") }, ["all", "on", "off"].map((value) =>
    h("button", { class: value === only ? "on" : "", onclick: (event) => {
      only = value;
      [...filters.children].forEach((b) => b.classList.toggle("on", b === event.currentTarget));
      applyFilter();
    } }, t(`web.ad_show_${value}`))));
  let query = "";
  const applyFilter = () => {
    cards.querySelectorAll(".addon-card").forEach((el) => {
      const hidden = (only !== "all" && el.dataset.state !== only) || (query && !el.textContent.toLowerCase().includes(query));
      el.classList.toggle("hidden", Boolean(hidden));
    });
    cards.querySelectorAll(".addon-group").forEach((g) => g.classList.toggle("hidden", !g.querySelector(".addon-card:not(.hidden)")));
  };
  const ownPanel = h("section", { class: "panel stack hidden" });
  const installed = h("section", { class: "panel stack hidden" });
  const drawOwn = async () => {
    const list = await api("/api/plugin-manager");
    const zip = h("input", { type: "file", accept: ".zip", "aria-label": t("web.pm_upload") });
    const name = h("input", { placeholder: t("web.pm_new_name"), "aria-label": t("web.pm_new_name") });
    const gitUrl = h("input", { type: "url", placeholder: "https://github.com/…", "aria-label": t("web.pm_git") });
    installed.classList.toggle("hidden", !list.length);
    fill(installed, h("h2", {}, t("web.pm_installed")),
      h("ul", { class: "list" }, list.map((p) => {
        const on = h("input", { type: "checkbox", checked: p.enabled, "aria-label": p.name });
        on.addEventListener("change", async () => { if (await attempt(() => api(`/api/plugin-manager/${p.id}/enabled`, { method: "PUT", body: { enabled: on.checked } }), t("web.saved"))) restartPrompt(); });
        return h("li", { class: "person-row" }, on, icon(p.icon), h("div", { class: "grow clip" }, h("div", {}, h("a", { href: `#/addons/plugin/${p.id}` }, p.name), " ", h("small", { class: "muted" }, `v${p.version}${p.authors.length ? ` · ${p.authors.join(", ")}` : ""}`)), h("small", {}, p.description || p.id)),
          h("button", { class: "icon-only", title: t("web.pm_edit"), onclick: () => editSource(p) }, icon("pencil")),
          h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => { if (await attempt(() => api(`/api/plugin-manager/${p.id}`, { method: "DELETE" }), t("web.saved"))) drawOwn(); } }, icon("trash")),
          p.git ? gitRow(p, drawOwn) : null);
      })));
    fill(ownPanel, h("h2", {}, t("web.pm_title")), h("p", { class: "notice" }, t("web.pm_warning")),
      h("h3", {}, t("web.pm_upload")), h("p", { class: "muted" }, h("small", {}, t("web.pm_upload_hint"))),
      h("div", { class: "row" }, zip, h("button", { onclick: async () => {
        if (!zip.files.length) return;
        const password = await askDialog(t("web.confirm_password"), { type: "password" });
        if (!password) return;
        const form = new FormData();
        form.append("password", password);
        form.append("file", zip.files[0], zip.files[0].name);
        if (await attempt(() => api("/api/plugin-manager", { method: "POST", body: form }), t("web.saved"))) drawOwn();
      } }, icon("upload"), t("web.upload"))),
      h("h3", {}, icon("brand-github"), " ", t("web.pm_git")),
      h("div", { class: "row" }, gitUrl, h("button", { onclick: async () => {
        const password = gitUrl.value && await askDialog(t("web.confirm_password"), { type: "password" });
        if (password && await attempt(() => api("/api/plugin-manager/git", { method: "POST", body: { url: gitUrl.value.trim(), password } }), t("web.saved"))) drawOwn();
      } }, icon("download"), t("web.pm_install"))),
      h("h3", {}, t("web.pm_create")),
      h("div", { class: "row" }, name, h("button", { onclick: async () => {
        const id = slug(name.value);
        const password = name.value && await askDialog(t("web.confirm_password"), { type: "password" });
        if (password && await attempt(() => api("/api/plugin-manager/create", { method: "POST", body: { id, name: name.value, password } }), t("web.saved"))) drawOwn();
      } }, icon("plus"), t("web.pm_create_button"))));
  };
  const search = searchToggle(t("web.pm_search"), (q) => {
    query = q;
    applyFilter();
    installed.querySelectorAll(".list li").forEach((el) => el.classList.toggle("hidden", q && !el.textContent.toLowerCase().includes(q)));
  });
  fill(main, h("div", { class: "topbar" }, h("div", { class: "title-row" }, h("h1", {}, t("web.nav_addons")), search,
    h("button", { class: "icon-only", title: t("web.pm_add_tip"), "aria-label": t("web.pm_add_tip"), onclick: () => { ownPanel.classList.toggle("hidden"); ownPanel.scrollIntoView({ block: "nearest" }); } }, icon("plus")))), h("div", { class: "row" }, filters), cards, installed, ownPanel);
  await drawOwn();
}

async function pluginPage(main, id) {
  const list = await api("/api/plugin-manager");
  const plugin = list.find((p) => p.id === id);
  if (!plugin) { location.hash = "#/addons"; return; }
  const widgets = await status(`/api/plugin-manager/${id}/panel`) || [];
  const widget = (w) => {
    if (w.type === "stat") return h("div", { class: "tile" }, h("div", {}, h("div", { class: "tile-value" }, String(w.value ?? "")), h("small", {}, String(w.label ?? ""))));
    if (w.type === "list") return h("ul", { class: "list" }, (w.items || []).map((x) => h("li", {}, String(x))));
    if (w.type === "button") return h("button", { onclick: async () => {
      const r = await attempt(() => api(`/api/plugin-manager/${id}/action/${encodeURIComponent(w.action)}`, { method: "POST" }));
      if (r?.message) toast(r.message);
    } }, String(w.label ?? w.action));
    return h("p", {}, String(w.text ?? ""));
  };
  const inputs = plugin.settings.map((f) => {
    const input = f.type === "bool" ? h("input", { type: "checkbox", checked: Boolean(plugin.values[f.key]) })
      : h("input", { type: f.type === "secret" ? "password" : f.type === "number" ? "number" : "text", value: plugin.values[f.key] ?? "" });
    return [f, input];
  });
  fill(main, h("div", { class: "topbar" }, h("h1", {}, icon(plugin.icon), " ", plugin.name), h("a", { class: "button", href: "#/addons" }, t("web.back"))),
    h("section", { class: "panel stack" }, h("p", { class: "muted" }, plugin.description),
      h("dl", { class: "kv" }, h("dt", {}, "Version"), h("dd", {}, plugin.version),
        plugin.authors.length ? [h("dt", {}, t("web.pm_authors")), h("dd", {}, plugin.authors.join(", "))] : null,
        plugin.license ? [h("dt", {}, t("web.pm_license")), h("dd", {}, plugin.license)] : null,
        plugin.source ? [h("dt", {}, t("web.pm_source")), h("dd", {}, h("a", { href: plugin.source, target: "_blank", rel: "noopener" }, plugin.source))] : null),
      plugin.permissions.length ? h("p", { class: "notice" }, t("web.pm_permissions"), " ", plugin.permissions.join(", ")) : null),
    widgets.length ? h("section", { class: "panel stack" }, widgets.map(widget)) : null,
    inputs.length ? h("section", { class: "panel stack" }, h("h2", {}, t("web.nav_settings")),
      inputs.map(([f, input]) => field(f.label, input)),
      h("button", { class: "primary", onclick: async () => {
        const body = Object.fromEntries(inputs.map(([f, input]) => [f.key, f.type === "bool" ? input.checked : f.type === "number" ? Number(input.value) : input.value]));
        if (await attempt(() => api(`/api/plugin-manager/${id}/settings`, { method: "PUT", body }), t("web.saved"))) restartPrompt();
      } }, icon("check"), t("web.save"))) : null);
}

async function editSource(plugin) {
  const data = await attempt(() => api(`/api/plugin-manager/${plugin.id}/source`));
  if (!data) return;
  const text = h("textarea", { class: "mono code", spellcheck: "false", rows: 22, "aria-label": data.entry });
  text.value = data.source;
  const dialog = h("dialog", { class: "editor" }, h("h2", {}, `${plugin.name} · ${data.entry}`), text,
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const password = await askDialog(t("web.confirm_password"), { type: "password" });
        if (password && await attempt(() => api(`/api/plugin-manager/${plugin.id}/source`, { method: "PUT", body: { source: text.value, password } }), t("web.saved"))) { dialog.close(); restartPrompt(); }
      } }, t("web.save"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}
