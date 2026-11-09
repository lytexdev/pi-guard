// Optional privacy upstream: multi-hop exit, DNS, Tor, profiles, diagnostics.
import { askDialog, api, attempt, confirmDialog, fill, h, icon, t, toast, whileShown } from "../api.js";

function badge(ok, label) {
  return h("span", { class: `badge ${ok ? "ok" : ""}` }, label);
}

function section(titleKey, introKey, ...kids) {
  return h("section", { class: "panel stack privacy-panel" },
    h("h2", {}, t(titleKey)),
    introKey ? h("p", { class: "muted" }, t(introKey)) : null,
    ...kids);
}

export function privacyPanels() {
  const root = h("div", { class: "stack privacy" });
  const draw = async () => {
    const [caps, cfg, diag] = await Promise.all([
      attempt(() => api("/api/privacy/capabilities")),
      attempt(() => api("/api/privacy/config")),
      attempt(() => api("/api/privacy/diagnostics")),
    ]);
    if (!caps || !cfg) return;
    fill(root,
      installNotice(caps),
      profilesPanel(cfg, draw),
      connectionPanel(cfg, diag, draw),
      chainPanel(cfg, caps, draw),
      providersPanel(cfg, caps, draw),
      dnsPanel(cfg, caps, draw),
      killSwitchPanel(cfg, caps, draw),
      torPanel(cfg, caps, draw),
      obfuscationPanel(caps, cfg, draw),
      diagnosticsPanel(diag, draw),
      // Existing home VPN pieces stay below privacy upstream.
      h("div", { class: "privacy-home", "data-fold-default": "open" }));
  };
  draw();
  whileShown(root, async () => {
    const diag = await attempt(() => api("/api/privacy/diagnostics"));
    const box = root.querySelector("[data-diag]");
    if (box && diag) fill(box, diagBody(diag));
  }, 8000);
  return root;
}

function installNotice(caps) {
  if (caps.installed) return null;
  return h("section", { class: "panel notice" },
    h("h2", {}, t("web.priv_install_title")),
    h("p", {}, t("web.priv_install_intro")),
    h("pre", { class: "mono wrap" }, "sudo ./scripts/privacy-server.sh install"));
}

function profilesPanel(cfg, redraw) {
  const ids = ["standard", "enhanced", "maximum", "custom"];
  return section("web.priv_profiles_title", "web.priv_profiles_intro",
    h("div", { class: "segmented" }, ids.map((id) =>
      h("button", {
        type: "button",
        class: cfg.profile === id ? "on" : "",
        onclick: async () => {
          if (await attempt(() => api("/api/privacy/profile", { method: "PUT", body: { profile: id } }), t("web.saved"))) redraw();
        },
      }, t(`web.priv_profile_${id}`)))),
    h("p", { class: "muted" }, h("small", {}, t(`web.priv_profile_${cfg.profile}_intro`))));
}

function connectionPanel(cfg, diag, redraw) {
  const active = cfg.enabled && (cfg.chain?.active || cfg.tor?.enabled);
  return section("web.priv_conn_title", "web.priv_conn_intro",
    h("div", { class: "row between" },
      badge(active, active ? t("web.priv_active") : t("web.priv_inactive")),
      h("div", { class: "row" },
        h("button", { class: "primary", onclick: async () => {
          if (await attempt(() => api("/api/privacy/activate", { method: "POST", body: {} }), t("web.priv_activated"))) redraw();
        } }, icon("player-play"), t("web.priv_activate")),
        h("button", { onclick: async () => {
          if (await attempt(() => api("/api/privacy/deactivate", { method: "POST", body: {} }), t("web.priv_deactivated"))) redraw();
        } }, icon("player-stop"), t("web.priv_deactivate")))),
    h("p", { class: "muted" }, t("web.priv_conn_layers", { layers: (diag?.layers || []).join(", ") || "—" })),
    h("p", { class: "muted" }, h("small", {}, t("web.priv_recovery"))));
}

