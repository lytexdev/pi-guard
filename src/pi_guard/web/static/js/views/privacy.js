// How VPN devices reach the internet: home line, Mullvad (optionally multi-hop) or an own server,
// AmneziaWG as disguise and the kill switch. One switch per layer; the rest is under "Advanced".
import { api, askDialog, attempt, confirmDialog, fill, h, icon, infoTip, t, toast } from "../api.js";

export function privacyPanels() {
  const root = h("div", { class: "stack privacy" });
  const draw = async () => {
    const [caps, cfg, diag] = await Promise.all([
      attempt(() => api("/api/privacy/capabilities")),
      attempt(() => api("/api/privacy/config")),
      api("/api/privacy/diagnostics").catch(() => null),
    ]);
    if (!caps || !cfg) return;
    fill(root, installNotice(caps), routeCard(cfg, diag), switchesCard(cfg, caps, draw), advanced(cfg, caps, diag, draw));
  };
  draw();
  return root;
}

function installNotice(caps) {
  if (caps.installed) return null;
  return h("section", { class: "panel notice" },
    h("h2", {}, t("web.priv_install_title")),
    h("p", {}, t("web.priv_install_intro")),
    h("pre", { class: "mono wrap" }, "sudo ./scripts/privacy-server.sh install"));
}

const exitHop = (cfg) => (cfg.chain?.hops || []).find((x) => x.enabled !== false) || null;

// One line that says where the traffic leaves, and whether that works right now.
function routeCard(cfg, diag) {
  const hop = exitHop(cfg);
  const selected = cfg.providers?.mullvad?.selected || {};
  const up = Boolean(diag?.exit?.up);
  let way = t("web.pv_way_home");
  if (hop?.provider === "mullvad") way = selected.entry_hostname ? t("web.pv_way_multi", { entry: selected.entry_hostname, exit: selected.hostname }) : t("web.pv_way_mullvad", { exit: selected.hostname || "Mullvad" });
  else if (hop) way = t("web.pv_way_custom", { name: hop.name });
  const state = !hop ? ["", "web.pv_state_home"] : up ? ["ok", "web.pv_state_up"] : ["warn", "web.pv_state_down"];
  return h("section", { class: `panel pv-route ${state[0]}` },
    h("div", { class: "row between wrap" },
      h("div", { class: "row" }, icon(hop ? "world" : "home", "lg"), h("div", {}, h("small", { class: "muted block" }, t("web.pv_way_title")), h("strong", {}, way))),
      h("span", { class: `badge ${state[0]}` }, t(state[1]))),
    diag?.exit_ip ? h("small", { class: "muted" }, t("web.pv_public_ip", { ip: diag.exit_ip })) : null,
    h("p", { class: "muted" }, h("small", {}, t("web.pv_way_hint"))));
}

function switchRow(id, label, hint, checked, disabled, onToggle, extra) {
  const input = h("input", { type: "checkbox", class: "toggle", role: "switch", id, checked, disabled });
  input.addEventListener("change", async () => {
    input.disabled = true;
    const ok = await onToggle(input.checked);
    if (!ok) input.checked = !input.checked;
    input.disabled = disabled;
  });
  return h("div", { class: "switch-row is-bool pv-switch" },
    h("div", { class: "switch-label" }, h("label", { for: id }, label), h("small", { class: "muted block" }, hint), extra || null),
    h("div", { class: "control" }, input));
}

async function setModule(body, redraw) {
  const done = await attempt(() => api("/api/privacy/modules", { method: "PUT", body }), t("web.saved"));
  if (done) redraw();
  return Boolean(done);
}

