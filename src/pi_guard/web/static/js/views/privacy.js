// Optional privacy upstream: multi-hop exit, DNS, Tor, profiles, diagnostics.
import { api, askDialog, attempt, confirmDialog, fill, h, icon, infoTip, t, toast, whileShown } from "../api.js";

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
      chainPanel(cfg, caps, draw, diag),
      providersPanel(cfg, caps, draw),
      dnsPanel(cfg, caps, draw),
      killSwitchPanel(cfg, caps, draw),
      torPanel(cfg, caps, draw),
      obfuscationPanel(caps, cfg, draw),
      diagnosticsPanel(diag, draw),
      h("div", { class: "privacy-home", "data-fold-default": "open" }));
  };
  draw().then(async () => {
    // One soft public-IP probe so Diagnose is not empty on first open.
    const first = await attempt(() => api("/api/privacy/diagnostics"));
    if (first && !first.exit_ip) {
      const checked = await attempt(() => api("/api/privacy/diagnostics?check=1"));
      const box = root.querySelector("[data-diag]");
      if (box && checked) fill(box, diagBody(checked));
    }
  });
  whileShown(root, async () => {
    const next = await attempt(() => api("/api/privacy/diagnostics"));
    const box = root.querySelector("[data-diag]");
    if (box && next) fill(box, diagBody(next));
    const track = root.querySelector(".chain-track");
    if (track && next) {
      track.classList.toggle("on", Boolean(next.enabled && (next.chain_active || next.tor?.enabled)));
      track.querySelectorAll(".chain-link").forEach((el) => {
        el.classList.toggle("live", Boolean(next.enabled && (next.chain_active || next.tor?.enabled)));
      });
    }
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

function chainPath(cfg, diag) {
  // Prefer the server path (includes the home VPN hop); fall back to a local build.
  if (diag?.path?.length) return diag.path;
  const hops = (cfg.chain?.hops || []).filter((h) => h.enabled !== false);
  return [
    { id: "clients", kind: "clients", label: "clients", title_key: "web.priv_hop_clients", status: "ok", fixed: true },
    { id: "home", kind: "home_vpn", label: "Pi-Guard", title_key: "web.priv_hop_home", status: "ok", fixed: true, iface: "wg0" },
    ...hops.map((hop) => ({
      id: hop.id, kind: "exit", label: hop.name || hop.provider, provider: hop.provider,
      status: cfg.chain?.active ? "pending" : "idle", fixed: false,
    })),
    ...(cfg.tor?.enabled ? [{ id: "tor", kind: "tor", label: "Tor", title_key: "web.priv_hop_tor", status: "pending", fixed: false }] : []),
    { id: "internet", kind: "internet", label: "Internet", title_key: "web.priv_hop_internet", status: "ok", fixed: true },
  ];
}

function hopIcon(kind) {
  return ({ clients: "device-mobile", home_vpn: "shield-lock", exit: "world", tor: "network", internet: "world" })[kind] || "router";
}

function hopTitle(node) {
  if (node.title_key) return t(node.title_key);
  if (node.kind === "exit") return node.label || node.provider || t("web.priv_hop_exit");
  return node.label || node.kind;
}

function chainPanel(cfg, caps, redraw, diag) {
  const hops = cfg.chain?.hops || [];
  const multi = caps.features?.multi_hop;
  const path = chainPath(cfg, diag);
  const active = cfg.enabled && (cfg.chain?.active || cfg.tor?.enabled);
  const detail = h("div", { class: "chain-detail muted", "aria-live": "polite" }, t("web.priv_chain_tap"));
  const show = (node) => {
    const lines = [
      hopTitle(node),
      node.iface ? `wg0` : null,
      node.provider || null,
      node.endpoint || null,
      node.detail_key ? t(node.detail_key) : null,
      t(`web.priv_hop_status_${node.status || "idle"}`),
    ].filter(Boolean);
    fill(detail, h("strong", {}, hopTitle(node)), h("div", {}, ...lines.slice(1).map((x) => h("div", {}, x))),
      !node.fixed && node.kind === "exit" ? h("button", { class: "danger", onclick: async () => {
        if (await attempt(() => api(`/api/privacy/chain/hops/${encodeURIComponent(node.id)}`, { method: "DELETE" }))) redraw();
      } }, icon("trash"), t("web.delete")) : null);
  };
  const track = h("div", {
    class: `chain-track ${active ? "on" : ""}`,
    role: "list",
    "aria-label": t("web.priv_chain_title"),
  }, path.flatMap((node, i) => {
    const hop = h("button", {
      type: "button",
      role: "listitem",
      class: `chain-hop ${node.kind} ${node.status || "idle"}`,
      onclick: () => show(node),
    }, icon(hopIcon(node.kind)), h("span", {}, hopTitle(node)),
      node.provider ? h("small", {}, node.provider) : (node.iface ? h("small", {}, node.iface) : null));
    if (i === 0) return [hop];
    return [h("div", { class: `chain-link ${active ? "live" : ""}`, "aria-hidden": "true" }, h("span", {})), hop];
  }));
  return section("web.priv_chain_title", "web.priv_chain_intro",
    !multi?.available ? h("p", { class: "notice" }, t(multi?.reason || "web.priv_need_wg_nft")) : null,
    track, detail,
    hops.length > 1 ? h("div", { class: "row" },
      h("button", { onclick: async () => {
        const order = hops.map((x) => x.id).reverse();
        if (await attempt(() => api("/api/privacy/chain/reorder", { method: "POST", body: { order } }), t("web.saved"))) redraw();
      } }, t("web.priv_chain_reverse"))) : null,
    !hops.length ? h("p", { class: "muted" }, h("small", {}, t("web.priv_chain_empty_ui"))) : null,
    h("p", { class: "muted" }, h("small", {}, t("web.priv_chain_limit"))));
}

function providersPanel(cfg, caps, redraw) {
  const mullvad = cfg.providers?.mullvad || {};
  const customs = cfg.providers?.custom || [];
  return section("web.priv_providers_title", "web.priv_providers_intro",
    h("h3", {}, t("web.priv_mullvad")),
    mullvad.has_account
      ? h("div", { class: "stack" },
        h("p", {}, t("web.priv_mullvad_linked", { name: mullvad.device_name || "—" }),
          mullvad.expiry ? h("small", { class: "muted block" }, t("web.priv_mullvad_paid_until", { date: new Date(mullvad.expiry).toLocaleDateString() })) : null),
        mullvad.selected?.hostname ? h("p", {}, h("span", { class: "badge accent" }, icon("world"), " ",
          mullvad.selected.entry_hostname ? `${mullvad.selected.entry_hostname} → ${mullvad.selected.hostname}` : mullvad.selected.hostname),
          mullvad.selected.entry_hostname ? h("small", { class: "muted block" }, t("web.priv_mullvad_multihop_on")) : null) : null,
        h("div", { class: "row" },
          h("button", { onclick: () => mullvadPick(redraw, mullvad.selected || {}) }, icon("world"), t("web.priv_mullvad_pick")),
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

// Country, city and server from Mullvad's list; an entry server makes it Mullvad's own
// multi-hop (in at one server, out at another). The Pi takes keys and ports from the list.
async function mullvadPick(redraw, current = {}) {
  const data = await attempt(() => api("/api/privacy/providers/mullvad/relays"));
  if (!data?.countries?.length) { toast(t("web.priv_mullvad_no_relay"), true); return; }
  const byName = (a, b) => a.name.localeCompare(b.name);
  const countries = [...data.countries].sort(byName);
  const picker = (prefix, chosen) => {
    const country = h("select", { "aria-label": t("web.priv_mullvad_country") });
    const city = h("select", { "aria-label": t("web.priv_mullvad_city") });
    const server = h("select", { "aria-label": t("web.priv_mullvad_server_pick") });
    const fillCities = () => {
      const c = countries.find((x) => x.code === country.value);
      fill(city, ...[...(c?.cities || [])].sort(byName).map((x) => h("option", { value: x.code, selected: x.code === chosen.city }, x.name)));
      fillServers();
    };
    const fillServers = () => {
      const c = countries.find((x) => x.code === country.value)?.cities?.find((x) => x.code === city.value);
      fill(server, h("option", { value: "" }, t("web.priv_mullvad_any")),
        ...(c?.relays || []).map((r) => h("option", { value: r.hostname, selected: r.hostname === chosen.hostname }, r.hostname)));
    };
    fill(country, ...countries.map((c) => h("option", { value: c.code, selected: c.code === (chosen.country || (prefix === "exit" ? "de" : "se")) }, c.name)));
    country.addEventListener("change", fillCities);
    city.addEventListener("change", fillServers);
    fillCities();
    // "Any" takes the first server of the city: they are equal for this purpose.
    const value = () => server.value || countries.find((x) => x.code === country.value)?.cities?.find((x) => x.code === city.value)?.relays?.[0]?.hostname || "";
    return { value, box: h("div", { class: "grid three" },
      h("label", { class: "field" }, t("web.priv_mullvad_country"), country), h("label", { class: "field" }, t("web.priv_mullvad_city"), city),
      h("label", { class: "field" }, t("web.priv_mullvad_server_pick"), server)) };
  };
  const exitPick = picker("exit", { country: current.country, city: current.city, hostname: current.hostname });
  const entryWanted = h("input", { type: "checkbox", class: "toggle", role: "switch", id: "mv_multihop", checked: Boolean(current.entry_hostname) });
  const entryRelay = current.entry_hostname ? findRelay(countries, current.entry_hostname) : {};
  const entryPick = picker("entry", entryRelay);
  const entryBox = h("div", { class: `stack ${entryWanted.checked ? "" : "hidden"}` }, h("h3", {}, t("web.priv_mullvad_entry")), entryPick.box);
  entryWanted.addEventListener("change", () => entryBox.classList.toggle("hidden", !entryWanted.checked));
  const dialog = h("dialog", { class: "editor" },
    h("div", { class: "row between" }, h("h2", {}, t("web.priv_mullvad_pick")), h("button", { class: "icon-only", title: t("web.close"), onclick: () => dialog.close() }, icon("x"))),
    h("div", { class: "stack" },
      h("h3", {}, t("web.priv_mullvad_exit")), h("p", { class: "muted" }, h("small", {}, t("web.priv_mullvad_exit_hint"))), exitPick.box,
      h("div", { class: "switch-row is-bool" }, h("div", { class: "switch-label" }, h("label", { for: "mv_multihop" }, t("web.priv_mullvad_multihop")),
        h("small", { class: "muted" }, t("web.priv_mullvad_multihop_hint"))), h("div", { class: "control" }, entryWanted)),
      entryBox),
    h("div", { class: "row end" }, h("button", { onclick: () => dialog.close() }, t("web.cancel")),
      h("button", { class: "primary", onclick: async () => {
        const body = { hostname: exitPick.value(), entry_hostname: entryWanted.checked ? entryPick.value() : null };
        if (await attempt(() => api("/api/privacy/providers/mullvad/server", { method: "PUT", body }), t("web.saved"))) { dialog.close(); redraw(); }
      } }, icon("check"), t("web.save"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

function findRelay(countries, hostname) {
  for (const c of countries) for (const ci of c.cities || []) for (const r of ci.relays || []) if (r.hostname === hostname) return { country: c.code, city: ci.code, hostname };
  return {};
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
      }), t("web.priv_dns_strict"), infoTip(t("web.priv_dns_strict_tip"))),
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

function bytes(n) {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1000) return `${n} B`;
  if (n < 1e6) return `${(n / 1000).toFixed(1)} KB`;
  if (n < 1e9) return `${(n / 1e6).toFixed(1)} MB`;
  return `${(n / 1e9).toFixed(2)} GB`;
}

function diagBody(diag) {
  if (!diag) return [h("p", { class: "muted" }, t("web.priv_diag_loading"))];
  const ipLabel = diag.exit_ip_kind === "public" ? t("web.priv_diag_public_ip") : t("web.priv_diag_exit_ip");
  const ipValue = diag.exit_ip
    ? `${diag.exit_ip}${diag.exit_ip_cached ? ` (${t("web.priv_diag_cached", { age: diag.exit_ip_age ?? 0 })})` : ""}`
    : t("web.priv_diag_ip_unknown");
  const mem = diag.resources?.memory_available != null && diag.resources?.memory_total != null
    ? `${bytes(diag.resources.memory_available)} / ${bytes(diag.resources.memory_total)}`
    : "—";
  return [
    diag.notes?.length ? h("ul", { class: "list compact" }, diag.notes.map((key) => h("li", { class: "notice" }, t(key)))) : null,
    h("div", { class: "chain-mini", "aria-hidden": "true" },
      ...(diag.path || []).flatMap((node, i) => {
        const chip = h("span", { class: `chain-chip ${node.status || ""}` }, hopTitle(node));
        return i ? [h("span", { class: "chain-mini-link" }, "→"), chip] : [chip];
      })),
    h("dl", { class: "kv" },
      h("dt", {}, t("web.priv_diag_layers")), h("dd", {}, (diag.layers || []).join(", ") || "—"),
      h("dt", {}, t("web.priv_diag_exit")), h("dd", {},
        !diag.exit?.has_status && !diag.installed ? t("web.priv_diag_need_install")
          : (diag.exit?.up ? t("web.priv_diag_up") : t("web.priv_diag_down"))),
      h("dt", {}, t("web.priv_diag_endpoint")), h("dd", { class: "mono" }, diag.exit?.endpoint || "—"),
      h("dt", {}, t("web.priv_diag_handshake")), h("dd", {}, diag.exit?.handshake_age != null ? `${diag.exit.handshake_age}s` : "—"),
      h("dt", {}, t("web.priv_diag_transfer")), h("dd", {}, `${bytes(diag.exit?.transfer_rx)} ↓ · ${bytes(diag.exit?.transfer_tx)} ↑`),
      h("dt", {}, t("web.priv_diag_fw")), h("dd", {}, diag.firewall?.mode || "—"),
      h("dt", {}, t("web.priv_diag_dns")), h("dd", {}, diag.dns?.mode || "—"),
      h("dt", {}, t("web.priv_diag_tor")), h("dd", {}, diag.tor?.enabled
        ? (diag.tor.running ? t("web.priv_diag_tor_up", { pct: diag.tor.bootstrap ?? "—" }) : t("web.priv_diag_tor_down"))
        : t("web.priv_diag_tor_off")),
      h("dt", {}, ipLabel), h("dd", { class: "mono" }, ipValue),
      h("dt", {}, t("web.priv_diag_load")), h("dd", {}, diag.resources?.load1 != null ? String(diag.resources.load1) : "—"),
      h("dt", {}, t("web.priv_diag_mem")), h("dd", {}, mem)),
  ];
}

function diagnosticsPanel(diag, redraw) {
  const box = h("div", { "data-diag": "1" }, ...(diag ? diagBody(diag) : []));
  return section("web.priv_diag_title", "web.priv_diag_intro", box,
    h("div", { class: "row" },
      h("button", { class: "primary", onclick: async () => {
        const data = await attempt(() => api("/api/privacy/diagnostics?check=1"));
        if (data) { fill(box, diagBody(data)); toast(t("web.priv_diag_checked")); }
      } }, icon("refresh"), t("web.priv_diag_check")),
      h("button", { onclick: async () => {
        const data = await attempt(() => api("/api/privacy/diagnostics"));
        if (data) fill(box, diagBody(data));
      } }, t("web.priv_diag_refresh"))));
}

