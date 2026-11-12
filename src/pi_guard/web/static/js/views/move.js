// Moving home, a new router or internet provider: before, a moving file and the Pi's Wi-Fi;
// after, what changed and what needs a hand. Nothing here changes the Pi's network itself.
import { askDialog, api, attempt, fill, h, icon, t, toast, when } from "../api.js";
import { restartPrompt } from "./settings.js";

const STATE = { ok: ["ok", "circle-check"], todo: ["danger", "alert-triangle"], info: ["", "bulb"] };
// A shell word in single quotes, so a Wi-Fi name or key with spaces or quotes stays one word.
const quote = (text) => `'${String(text).replace(/'/g, "'\\''")}'`;

function step(number, title, ...body) {
  return h("li", { class: "mv-step" }, h("span", { class: "mv-number" }, number), h("div", { class: "grow stack" }, h("h3", {}, title), ...body));
}

function packageForm() {
  const pass = h("input", { type: "password", autocomplete: "new-password", minlength: 12, "aria-label": t("web.mv_passphrase") });
  const again = h("input", { type: "password", autocomplete: "new-password", "aria-label": t("web.mv_passphrase_again") });
  const recordings = h("input", { type: "checkbox", id: "mv_rec" });
  const busy = h("small", { class: "muted", "aria-live": "polite" });
  const button = h("button", { class: "primary", onclick: async () => {
    if (pass.value.length < 12) { toast(t("web.mv_short_passphrase"), true); return; }
    if (pass.value !== again.value) { toast(t("web.mv_passphrase_differs"), true); return; }
    const password = await askDialog(t("web.confirm_password"), { type: "password" });
    if (!password) return;
    button.disabled = true;
    busy.textContent = t("web.mv_packing");
    const response = await attempt(() => api("/api/move/package", { method: "POST", raw: true, body: { password, passphrase: pass.value, recordings: recordings.checked } }));
    button.disabled = false;
    busy.textContent = "";
    if (!response) return;
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "pi-guard.pgbk";
    const url = URL.createObjectURL(await response.blob());
    h("a", { href: url, download: name }).click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    pass.value = again.value = "";
    toast(t("web.mv_packed"));
  } }, icon("download"), t("web.mv_package"));
  return h("div", { class: "stack" },
    h("div", { class: "grid two" }, h("label", { class: "field" }, t("web.mv_passphrase"), pass), h("label", { class: "field" }, t("web.mv_passphrase_again"), again)),
    h("label", { class: "check", for: "mv_rec" }, recordings, t("web.mv_with_recordings")),
    h("div", { class: "row" }, button, busy),
    h("details", { class: "guide" }, h("summary", {}, t("web.mv_new_pi")), h("ol", { class: "howto" },
      h("li", {}, t("web.mv_new_pi_1")), h("li", {}, t("web.mv_new_pi_2"), h("pre", { class: "mono wrap" }, "sudo ./scripts/restore.sh pi-guard-backup-….pgbk")),
      h("li", {}, t("web.mv_new_pi_3")))));
}

function wifiForm(net) {
  // The command is built here and never sent: the key stays in this browser.
  const ssid = h("input", { autocomplete: "off", "aria-label": t("web.wifi_name"), placeholder: t("web.wifi_name") });
  const key = h("input", { type: "password", autocomplete: "off", "aria-label": t("web.wifi_key"), placeholder: t("web.wifi_key") });
  const out = h("pre", { class: "mono wrap hidden" });
  const make = () => {
    if (!ssid.value) return;
    const device = net.interface && net.interface.startsWith("wl") ? net.interface : "wlan0";
    out.textContent = `sudo nmcli connection add type wifi con-name ${quote(`pi-guard ${ssid.value}`.slice(0, 60))} ifname ${device} ssid ${quote(ssid.value)} `
      + `wifi-sec.key-mgmt wpa-psk wifi-sec.psk ${quote(key.value)} connection.autoconnect yes`;
    out.classList.remove("hidden");
  };
  return h("div", { class: "stack" },
    h("p", { class: "muted" }, net.ssid ? t("web.mv_wifi_now", { ssid: net.ssid }) : t("web.mv_wifi_cable")),
    h("p", { class: "notice" }, icon("bulb"), " ", t("web.mv_same_wifi")),
    h("details", { class: "guide" }, h("summary", {}, t("web.mv_wifi_add")), h("p", { class: "muted" }, h("small", {}, t("web.mv_wifi_add_hint"))),
      h("div", { class: "row wrap" }, ssid, key, h("button", { onclick: make }, icon("pencil"), t("web.mv_wifi_make"))), out,
      h("div", { class: "row" }, h("button", { class: "ghost", onclick: () => out.textContent && navigator.clipboard?.writeText(out.textContent).then(() => toast(t("web.copied"))) }, icon("copy"), t("web.copy")))));
}

