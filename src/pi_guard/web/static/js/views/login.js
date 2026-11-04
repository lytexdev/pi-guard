// Login: password and/or key file, then TOTP if configured.
import { api, h, t, toast, fill } from "../api.js";
import { state } from "../app.js";

export async function render(root) {
  const methods = state.session.methods;
  const panel = h("div", { class: "panel" },
    h("div", { class: "brand" }, h("img", { src: "/static/img/logo.svg", alt: "" }), "pi-guard"));
  root.append(h("div", { class: "login" }, panel));

  const done = async (result) => {
    if (result.totp_required) { totpForm(); return; }
    location.hash = "#/overview";
  };
  const fail = (error) => toast(error.status === 429 ? t("web.login_rate_limited") : t("web.login_failed"), true);

  function totpForm() {
    const code = h("input", { id: "code", inputmode: "numeric", autocomplete: "one-time-code",
                              pattern: "[0-9 ]*", maxlength: 7, required: true });
    const form = h("form", {},
      h("h1", {}, t("web.totp_title")),
      h("p", { class: "muted" }, t("web.totp_hint")),
      h("div", { class: "field" }, h("label", { for: "code" }, t("web.totp_code")), code),
      h("button", { class: "primary big", type: "submit" }, t("web.login")));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try { await api("/api/login/totp", { method: "POST", body: { code: code.value } }); location.hash = "#/overview"; }
      catch (error) { fail(error); code.select(); }
    });
    fill(panel, panel.firstChild, form);
    code.focus();
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
  panel.append(h("p", { class: "muted" }, h("small", {}, "pi-guard · made by lytex")));
}