function chainPanel(cfg, caps, redraw) {
  const hops = cfg.chain?.hops || [];
  const multi = caps.features?.multi_hop;
  return section("web.priv_chain_title", "web.priv_chain_intro",
    !multi?.available ? h("p", { class: "notice" }, t(multi?.reason || "web.priv_need_wg_nft")) : null,
    h("ul", { class: "list chain-hops" }, hops.length ? hops.map((hop, i) =>
      h("li", {},
        h("span", { class: "badge" }, String(i + 1)),
        h("div", { class: "grow" },
          h("strong", {}, hop.name || hop.provider),
          h("small", { class: "muted" }, hop.provider)),
        h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
          if (await attempt(() => api(`/api/privacy/chain/hops/${encodeURIComponent(hop.id)}`, { method: "DELETE" }))) redraw();
        } }, icon("trash")))) : h("li", { class: "empty" }, t("web.priv_chain_empty_ui"))),
    hops.length > 1 ? h("div", { class: "row" },
      h("button", { onclick: async () => {
        const order = hops.map((x) => x.id).reverse();
        if (await attempt(() => api("/api/privacy/chain/reorder", { method: "POST", body: { order } }), t("web.saved"))) redraw();
      } }, t("web.priv_chain_reverse"))) : null,
    h("p", { class: "muted" }, h("small", {}, t("web.priv_chain_limit"))));
}

function providersPanel(cfg, caps, redraw) {
  const mullvad = cfg.providers?.mullvad || {};
  const customs = cfg.providers?.custom || [];
  return section("web.priv_providers_title", "web.priv_providers_intro",
    h("h3", {}, t("web.priv_mullvad")),
    mullvad.has_account
      ? h("div", { class: "stack" },
        h("p", {}, t("web.priv_mullvad_linked", { name: mullvad.device_name || "—" })),
        h("div", { class: "row" },
          h("button", { onclick: () => mullvadPick(redraw) }, t("web.priv_mullvad_pick")),
          h("button", { class: "danger", onclick: async () => {
            const password = await askDialog(t("web.priv_confirm_pw"), { type: "password" });
            if (password == null) return;
            if (await attempt(() => api("/api/privacy/providers/mullvad/account", { method: "DELETE", body: { password } }), t("web.saved"))) redraw();
          } }, t("web.priv_mullvad_unlink"))))
      : h("button", { class: "primary", disabled: !caps.features?.mullvad?.available, onclick: async () => {
        const account = await askDialog(t("web.priv_mullvad_account_prompt"));
        if (account == null) return;
        const password = await askDialog(t("web.priv_confirm_pw"), { type: "password" });
        if (password == null) return;
        if (await attempt(() => api("/api/privacy/providers/mullvad/account", { method: "POST", body: { account, password } }), t("web.priv_mullvad_ok"))) redraw();
      } }, t("web.priv_mullvad_link")),
    h("h3", {}, t("web.priv_custom")),
    h("ul", { class: "list" }, customs.length ? customs.map((s) =>
      h("li", {},
        h("div", { class: "grow" }, h("strong", {}, s.name), h("small", { class: "mono muted" }, s.endpoint)),
        h("button", { onclick: async () => {
          if (await attempt(() => api("/api/privacy/chain/hops", { method: "POST", body: { provider: "custom", name: s.name, server_id: s.id } }), t("web.saved"))) redraw();
        } }, t("web.priv_add_hop")),
        h("button", { class: "icon-only danger", onclick: async () => {
          if (!await confirmDialog(t("web.priv_custom_remove", { name: s.name }), t("web.delete"), true)) return;
          const password = await askDialog(t("web.priv_confirm_pw"), { type: "password" });
          if (password == null) return;
          if (await attempt(() => api(`/api/privacy/providers/custom/${encodeURIComponent(s.id)}`, { method: "DELETE", body: { password } }))) redraw();
        } }, icon("trash")))) : h("li", { class: "empty" }, t("web.priv_custom_none"))),
    h("div", { class: "row" },
      h("button", { onclick: () => customManual(redraw) }, icon("plus"), t("web.priv_custom_add")),
      h("button", { onclick: () => customImport(redraw) }, icon("file-text"), t("web.priv_custom_import"))));
}

