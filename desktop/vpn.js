// The app's side of the VPN: talks to the root service on its local socket, and gets this
// computer its own access from pi-guard. The app itself never needs root.
const net = require("node:net");
const os = require("node:os");

const SOCKET = process.env.PI_GUARD_VPN_SOCKET || "/run/pi-guard-vpn.sock";
const TIMEOUT = 20_000;

function ask(request) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(SOCKET);
    let text = "";
    socket.setEncoding("utf8");
    socket.setTimeout(TIMEOUT, () => socket.destroy(new Error("the service does not answer")));
    socket.on("connect", () => socket.write(`${JSON.stringify(request)}\n`));
    socket.on("data", (part) => {
      text += part;
      if (!text.includes("\n")) return;
      socket.destroy();
      try { resolve(JSON.parse(text)); } catch (error) { reject(error); }
    });
    socket.on("error", reject);
  });
}

async function json(answer) {
  if (!answer.ok) throw Object.assign(new Error(await answer.text()), { status: answer.status });
  return answer.json();
}

function platform() {
  return { linux: "linux", win32: "windows", darwin: "macos" }[process.platform];
}

// After the login in this app: this computer becomes a device of its own, like a phone.
async function pair(fetcher, url) {
  const session = await json(await fetcher(`${url}/api/session`, { credentials: "include" }));
  if (!session.authenticated) throw new Error("login");
  const answer = await fetcher(`${url}/api/mobile/pair`, {
    method: "POST", credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": session.csrf },
    body: JSON.stringify({ name: os.hostname(), platform: platform() }),
  });
  return (await json(answer)).token;
}

async function link(fetcher, url, token) {
  const headers = { Authorization: `Bearer ${token}`, "Content-Type": "application/json" };
  const access = await json(await fetcher(`${url}/api/mobile/vpn`, { method: "POST", headers, body: JSON.stringify({ full_tunnel: true }) }));
  const request = { cmd: "configure", config: access.config, ways: access.ways };
  const own = await ask({ cmd: "public_key" });
  if (own.key) {
    const answer = await fetcher(`${url}/api/mobile/vpn/pq`, { method: "POST", headers, body: JSON.stringify({ key: own.key }) });
    if (answer.ok) request.server_key = (await answer.json()).server_key; // without: the tunnel still works
  }
  const result = await ask(request);
  if (result.error) throw new Error(result.error);
  return result;
}

module.exports = { ask, link, pair };
