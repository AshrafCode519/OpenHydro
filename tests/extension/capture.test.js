// Tests for the extension's automatic download capture (openhydro/extension/background.js).
// Run with: node --test tests/extension
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "..", "openhydro", "extension", "background.js"), "utf8");
const INSTALLER = "https://cdn.example/files/setup.exe";

// Loads background.js against a fake `chrome` and a fake OpenHydro server
function loadBackground({ alive = true, accept = true, settings = {}, cookies = [] } = {}) {
  const calls = { cancel: [], erase: [], download: [], posts: [], notifications: 0 };
  let onDeterminingFilename;
  const chrome = {
    runtime: { id: "test", onInstalled: { addListener() {} }, onMessage: { addListener() {} } },
    contextMenus: { create() {}, onClicked: { addListener() {} } },
    notifications: { create() { calls.notifications++; } },
    storage: { local: { get: async () => settings } },
    cookies: { getAll: async () => cookies },
    downloads: {
      onDeterminingFilename: { addListener(fn) { onDeterminingFilename = fn; } },
      cancel: async (id) => { calls.cancel.push(id); },
      erase: async (query) => { calls.erase.push(query.id); },
      download: (options) => { calls.download.push(options.url); },
    },
  };
  const fetch = async (url, options = {}) => {
    if (url.endsWith("/api/version")) {
      if (!alive) throw new Error("connection refused");
      return { ok: true };
    }
    calls.posts.push({ url, body: JSON.parse(options.body) });
    return { ok: accept };
  };
  vm.runInContext(SOURCE, vm.createContext({
    chrome, fetch, AbortSignal, console, navigator: { userAgent: "TestBrowser/1.0" },
  }));

  return {
    calls,
    // Fires the browser event and waits for suggest() plus the work that follows it
    async browserDownload(item) {
      let suggested;
      const done = new Promise((resolve) => { suggested = resolve; });
      const keepsChannelOpen = onDeterminingFilename(
        { id: 7, referrer: "https://site.example/page", ...item }, () => suggested());
      assert.equal(keepsChannelOpen, true, "the listener must keep suggest() open for async work");
      await done;
      await new Promise((resolve) => setTimeout(resolve, 20));
    },
  };
}

test("an installer is taken over, with the browser's cookies and user agent", async () => {
  const ext = loadBackground({ cookies: [{ name: "session", value: "abc" }, { name: "lang", value: "ar" }] });
  await ext.browserDownload({ url: INSTALLER, filename: "setup.exe" });

  assert.deepEqual(ext.calls.cancel, [7]);
  assert.deepEqual(ext.calls.erase, [7]);
  assert.equal(ext.calls.posts.length, 1);
  const { url, body } = ext.calls.posts[0];
  assert.ok(url.endsWith("/api/tasks"));
  assert.equal(body.url, INSTALLER);
  assert.equal(body.filename, "setup.exe");
  assert.equal(body.referrer, "https://site.example/page");
  assert.equal(body.custom_headers.Cookie, "session=abc; lang=ar");
  assert.equal(body.custom_headers["User-Agent"], "TestBrowser/1.0");
  assert.equal(ext.calls.notifications, 1);
  assert.deepEqual(ext.calls.download, []);
});

test("the final URL after redirects is the one sent", async () => {
  const ext = loadBackground();
  await ext.browserDownload({ url: "https://site.example/get?id=1", finalUrl: INSTALLER, filename: "setup.exe" });
  assert.equal(ext.calls.posts[0].body.url, INSTALLER);
});

test("Windows paths are reduced to the file name", async () => {
  const ext = loadBackground();
  await ext.browserDownload({ url: "https://cdn.example/v.mp4", filename: "C:\\Users\\me\\Downloads\\video.mp4" });
  assert.equal(ext.calls.posts[0].body.filename, "video.mp4");
});

test("when OpenHydro is not running the browser keeps the download", async () => {
  const ext = loadBackground({ alive: false });
  await ext.browserDownload({ url: INSTALLER, filename: "setup.exe" });
  assert.deepEqual(ext.calls.cancel, []);
  assert.equal(ext.calls.posts.length, 0);
});

test("a download OpenHydro refuses goes back to the browser, without being captured again", async () => {
  const ext = loadBackground({ accept: false });
  await ext.browserDownload({ url: INSTALLER, filename: "setup.exe" });
  assert.deepEqual(ext.calls.download, [INSTALLER], "the file must not be lost");

  // The browser's retry of that URL is left alone
  await ext.browserDownload({ url: INSTALLER, filename: "setup.exe" });
  assert.deepEqual(ext.calls.cancel, [7]);
  assert.equal(ext.calls.posts.length, 1);
});

test("pages, unknown types and blob: URLs stay in the browser", async () => {
  const ext = loadBackground();
  await ext.browserDownload({ url: "https://site.example/page.html", filename: "page.html" });
  await ext.browserDownload({ url: "https://site.example/data", filename: "data" });
  await ext.browserDownload({ url: "blob:https://site.example/1234", filename: "export.zip" });
  assert.deepEqual(ext.calls.cancel, []);
  assert.equal(ext.calls.posts.length, 0);
});

test("the popup switch turns capture off", async () => {
  const ext = loadBackground({ settings: { captureEnabled: false } });
  await ext.browserDownload({ url: INSTALLER, filename: "setup.exe" });
  assert.deepEqual(ext.calls.cancel, []);
});