async function mullvadPick(redraw) {
  const data = await attempt(() => api("/api/privacy/providers/mullvad/relays"));
  if (!data?.countries?.length) return;
  const country = data.countries[0];
  const city = country.cities?.[0];
  const relay = city?.relays?.[0];
  if (!relay) { toast(t("web.priv_mullvad_no_relay")); return; }
  // Simple first-relay pick; advanced UI can refine later via same API.
  const pick = await askDialog(t("web.priv_mullvad_hostname_prompt", { example: relay.hostname }));
  if (pick == null) return;
  let found = null;
  for (const c of data.countries) {
    for (const ci of c.cities || []) {
      for (const r of ci.relays || []) {
        if (r.hostname === pick.trim()) found = { country: c.code, city: ci.code, ...r };
      }
    }
  }
  if (!found) { toast(t("web.priv_mullvad_server")); return; }
  if (await attempt(() => api("/api/privacy/providers/mullvad/server", { method: "PUT", body: {
    country: found.country, city: found.city, hostname: found.hostname,
    ipv4: found.ipv4, public_key: found.public_key, multihop_port: found.multihop_port,
  } }), t("web.saved"))) redraw();
}

async function customManual(redraw) {
  const name = await askDialog(t("web.priv_server_name_prompt"));
  if (name == null) return;
  const endpoint = await askDialog(t("web.priv_endpoint_prompt"));
  if (endpoint == null) return;
  const public_key = await askDialog(t("web.priv_pubkey_prompt"));
  if (public_key == null) return;
  const address = await askDialog(t("web.priv_address_prompt"));
  if (address == null) return;
  const password = await askDialog(t("web.priv_confirm_pw"), { type: "password" });
  if (password == null) return;
  if (await attempt(() => api("/api/privacy/providers/custom", { method: "POST", body: {
    name, endpoint, public_key, address, password,
  } }), t("web.saved"))) redraw();
}

async function customImport(redraw) {
  const config = await askDialog(t("web.priv_import_prompt"));
  if (config == null) return;
  const password = await askDialog(t("web.priv_confirm_pw"), { type: "password" });
  if (password == null) return;
  if (await attempt(() => api("/api/privacy/providers/custom/import", { method: "POST", body: { config, password } }), t("web.saved"))) redraw();
}

function dnsPanel(cfg, caps, redraw) {
  const dns = cfg.dns || {};
  const modes = ["system", "custom", "vpn_routed", "provider", "dot"];
  return section("web.priv_dns_title", "web.priv_dns_intro",
    h("div", { class: "segmented wrap" }, modes.map((mode) =>
      h("button", {
        type: "button",
        class: dns.mode === mode ? "on" : "",
        disabled: mode === "dot" && !caps.features?.dns_dot?.available,
        onclick: async () => {
          const body = { ...dns, mode };
          if (mode === "custom" && !(dns.servers || []).length) {
            const server = await askDialog(t("web.priv_dns_server_prompt"));
            if (server == null) return;
            body.servers = [server];
          }
          if (await attempt(() => api("/api/privacy/dns", { method: "PUT", body }), t("web.saved"))) redraw();
        },
      }, t(`web.priv_dns_${mode}`)))),
    h("label", { class: "check" },
      h("input", {
        type: "checkbox", checked: !!dns.strict,
        onchange: async (ev) => {
          if (await attempt(() => api("/api/privacy/dns", { method: "PUT", body: { ...dns, strict: ev.target.checked } }), t("web.saved"))) redraw();
          else redraw();
        },
      }), t("web.priv_dns_strict")),
    h("p", { class: "muted" }, h("small", {}, t("web.priv_dns_strict_hint"))));
}

function killSwitchPanel(cfg, caps, redraw) {
  const modes = ["off", "vpn", "strict"];
  return section("web.priv_ks_title", "web.priv_ks_intro",
    !caps.features?.kill_switch?.available ? h("p", { class: "notice" }, t(caps.features?.kill_switch?.reason || "web.priv_need_nft")) : null,
    h("div", { class: "segmented" }, modes.map((mode) =>
      h("button", {
        type: "button",
        class: cfg.kill_switch === mode ? "on" : "",
        onclick: async () => {
          if (await attempt(() => api("/api/privacy/killswitch", { method: "PUT", body: { mode } }), t("web.saved"))) redraw();
        },
      }, t(`web.priv_ks_${mode}`)))),
    h("p", { class: "muted" }, h("small", {}, t("web.priv_ks_recovery"))));
}