function switchesCard(cfg, caps, redraw) {
  const mullvad = cfg.providers?.mullvad || {};
  const selected = mullvad.selected || {};
  const hop = exitHop(cfg);
  const mullvadOn = hop?.provider === "mullvad";
  const multiOn = mullvadOn && Boolean(selected.entry_hostname);
  const awg = cfg.amneziawg || {};
  const ready = caps.installed && caps.features?.mullvad?.available;
  const link = (label, onclick) => h("button", { class: "link", type: "button", onclick }, label);

  const mullvadExtra = !mullvad.has_account
    ? h("div", { class: "row wrap" }, h("button", { class: "small primary", disabled: !ready, onclick: () => linkMullvad(redraw) }, icon("link"), t("web.priv_mullvad_link")))
    : h("small", { class: "muted block" },
      selected.hostname ? t("web.pv_mullvad_server", { server: selected.hostname }) : t("web.pv_mullvad_no_server"), " · ",
      link(t("web.pv_change"), () => mullvadPick(redraw, selected)),
      mullvad.expiry ? ` · ${t("web.priv_mullvad_paid_until", { date: new Date(mullvad.expiry).toLocaleDateString() })}` : "");

  return h("section", { class: "panel stack" },
    h("h2", {}, t("web.pv_switches")),
    h("p", { class: "muted" }, t("web.pv_switches_hint")),
    switchRow("pv_mullvad", t("web.pv_mullvad"), t("web.pv_mullvad_hint"), mullvadOn, !ready || !mullvad.has_account, async (on) => {
      if (on && !selected.hostname) { await mullvadPick(redraw, selected); return false; }
      if (!on && !await confirmDialog(t("web.pv_mullvad_off_confirm"), t("web.priv_mod_turn_off"), true)) return false;
      return setModule({ mullvad: on }, redraw);
    }, mullvadExtra),
    switchRow("pv_multihop", t("web.pv_multihop"), t("web.pv_multihop_hint"), multiOn, !ready || !mullvad.has_account, async (on) => {
      if (on && !selected.entry_hostname) { await mullvadPick(redraw, selected, { wantMulti: true }); return false; }
      return setModule({ multi_hop: on }, redraw);
    }, selected.entry_hostname ? h("small", { class: "muted block" }, t("web.pv_multihop_entry", { entry: selected.entry_hostname }), " · ",
      link(t("web.pv_change"), () => mullvadPick(redraw, selected, { wantMulti: true }))) : null),
    switchRow("pv_awg", t("web.pv_awg"), awg.installed ? t("web.pv_awg_hint") : t("web.pv_awg_missing"), Boolean(awg.on), !awg.installed,
      (on) => setModule({ amneziawg: on }, redraw)),
    switchRow("pv_kill", t("web.pv_kill"), t("web.pv_kill_hint"), cfg.kill_switch !== "off", !caps.features?.kill_switch?.available, async (on) => {
      const done = await attempt(() => api("/api/privacy/killswitch", { method: "PUT", body: { mode: on ? "vpn" : "off" } }), t("web.saved"));
      if (done) redraw();
      return Boolean(done);
    }));
}

async function linkMullvad(redraw) {
  const account = await askDialog(t("web.priv_mullvad_account_prompt"));
  if (!account) return;
  const password = await askDialog(t("web.priv_confirm_pw"), { type: "password" });
  if (!password) return;
  if (await attempt(() => api("/api/privacy/providers/mullvad/account", { method: "POST", body: { account, password } }), t("web.priv_mullvad_ok"))) {
    redraw();
    mullvadPick(redraw, {});
  }
}

