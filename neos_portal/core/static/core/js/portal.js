/* ABC Portal shared UI behaviour:
 *   [data-confirm="message"]  styled confirmation before a delete/other action
 *   a[data-post]              link that submits as a POST (with CSRF)
 *   a[data-modal]             opens the linked page's form in a popup window
 *   POST forms                "Add / Save changes?" confirmation before saving
 *   .toast messages           success / warning / error notices that fade out
 */
(function () {
  "use strict";
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var csrfToken = function () {
    var i = $("#post-link-form input[name=csrfmiddlewaretoken]");
    return i ? i.value : "";
  };

  /* ── confirmation window ───────────────────────────────────────────── */
  function ask(opts) {
    return new Promise(function (resolve) {
      var d = $("#confirm-modal");
      $("#confirm-title").textContent = opts.title;
      $("#confirm-message").textContent = opts.message;
      d.classList.toggle("is-danger", !!opts.danger);
      var yes = $("#confirm-yes"), no = $("#confirm-no");
      yes.textContent = opts.yesLabel;
      yes.className = "btn " + (opts.danger ? "btn-danger" : "btn-primary");
      var answer = false;
      function done() { d.removeEventListener("close", done); resolve(answer); }
      d.addEventListener("close", done);
      yes.onclick = function () { answer = true; d.close(); };
      no.onclick = function () { d.close(); };
      d.showModal();
      yes.focus();
    });
  }

  function describe(message) {
    var danger = /^\s*(delete|remove|clear|block|deactivate|return)/i.test(message);
    return {
      danger: danger,
      title: danger ? "Please confirm" : "Confirm action",
      yesLabel: /^\s*delete|^\s*remove|^\s*clear/i.test(message) ? "Yes, delete" : "Yes, continue",
    };
  }

  function askAction(message) {
    var d = describe(message);
    return ask({ title: d.title, message: message, danger: d.danger, yesLabel: d.yesLabel });
  }

  function saveMessage(form) {
    if (form.dataset.confirm) return form.dataset.confirm;
    var url = (form.getAttribute("action") || (activeModalUrl() || location.pathname)).toLowerCase();
    if (/confirm/.test(url)) return "Confirm and save these records?";
    if (/import|upload|bulk/.test(url)) return "Upload this file and import it now?";
    if (/edit|update|status|manage|fields|certtypes/.test(url)) return "Save your changes?";
    return "Add this record?";
  }

  function saveTitle(message) {
    return /^save/i.test(message) ? "Save changes" : /^upload/i.test(message) ? "Import file" : /^confirm/i.test(message) ? "Confirm records" : "Add record";
  }

  /* ── helpers ───────────────────────────────────────────────────────── */
  function postTo(url) {
    var f = $("#post-link-form");
    f.action = url;
    f.dataset.skipConfirm = "1";
    f.submit();
  }

  function requestSubmit(form, submitter) {
    form.dataset.skipConfirm = "1";
    if (form.requestSubmit) form.requestSubmit(submitter || undefined);
    else form.submit();
  }

  /* ── compact form layout: as_p forms get .form-grid ───────────────── */
  function markFormGrids(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("form"), function (f) {
      if (!f.querySelector(":scope > p") || f.classList.contains("form-grid")) return;
      f.classList.add("form-grid");
      // Save / Cancel buttons go together on a row of their own.
      var actions = Array.prototype.filter.call(f.children, function (el) {
        return el.matches("button, input[type=submit], a.btn");
      });
      if (actions.length) {
        var row = document.createElement("div");
        row.className = "form-actions";
        actions[0].before(row);
        actions.forEach(function (el) { row.appendChild(el); });
      }
    });
  }

  /* ── popup form window ─────────────────────────────────────────────── */
  var modalUrl = "";
  var modalSelector = "";
  function activeModalUrl() { return $("#form-modal").open ? modalUrl : ""; }

  function renderModal(html, url) {
    var body = $("#form-modal-body");
    var doc = new DOMParser().parseFromString(html, "text/html");
    var main = doc.querySelector("main.main");
    var box = modalSelector ? doc.querySelector('[data-popup-form="' + modalSelector + '"]') : null;
    if (box) body.innerHTML = "<h1>" + (box.dataset.popupTitle || "") + "</h1>" + box.innerHTML;
    else body.innerHTML = main ? main.innerHTML : doc.body.innerHTML;
    var stack = body.querySelector("#toast-stack");
    if (stack) stack.remove();
    var bar = body.querySelector(".menu-bar");
    if (bar) bar.remove();
    modalUrl = url;
    $("#form-modal").classList.toggle("is-wide", body.querySelectorAll("input, select, textarea").length > 16);
    // Scripts inserted through innerHTML don't run; recreate them.
    Array.prototype.forEach.call(body.querySelectorAll("script"), function (old) {
      var s = document.createElement("script");
      Array.prototype.forEach.call(old.attributes, function (a) { s.setAttribute(a.name, a.value); });
      s.textContent = old.textContent;
      old.replaceWith(s);
    });
    Array.prototype.forEach.call(body.querySelectorAll("form"), function (f) {
      if (!f.getAttribute("action")) f.setAttribute("action", url);
    });
    // "Cancel" / "Back" links just close the popup.
    Array.prototype.forEach.call(body.querySelectorAll("a"), function (a) {
      if (/^\s*(cancel|back)\b/i.test(a.textContent) && !a.hasAttribute("data-post")) a.setAttribute("data-close-modal", "1");
    });
    markFormGrids(body);
    wrapTables(body);
    body.scrollTop = 0;
  }

  function handleResponse(res, url) {
    var type = res.headers.get("content-type") || "";
    if (type.indexOf("application/json") !== -1) {
      return res.json().then(function (j) { if (j.redirect) window.location.href = j.redirect; });
    }
    return res.text().then(function (html) { renderModal(html, url); });
  }

  function openModal(url) {
    modalSelector = "";
    var d = $("#form-modal");
    $("#form-modal-body").innerHTML = '<p class="page-sub">Loading…</p>';
    if (!d.open) d.showModal();
    modalUrl = url;
    fetch(url, { credentials: "same-origin", headers: { "X-Modal": "1" } })
      .then(function (res) { return handleResponse(res, url); })
      .catch(function () { $("#form-modal-body").innerHTML = '<div class="alert alert-error">Couldn\'t load this form. Please try again.</div>'; });
  }

  function submitModalForm(form, submitter) {
    var url = form.getAttribute("action") || modalUrl;
    var btn = submitter || form.querySelector("[type=submit]");
    if (btn) btn.disabled = true;
    fetch(url, {
      method: "POST", body: new FormData(form, submitter || undefined), credentials: "same-origin",
      headers: { "X-Modal": "1", "X-CSRFToken": csrfToken() },
    })
      .then(function (res) { return handleResponse(res, url); })
      .catch(function () { if (btn) btn.disabled = false; alert("Couldn't save. Please try again."); });
  }

  /* Inline "add" forms become a button + popup: wrap the form in
     <div data-popup-form="id" data-popup-title="New part" data-popup-button="+ New part">. */
  function initPopupForms() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-popup-form]"), function (box) {
      if (box.dataset.popupReady) return;
      box.dataset.popupReady = "1";
      var formHtml = box.innerHTML;   // kept in memory so the page has no duplicate field ids
      box.innerHTML = "";
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-primary";
      btn.textContent = box.dataset.popupButton || "+ " + (box.dataset.popupTitle || "New");
      btn.addEventListener("click", function () {
        var body = $("#form-modal-body");
        modalSelector = box.dataset.popupForm;
        modalUrl = location.href.split("#")[0];
        body.innerHTML = "<h1>" + (box.dataset.popupTitle || "") + "</h1>" + formHtml;
        markFormGrids(body);
        $("#form-modal").classList.toggle("is-wide", body.querySelectorAll("input, select, textarea").length > 16);
        $("#form-modal").showModal();
      });
      box.before(btn);
      box.hidden = true;
    });
  }

  /* Fields marked data-autosubmit submit their form when changed (older iPads lack form.requestSubmit). */
  document.addEventListener("change", function (e) {
    var el = e.target.closest ? e.target.closest("[data-autosubmit]") : null;
    if (!el || !el.form) return;
    var form = el.form;
    if (form.requestSubmit) { form.requestSubmit(); return; }
    var ev = new Event("submit", { bubbles: true, cancelable: true });
    if (form.dispatchEvent(ev)) form.submit();
  });

  /* ── events ────────────────────────────────────────────────────────── */
  document.addEventListener("click", function (e) {
    var closer = e.target.closest("[data-close-modal]");
    if (closer) { e.preventDefault(); $("#form-modal").close(); return; }

    var el = e.target.closest("[data-confirm]");
    // A form that carries data-confirm is confirmed by the submit handler below; a click on a plain
    // button inside it just submits (otherwise the confirm would be asked here and the save lost).
    if (el && el.tagName === "FORM") el = null;
    if (el) {
      e.preventDefault();
      askAction(el.dataset.confirm).then(function (ok) {
        if (!ok) return;
        if (el.tagName === "A" && el.hasAttribute("data-post")) postTo(el.href);
        else if (el.tagName === "A") window.location.href = el.href;
        else if (el.form) requestSubmit(el.form, el);
      });
      return;
    }

    var post = e.target.closest("a[data-post]");
    if (post) { e.preventDefault(); postTo(post.href); return; }

    var modal = e.target.closest("a[data-modal]");
    if (modal && !(e.ctrlKey || e.metaKey || e.shiftKey)) { e.preventDefault(); openModal(modal.href); }
  });

  document.addEventListener("submit", function (e) {
    var f = e.target;
    if ((f.method || "").toLowerCase() !== "post") return;
    var skip = f.hasAttribute("data-noconfirm") || f.id === "post-link-form" || f.dataset.skipConfirm;
    var inModal = !!f.closest("#form-modal");
    if (f.dataset.skipConfirm) delete f.dataset.skipConfirm;

    if (!skip) {
      e.preventDefault();
      var msg = saveMessage(f);
      var submitter = e.submitter;
      ask({ title: saveTitle(msg), message: msg, danger: false, yesLabel: /^save/i.test(msg) ? "Yes, save" : "Yes, continue" })
        .then(function (ok) { if (ok) requestSubmit(f, submitter); });
      return;
    }
    if (inModal) { e.preventDefault(); submitModalForm(f, e.submitter); }
  }, true);

  /* ── notices ───────────────────────────────────────────────────────── */
  function initToasts() {
    Array.prototype.forEach.call(document.querySelectorAll("#toast-stack .toast"), function (t) {
      var close = t.querySelector(".toast-close");
      if (close) close.addEventListener("click", function () { t.remove(); });
      if (!t.classList.contains("toast-error") && !t.classList.contains("toast-warning")) {
        setTimeout(function () { t.classList.add("toast-hide"); setTimeout(function () { t.remove(); }, 400); }, 6000);
      }
    });
  }
  /* Wide tables scroll inside their own box instead of pushing the whole page sideways
     (the iPad in portrait is the narrowest screen this is used on). */
  function wrapTables(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll("table"), function (t) {
      if (t.closest(".table-wrap, .table-scroll")) return;
      var box = document.createElement("div");
      box.className = "table-scroll";
      t.parentNode.insertBefore(box, t);
      box.appendChild(t);
    });
  }

  /* The menu is a slide-over drawer on narrow screens (iPad portrait). */
  function initMenu() {
    var toggle = $("#menu-toggle"), backdrop = $("#nav-backdrop");
    if (!toggle) return;
    function set(open) { document.body.classList.toggle("nav-open", open); toggle.setAttribute("aria-expanded", open ? "true" : "false"); }
    toggle.addEventListener("click", function () { set(!document.body.classList.contains("nav-open")); });
    backdrop.addEventListener("click", function () { set(false); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") set(false); });
    Array.prototype.forEach.call(document.querySelectorAll("#sidebar nav a"), function (a) { a.addEventListener("click", function () { set(false); }); });
  }

  /* Operations group in the menu. On a laptop it opens as a flyout beside the menu (on tap, or when the pointer rests
     on it); in the drawer (iPad portrait, phone) it opens downward in place. Delegated, so it survives the menu being refreshed. */
  function initNavGroups() {
    var drawer = window.matchMedia("(max-width: 1000px)"), canHover = window.matchMedia("(hover: hover)");
    var timers = new WeakMap();
    function all() { return document.querySelectorAll("#sidebar [data-nav-group]"); }
    function place(g) {
      var fly = g.querySelector(".nav-flyout"), btn = g.querySelector(".nav-group-btn"), sb = $("#sidebar");
      if (!fly || !btn || !sb) return;
      if (drawer.matches) { fly.style.top = fly.style.left = ""; return; }
      var r = btn.getBoundingClientRect(), left = sb.getBoundingClientRect().right + 8;
      fly.style.left = left + "px";
      fly.style.top = Math.max(8, Math.min(r.top, window.innerHeight - fly.offsetHeight - 8)) + "px";
    }
    function setOpen(g, on) {
      clearTimeout(timers.get(g));
      g.classList.toggle("open", on);
      var btn = g.querySelector(".nav-group-btn");
      if (btn) btn.setAttribute("aria-expanded", on ? "true" : "false");
      if (on) place(g);
    }
    function closeAll(except) { Array.prototype.forEach.call(all(), function (g) { if (g !== except) setOpen(g, false); }); }
    function settle() {            // the drawer starts with the current page's group open; a laptop starts closed
      Array.prototype.forEach.call(all(), function (g) { setOpen(g, drawer.matches && g.classList.contains("has-active")); });
    }
    document.addEventListener("click", function (e) {
      var btn = e.target.closest && e.target.closest(".nav-group-btn");
      if (btn) {
        var g = btn.closest("[data-nav-group]");
        closeAll(g);
        if (g.classList.contains("open") && g._byHover) { g._byHover = false; return; }     // opened by resting the pointer: a click keeps it open
        g._byHover = false;
        setOpen(g, !g.classList.contains("open"));
        return;
      }
      if (!(e.target.closest && e.target.closest(".nav-flyout")) && !drawer.matches) closeAll();
    });
    document.addEventListener("mouseover", function (e) {
      if (drawer.matches || !canHover.matches) return;
      var g = e.target.closest && e.target.closest("[data-nav-group]");
      if (g) {
        clearTimeout(timers.get(g));
        if (!g.classList.contains("open")) { closeAll(g); setOpen(g, true); g._byHover = true; }
      }
    });
    document.addEventListener("mouseout", function (e) {
      if (drawer.matches || !canHover.matches) return;
      var g = e.target.closest && e.target.closest("[data-nav-group]");
      if (g && g._byHover && !g.contains(e.relatedTarget)) timers.set(g, setTimeout(function () { setOpen(g, false); g._byHover = false; }, 220));
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || drawer.matches) return;
      var open = document.querySelector("#sidebar .nav-group.open .nav-group-btn");
      closeAll();
      if (open) open.focus();
    });
    var nav = $("#sidebar nav");
    if (nav) nav.addEventListener("scroll", function () { if (!drawer.matches) closeAll(); }, { passive: true });
    window.addEventListener("resize", settle);
    settle();
  }

  /* The menu scrolls when the screen is short: bring the current page's item into view when a page opens. */
  function initSidebarScroll() {
    var nav = document.querySelector("#sidebar nav"), active = nav && nav.querySelector("a.active, .nav-group-btn.active");
    if (!active) return;
    var top = active.offsetTop, bottom = top + active.offsetHeight;
    if (top < nav.scrollTop || bottom > nav.scrollTop + nav.clientHeight) nav.scrollTop = Math.max(0, top - (nav.clientHeight - active.offsetHeight) / 2);
  }

  /* Notification bell: every 5 s ask for the unread count; pop up anything new. */
  function initNotifications() {
    var link = $("#notif-link");
    if (!link) return;
    var last = parseInt(link.dataset.latest || "0", 10);
    var badges = [$("#notif-badge"), $("#notif-badge-top")];
    function show(count) {
      badges.forEach(function (b) { if (b) { b.textContent = count; b.hidden = !count; } });
      var base = document.title.replace(/^\(\d+\)\s*/, "");
      document.title = count ? "(" + count + ") " + base : base;
    }
    /* Alert sound: a short two-note chime made in the browser (no file). Browsers only allow
       sound after the person has clicked or typed on the page once, so it is unlocked then. */
    var audio = null, soundOn = true;
    try { soundOn = localStorage.getItem("neos-sound") !== "off"; } catch (e) {}
    var soundBtn = $("#sound-toggle");
    function paintSound() {
      if (!soundBtn) return;
      soundBtn.innerHTML = soundOn ? "&#128266;" : "&#128263;";
      soundBtn.setAttribute("aria-pressed", soundOn ? "true" : "false");
      soundBtn.title = "Notification sound: " + (soundOn ? "on" : "off");
    }
    function getAudio() {
      if (!audio) { var AC = window.AudioContext || window.webkitAudioContext; if (AC) audio = new AC(); }
      if (audio && audio.state === "suspended") audio.resume();
      return audio;
    }
    function chime() {
      var ctx = soundOn && getAudio();
      if (!ctx || ctx.state !== "running") return;
      [[880, 0], [1175, 0.16]].forEach(function (n) {
        var o = ctx.createOscillator(), g = ctx.createGain(), t = ctx.currentTime + n[1];
        o.type = "sine"; o.frequency.value = n[0];
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.25, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.45);
        o.connect(g); g.connect(ctx.destination);
        o.start(t); o.stop(t + 0.5);
      });
    }
    ["pointerdown", "keydown"].forEach(function (ev) { document.addEventListener(ev, function () { getAudio(); }, { once: true, capture: true }); });
    if (soundBtn) soundBtn.addEventListener("click", function () {
      soundOn = !soundOn;
      try { localStorage.setItem("neos-sound", soundOn ? "on" : "off"); } catch (e) {}
      paintSound();
      if (soundOn) chime();
    });
    paintSound();
    function toast(n) {
      var stack = $("#toast-stack");
      if (!stack) return;
      var t = document.createElement("div");
      t.className = "toast toast-info";
      t.innerHTML = '<span class="toast-icon" aria-hidden="true"></span><div class="toast-body"><div class="toast-title"></div><div class="toast-text"></div></div><button type="button" class="toast-close" aria-label="Dismiss">&times;</button>';
      t.querySelector(".toast-title").textContent = n.kind === "assigned" ? "Assigned to you" : "New activity";
      var a = document.createElement("a");
      a.href = n.url; a.textContent = n.message;
      t.querySelector(".toast-text").appendChild(a);
      t.querySelector(".toast-close").addEventListener("click", function () { t.remove(); });
      stack.appendChild(t);
      setTimeout(function () { t.remove(); }, 12000);
    }
    function poll() {
      if (document.hidden) return;
      fetch("/notifications/poll/?after=" + last + "&t=" + Date.now(), { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "XMLHttpRequest" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) return;
          show(d.count);
          d.new.forEach(toast);
          if (d.new.length) chime();
          if (d.latest > last) last = d.latest;
        })
        .catch(function () {});
    }
    show(parseInt((badges[0] && badges[0].textContent) || "0", 10) || 0);
    setInterval(poll, 5000);
    // Coming back to the tab / app, or the network returning, checks straight away.
    document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
    window.addEventListener("focus", poll);
    window.addEventListener("online", poll);
    window.addEventListener("pageshow", poll);
  }

  /* Live refresh: every 5 s the page quietly asks the server for its own latest content and, only when
     something changed, swaps in the new blocks. Nothing flashes or reloads, scroll position is kept, and
     it holds back whenever someone is typing, has a pop-up open, or has edited a field. Pages that run
     their own scripts (sorting, charts, filters) and the Backup / Whiteboard pages are left alone. */
  function initLiveRefresh() {
    var EVERY = 5000, IDLE_AFTER = 10 * 60 * 1000;
    var main = $("main.main");
    if (!main || document.body.dataset.live === "off") return;
    if (/^\/(whiteboard|accounts\/(backup|cleanup))/.test(location.pathname)) return;
    if (main.querySelector("script, [data-no-refresh]")) return;
    var baseSig = null, navSig = null, busyFetch = false, lastActive = Date.now(), pointerDown = false;
    ["pointerdown", "keydown", "wheel", "touchstart"].forEach(function (ev) {
      document.addEventListener(ev, function () { lastActive = Date.now(); if (ev === "pointerdown") pointerDown = true; }, true);
    });
    ["pointerup", "pointercancel"].forEach(function (ev) { document.addEventListener(ev, function () { pointerDown = false; }, true); });

    function userBusy() {
      var a = document.activeElement;
      if (a && a !== document.body && main.contains(a) && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return true;
      if (document.querySelector("dialog[open]") || pointerDown) return true;
      var sel = window.getSelection && window.getSelection();
      if (sel && !sel.isCollapsed && sel.anchorNode && main.contains(sel.anchorNode)) return true;
      var fields = main.querySelectorAll("input, textarea, select");
      for (var i = 0; i < fields.length; i++) {
        var f = fields[i], t = f.type;
        if (t === "hidden" || t === "submit" || t === "button" || t === "image" || t === "reset") continue;
        if (t === "file") { if (f.files && f.files.length) return true; continue; }
        if (t === "checkbox" || t === "radio") { if (f.checked !== f.defaultChecked) return true; continue; }
        if (f.tagName === "SELECT") {
          var j, def = -1;
          if (f.multiple) {
            for (j = 0; j < f.options.length; j++) if (f.options[j].selected !== f.options[j].defaultSelected) return true;
          } else {
            for (j = 0; j < f.options.length; j++) if (f.options[j].defaultSelected) { def = j; break; }
            if (f.selectedIndex !== (def < 0 ? 0 : def)) return true;       // a menu with no preset shows its first choice
          }
          continue;
        }
        if (f.value !== f.defaultValue) return true;
      }
      return false;
    }
    function blocks(root) {          // the page content between the notices and the footer
      var out = [], on = false;
      Array.prototype.forEach.call(root.children, function (el) {
        if (el.id === "toast-stack") { on = true; return; }
        if (el.classList.contains("app-footer")) { on = false; return; }
        if (on) out.push(el);
      });
      return out;
    }
    function sigOf(nodes) {
      return nodes.map(function (n) {
        var c = n.cloneNode(true);
        Array.prototype.forEach.call(c.querySelectorAll("input[name=csrfmiddlewaretoken]"), function (i) { i.removeAttribute("value"); });
        return c.outerHTML;
      }).join("\n");
    }
    function fetchLatest() {
      return fetch(location.href, { credentials: "same-origin", cache: "no-store", headers: { "X-Requested-With": "XMLHttpRequest", "X-Live-Refresh": "1" } })
        .then(function (r) {
          if (!r.ok || r.redirected || (r.headers.get("content-type") || "").indexOf("text/html") < 0) return null;
          return r.text();
        })
        .then(function (html) {
          if (!html) return null;
          var doc = new DOMParser().parseFromString(html, "text/html");
          var m = doc.querySelector("main.main");
          if (!m || m.querySelector("script")) return null;
          wrapTables(m); markFormGrids(m);
          return { main: m, nav: doc.querySelector("#sidebar nav") };
        });
    }
    function swap(latest) {
      var y = window.scrollY, lefts = Array.prototype.map.call(document.querySelectorAll(".table-scroll"), function (t) { return t.scrollLeft; });
      var old = blocks(main), fresh = blocks(latest.main), footer = main.querySelector(".app-footer");
      old.forEach(function (n) { n.remove(); });
      fresh.forEach(function (n) { main.insertBefore(document.importNode(n, true), footer); });
      Array.prototype.forEach.call(document.querySelectorAll(".table-scroll"), function (t, i) { if (lefts[i]) t.scrollLeft = lefts[i]; });
      window.scrollTo(window.scrollX, y);
      initPopupForms();
    }
    function swapNav(latest) {
      var nav = $("#sidebar nav");
      if (!nav || !latest.nav) return;
      var top = nav.scrollTop;
      nav.innerHTML = latest.nav.innerHTML;
      nav.scrollTop = top;
      window.dispatchEvent(new Event('resize'));
    }
    function tick(force) {
      if (busyFetch || document.hidden || navigator.onLine === false) return;
      if (!force && Date.now() - lastActive > IDLE_AFTER) return;
      busyFetch = true;
      fetchLatest().then(function (latest) {
        if (!latest) return;
        var sig = sigOf(blocks(latest.main)), nsig = latest.nav ? latest.nav.innerHTML.replace(/\s+/g, " ") : "";
        if (baseSig === null) { baseSig = sig; navSig = nsig; return; }       // first look: just remember it
        if (nsig !== navSig) { navSig = nsig; swapNav(latest); }
        if (sig !== baseSig && !userBusy()) { baseSig = sig; swap(latest); }
      }).catch(function () {}).then(function () { busyFetch = false; });
    }
    setTimeout(function () { tick(true); }, 1200);
    setInterval(tick, EVERY);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) tick(true); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNotifications();
    wrapTables(document);
    initMenu();
    initSidebarScroll();
    initNavGroups();
    initToasts();
    markFormGrids(document);
    initPopupForms();
    initLiveRefresh();
  });
})();
