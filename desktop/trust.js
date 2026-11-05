// Which certificates count. Kept apart from Electron so it can be tested on its own.
const crypto = require("node:crypto");
const net = require("node:net");

function fingerprint(pem) {
  return new crypto.X509Certificate(pem).fingerprint256;
}

// Issued by the pi-guard this app was set up with, valid now, and for this very name.
function issuedByPinned(leafPem, caPem, host, now = new Date()) {
  try {
    const leaf = new crypto.X509Certificate(leafPem);
    const authority = new crypto.X509Certificate(caPem);
    if (!leaf.verify(authority.publicKey)) return false;
    if (now < new Date(leaf.validFrom) || now > new Date(leaf.validTo)) return false;
    const name = host.replace(/^\[|\]$/g, "");
    return Boolean(net.isIP(name) ? leaf.checkIP(name) : leaf.checkHost(name));
  } catch {
    return false;
  }
}

function cleanAddress(text) {
  let address = text.trim().replace(/\/+$/, "");
  if (!/^https?:\/\//i.test(address)) address = `https://${address}`;
  const url = new URL(address);
  if (url.protocol !== "https:") throw new Error("https only");
  // A public name is served on the usual port, the Pi itself on its own.
  const local = !url.hostname.includes(".") || /^[\d.]+$/.test(url.hostname);
  if (!url.port && local) url.port = "8443";
  return url.origin;
}

module.exports = { cleanAddress, fingerprint, issuedByPinned };