// Country, city and server from Mullvad's list; an entry server makes it Mullvad's own
// multi-hop (in at one server, out at another). Saving uses the server at once.
async function mullvadPick(redraw, current = {}, opts = {}) {
  const data = await attempt(() => api("/api/privacy/providers/mullvad/relays"));
  if (!data?.countries?.length) { toast(t("web.priv_mullvad_no_relay"), true); return; }
  const byName = (a, b) => a.name.localeCompare(b.name);
  const countries = [...data.countries].sort(byName);
  const picker = (fallback, chosen) => {
    const country = h("select", { "aria-label": t("web.priv_mullvad_country") });
    const city = h("select", { "aria-label": t("web.priv_mullvad_city") });
    const server = h("select", { "aria-label": t("web.priv_mullvad_server_pick") });
    const cityOf = () => countries.find((x) => x.code === country.value)?.cities?.find((x) => x.code === city.value);
    const fillServers = () => fill(server, h("option", { value: "" }, t("web.priv_mullvad_any")),
      ...(cityOf()?.relays || []).map((r) => h("option", { value: r.hostname, selected: r.hostname === chosen.hostname }, r.hostname)));
    const fillCities = () => {
      const c = countries.find((x) => x.code === country.value);
      fill(city, ...[...(c?.cities || [])].sort(byName).map((x) => h("option", { value: x.code, selected: x.code === chosen.city }, x.name)));
      fillServers();
    };
    fill(country, ...countries.map((c) => h("option", { value: c.code, selected: c.code === (chosen.country || fallback) }, c.name)));
    country.addEventListener("change", fillCities);
    city.addEventListener("change", fillServers);
    fillCities();
    // "Any" takes the first server of the city: they are equal for this purpose.
    const value = () => server.value || cityOf()?.relays?.[0]?.hostname || "";
    return { value, box: h("div", { class: "grid three" },
      h("label", { class: "field" }, t("web.priv_mullvad_country"), country), h("label", { class: "field" }, t("web.priv_mullvad_city"), city),
      h("label", { class: "field" }, t("web.priv_mullvad_server_pick"), server)) };
  };
  const exitPick = picker("de", current);
  const multi = h("input", { type: "checkbox", class: "toggle", role: "switch", id: "mv_multihop", checked: Boolean(current.entry_hostname) || Boolean(opts.wantMulti) });
  const entryPick = picker("se", current.entry_hostname ? findRelay(countries, current.entry_hostname) : {});
  const entryBox = h("div", { class: `stack ${multi.checked ? "" : "hidden"}` }, h("h3", {}, t("web.priv_mullvad_entry")), entryPick.box);
  multi.addEventListener("change", () => entryBox.classList.toggle("hidden", !multi.checked));
  const dialog = h("dialog", { class: "editor" },
    h("div", { class: "row between" }, h("h2", {}, t("web.priv_mullvad_pick")), h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))),
    h("div", { class: "stack" },
      h("h3", {}, t("web.priv_mullvad_exit")), h("p", { class: "muted" }, h("small", {}, t("web.priv_mullvad_exit_hint"))), exitPick.box,
      h("div", { class: "switch-row is-bool" }, h("div", { class: "switch-label" }, h("label", { for: "mv_multihop" }, t("web.pv_multihop")),
        h("small", { class: "muted" }, t("web.pv_multihop_hint"))), h("div", { class: "control" }, multi)),
      entryBox),
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const body = { hostname: exitPick.value(), entry_hostname: multi.checked ? entryPick.value() : null };
        if (await attempt(() => api("/api/privacy/providers/mullvad/server", { method: "PUT", body }), t("web.saved"))) { dialog.close(); redraw(); }
      } }, icon("check"), t("web.pv_use_server"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function findRelay(countries, hostname) {
  for (const c of countries) for (const ci of c.cities || []) for (const r of ci.relays || []) if (r.hostname === hostname) return { country: c.code, city: ci.code, hostname };
  return {};
}

// --- advanced: DNS, strict kill switch, own servers, Mullvad account, diagnosis ----------------

function advanced(cfg, caps, diag, redraw) {
  const mullvad = cfg.providers?.mullvad || {};
  return h("details", { class: "panel guide pv-advanced" }, h("summary", {}, icon("settings"), " ", t("web.pv_advanced")),
    h("div", { class: "stack" },
      dnsBox(cfg, caps, redraw),
      strictBox(cfg, caps, redraw),
      customBox(cfg, redraw),
      mullvad.has_account ? h("div", { class: "stack" }, h("h3", {}, t("web.priv_mullvad")),
        h("p", { class: "muted" }, t("web.priv_mullvad_linked", { name: mullvad.device_name || "—" })),
        h("div", {}, h("button", { class: "danger", onclick: async () => {
          if (!await confirmDialog(t("web.pv_unlink_confirm"), t("web.priv_mullvad_unlink"), true)) return;
          const password = await askDialog(t("web.priv_confirm_pw"), { type: "password" });
          if (!password) return;
          if (await attempt(() => api("/api/privacy/providers/mullvad/account", { method: "DELETE", body: { password } }), t("web.saved"))) redraw();
        } }, icon("x"), t("web.priv_mullvad_unlink")))) : null,
      diagnosisBox(diag)));
}

function dnsBox(cfg, caps, redraw) {
  const dns = cfg.dns || {};
  const put = async (body) => { if (await attempt(() => api("/api/privacy/dns", { method: "PUT", body }), t("web.saved"))) redraw(); };
  return h("div", { class: "stack" }, h("h3", {}, t("web.priv_dns_title")), h("p", { class: "muted" }, h("small", {}, t("web.priv_dns_intro"))),
    h("div", { class: "segmented wrap" }, ["system", "custom", "vpn_routed", "provider", "dot"].map((mode) =>
      h("button", { type: "button", class: dns.mode === mode ? "on" : "", disabled: mode === "dot" && !caps.features?.dns_dot?.available, onclick: async () => {
        const body = { ...dns, mode };
        if (mode === "custom" && !(dns.servers || []).length) {
          const server = await askDialog(t("web.priv_dns_server_prompt"));
          if (!server) return;
          body.servers = [server];
        }
        put(body);
      } }, t(`web.priv_dns_${mode}`)))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: Boolean(dns.strict), onchange: (ev) => put({ ...dns, strict: ev.target.checked }) }),
      t("web.priv_dns_strict"), infoTip(t("web.priv_dns_strict_tip"))));
}

