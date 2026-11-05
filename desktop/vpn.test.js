const assert = require("node:assert");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const socket = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "vpn-")), "s");
process.env.PI_GUARD_VPN_SOCKET = socket;
const { ask, link, pair } = require("./vpn.js");

const seen = [];
const service = net.createServer((connection) => {
  connection.setEncoding("utf8");
  connection.once("data", (line) => {
    const request = JSON.parse(line);
    seen.push(request);
    connection.write(`${JSON.stringify(request.cmd === "public_key" ? { key: "CLIENTKEY" } : { state: "off" })}\n`);
  });
});
test.before(() => new Promise((done) => { service.listen(socket, done); }));
test.after(() => service.close());

const answer = (body, status = 200) => ({ ok: status < 400, status, json: async () => body, text: async () => JSON.stringify(body) });

test("the login of this app makes the computer a device", async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push([url, options]);
    return url.endsWith("/api/session") ? answer({ authenticated: true, csrf: "abc" }) : answer({ token: "tok" });
  };
  assert.equal(await pair(fetcher, "https://pi"), "tok");
  assert.equal(calls[1][1].headers["X-CSRF-Token"], "abc");
  assert.equal(JSON.parse(calls[1][1].body).platform, "linux");
});

test("without a login nothing is paired", async () => {
  await assert.rejects(pair(async () => answer({ authenticated: false }), "https://pi"), /login/);
});

test("access and quantum keys reach the service", async () => {
  seen.length = 0;
  const fetcher = async (url) => (url.endsWith("/pq") ? answer({ server_key: "SERVERKEY" }) : answer({ config: "CONF", ways: [{ kind: "ipv4" }] }));
  await link(fetcher, "https://pi", "tok");
  const configure = seen.find((r) => r.cmd === "configure");
  assert.equal(configure.config, "CONF");
  assert.equal(configure.server_key, "SERVERKEY");
});

test("a Pi without Rosenpass still gives a working tunnel", async () => {
  seen.length = 0;
  const fetcher = async (url) => (url.endsWith("/pq") ? answer("web.pq_not_installed", 409) : answer({ config: "CONF", ways: [] }));
  await link(fetcher, "https://pi", "tok");
  assert.equal(seen.find((r) => r.cmd === "configure").server_key, undefined);
});

test("a stopped service is an error, not silence", async () => {
  const old = socket;
  assert.equal((await ask({ cmd: "status" })).state, "off");
  assert.ok(old);
});
