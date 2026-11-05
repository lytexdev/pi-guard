// What counts as a release. Your pi-guard only passes files on: they must carry the signature of
// the key below, whose private half never comes to a pi-guard. Older versions are refused too.
const crypto = require("node:crypto");

const PUBLIC_KEY = "fN8bID93R4A9BOr3zcUjUMaE9uWzsOpkzRY2tZO4txY=";
const SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex"); // "this is an Ed25519 key"

const publicKey = (raw = PUBLIC_KEY) =>
  crypto.createPublicKey({ key: Buffer.concat([SPKI_PREFIX, Buffer.from(raw, "base64")]), format: "der", type: "spki" });

function parts(version) {
  return /^\d+\.\d+\.\d+$/.test(version || "") ? version.split(".").map(Number) : null;
}

function newer(candidate, current) {
  const [a, b] = [parts(candidate), parts(current)];
  if (!a || !b) return false;
  const index = a.findIndex((value, i) => value !== b[i]);
  return index >= 0 && a[index] > b[index];
}

function signed(manifest, platform, key = publicKey()) {
  try {
    const message = `pi-guard-update|desktop|${platform}|${manifest.version}|${manifest.sha256}`;
    return crypto.verify(null, Buffer.from(message), key, Buffer.from(manifest.signature, "hex"));
  } catch {
    return false;
  }
}

function platformName(platform = process.platform, arch = process.arch) {
  return `${{ linux: "linux", win32: "win", darwin: "mac" }[platform]}-${arch}`;
}

module.exports = { newer, platformName, publicKey, signed };
