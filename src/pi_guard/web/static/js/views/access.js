// Who can use pi-guard: chat users, invitations, key files, sessions, bans.
import { api, attempt, confirmDialog, h, icon, t, toast, when, fill } from "../api.js";
import { createCredential } from "../webauthn.js";

function showCodes(codes) {
  const dialog = h("dialog", {}, h("h2", {}, t("web.recovery_title")), h("p", {}, t("web.recovery_save")),
    h("pre", { class: "mono codes" }, codes.join("\n")),
    h("div", { class: "row end" },
      h("button", { onclick: () => navigator.clipboard?.writeText(codes.join("\n")).then(() => toast(t("web.copied"))) }, t("web.copy")),
      h("button", { class: "primary", onclick: () => dialog.close() }, t("web.done"))));
  dialog.addEventListener("close", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

async function loginPanel(account, draw) {
  // Second factors and passkeys.
  const keys = await api("/api/account/webauthn");
  const name = h("input", { placeholder: t("web.webauthn_name"), maxlength: 40, "aria-label": t("web.webauthn_name") });
  return h("section", { class: "panel stack" }, h("h2", {}, icon("shield-lock"), " ", t("web.login_security")),
    h("h3", {}, t("web.webauthn_title")), h("p", { class: "muted" }, t("web.webauthn_hint")),
    keys.usable_here ? null : h("p", { class: "notice" }, t("web.webauthn_needs_name")),
    h("ul", { class: "list" }, keys.keys.length ? keys.keys.map((k) => h("li", {}, icon("key"),
      h("div", { class: "grow" }, h("div", {}, k.name), h("small", {}, k.last_used ? t("web.last_used", { time: when(k.last_used) }) : t("web.never_used"))),
      h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
        if (await confirmDialog(t("web.webauthn_delete", { name: k.name }), t("web.delete"), true)
            && await attempt(() => api(`/api/account/webauthn/${k.id}`, { method: "DELETE" }))) draw();
      } }, icon("trash")))) : h("li", { class: "empty" }, t("web.nothing"))),
    keys.usable_here ? h("div", { class: "row" }, name, h("button", { onclick: async () => {
      const start = await attempt(() => api("/api/account/webauthn/begin", { method: "POST", body: {} }));
      if (!start) return;
      try {
        const credential = await createCredential(start.options);
        if (await attempt(() => api("/api/account/webauthn/finish", { method: "POST", body: { flow: start.flow, credential, name: name.value } }), t("web.saved"))) draw();
      } catch { toast(t("web.webauthn_cancelled"), true); }
    } }, icon("plus"), t("web.webauthn_add"))) : null,
    h("h3", {}, t("web.recovery_title")),
    account.totp ? h("p", { class: "muted" }, t("web.recovery_left", { count: account.recovery_left })) : h("p", { class: "muted" }, t("web.recovery_needs_totp")),
    account.totp ? h("button", { onclick: async () => {
      const password = prompt(t("web.confirm_password"));
      if (!password) return;
      const result = await attempt(() => api("/api/account/recovery", { method: "POST", body: { password } }));
      if (result) { showCodes(result.codes); draw(); }
    } }, icon("refresh"), t("web.recovery_new")) : null,
    h("h3", {}, t("web.codeword")), h("p", { class: "muted" }, t("web.codeword_hint")),
    h("div", { class: "row" }, h("button", { onclick: async () => {
      const codeword = prompt(t("web.codeword_new"));
      if (!codeword) return;
      const password = prompt(t("web.confirm_password"));
      if (password && await attempt(() => api("/api/account/codeword", { method: "POST", body: { password, codeword } }), t("web.saved"))) draw();
    } }, icon("key"), account.codeword ? t("web.codeword_change") : t("web.codeword_set")),
    account.codeword ? h("button", { class: "danger", onclick: async () => {
      const password = prompt(t("web.confirm_password"));
      if (password && await attempt(() => api("/api/account/codeword", { method: "POST", body: { password, codeword: "" } }), t("web.saved"))) draw();
    } }, t("web.codeword_remove")) : null));
}

