const assert = require("node:assert");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const { INSTALL, UNIT, agentConfig, agentPlatform, signedAgent } = require("./agent.js");

const { publicKey: ours, privateKey } = crypto.generateKeyPairSync("ed25519");
function release(version, product = "agent", platform = "linux-x86_64") {
  const sha256 = "ab".repeat(32);
  const message = `pi-guard-update|${product}|${platform}|${version}|${sha256}`;
  return { version, sha256, signature: crypto.sign(null, Buffer.from(message), privateKey).toString("hex") };
}
const token = `pga_${"x".repeat(43)}`;

test("only a signed agent for this platform counts", () => {
  assert.ok(signedAgent(release("1.18.0"), "linux-x86_64", ours));
  assert.ok(!signedAgent(release("1.18.0", "desktop"), "linux-x86_64", ours)); // a desktop app is no agent
  assert.ok(!signedAgent(release("1.18.0"), "linux-aarch64", ours));
  assert.ok(!signedAgent({ ...release("1.18.0"), sha256: "x; rm -rf /" }, "linux-x86_64", ours));
});

test("platforms are named like the agent names them", () => {
  assert.equal(agentPlatform("x64"), "linux-x86_64");
  assert.equal(agentPlatform("arm64"), "linux-aarch64");
  assert.equal(agentPlatform("ia32"), null);
});

test("the configuration holds only what was chosen", () => {
  const text = agentConfig({ url: "https://192.168.178.109:8443", token, user: "anna", connect: ["media", "shell"] });
  assert.match(text, /^URL=https:\/\/192\.168\.178\.109:8443\nTOKEN=pga_/);
  assert.match(text, /\nCONNECT_USER=anna\nCONNECT=media\n/);
  assert.match(text, /\nALLOW_DOCKER=false\n/);
  assert.doesNotMatch(agentConfig({ url: "https://pi:8443", token, user: "anna" }), /CONNECT/);
});

test("nothing can slip a line into the configuration", () => {
  assert.throws(() => agentConfig({ url: "https://pi:8443\nALLOW_UPGRADE=true", token, user: "anna" }));
  assert.throws(() => agentConfig({ url: "http://pi:8443", token, user: "anna" }));
  assert.throws(() => agentConfig({ url: "https://pi:8443", token: `${token}\nX=1`, user: "anna" }));
  assert.throws(() => agentConfig({ url: "https://pi:8443", token, user: "anna\nCONNECT=files", connect: ["media"] }));
  assert.throws(() => agentConfig({ url: "https://pi:8443", token, user: "root", connect: ["media"] }));
});

test("the installer writes the same service as agent/install.sh", () => {
  assert.equal(UNIT, fs.readFileSync(path.join(__dirname, "..", "agent", "pi-guard-agent.service"), "utf8"));
  assert.match(INSTALL, /sha256sum -c/); // checks the agent again as root before it is used
  assert.doesNotMatch(INSTALL, /\$\{|`/); // fixed text: the files only come in as arguments
});
