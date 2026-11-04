// WebAuthn: the server speaks base64url JSON, the browser ArrayBuffers.
import { t } from "./api.js";

// The browser's own words stay visible: "NotAllowedError" alone covers very different causes.
export function explainKeyError(error) {
  const text = `${error?.name || ""} ${error?.message || ""}`.trim();
  const key = /certificate/i.test(text) ? "web.webauthn_err_cert"
    : { NotAllowedError: "web.webauthn_err_denied", InvalidStateError: "web.webauthn_err_known",
        SecurityError: "web.webauthn_err_host", NotSupportedError: "web.webauthn_err_support" }[error?.name];
  return key ? `${t(key)} (${text})` : text || t("web.error");
}

const toBytes = (text) => Uint8Array.from(atob(text.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(text.length / 4) * 4, "=")), (c) => c.charCodeAt(0));
const toText = (buffer) => btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const list = (items) => (items || []).map((item) => ({ ...item, id: toBytes(item.id) }));

export async function createCredential(options) {
  const key = await navigator.credentials.create({ publicKey: {
    ...options, challenge: toBytes(options.challenge), user: { ...options.user, id: toBytes(options.user.id) },
    excludeCredentials: list(options.excludeCredentials),
  } });
  return { id: key.id, rawId: toText(key.rawId), type: key.type, clientExtensionResults: key.getClientExtensionResults(),
    response: { clientDataJSON: toText(key.response.clientDataJSON), attestationObject: toText(key.response.attestationObject),
                transports: key.response.getTransports?.() || [] } };
}

export async function getCredential(options) {
  const key = await navigator.credentials.get({ publicKey: {
    ...options, challenge: toBytes(options.challenge), allowCredentials: list(options.allowCredentials),
  } });
  return { id: key.id, rawId: toText(key.rawId), type: key.type, clientExtensionResults: key.getClientExtensionResults(),
    response: { clientDataJSON: toText(key.response.clientDataJSON), authenticatorData: toText(key.response.authenticatorData),
                signature: toText(key.response.signature), userHandle: key.response.userHandle ? toText(key.response.userHandle) : null } };
}
