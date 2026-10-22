// Tests for core/static/core/js/theme.js.   Run:  node --test accounts/js_tests/theme.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Theme = require("../../core/static/core/js/theme.js");

const memory = (initial = {}) => ({ data: { ...initial }, getItem(k) { return k in this.data ? this.data[k] : null; }, setItem(k, v) { this.data[k] = String(v); } });

test("Auto follows the device; Light and Night do not", () => {
  assert.equal(Theme.resolve("auto", true), "dark");
  assert.equal(Theme.resolve("auto", false), "light");
  assert.equal(Theme.resolve("light", true), "light");
  assert.equal(Theme.resolve("dark", false), "dark");
});

test("anything unknown means Auto", () => {
  for (const bad of [null, undefined, "", "blue", "DARK", 5]) assert.equal(Theme.normalise(bad), "auto");
  for (const good of ["light", "dark", "auto"]) assert.equal(Theme.normalise(good), good);
});

test("the choice is remembered, and a missing or broken store falls back to Auto", () => {
  const store = memory();
  assert.equal(Theme.read(store), "auto");
  assert.equal(Theme.write(store, "dark"), true);
  assert.equal(Theme.read(store), "dark");
  assert.equal(store.data[Theme.KEY], "dark");
  Theme.write(store, "nonsense");
  assert.equal(Theme.read(store), "auto");
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };   // a private window
  assert.equal(Theme.read(broken), "auto");
  assert.equal(Theme.write(broken, "dark"), false);
});

test("the script sets the page theme, marks the pressed button, and tells charts", async () => {
  // run the browser half against a tiny fake page
  const attrs = {}, buttons = [{ a: { "data-theme-set": "light" } }, { a: { "data-theme-set": "dark" } }, { a: { "data-theme-set": "auto" } }]
    .map((b) => ({ ...b, getAttribute(k) { return this.a[k]; }, setAttribute(k, v) { this.a[k] = v; } }));
  const meta = { a: {}, setAttribute(k, v) { this.a[k] = v; } };
  const listeners = {}, events = [];
  const win = {
    localStorage: memory({ "neos-theme": "dark" }),
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
    dispatchEvent(e) { events.push(e); },
    document: {
      documentElement: { setAttribute(k, v) { attrs[k] = v; } },
      querySelector: () => meta,
      querySelectorAll: () => buttons,
      addEventListener(type, fn) { listeners[type] = fn; },
    },
  };
  delete require.cache[require.resolve("../../core/static/core/js/theme.js")];
  const factory = require("../../core/static/core/js/theme.js");           // node gets only the pure half ...
  assert.equal(factory.apply, undefined);
  // ... so evaluate the file the way a browser would, with `window` as the root
  const fs = await import("node:fs");
  const source = fs.readFileSync(new URL("../../core/static/core/js/theme.js", import.meta.url), "utf8");
  const run = new Function("self", "module", source);
  run(win, undefined);
  const Browser = win.NeosTheme;
  assert.equal(attrs["data-theme"], "dark");                                // the stored choice wins over the device
  assert.equal(attrs["data-theme-choice"], "dark");
  assert.equal(meta.a.content, "#1B1830");
  assert.deepEqual(buttons.map((b) => b.a["aria-pressed"]), ["false", "true", "false"]);
  assert.equal(events.at(-1).type, "themechange");
  Browser.choose("light");
  assert.equal(attrs["data-theme"], "light");
  assert.equal(win.localStorage.data["neos-theme"], "light");
  assert.equal(meta.a.content, "#43358A");
  assert.deepEqual(buttons.map((b) => b.a["aria-pressed"]), ["true", "false", "false"]);
  Browser.choose("auto");
  assert.equal(attrs["data-theme"], "light");                               // this fake device is in light mode
  assert.equal(attrs["data-theme-choice"], "auto");
});
