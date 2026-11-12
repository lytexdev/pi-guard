const assert = require("node:assert");
const crypto = require("node:crypto");
const test = require("node:test");
const { newer, platformName, publicKey, signed, step } = require("./updates.js");

const { publicKey: ours, privateKey } = crypto.generateKeyPairSync("ed25519");
const stranger = crypto.generateKeyPairSync("ed25519").privateKey;
function release(version, key = privateKey, product = "desktop", platform = "linux-x64") {
  const sha256 = "ab".repeat(32);
  const message = `pi-guard-update|${product}|${platform}|${version}|${sha256}`;
  return { version, sha256, signature: crypto.sign(null, Buffer.from(message), key).toString("hex") };
}

test("a signed release counts", () => assert.ok(signed(release("1.2.3"), "linux-x64", ours)));
test("anyone else's signature does not", () => assert.ok(!signed(release("1.2.3", stranger), "linux-x64", ours)));
test("nothing signed can be changed", () => {
  assert.ok(!signed({ ...release("1.2.3"), sha256: "cd".repeat(32) }, "linux-x64", ours));
  assert.ok(!signed({ ...release("1.2.3"), version: "9.9.9" }, "linux-x64", ours));
  assert.ok(!signed(release("1.2.3"), "win-x64", ours));
});
test("a release of another product does not count", () => assert.ok(!signed(release("1.2.3", privateKey, "agent"), "linux-x64", ours)));
test("nonsense is refused, not thrown", () => {
  assert.ok(!signed({}, "linux-x64", ours));
  assert.ok(!signed({ version: "1.0.0", sha256: "x", signature: "zz" }, "linux-x64", ours));
});
test("only newer versions", () => {
  assert.ok(newer("1.1.1", "1.1.0") && newer("1.10.0", "1.9.0") && newer("2.0.0", "1.9.9"));
  assert.ok(!newer("1.1.0", "1.1.0") && !newer("1.0.9", "1.1.0"));
  assert.ok(!newer("", "1.0.0") && !newer("1.2", "1.0.0") && !newer("1.2.3.4", "1.0.0") && !newer(undefined, "1.0.0"));
});
test("the built-in key is one", () => assert.equal(publicKey().asymmetricKeyType, "ed25519"));
test("platforms are named as they are published", () => {
  assert.equal(platformName("linux", "x64"), "linux-x64");
  assert.equal(platformName("win32", "x64"), "win-x64");
  assert.equal(platformName("darwin", "arm64"), "mac-arm64");
});
test("a new major or minor version installs, a patch is offered", () => {
  assert.strictEqual(step("2.0.0", "1.9.3"), "major");
  assert.strictEqual(step("1.10.0", "1.9.3"), "minor");
  assert.strictEqual(step("1.9.4", "1.9.3"), "patch");
  assert.strictEqual(step("1.9.3", "1.9.3"), null);
  assert.strictEqual(step("1.9.2", "1.9.3"), null);
});
