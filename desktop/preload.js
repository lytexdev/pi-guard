// Only the app's own setup pages get this. The dashboard itself runs without it.
const { contextBridge, ipcRenderer } = require("electron");

if (location.protocol === "file:") {
  contextBridge.exposeInMainWorld("piguard", {
    look: (address) => ipcRenderer.invoke("look", address),
    trust: () => ipcRenderer.invoke("trust"),
    retry: () => ipcRenderer.invoke("retry"),
    change: () => ipcRenderer.invoke("change"),
    address: () => ipcRenderer.invoke("address"),
  });
}