function checkItem(c, redraw) {
  const [tone, ic] = STATE[c.state] || STATE.info;
  const body = [];
  if (c.id === "backup") {
    body.push(h("p", { class: "muted" }, c.last ? t("web.mv_backup_last", { time: when(c.last) }) : t("web.mv_backup_never")),
      h("a", { class: "button small", href: "#/settings/data/backup" }, t("web.mv_to_backup")));
  } else if (c.id === "wifi") {
    body.push(h("p", { class: "muted" }, c.ssid ? t("web.mv_wifi_now", { ssid: c.ssid }) : t("web.mv_wifi_cable")));
  } else if (c.id === "cameras") {
    body.push(c.cameras?.length ? h("ul", {}, c.cameras.map((x) => h("li", {}, `${x.name} (${x.host})`, x.outside ? ` – ${t("web.mv_camera_outside")}` : ` – ${t("web.mv_camera_offline")}`)))
      : h("p", { class: "muted" }, t("web.mv_cameras_ok")), c.cameras?.length ? h("a", { class: "button small", href: "#/settings/devices" }, t("web.mv_to_cameras")) : null);
  } else if (c.id === "router") {
    body.push(h("p", { class: "muted" }, c.state === "ok" ? t("web.mv_router_ok", { host: c.host }) : t("web.mv_router_todo", { host: c.host, gateway: c.gateway || "–" })),
      c.state === "ok" ? null : h("a", { class: "button small", href: "#/settings/integrations" }, t("web.mv_to_router")));
  } else if (c.id === "dns") {
    body.push(h("p", { class: "muted" }, c.state === "ok" ? t("web.mv_dns_ok", { count: c.clients }) : t("web.mv_dns_todo", { pi: c.pi || "–" })),
      c.state === "ok" ? null : h("p", {}, h("small", {}, t("web.mv_dns_fritz"))));
  } else if (c.id === "vpn") {
    body.push(h("p", { class: "muted" }, c.kind === "none" ? t("web.mv_vpn_none") : c.kind === "name" ? t("web.mv_vpn_name", { endpoint: c.endpoint })
      : c.state === "todo" ? t("web.mv_vpn_changed", { endpoint: c.endpoint, public: c.public }) : t("web.mv_vpn_ip", { endpoint: c.endpoint })),
    h("p", {}, h("small", {}, t("web.mv_vpn_forward", { port: c.port, pi: c.pi || "–" }))));
    if (c.state === "todo" && c.public) body.push(h("button", { class: "primary small", onclick: async () => {
      const password = await askDialog(t("web.confirm_password"), { type: "password" });
      if (!password) return;
      const done = await attempt(() => api("/api/move/endpoint", { method: "POST", body: { password } }));
      if (done) { toast(t("web.mv_vpn_set", { endpoint: done.endpoint })); restartPrompt(); redraw(); }
    } }, icon("check"), t("web.mv_vpn_use", { public: c.public })));
    body.push(h("a", { class: "button small", href: "#/network/vpn" }, t("web.mv_to_vpn")));
  } else if (c.id === "blocks") {
    body.push(h("p", { class: "muted" }, t("web.mv_blocks", { count: c.count })), h("a", { class: "button small", href: "#/network/blocks" }, t("web.mv_to_blocks")));
  }
  return h("li", { class: `mv-check ${c.state}` }, h("span", { class: `badge ${tone}` }, icon(ic)),
    h("div", { class: "grow stack" }, h("strong", {}, t(`web.mv_c_${c.id}`)), ...body));
}

export async function render(box) {
  const after = h("section", { class: "panel stack" });
  const draw = async (withPublic = false) => {
    fill(after, h("p", { class: "muted" }, t("web.mv_checking")));
    const data = await attempt(() => api(`/api/move${withPublic ? "?check=1" : ""}`));
    if (!data) return;
    const net = data.network;
    const changed = data.changes.length > 0;
    fill(after,
      h("div", { class: "row between wrap" }, h("h2", {}, icon("router"), " ", t("web.mv_after")),
        h("button", { onclick: () => draw(true) }, icon("refresh"), t("web.mv_check"))),
      h("div", { class: `mv-banner ${changed ? "changed" : ""}` }, icon(changed ? "alert-triangle" : "home-shield", "lg"),
        h("div", { class: "grow" }, h("strong", {}, changed ? t("web.mv_changed") : t("web.mv_same")),
          changed ? h("p", {}, data.changes.map((c) => t(`web.mv_change_${c}`)).join(" · ")) : null,
          h("small", { class: "muted block" }, t("web.mv_network", { address: net.addresses.join(", ") || "–", gateway: net.gateway || "–", device: net.interface || "–" })))),
      withPublic ? null : h("p", { class: "muted" }, h("small", {}, t("web.mv_public_hint"))),
      h("ul", { class: "list mv-checks" }, data.checks.map((c) => checkItem(c, () => draw(true)))),
      h("div", { class: "row wrap" }, h("button", { class: changed ? "primary" : "", onclick: async () => {
        if (await attempt(() => api("/api/move/home", { method: "POST" }), t("web.mv_home_saved"))) draw();
      } }, icon("home"), t("web.mv_set_home")), h("small", { class: "muted" }, t("web.mv_set_home_hint"))));
    fill(wifiBox, wifiForm(net));
  };
  const wifiBox = h("div");
  fill(box,
    h("p", { class: "muted" }, t("web.mv_intro")),  // the page heading is the Data & backup tab
    h("section", { class: "panel stack" }, h("h2", {}, t("web.mv_before")),
      h("ol", { class: "mv-steps" },
        step(1, t("web.mv_step_package"), h("p", { class: "muted" }, t("web.mv_step_package_hint")), packageForm()),
        step(2, t("web.mv_step_wifi"), wifiBox),
        step(3, t("web.mv_step_router"), h("p", { class: "muted" }, t("web.mv_step_router_hint"))))),
    after);
  await draw();
}
