// Connecting this computer to pi-guard with the agent (Linux): what the app writes and checks.
// Kept apart from Electron so it can be tested on its own. The app never runs as root: one
// fixed script below does the installing, started through pkexec, which asks the system for
// the password.
const crypto = require("node:crypto");
const { publicKey } = require("./updates.js");

const PARTS = ["media", "files", "ring", "text"];
const USER = /^[a-z_][a-z0-9_-]{0,31}$/;

// The agent names its platform like Rust does (std::env::consts::ARCH).
function agentPlatform(arch = process.arch) {
  return { x64: "linux-x86_64", arm64: "linux-aarch64", arm: "linux-arm" }[arch] || null;
}

function signedAgent(manifest, platform, key = publicKey()) {
  try {
    const message = `pi-guard-update|agent|${platform}|${manifest.version}|${manifest.sha256}`;
    return /^[0-9a-f]{64}$/.test(manifest.sha256)
      && crypto.verify(null, Buffer.from(message), key, Buffer.from(manifest.signature, "hex"));
  } catch {
    return false;
  }
}

// agent.conf as agent/install.sh writes it, with the choices made in the app.
function agentConfig({ url, token, user, power = true, docker = false, connect = [] }) {
  if (!/^https:\/\/[^\s/]+$/.test(url)) throw new Error("bad address");
  if (!/^pga_[A-Za-z0-9_-]{20,}$/.test(token)) throw new Error("bad token");
  const parts = connect.filter((p) => PARTS.includes(p));
  if (parts.length && !USER.test(user || "")) throw new Error("bad user name");
  if (user === "root" && parts.length) throw new Error("not as root");
  return [
    `URL=${url}`,
    `TOKEN=${token}`,
    "CA_FILE=/etc/pi-guard-agent/ca.crt",
    `ALLOW_POWER=${power ? "true" : "false"}`,
    `ALLOW_DOCKER=${docker ? "true" : "false"}`,
    "AUTO_UPDATE=true",
    ...(parts.length ? [`CONNECT_USER=${user}`, `CONNECT=${parts.join(",")}`] : []),
    "",
  ].join("\n");
}

// The service, as agent/pi-guard-agent.service (a test keeps them equal).
const UNIT = "[Unit]\nDescription=pi-guard agent\nAfter=network-online.target\nWants=network-online.target\n\n[Service]\nExecStart=/usr/local/bin/pi-guard-agent /etc/pi-guard-agent/agent.conf\n# Log of the last maintenance job (updates, Compose, Git).\nStateDirectory=pi-guard-agent\nRestart=always\nRestartSec=5\n# Needs root for shutdown and Docker, but nothing else of the system.\nNoNewPrivileges=yes\n# Git repositories under /home need `ProtectHome=no` in a drop-in (systemctl edit pi-guard-agent).\nProtectHome=yes\nPrivateTmp=yes\nProtectKernelTunables=yes\nProtectKernelModules=yes\nProtectControlGroups=yes\n\n[Install]\nWantedBy=multi-user.target\n";

// Run as root through pkexec, with the files as arguments: $1 agent, $2 its SHA-256, $3 agent.conf,
// $4 the pinned certificate authority, $5 the unit. It checks the agent again, because the files
// lie in the person's own folder until now.
const INSTALL = String.raw`set -eu
echo "$2  $1" | sha256sum -c --quiet - || { echo "the agent changed after it was checked" >&2; exit 3; }
install -d -m 0700 /etc/pi-guard-agent
install -m 0600 "$3" /etc/pi-guard-agent/agent.conf
install -m 0644 "$4" /etc/pi-guard-agent/ca.crt
install -m 0755 "$1" /usr/local/bin/pi-guard-agent
install -m 0644 "$5" /etc/systemd/system/pi-guard-agent.service
systemctl daemon-reload
systemctl enable --quiet pi-guard-agent
systemctl restart pi-guard-agent
`;

const REMOVE = String.raw`set -eu
systemctl disable --now --quiet pi-guard-agent 2>/dev/null || true
rm -f /etc/systemd/system/pi-guard-agent.service /usr/local/bin/pi-guard-agent
rm -rf /etc/pi-guard-agent
systemctl daemon-reload
`;

module.exports = { INSTALL, PARTS, REMOVE, UNIT, agentConfig, agentPlatform, signedAgent };