function strictBox(cfg, caps, redraw) {
  if (!caps.features?.kill_switch?.available) return null;
  return h("div", { class: "stack" }, h("h3", {}, t("web.pv_strict")),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: cfg.kill_switch === "strict", onchange: async (ev) => {
      const mode = ev.target.checked ? "strict" : (cfg.kill_switch === "off" ? "off" : "vpn");
      if (await attempt(() => api("/api/privacy/killswitch", { method: "PUT", body: { mode } }), t("web.saved"))) redraw();
    } }), t("web.pv_strict_label")),
    h("p", { class: "muted" }, h("small", {}, t("web.priv_ks_recovery"))));
}

function customBox(cfg, redraw) {
  const customs = cfg.providers?.custom || [];
  const hop = exitHop(cfg);
  const ask = () => askDialog(t("web.priv_confirm_pw"), { type: "password" });
  return h("div", { class: "stack" }, h("h3", {}, t("web.priv_custom")), h("p", { class: "muted" }, h("small", {}, t("web.pv_custom_hint"))),
    h("ul", { class: "list" }, customs.length ? customs.map((s) => {
      const inUse = hop?.provider === "custom" && hop.server_id === s.id;
      return h("li", {},
        h("div", { class: "grow" }, h("strong", {}, s.name), inUse ? h("span", { class: "badge ok" }, t("web.pv_in_use")) : null, h("small", { class: "mono muted block" }, s.endpoint)),
        inUse
          ? h("button", { class: "small", onclick: async () => { if (await attempt(() => api(`/api/privacy/chain/hops/${encodeURIComponent(hop.id)}`, { method: "DELETE" }), t("web.saved"))) redraw(); } }, t("web.pv_stop_using"))
          : h("button", { class: "small", onclick: async () => { if (await attempt(() => api("/api/privacy/chain/hops", { method: "POST", body: { provider: "custom", name: s.name, server_id: s.id } }), t("web.saved"))) redraw(); } }, t("web.pv_use_server")),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (!await confirmDialog(t("web.priv_custom_remove", { name: s.name }), t("web.delete"), true)) return;
          const password = await ask();
          if (!password) return;
          if (await attempt(() => api(`/api/privacy/providers/custom/${encodeURIComponent(s.id)}`, { method: "DELETE", body: { password } }))) redraw();
        } }, icon("trash")));
    }) : h("li", { class: "empty" }, t("web.priv_custom_none"))),
    h("div", { class: "row wrap" },
      h("button", { onclick: async () => {
        const config = await askDialog(t("web.priv_import_prompt"), { multiline: true });
        if (!config) return;
        const password = await ask();
        if (!password) return;
        if (await attempt(() => api("/api/privacy/providers/custom/import", { method: "POST", body: { config, password } }), t("web.saved"))) redraw();
      } }, icon("file-text"), t("web.priv_custom_import"))));
}

function bytes(n) {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1000) return `${n} B`;
  if (n < 1e6) return `${(n / 1000).toFixed(1)} KB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
  return `${(n / 1e9).toFixed(2)} GB`;
}

function diagnosisBox(diag) {
  const box = h("dl", { class: "kv" });
  const draw = (d) => fill(box,
    h("dt", {}, t("web.priv_diag_exit")), h("dd", {}, d?.exit?.up ? t("web.priv_diag_up") : t("web.priv_diag_down")),
    h("dt", {}, t("web.priv_diag_endpoint")), h("dd", { class: "mono" }, d?.exit?.endpoint || "—"),
    h("dt", {}, t("web.priv_diag_handshake")), h("dd", {}, d?.exit?.handshake_age != null ? `${d.exit.handshake_age}s` : "—"),
    h("dt", {}, t("web.priv_diag_transfer")), h("dd", {}, `${bytes(d?.exit?.transfer_rx)} ↓ · ${bytes(d?.exit?.transfer_tx)} ↑`),
    h("dt", {}, t("web.priv_diag_fw")), h("dd", {}, d?.firewall?.mode || "—"),
    h("dt", {}, t("web.priv_diag_dns")), h("dd", {}, d?.dns?.mode || "—"),
    h("dt", {}, t("web.priv_diag_public_ip")), h("dd", { class: "mono" }, d?.exit_ip || t("web.priv_diag_ip_unknown")));
  draw(diag);
  return h("div", { class: "stack" }, h("h3", {}, t("web.priv_diag_title")), box,
    h("div", {}, h("button", { onclick: async () => {
      const data = await attempt(() => api("/api/privacy/diagnostics?check=1"));
      if (data) { draw(data); toast(t("web.priv_diag_checked")); }
    } }, icon("refresh"), t("web.priv_diag_check"))));
}
