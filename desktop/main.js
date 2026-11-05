// The dashboard in its own window. It runs on the Pi; this is only the frame around it.
const { app, BrowserWindow, Menu, Notification, dialog, ipcMain, session, shell } = require("electron");
const { execFile, spawn } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const https = require("node:https");
const path = require("node:path");
const { pipeline } = require("node:stream/promises");
const { Readable } = require("node:stream");
const { promisify } = require("node:util");
const { cleanAddress, fingerprint, issuedByPinned } = require("./trust.js");
const { newer, platformName, signed } = require("./updates.js");

const german = () => app.getLocale().startsWith("de");
const page = (name) => path.join(__dirname, "pages", name);
const configFile = () => path.join(app.getPath("userData"), "config.json");
let config = null;
let window = null;

function loadConfig() {
  try {
    config = JSON.parse(fs.readFileSync(configFile(), "utf8"));
  } catch {
    config = null;
  }
}

function saveConfig(value) {
  config = value;
  fs.mkdirSync(path.dirname(configFile()), { recursive: true });
  fs.writeFileSync(configFile(), JSON.stringify(value), { mode: 0o600 });
}

const sameOrigin = (url) => {
  try {
    return Boolean(config) && new URL(url).origin === config.url;
  } catch {
    return false;
  }
};

// Only for reading the public certificate at the first start. Nothing secret goes over this
// connection, and the person confirms the fingerprint before it is trusted.
function firstContact(url) {
  return new Promise((resolve, reject) => {
    const request = https.get(url, { rejectUnauthorized: false, timeout: 5000 }, (answer) => {
      let body = "";
      answer.setEncoding("utf8");
      answer.on("data", (part) => { body += part; if (body.length > 100000) request.destroy(); });
      answer.on("end", () => (answer.statusCode === 200 ? resolve(body) : reject(new Error(String(answer.statusCode)))));
    });
    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.on("error", reject);
  });
}

let pending = null;

function fromOwnPage(event) {
  return event.senderFrame?.url.startsWith("file://");
}

ipcMain.handle("look", async (event, text) => {
  if (!fromOwnPage(event)) throw new Error("refused");
  const url = cleanAddress(text);
  const hello = JSON.parse(await firstContact(`${url}/api/hello`));
  if (hello.app !== "pi-guard") throw new Error("not pi-guard");
  const ca = await firstContact(`${url}/ca.crt`);
  pending = { url, ca };
  return { url, fingerprint: fingerprint(ca) };
});

ipcMain.handle("trust", (event) => {
  if (!fromOwnPage(event) || !pending) throw new Error("refused");
  saveConfig(pending);
  pending = null;
  open();
});

ipcMain.handle("retry", (event) => {
  if (fromOwnPage(event)) open();
});

ipcMain.handle("change", (event) => {
  if (fromOwnPage(event)) window.loadFile(page("setup.html"));
});

ipcMain.handle("address", () => config?.url || "");

function open() {
  if (!config) return window.loadFile(page("setup.html"));
  return window.loadURL(config.url).catch(() => {}); // did-fail-load below shows what happened
}

function secure() {
  const ses = session.defaultSession;
  ses.setSpellCheckerEnabled(false);
  ses.setCertificateVerifyProc((request, done) => {
    if (request.verificationResult === "net::OK") return done(0); // a public certificate
    if (!config || request.hostname !== new URL(config.url).hostname) return done(-3);
    return done(issuedByPinned(request.certificate.data, config.ca, request.hostname) ? 0 : -2);
  });
  const allowed = new Set(["media", "notifications", "fullscreen", "clipboard-sanitized-write"]);
  ses.setPermissionRequestHandler((contents, permission, done) => done(allowed.has(permission) && sameOrigin(contents.getURL())));
  ses.setPermissionCheckHandler((contents, permission, origin) => allowed.has(permission) && sameOrigin(origin));
}

function openOutside(url) {
  if (url.startsWith("https://")) shell.openExternal(url);
}

function create() {
  window = new BrowserWindow({
    width: 1280, height: 860, minWidth: 360, minHeight: 500, backgroundColor: "#121418",
    title: "PiGuard", icon: path.join(__dirname, "build", "icon.png"), autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false,
      spellcheck: false, // it would fetch dictionaries from Google
    },
  });
  const contents = window.webContents;
  contents.setWindowOpenHandler(({ url }) => {
    openOutside(url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) => {
    if (sameOrigin(url) || url.startsWith("file://")) return;
    event.preventDefault();
    openOutside(url);
  });
  contents.on("did-fail-load", (_event, code, _text, url, mainFrame) => {
    // Not from inside this event: Chromium would abort a navigation started here.
    if (mainFrame && code !== -3 && sameOrigin(url)) setTimeout(() => window.loadFile(page("offline.html")).catch(() => {}), 100);
  });
  open();
}

const FIRST_CHECK = 30_000;
const CHECK_EVERY = 6 * 3600_000;
let updating = false;

function tell(text, click) {
  const note = new Notification({ title: "PiGuard", body: text });
  if (click) note.on("click", click);
  note.show();
}

// Where a Linux install keeps its versions: .../versions/1.2.3/pi-guard-desktop.
function installRoot() {
  const folder = path.dirname(fs.realpathSync(process.execPath));
  return path.basename(path.dirname(folder)) === "versions" ? path.dirname(path.dirname(folder)) : null;
}

