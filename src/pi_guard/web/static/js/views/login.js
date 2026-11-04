// Login: password, key file, passkey or Telegram; then TOTP and the code word if configured.
import { api, h, icon, t, toast, fill } from "../api.js";
import { state } from "../app.js";
import { getCredential } from "../webauthn.js";

export async function render(root) {
  const methods = state.session.methods;
  const panel = h("div", { class: "panel" },
    h("div", { class: "brand" }, h("img", { src: "/static/img/logo.svg", alt: "" }), "pi-guard"));
  root.append(h("div", { class: "login" }, panel));

  const done = (result) => next(result);
  const fail = (error) => toast(error.status === 429 ? t("web.login_rate_limited") : t("web.login_failed"), true);

  function totpForm() {
    // What is still missing: TOTP or a recovery code, and the code word if one is set.
    const session = state.session;
    const needsCode = session.pending_level !== 2 && session.totp;
    const code = h("input", { id: "code", autocomplete: "one-time-code", maxlength: 24, required: true });
    const word = h("input", { id: "word", type: "password", autocomplete: "off", required: true });
    const form = h("form", {},
      h("h1", {}, t("web.totp_title")),
      needsCode ? [h("p", { class: "muted" }, t("web.totp_hint")),
        h("div", { class: "field" }, h("label", { for: "code" }, t("web.totp_code")), code,
          h("div", { class: "hint" }, t("web.recovery_hint")))] : null,
      session.codeword ? h("div", { class: "field" }, h("label", { for: "word" }, t("web.codeword")), word) : null,
      h("button", { class: "primary big", type: "submit" }, t("web.login")));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try { await api("/api/login/totp", { method: "POST", body: { code: code.value, codeword: word.value } }); location.hash = "#/overview"; location.reload(); }
      catch (error) { fail(error); code.select(); }
    });
    fill(panel, panel.firstChild, form);
    (needsCode ? code : word).focus();
  }

  async function next(result) {
    if (result.totp_required) { state.session = await api("/api/session"); totpForm(); return; }
    location.hash = "#/overview";
    location.reload();
  }

  function telegramWait(start) {
    const info = h("p", { class: "muted" }, t("web.tg_login_wait"));
    fill(panel, panel.firstChild, h("h1", {}, t("web.tg_login_title")), h("div", { class: "stat center" }, start.number), info,
      h("button", { onclick: () => { stop = true; location.reload(); } }, t("web.cancel")));
    let stop = false;
    const poll = async () => {
      if (stop) return;
      let answer;
      try { answer = await api("/api/login/telegram/poll", { method: "POST", body: { poll: start.poll } }); }
      catch { setTimeout(poll, 3000); return; }
      if (answer.ok) { next(answer); return; }
      if (answer.state === "waiting") { setTimeout(poll, 2000); return; }
      fill(info, t(answer.state === "denied" ? "web.tg_login_refused" : "web.tg_login_expired"));
    };
    poll();
  }

  if (state.session.totp_pending) { totpForm(); return; }

  if (methods.includes("password")) {
    const user = h("input", { id: "user", autocomplete: "username", required: true, value: "" });
    const pass = h("input", { id: "pass", type: "password", autocomplete: "current-password", required: true });
    const honey = h("input", { name: "website", tabindex: "-1", autocomplete: "off" });
    const form = h("form", {},
      h("h1", {}, t("web.login_title")),
      h("div", { class: "field" }, h("label", { for: "user" }, t("web.username")), user),
      h("div", { class: "field" }, h("label", { for: "pass" }, t("web.password")), pass),
      h("div", { class: "honey", "aria-hidden": "true" }, h("label", {}, "Website"), honey),
      h("button", { class: "primary big", type: "submit" }, t("web.login")));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        done(await api("/api/login", { method: "POST",
          body: { username: user.value, password: pass.value, website: honey.value } }));
      } catch (error) { fail(error); pass.select(); }
    });
    panel.append(form);
  }
  if (methods.includes("keyfile")) {
    const file = h("input", { id: "keyfile", type: "file", accept: ".key,text/plain" });
    file.addEventListener("change", async () => {
      const content = await file.files[0]?.text();
      if (!content) return;
      try { done(await api("/api/login", { method: "POST", body: { keyfile: content } })); }
      catch (error) { fail(error); file.value = ""; }
    });
    panel.append(h("div", { class: "field" },
      h("h3", {}, t("web.keyfile_login")),
      h("label", { for: "keyfile" }, t("web.keyfile_choose")), file,
      h("div", { class: "hint" }, t("web.keyfile_hint"))));
  }
  if (methods.includes("webauthn") && window.PublicKeyCredential) {
    panel.append(h("button", { class: "big", onclick: async () => {
      try {
        const { flow, options } = await api("/api/login/webauthn/begin", { method: "POST", body: {} });
        const credential = await getCredential(options);
        done(await api("/api/login/webauthn/finish", { method: "POST", body: { flow, credential } }));
      } catch (error) {
        toast(error.status === 409 ? t(error.message) : error.name === "NotAllowedError" ? t("web.webauthn_cancelled") : t("web.login_failed"), true);
      }
    } }, icon("key"), t("web.webauthn_login")));
  }
  if (methods.includes("telegram")) {
    panel.append(h("button", { class: "big", onclick: async () => {
      try { telegramWait(await api("/api/login/telegram", { method: "POST", body: {} })); }
      catch (error) { fail(error); }
    } }, icon("device-mobile"), t("web.tg_login_button")));
  }
  panel.append(h("p", { class: "muted" }, h("small", {}, "pi-guard · made by ", h("a", { href: "https://links.lytex.de", target: "_blank", rel: "noopener" }, "lytex"))));
}
