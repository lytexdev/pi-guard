const assert = require("node:assert");
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { cleanAddress, fingerprint, issuedByPinned } = require("./trust.js");

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "trust-"));
const ssl = (...args) => execFileSync("openssl", args, { cwd: dir, stdio: "pipe" });
function authority(name) {
  ssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", `${name}.key`, "-out", `${name}.crt`, "-days", "2", "-subj", `/CN=${name}`);
}
function leaf(ca, name, san, days = "2") {
  ssl("req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", `${name}.key`, "-out", `${name}.csr`, "-subj", "/CN=x");
  fs.writeFileSync(path.join(dir, `${name}.ext`), `subjectAltName=${san}\n`);
  ssl("x509", "-req", "-in", `${name}.csr`, "-CA", `${ca}.crt`, "-CAkey", `${ca}.key`, "-CAcreateserial", "-out", `${name}.crt`, "-days", days, "-extfile", `${name}.ext`);
}
const read = (name) => fs.readFileSync(path.join(dir, `${name}.crt`), "utf8");
authority("home");
authority("stranger");
leaf("home", "pi", "DNS:lytexsystem,IP:192.168.178.109");
leaf("stranger", "fake", "DNS:lytexsystem,IP:192.168.178.109");

test("its own pi-guard is trusted, by name and by address", () => {
  assert.ok(issuedByPinned(read("pi"), read("home"), "lytexsystem"));
  assert.ok(issuedByPinned(read("pi"), read("home"), "192.168.178.109"));
});

test("a certificate from anyone else is not, whatever it claims", () => {
  assert.ok(!issuedByPinned(read("fake"), read("home"), "lytexsystem"));
});

test("a name the certificate does not list is not", () => {
  assert.ok(!issuedByPinned(read("pi"), read("home"), "other.example"));
  assert.ok(!issuedByPinned(read("pi"), read("home"), "192.168.178.5"));
});

test("an expired certificate is not", () => {
  assert.ok(!issuedByPinned(read("pi"), read("home"), "lytexsystem", new Date("2099-01-01")));
});

test("nonsense is refused, not thrown", () => {
  assert.ok(!issuedByPinned("nonsense", read("home"), "lytexsystem"));
});

test("the fingerprint is the one people compare", () => {
  assert.match(fingerprint(read("home")), /^([0-9A-F]{2}:){31}[0-9A-F]{2}$/);
});

test("addresses are completed and only https counts", () => {
  assert.equal(cleanAddress("192.168.178.109"), "https://192.168.178.109:8443");
  assert.equal(cleanAddress("https://192.168.178.109:8443/"), "https://192.168.178.109:8443");
  assert.equal(cleanAddress("lytexsystem"), "https://lytexsystem:8443");
  assert.equal(cleanAddress("https://guard.lytex.de"), "https://guard.lytex.de");
  assert.throws(() => cleanAddress("http://192.168.178.109"));
});
