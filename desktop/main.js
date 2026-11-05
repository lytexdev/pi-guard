// The dashboard in its own window. It runs on the Pi; this is only the frame around it.
const { app, BrowserWindow, Menu, ipcMain, session, shell } = require("electron");
const fs = require("node:fs");
const https = require("node:https");
const path = require("node:path");
const { cleanAddress, fingerprint, issuedByPinned } = require("./trust.js");

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

function menu() {
  const de = german();
  const change = () => window.loadFile(page("setup.html"));
  Menu.setApplicationMenu(Menu.buildFromTemplate([
    ...(process.platform === "darwin" ? [{ role: "appMenu" }] : []),
    { label: "PiGuard", submenu: [
      { label: de ? "Adresse ändern" : "Change address", click: change },
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
if (!app.requestSingleInstanceLock()) app.quit();
app.on("second-instance", () => { if (window) { window.show(); window.focus(); } });
app.whenReady().then(() => {
  loadConfig();
  secure();
  menu();
  create();
});
app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) create(); });
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
