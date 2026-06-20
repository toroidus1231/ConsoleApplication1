// Registers /service-worker.js for the offline checklist (Contracts §6.5) and
// bridges the SW's flush request to the IndexedDB queue drain in offline.js.
import { flushQueue } from "./offline.js";

export function register() {
  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", () => {
    navigator.serviceWorker
      .register("/service-worker.js")
      .then((reg) => {
        // Ask for a Background Sync so queued submissions flush when the device
        // regains connectivity even if the tab is backgrounded (best-effort).
        if ("sync" in reg) {
          reg.sync.register("sync-checklist").catch(() => {});
        }
      })
      .catch(() => {
        // SW registration is optional; the app still works online without it.
      });

    // The SW posts FLUSH_CHECKLIST_QUEUE when sync fires; drain the queue here.
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data && event.data.type === "FLUSH_CHECKLIST_QUEUE") {
        flushQueue();
      }
    });
  });

  // Belt and suspenders: also flush on the window regaining connectivity.
  window.addEventListener("online", () => {
    flushQueue();
  });
}

export function unregister() {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.ready.then((reg) => reg.unregister()).catch(() => {});
  }
}
