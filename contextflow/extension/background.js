// ContextFlow Bridge — background service worker
// Periodically sends current tabs to the native desktop app.

const POLL_INTERVAL_MS = 10000; // 10 seconds
const NATIVE_HOST_NAME = "com.contextflow.bridge";

let connected = false;

// ── connect to native host ──────────────────────────────────────

function connect() {
  try {
    chrome.runtime.connectNative(NATIVE_HOST_NAME);
    connected = true;
    console.log("[ContextFlow] Connected to native host");
    return true;
  } catch (e) {
    console.warn("[ContextFlow] Native host not available:", e.message);
    connected = false;
    return false;
  }
}

// ── send tabs ───────────────────────────────────────────────────

async function sendTabs() {
  if (!connected) {
    if (!connect()) return;
  }

  try {
    const tabs = await chrome.tabs.query({});
    const data = {
      type: "tabs_update",
      timestamp: Date.now(),
      tabs: tabs.map(t => ({
        id: t.id,
        title: t.title || "",
        url: t.url || "",
        active: t.active || false,
        pinned: t.pinned || false,
        windowId: t.windowId,
      })),
    };

    const port = chrome.runtime.connectNative(NATIVE_HOST_NAME);
    port.postMessage(data);
    port.onDisconnect.addListener(() => {
      if (chrome.runtime.lastError) {
        console.warn("[ContextFlow] Disconnected:", chrome.runtime.lastError.message);
        connected = false;
      }
    });
    port.onMessage.addListener((msg) => {
      // Acknowledge from native host
      if (msg && msg.status === "ok") {
        // All good
      }
    });
  } catch (e) {
    console.warn("[ContextFlow] Error sending tabs:", e.message);
    connected = false;
  }
}

// ── lifecycle ───────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log("[ContextFlow] Extension installed");
  connect();
  sendTabs();
});

chrome.runtime.onStartup.addListener(() => {
  connect();
  sendTabs();
});

// Periodic polling
setInterval(sendTabs, POLL_INTERVAL_MS);

// Send on tab changes
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url || changeInfo.title || changeInfo.status === "complete") {
    sendTabs();
  }
});

chrome.tabs.onCreated.addListener(() => sendTabs());
chrome.tabs.onRemoved.addListener(() => sendTabs());