async function download(url, target, expected) {
  const answer = await session.defaultSession.fetch(url);
  if (!answer.ok) throw new Error(`download: ${answer.status}`);
  const hash = crypto.createHash("sha256");
  const body = Readable.fromWeb(answer.body);
  body.on("data", (part) => hash.update(part));
  await pipeline(body, fs.createWriteStream(target, { mode: 0o600 }));
  if (hash.digest("hex") !== expected) {
    fs.rmSync(target, { force: true });
    throw new Error("the file does not match its signature");
  }
}

// The running version stays untouched: the link moves in one step, and if anything fails before
// that, nothing has changed.
async function installLinux(file, version) {
  const root = installRoot();
  const versions = path.join(root, "versions");
  const fresh = path.join(versions, `.new-${version}`);
  const final = path.join(versions, version);
  fs.rmSync(fresh, { recursive: true, force: true });
  fs.mkdirSync(fresh, { recursive: true });
  await promisify(execFile)("tar", ["-xzf", file, "-C", fresh, "--strip-components=1"]);
  fs.rmSync(final, { recursive: true, force: true });
  fs.renameSync(fresh, final);
  const link = path.join(root, ".current-new");
  fs.rmSync(link, { force: true });
  fs.symlinkSync(path.join("versions", version), link);
  fs.renameSync(link, path.join(root, "current"));
  const running = path.basename(path.dirname(fs.realpathSync(process.execPath)));
  for (const old of fs.readdirSync(versions)) {
    if (old !== version && old !== running) fs.rmSync(path.join(versions, old), { recursive: true, force: true });
  }
  const restart = () => {
    app.relaunch({ execPath: path.join(root, "current", path.basename(process.execPath)) });
    app.quit();
  };
  tell(german() ? `Version ${version} ist installiert und startet beim nächsten Öffnen. Klicken für jetzt.` : `Version ${version} is installed and starts next time. Click for now.`, restart);
}

async function checkForUpdate(asked = false) {
  if (updating || !config || !app.isPackaged) return;
  const platform = platformName();
  const managed = process.platform !== "linux" || installRoot();
  if (!managed) return;
  updating = true;
  const folder = path.join(app.getPath("userData"), "updates");
  try {
    const base = `${config.url}/api/updates/desktop/${platform}`;
    const manifest = await (await session.defaultSession.fetch(base)).json();
    if (!newer(manifest.version, app.getVersion())) {
      if (asked) dialog.showMessageBox(window, { message: german() ? "PiGuard ist aktuell." : "PiGuard is up to date." });
      return;
    }
    if (!signed(manifest, platform)) throw new Error(`${manifest.version} has no valid signature`);
    fs.mkdirSync(folder, { recursive: true });
    const file = path.join(folder, `PiGuard-${manifest.version}`);
    await download(`${base}/file`, file, manifest.sha256);
    if (process.platform === "linux") {
      await installLinux(file, manifest.version);
      fs.rmSync(file, { force: true });
    } else if (process.platform === "win32") {
      fs.renameSync(file, `${file}.exe`);
      spawn(`${file}.exe`, ["/S", "--updated"], { detached: true, stdio: "ignore" }).unref();
      app.quit();
    } else {
      fs.renameSync(file, `${file}.zip`);
      tell(german() ? `Version ${manifest.version} ist geprüft. Klicken zum Installieren.` : `Version ${manifest.version} is verified. Click to install.`, () => shell.showItemInFolder(`${file}.zip`));
    }
  } catch (error) {
    console.error("update:", error.message);
    if (asked) dialog.showErrorBox("PiGuard", error.message);
  } finally {
    updating = false;
  }
}

function menu() {
  const de = german();
  const change = () => window.loadFile(page("setup.html"));
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { label: "PiGuard", submenu: [
      { label: de ? "Adresse ändern" : "Change address", click: change },
      { label: de ? "Nach Updates suchen" : "Check for updates", click: () => checkForUpdate(true) },
      { type: "separator" },
      { role: "quit", label: de ? "Beenden" : "Quit" },
    ] },
    { role: "editMenu", label: de ? "Bearbeiten" : "Edit" },
    { label: de ? "Ansicht" : "View", submenu: [
      { role: "reload", label: de ? "Neu laden" : "Reload" },
      { type: "separator" },
      { role: "zoomIn", label: de ? "Größer" : "Larger" },
      { role: "zoomOut", label: de ? "Kleiner" : "Smaller" },
      { role: "resetZoom", label: de ? "Normale Größe" : "Normal size" },
      { type: "separator" },
      { role: "togglefullscreen", label: de ? "Vollbild" : "Full screen" },
    ] },
  ]));
}

// Native Wayland where there is one: sharper, and the GPU stays in use. Elsewhere it does nothing.
app.commandLine.appendSwitch("ozone-platform-hint", "auto");
app.setDesktopName("pi-guard.desktop"); // the entry with the icon, also on Wayland
if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => { if (window) { window.show(); window.focus(); } });
app.whenReady().then(() => {
  loadConfig();
  secure();
  menu();
  create();
  setTimeout(checkForUpdate, FIRST_CHECK).unref();
  setInterval(checkForUpdate, CHECK_EVERY).unref();
});
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) create(); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
