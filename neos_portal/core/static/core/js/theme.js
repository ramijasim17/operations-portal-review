/*
 * Light / Night / Auto.
 *   The choice is kept on this device (localStorage "neos-theme": "light" | "dark" | "auto"); Auto follows the device.
 *   This file is loaded in <head> and runs at once, so the right theme is set before the page is painted (no flash).
 *   <html data-theme="light|dark"> is what the stylesheet reads; a "themechange" event tells charts to repaint.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.NeosTheme = factory(root);
})(typeof self !== "undefined" ? self : this, function (win) {
  "use strict";

  var KEY = "neos-theme";
  var CHOICES = ["light", "dark", "auto"];
  var THEME_COLOUR = { light: "#43358A", dark: "#1B1830" };         // the browser's address bar / status bar colour

  function resolve(choice, deviceIsDark) {
    if (choice === "light" || choice === "dark") return choice;
    return deviceIsDark ? "dark" : "light";
  }

  function normalise(choice) {
    return CHOICES.indexOf(choice) >= 0 ? choice : "auto";
  }

  function read(storage) {
    try { return normalise(storage.getItem(KEY)); } catch (e) { return "auto"; }
  }

  function write(storage, choice) {
    try { storage.setItem(KEY, normalise(choice)); return true; } catch (e) { return false; }
  }

  // ── in the browser ──
  function deviceIsDark() {
    return !!(win && win.matchMedia && win.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function apply(choice) {
    choice = normalise(choice);
    var theme = resolve(choice, deviceIsDark());
    var html = win.document.documentElement;
    html.setAttribute("data-theme", theme);
    html.setAttribute("data-theme-choice", choice);
    var meta = win.document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", THEME_COLOUR[theme]);
    syncButtons(choice);
    try { win.dispatchEvent(new win.CustomEvent("themechange", { detail: { theme: theme, choice: choice } })); } catch (e) { /* old browser */ }
    return theme;
  }

  function syncButtons(choice) {
    var buttons = win.document.querySelectorAll("[data-theme-set]");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].getAttribute("data-theme-set") === choice ? "true" : "false");
    }
  }

  function choose(choice) {
    write(win.localStorage, choice);
    return apply(choice);
  }

  function init() {
    apply(read(win.localStorage));
    if (win.matchMedia) {
      var query = win.matchMedia("(prefers-color-scheme: dark)");
      var follow = function () { if (read(win.localStorage) === "auto") apply("auto"); };
      if (query.addEventListener) query.addEventListener("change", follow); else if (query.addListener) query.addListener(follow);
    }
    win.document.addEventListener("click", function (event) {
      var button = event.target.closest && event.target.closest("[data-theme-set]");
      if (button) choose(button.getAttribute("data-theme-set"));
    });
    win.document.addEventListener("DOMContentLoaded", function () { syncButtons(read(win.localStorage)); });
  }

  var api = { resolve: resolve, normalise: normalise, read: read, write: write, KEY: KEY, CHOICES: CHOICES };
  if (win && win.document) { api.apply = apply; api.choose = choose; init(); }
  return api;
});