function torPanel(cfg, caps, redraw) {
  const tor = cfg.tor || {};
  const avail = caps.features?.tor?.available;
  return section("web.priv_tor_title", "web.priv_tor_intro",
    !avail ? h("p", { class: "notice" }, t(caps.features?.tor?.reason || "web.priv_need_tor")) : null,
    h("p", { class: "muted" }, h("small", {}, t("web.priv_tor_warning"))),
    h("div", { class: "segmented" }, ["off", "socks", "vpn_then_tor"].map((mode) =>
      h("button", {
        type: "button",
        class: (tor.mode || "off") === mode ? "on" : "",
        disabled: !avail && mode !== "off",
        onclick: async () => {
          if (await attempt(() => api("/api/privacy/tor", { method: "PUT", body: { ...tor, mode, enabled: mode !== "off" } }), t("web.saved"))) redraw();
        },
      }, t(`web.priv_tor_${mode}`)))),
    avail ? h("button", { onclick: async () => {
      const bridges = await askDialog(t("web.priv_tor_bridges_prompt"));
      if (bridges == null) return;
      if (await attempt(() => api("/api/privacy/tor", { method: "PUT", body: { ...tor, bridges, use_bridges: !!bridges.trim(), enabled: true, mode: tor.mode === "off" ? "socks" : tor.mode } }), t("web.saved"))) redraw();
    } }, t("web.priv_tor_bridges")) : null);
}

function obfuscationPanel(caps, cfg, redraw) {
  const modes = caps.obfuscation?.modes || [];
  const current = cfg.obfuscation?.mode || "off";
  return section("web.priv_obfs_title", "web.priv_obfs_intro",
    h("p", { class: "muted" }, h("small", {}, t("web.priv_obfs_warning"))),
    h("div", { class: "segmented wrap" }, modes.map((m) =>
      h("button", {
        type: "button",
        class: current === m.id ? "on" : "",
        disabled: !m.available && m.id !== "off" && m.id !== "auto",
        title: m.reason ? t(m.reason) : "",
        onclick: async () => {
          if (await attempt(() => api("/api/privacy/obfuscation", { method: "PUT", body: { mode: m.id } }), t("web.saved"))) redraw();
        },
      }, t(`web.priv_obfs_${m.id}`)))));
}

function diagBody(diag) {
  return [
    h("dl", { class: "kv" },
      h("dt", {}, t("web.priv_diag_layers")), h("dd", {}, (diag.layers || []).join(", ") || "—"),
      h("dt", {}, t("web.priv_diag_exit")), h("dd", {}, diag.exit?.up ? t("web.priv_diag_up") : t("web.priv_diag_down")),
      h("dt", {}, t("web.priv_diag_handshake")), h("dd", {}, diag.exit?.handshake_age != null ? `${diag.exit.handshake_age}s` : "—"),
      h("dt", {}, t("web.priv_diag_fw")), h("dd", {}, diag.firewall?.mode || "—"),
      h("dt", {}, t("web.priv_diag_exit_ip")), h("dd", { class: "mono" }, diag.exit_ip || "—"),
      h("dt", {}, t("web.priv_diag_load")), h("dd", {}, diag.resources?.load1 != null ? String(diag.resources.load1) : "—")),
  ];
}

function diagnosticsPanel(diag, redraw) {
  const box = h("div", { "data-diag": "1" }, ...(diag ? diagBody(diag) : []));
  return section("web.priv_diag_title", "web.priv_diag_intro", box,
    h("button", { onclick: async () => {
      const data = await attempt(() => api("/api/privacy/diagnostics?check=1"));
      if (data) { fill(box, diagBody(data)); toast(t("web.priv_diag_checked")); }
    } }, icon("refresh"), t("web.priv_diag_check")));
}