export async function render(main) {
  const draw = async () => {
    const [users, access, account] = await Promise.all([api("/api/users"), api("/api/access"), api("/api/account")]);
    const security = await loginPanel(account, draw);
    const keyName = h("input", { id: "kname", placeholder: "laptop", pattern: "[A-Za-z0-9_-]{1,40}" });
    const inviteOut = h("div", { class: "stack" });
    fill(main,
      h("div", { class: "topbar" }, h("h1", {}, t("web.nav_access"))),
      h("div", { class: "grid wide" },
        h("section", { class: "panel" }, h("h2", {}, t("web.account")),
          h("p", {}, icon("user"), " ", account.username, " ",
            account.totp ? h("span", { class: "badge ok" }, t("web.totp_on")) : null),
          h("div", { class: "row" },
            h("a", { class: "button", href: "#/setup/account" }, icon("key"), t("web.account_change")),
            account.totp ? h("button", { class: "danger", onclick: async () => {
              const password = prompt(t("web.totp_disable_confirm"));
              if (password && await attempt(() => api("/api/account/totp/disable", { method: "POST", body: { password } }), t("web.saved"))) draw();
            } }, t("web.totp_disable")) : null)),
        security,
        h("section", { class: "panel" }, h("h2", {}, t("web.chat_users")),
          h("ul", { class: "list" }, users.map((u) => h("li", {},
            icon(u.role === "admin" ? "shield-check" : "user"),
            h("div", { class: "grow" }, h("div", {}, u.name || u.user_id), h("small", {}, `${u.provider} · ${u.role}${u.configured ? ` · ${t("web.configured")}` : ""}`)),
            u.configured ? null : h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
              if (await confirmDialog(t("web.remove_user_confirm"), t("web.delete"), true)) {
                await attempt(() => api(`/api/users/${u.provider}/${encodeURIComponent(u.user_id)}`, { method: "DELETE" })); draw();
              } } }, icon("user-x"))))),
          h("div", { class: "row" },
            h("button", { onclick: async () => {
              const r = await attempt(() => api("/api/invite", { method: "POST", body: { role: "viewer" } }));
              if (r) fill(inviteOut, h("p", {}, t("web.invite_code"), " ", h("code", {}, r.code)),
                r.link ? h("p", {}, h("a", { href: r.link, target: "_blank", rel: "noopener" }, r.link)) : null,
                h("small", { class: "muted" }, t("web.invite_expires", { time: when(r.expires) })));
            } }, icon("user-plus"), t("web.invite"))),
          inviteOut),
        h("section", { class: "panel" }, h("h2", {}, t("web.keyfiles")),
          h("p", { class: "muted" }, t("web.keyfiles_hint")),
          h("ul", { class: "list" }, access.keyfiles.length ? access.keyfiles.map((k) => h("li", {}, icon("key"),
            h("div", { class: "grow" }, h("div", {}, k.name), h("small", {}, k.last_used ? t("web.last_used", { time: when(k.last_used) }) : t("web.never_used"))),
            h("button", { class: "icon-only danger", title: t("web.delete"), onclick: async () => {
              await attempt(() => api(`/api/keyfiles/${encodeURIComponent(k.name)}`, { method: "DELETE" })); draw(); } }, icon("trash"))))
            : h("li", { class: "empty" }, t("web.nothing"))),
          h("div", { class: "row" }, keyName, h("button", { onclick: async () => {
            const response = await attempt(() => api("/api/keyfiles", { method: "POST", body: { name: keyName.value }, raw: true }));
            if (!response) return;
            const url = URL.createObjectURL(await response.blob());
            h("a", { href: url, download: `pi-guard-${keyName.value}.key` }).click();
            URL.revokeObjectURL(url); draw();
          } }, icon("download"), t("web.create_keyfile")))),
        h("section", { class: "panel" }, h("h2", {}, t("web.sessions")),
          h("p", {}, t("web.login_methods"), ": ", access.methods.join(", "), access.totp ? " + TOTP" : ""),
          h("ul", { class: "list" }, access.sessions.map((s) => h("li", {}, icon("device-mobile"),
            h("div", { class: "grow" }, h("div", {}, `${s.username} · ${s.ip}`), h("small", {}, `${when(s.last_seen)} · ${s.agent.slice(0, 60)}`))))),
          h("button", { class: "danger", onclick: async () => {
            if (await confirmDialog(t("web.logout_all_confirm"), t("web.logout_all"), true)) {
              await attempt(() => api("/api/sessions/logout-all", { method: "POST" })); location.hash = "#/login";
            } } }, icon("logout"), t("web.logout_all"))),
        h("section", { class: "panel" }, h("h2", {}, t("web.bans")),
          h("ul", { class: "list" }, Object.keys(access.bans).length ? Object.entries(access.bans).map(([ip, seconds]) => h("li", {}, icon("ban"),
            h("span", { class: "grow mono" }, ip), h("small", {}, `${Math.ceil(seconds / 60)} min`),
            h("button", { onclick: async () => { await attempt(() => api(`/api/bans/${encodeURIComponent(ip)}`, { method: "DELETE" })); draw(); } }, t("web.unban"))))
            : h("li", { class: "empty" }, t("web.nothing"))),
          h("p", {}, h("a", { href: "/ca.crt" }, icon("download"), " ", t("web.download_ca"))),
          h("p", { class: "muted" }, h("small", {}, t("web.ca_hint"))))));
  };
  await draw();
}
