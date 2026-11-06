// OpenHydro no longer uses a service worker. Registered at /static/, the old one never controlled the
// dashboard (/), only /static/ pages such as the download window, from a cache-first copy; there is
// nothing to keep offline either, since every page needs the running app. This version only removes
// itself and its cache from browsers that installed an earlier one (a deleted file would not: a
// failed update keeps the old worker).

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      await caches.delete(key);
    }
    await self.registration.unregister();
  })());
});
