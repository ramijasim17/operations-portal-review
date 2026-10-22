/* ABC Whiteboard: private notes with pen, highlighter, shapes and text.
   Everything is stored as vector items per A4 page (794 x 1123 units) and
   drawn on one canvas per page; exports are drawn again at 150 dpi. */
(function () {
  "use strict";
  var W = 794, H = 1123, HEAD = 70, FOOT = 34;
  var FONT = 'system-ui, -apple-system, "Segoe UI", Arial, sans-serif';
  var app = document.getElementById("wb-app");
  if (!app) return;

  var board = { bg: "lined", pages: [{ items: [] }] };
  try {
    var parsed = JSON.parse(document.getElementById("wb-data").textContent);
    if (parsed && parsed.pages && parsed.pages.length) board = parsed;
  } catch (e) {}
  if (["blank", "lined", "grid"].indexOf(board.bg) < 0) board.bg = "lined";

  var $ = function (s) { return document.querySelector(s); };
  document.body.classList.add("wb-wide");
  var pagesBox = $("#wb-pages"), statusEl = $("#wb-status"), titleEl = $("#wb-title");
  var tool = "pen", color = "#1B1830", width = 2, fingerScrolls = !!(window.matchMedia && window.matchMedia("(pointer:coarse)").matches);
  var views = [];                     // one per page: {wrap, canvas, active}
  var history = [], redoStack = [];
  var dirty = false, saveTimer = null, saving = false;

  var logo = new Image();
  logo.onload = function () { renderAll(); };
  logo.src = app.dataset.logo;

  function csrf() {
    var el = document.querySelector("input[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }
  function todayText() {
    return new Date().toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
  }

  /* ───────── drawing ───────── */
  function strokePath(ctx, pts) {
    ctx.beginPath();
    if (pts.length === 1) { ctx.moveTo(pts[0][0], pts[0][1]); ctx.lineTo(pts[0][0] + 0.01, pts[0][1]); return; }
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (var i = 1; i < pts.length - 1; i++) {
      var mx = (pts[i][0] + pts[i + 1][0]) / 2, my = (pts[i][1] + pts[i + 1][1]) / 2;
      ctx.quadraticCurveTo(pts[i][0], pts[i][1], mx, my);
    }
    var last = pts[pts.length - 1];
    ctx.lineTo(last[0], last[1]);
  }

  function drawItem(ctx, it) {
    ctx.save();
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.strokeStyle = it.c; ctx.fillStyle = it.c;
    if (it.t === "s") {
      if (it.hl) { ctx.globalAlpha = 0.35; ctx.globalCompositeOperation = "multiply"; ctx.lineCap = "butt"; }
      ctx.lineWidth = it.hl ? it.w * 4 : it.w;
      strokePath(ctx, it.p); ctx.stroke();
    } else if (it.t === "text") {
      ctx.font = it.z + "px " + FONT; ctx.textBaseline = "top";
      it.s.split("\n").forEach(function (line, i) { ctx.fillText(line, it.x, it.y + i * it.z * 1.25); });
    } else {
      ctx.lineWidth = it.w;
      var a = it.a, b = it.b;
      ctx.beginPath();
      if (it.t === "line" || it.t === "arrow") {
        ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
        if (it.t === "arrow") {
          var ang = Math.atan2(b[1] - a[1], b[0] - a[0]), len = 10 + it.w * 2;
          ctx.beginPath();
          ctx.moveTo(b[0], b[1]); ctx.lineTo(b[0] - len * Math.cos(ang - 0.45), b[1] - len * Math.sin(ang - 0.45));
          ctx.moveTo(b[0], b[1]); ctx.lineTo(b[0] - len * Math.cos(ang + 0.45), b[1] - len * Math.sin(ang + 0.45));
          ctx.stroke();
        }
      } else if (it.t === "rect") {
        ctx.strokeRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
      } else if (it.t === "ellipse") {
        ctx.ellipse((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, Math.abs(b[0] - a[0]) / 2 || 1, Math.abs(b[1] - a[1]) / 2 || 1, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  function paper(ctx, index, total, title) {
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, W, H);
    ctx.save();
    ctx.strokeStyle = "#DCDAE6"; ctx.lineWidth = 1;
    ctx.beginPath();
    if (board.bg === "lined") {
      for (var y = HEAD + 22; y < H - FOOT; y += 32) { ctx.moveTo(32, y + 0.5); ctx.lineTo(W - 32, y + 0.5); }
    } else if (board.bg === "grid") {
      for (var gy = HEAD + 14; gy < H - FOOT; gy += 28) { ctx.moveTo(0, gy + 0.5); ctx.lineTo(W, gy + 0.5); }
      for (var gx = 14; gx < W; gx += 28) { ctx.moveTo(gx + 0.5, HEAD); ctx.lineTo(gx + 0.5, H - FOOT); }
    }
    ctx.stroke();
    ctx.restore();
    // header: logo, title, date
    if (logo.complete && logo.naturalWidth) ctx.drawImage(logo, 32, 16, 190, 37);
    ctx.fillStyle = "#1B1830"; ctx.textAlign = "right"; ctx.textBaseline = "alphabetic";
    ctx.font = "600 17px " + FONT;
    var t = title || "Untitled board";
    if (t.length > 46) t = t.slice(0, 45) + "…";
    ctx.fillText(t, W - 32, 34);
    ctx.fillStyle = "#6B6880"; ctx.font = "12px " + FONT;
    ctx.fillText(todayText(), W - 32, 52);
    ctx.fillStyle = "#43358A"; ctx.fillRect(32, HEAD - 8, W - 64, 2);
    // footer
    ctx.fillStyle = "#6B6880"; ctx.font = "11px " + FONT;
    ctx.textAlign = "left"; ctx.fillText("ABC Operations Portal", 32, H - 14);
    ctx.textAlign = "right"; ctx.fillText("Page " + (index + 1) + " of " + total, W - 32, H - 14);
    ctx.textAlign = "left";
  }

  function renderPage(ctx, index, scale, extra) {
    ctx.save();
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    paper(ctx, index, board.pages.length, titleEl.value.trim());
    board.pages[index].items.forEach(function (it) { drawItem(ctx, it); });
    if (extra) drawItem(ctx, extra);
    ctx.restore();
  }

  function paint(v, extra) {
    if (!v.active) return;
    var ctx = v.canvas.getContext("2d");
    if (v.base && extra !== undefined) {         // while drawing: cached page + the item being drawn
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(v.base, 0, 0);
      if (extra) { ctx.save(); var s = v.canvas.width / W; ctx.setTransform(s, 0, 0, s, 0, 0); drawItem(ctx, extra); ctx.restore(); }
      return;
    }
    renderPage(ctx, views.indexOf(v), v.canvas.width / W, null);
  }
  function renderAll() { views.forEach(function (v) { paint(v); }); }

  /* ───────── pages in the DOM (only pages near the screen hold pixels) ───────── */
  var observer = ("IntersectionObserver" in window) ? new IntersectionObserver(function (entries) {
    entries.forEach(function (en) {
      var v = en.target._view;
      if (!v) return;
      if (en.isIntersecting) activate(v); else deactivate(v);
    });
  }, { rootMargin: "700px 0px" }) : null;

  function activate(v) {
    var cssW = v.wrap.clientWidth || W;
    var px = Math.min(Math.round(cssW * Math.min(window.devicePixelRatio || 1, 2)), 2000);
    if (v.active && v.canvas.width === px) return;
    v.active = true;
    v.canvas.width = px; v.canvas.height = Math.round(px * H / W);
    paint(v);
  }
  function deactivate(v) {
    if (!v.active || v.drawing) return;
    v.active = false;
    v.canvas.width = 1; v.canvas.height = 1;
  }

  function buildPages() {
    if (observer) views.forEach(function (v) { observer.unobserve(v.wrap); });
    pagesBox.innerHTML = ""; views = [];
    board.pages.forEach(function (page, i) {
      var wrap = document.createElement("div");
      wrap.className = "wb-page" + (fingerScrolls ? " scroll-touch" : "");
      wrap.style.aspectRatio = W + " / " + H;
      var canvas = document.createElement("canvas");
      canvas.width = 1; canvas.height = 1;
      canvas.style.webkitTouchCallout = "none"; canvas.style.userSelect = "none";
      wrap.appendChild(canvas);
      pagesBox.appendChild(wrap);
      var v = { wrap: wrap, canvas: canvas, active: false, base: null, drawing: false };
      wrap._view = v; views.push(v);
      attach(v);
      if (observer) observer.observe(wrap); else activate(v);
    });
    markToolClasses();
  }

  function markToolClasses() {
    views.forEach(function (v) {
      v.wrap.classList.toggle("is-eraser", tool === "eraser");
      v.wrap.classList.toggle("is-text", tool === "text");
      v.wrap.classList.toggle("scroll-touch", fingerScrolls);
    });
  }

  /* ───────── history & saving ───────── */
  function snapshot() { return JSON.stringify(board); }
  function remember(before) {
    history.push(before); if (history.length > 40) history.shift();
    redoStack = [];
    changed();
  }
  function restore(json) {
    board = JSON.parse(json);
    $("#wb-bg").value = board.bg;
    buildPages(); changed();
  }
  function undo() { if (!history.length) return; redoStack.push(snapshot()); restore(history.pop()); }
  function redo() { if (!redoStack.length) return; history.push(snapshot()); restore(redoStack.pop()); }

  function setStatus(t) { if (statusEl) statusEl.textContent = t; }
  function changed() {
    dirty = true; setStatus("Saving…");
    clearTimeout(saveTimer); saveTimer = setTimeout(save, 1200);
  }
  function save() {
    if (!dirty || saving) { if (dirty) { clearTimeout(saveTimer); saveTimer = setTimeout(save, 800); } return; }
    saving = true; dirty = false;
    fetch(app.dataset.saveUrl, {
      method: "POST", credentials: "same-origin", keepalive: true,
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify({ title: titleEl.value, data: board })
    }).then(function (r) {
      if (!r.ok) throw new Error("save failed");
      saving = false; if (!dirty) setStatus("Saved");
    }).catch(function () {
      saving = false; dirty = true; setStatus("Not saved – retrying");
      clearTimeout(saveTimer); saveTimer = setTimeout(save, 4000);
    });
  }
  document.addEventListener("visibilitychange", function () { if (document.hidden && dirty) save(); });
  window.addEventListener("pagehide", function () { if (dirty) save(); });
  window.addEventListener("beforeunload", function (e) { if (dirty || saving) { e.preventDefault(); e.returnValue = ""; } });

  /* ───────── pointer handling ───────── */
  function pos(v, e) {
    var r = v.canvas.getBoundingClientRect();
    return [Math.round((e.clientX - r.left) * W / r.width * 10) / 10, Math.round((e.clientY - r.top) * H / r.height * 10) / 10];
  }
  var TEXT_SIZE = { 2: 18, 5: 26, 10: 38 };

  /* Palm rejection. While the Pencil is down (or was a moment ago) every touch is ignored, so a resting
     hand can neither draw nor interrupt the stroke. With "Finger scrolls" on, a finger scrolls the page by hand
     (the browser's own scrolling is switched off on the paper, because it would cancel the Pencil stroke). */
  var penDown = 0, lastPen = 0;
  ["pointerdown", "pointermove", "pointerup", "pointercancel"].forEach(function (ev) {
    document.addEventListener(ev, function (e) {
      if (e.pointerType !== "pen") return;
      lastPen = Date.now();
      if (ev === "pointerdown") penDown = 1;
      else if (ev === "pointerup" || ev === "pointercancel") penDown = 0;
    }, true);
  });
  function isPalm(e) {
    if (e.pointerType !== "touch") return false;
    if (penDown && Date.now() - lastPen < 4000) return true;     // Pencil on the screen (a stale flag expires)
    return Date.now() - lastPen < 700;                           // hand still settling just after a stroke
  }
  function scroller(el) {
    for (var n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      var o = getComputedStyle(n).overflowY;
      if ((o === "auto" || o === "scroll") && n.scrollHeight > n.clientHeight + 1) return n;
    }
    return null;
  }
  function scrollBy(el, dy) {
    var sc = scroller(el);
    if (sc) sc.scrollTop += dy; else window.scrollBy(0, dy);
  }

  function attach(v) {
    var canvas = v.canvas, item = null, before = null, erased = false, start = null;
    ["gesturestart", "gesturechange", "touchstart", "touchmove"].forEach(function (ev) {
      canvas.addEventListener(ev, function (e) { e.preventDefault(); }, { passive: false });
    });
    function abort() {                       // a touch stroke is dropped when the Pencil lands
      if (before && tool === "eraser" && erased) { board = JSON.parse(before); }
      item = null; v.drawing = false; v.base = null; v.pid = null; paint(v);
    }
    canvas.addEventListener("pointerdown", function (e) {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      e.preventDefault();
      if (isPalm(e)) return;
      if (e.pointerType === "pen" && !fingerScrolls && !fingerTouched) { setFinger(true); }
      if (e.pointerType === "touch" && fingerScrolls) {
        v.scroll = { id: e.pointerId, y: e.clientY };
        try { canvas.setPointerCapture(e.pointerId); } catch (x) {}
        return;
      }
      if (v.drawing) {
        if (e.pointerType === "pen" && v.ptype === "touch") abort(); else return;
      }
      var p = pos(v, e);
      if (tool === "text") { openText(v, p); return; }
      closeText();
      try { canvas.setPointerCapture(e.pointerId); } catch (x) {}
      before = snapshot(); v.drawing = true; v.pid = e.pointerId; v.ptype = e.pointerType;
      var items = board.pages[views.indexOf(v)].items;
      if (tool === "eraser") { erased = false; eraseAt(items, p); return; }
      v.base = document.createElement("canvas"); v.base.width = canvas.width; v.base.height = canvas.height;
      renderPage(v.base.getContext("2d"), views.indexOf(v), canvas.width / W, null);
      start = p;
      if (tool === "pen" || tool === "hl") item = { t: "s", c: color, w: width, hl: tool === "hl" ? 1 : 0, p: [p] };
      else item = { t: tool, c: color, w: width, a: p, b: p };
      paint(v, item);
    });
    canvas.addEventListener("pointermove", function (e) {
      if (v.scroll && e.pointerId === v.scroll.id) {
        e.preventDefault();
        scrollBy(canvas, v.scroll.y - e.clientY); v.scroll.y = e.clientY;
        return;
      }
      if (!v.drawing || e.pointerId !== v.pid) return;
      e.preventDefault();
      var events = (e.getCoalescedEvents && e.getCoalescedEvents()) || [];
      if (!events.length) events = [e];
      if (tool === "eraser") { events.forEach(function (ev) { eraseAt(board.pages[views.indexOf(v)].items, pos(v, ev)); }); return; }
      if (!item) return;
      events.forEach(function (ev) {
        var p = pos(v, ev);
        if (item.t === "s") {
          var l = item.p[item.p.length - 1];
          if (Math.abs(p[0] - l[0]) + Math.abs(p[1] - l[1]) >= 1.2) item.p.push(p);
        } else item.b = p;
      });
      paint(v, item);
    });
    function finish(e) {
      if (v.scroll && e.pointerId === v.scroll.id) { v.scroll = null; return; }
      if (!v.drawing || e.pointerId !== v.pid) return;
      v.drawing = false; v.base = null; v.pid = null;
      var items = board.pages[views.indexOf(v)].items;
      if (tool === "eraser") { if (erased) { remember(before); paint(v); } return; }
      if (item) {
        var ok = item.t === "s" || Math.abs(item.b[0] - start[0]) + Math.abs(item.b[1] - start[1]) > 4;
        if (ok) { items.push(item); remember(before); }
        item = null;
      }
      paint(v);
    }
    canvas.addEventListener("pointerup", finish);
    canvas.addEventListener("pointercancel", finish);
    canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });

    function eraseAt(items, p) {
      var r = 10 + width;
      for (var i = items.length - 1; i >= 0; i--) {
        if (hits(items[i], p, r)) { items.splice(i, 1); erased = true; paint(v); break; }
      }
    }
  }

  function segDist(p, a, b) {
    var dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
    var t = l2 ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2)) : 0;
    return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy));
  }
  function hits(it, p, r) {
    if (it.t === "s") {
      var pad = r + (it.hl ? it.w * 2 : it.w / 2);
      if (it.p.length === 1) return Math.hypot(p[0] - it.p[0][0], p[1] - it.p[0][1]) <= pad;
      for (var i = 1; i < it.p.length; i++) if (segDist(p, it.p[i - 1], it.p[i]) <= pad) return true;
      return false;
    }
    if (it.t === "text") {
      var lines = it.s.split("\n"), wd = 0;
      lines.forEach(function (l) { wd = Math.max(wd, l.length * it.z * 0.55); });
      return p[0] >= it.x - r && p[0] <= it.x + wd + r && p[1] >= it.y - r && p[1] <= it.y + lines.length * it.z * 1.25 + r;
    }
    var a = it.a, b = it.b, pad2 = r + it.w / 2;
    if (it.t === "line" || it.t === "arrow") return segDist(p, a, b) <= pad2;
    var x1 = Math.min(a[0], b[0]), x2 = Math.max(a[0], b[0]), y1 = Math.min(a[1], b[1]), y2 = Math.max(a[1], b[1]);
    if (it.t === "rect") {
      return segDist(p, [x1, y1], [x2, y1]) <= pad2 || segDist(p, [x2, y1], [x2, y2]) <= pad2 ||
             segDist(p, [x2, y2], [x1, y2]) <= pad2 || segDist(p, [x1, y2], [x1, y1]) <= pad2;
    }
    var cx = (x1 + x2) / 2, cy = (y1 + y2) / 2, rx = (x2 - x1) / 2 || 1, ry = (y2 - y1) / 2 || 1;
    var d = Math.hypot((p[0] - cx) / rx, (p[1] - cy) / ry);
    return Math.abs(d - 1) * Math.min(rx, ry) <= pad2;
  }

  /* ───────── typed text ───────── */
  var box = null;
  function closeText(commit) {
    if (!box) return;
    var b = box; box = null;
    var s = b.ta.value.replace(/\s+$/, "");
    if (commit !== false && s) {
      var before = snapshot();
      board.pages[b.index].items.push({ t: "text", c: color, x: b.p[0], y: b.p[1], s: s, z: b.z });
      remember(before);
    }
    b.ta.remove(); paint(views[b.index]);
  }
  function openText(v, p) {
    closeText();
    var index = views.indexOf(v), z = TEXT_SIZE[width] || 22;
    var rect = v.canvas.getBoundingClientRect(), k = rect.width / W;
    var ta = document.createElement("textarea");
    ta.className = "wb-textbox"; ta.rows = 1; ta.setAttribute("aria-label", "Type your note");
    ta.style.left = (p[0] * k) + "px"; ta.style.top = (p[1] * k) + "px";
    ta.style.fontSize = (z * k) + "px"; ta.style.color = color;
    ta.style.width = Math.max(140, Math.min(rect.width - p[0] * k - 8, 360 * k)) + "px";
    function grow() { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; }
    ta.addEventListener("input", grow);
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeText(false);
      else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) closeText();
    });
    ta.addEventListener("blur", function () { setTimeout(function () { if (box && box.ta === ta) closeText(); }, 0); });
    v.wrap.appendChild(ta);
    box = { ta: ta, index: index, p: p, z: z };
    ta.focus(); grow();
  }

  /* ───────── toolbar ───────── */
  var fingerTouched = false;
  function setFinger(on) {
    fingerScrolls = on;
    var b = $("#wb-finger"); b.setAttribute("aria-pressed", on ? "true" : "false");
    markToolClasses();
  }
  function setFull(on) {
    document.body.classList.toggle("wb-full", on);
    $("#wb-full").textContent = on ? "Exit full screen" : "Full screen";
    setTimeout(function () { views.forEach(function (v) { if (v.active) activate(v); }); }, 60);
  }
  $("#wb-full").addEventListener("click", function () { setFull(!document.body.classList.contains("wb-full")); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && document.body.classList.contains("wb-full") && !box) setFull(false); });
  $("#wb-finger").addEventListener("click", function () { fingerTouched = true; setFinger(!fingerScrolls); });

  function pick(groupSel, attr, fn) {
    document.querySelectorAll(groupSel + " button[" + attr + "]").forEach(function (b) {
      b.addEventListener("click", function () {
        document.querySelectorAll(groupSel + " button").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on"); fn(b.getAttribute(attr));
      });
    });
  }
  pick("#wb-tools", "data-tool", function (t) { closeText(); tool = t; markToolClasses(); });
  pick("#wb-colors", "data-color", function (c) { color = c; $("#wb-custom").value = c; });
  pick("#wb-widths", "data-width", function (w) { width = parseInt(w, 10); });
  $("#wb-custom").addEventListener("input", function (e) {
    color = e.target.value;
    document.querySelectorAll("#wb-colors button").forEach(function (x) { x.classList.remove("on"); });
  });

  $("#wb-undo").addEventListener("click", function () { closeText(false); undo(); });
  $("#wb-redo").addEventListener("click", function () { closeText(false); redo(); });
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z" && !/INPUT|TEXTAREA|SELECT/.test((document.activeElement || {}).tagName || "")) {
      e.preventDefault(); if (e.shiftKey) redo(); else undo();
    }
  });

  $("#wb-bg").value = board.bg;
  $("#wb-bg").addEventListener("change", function (e) {
    var before = snapshot(); board.bg = e.target.value; remember(before); renderAll();
  });
  $("#wb-addpage").addEventListener("click", function () {
    closeText(); var before = snapshot();
    if (board.pages.length >= 60) { setStatus("Maximum 60 pages"); return; }
    board.pages.push({ items: [] }); remember(before); buildPages();
    views[views.length - 1].wrap.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  $("#wb-delpage").addEventListener("click", function () {
    closeText();
    var idx = currentPage(), before = snapshot();
    if (board.pages.length === 1) board.pages[0] = { items: [] };
    else board.pages.splice(idx, 1);
    remember(before); buildPages(); pageNo();
    setStatus("Page removed – Undo brings it back");
  });
  titleEl.addEventListener("input", function () { changed(); renderAll(); });

  function goPage(i) {
    i = Math.max(0, Math.min(views.length - 1, i));
    views[i].wrap.scrollIntoView({ behavior: "smooth", block: "start" });
    pageNo();
  }
  function pageNo() {
    var el = $("#wb-pageno"); if (el) el.textContent = (currentPage() + 1) + " / " + views.length;
  }
  $("#wb-prev").addEventListener("click", function () { goPage(currentPage() - 1); });
  $("#wb-next").addEventListener("click", function () { goPage(currentPage() + 1); });
  window.addEventListener("scroll", pageNo, { passive: true, capture: true });

  function currentPage() {
    var mid = window.innerHeight / 2, best = 0, bestD = 1e9;
    views.forEach(function (v, i) {
      var r = v.wrap.getBoundingClientRect();
      var d = (r.top <= mid && r.bottom >= mid) ? 0 : Math.min(Math.abs(r.top - mid), Math.abs(r.bottom - mid));
      if (d < bestD) { bestD = d; best = i; }
    });
    return best;
  }

  /* ───────── export ───────── */
  function pageCanvas(i, w) {
    var c = document.createElement("canvas"); c.width = w; c.height = Math.round(w * H / W);
    renderPage(c.getContext("2d"), i, w / W, null);
    return c;
  }
  function safeName() { return (titleEl.value.trim() || "whiteboard").replace(/[^\w\-. ]+/g, "_"); }
  function download(blob, name) {
    var a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1500);
  }
  $("#wb-png").addEventListener("click", function () {
    closeText();
    var i = currentPage();
    pageCanvas(i, 1240).toBlob(function (b) { download(b, safeName() + "_page" + (i + 1) + ".png"); }, "image/png");
  });
  $("#wb-pdf").addEventListener("click", function () {
    closeText();
    var btn = this, old = btn.textContent; btn.disabled = true; btn.textContent = "Preparing…";
    var fd = new FormData(), n = board.pages.length, done = 0;
    function next(i) {
      if (i >= n) { send(); return; }
      pageCanvas(i, 1240).toBlob(function (b) { fd.append("pages", b, "p" + i + ".png"); next(i + 1); }, "image/png");
    }
    function send() {
      fetch(app.dataset.exportUrl, { method: "POST", credentials: "same-origin", headers: { "X-CSRFToken": csrf() }, body: fd })
        .then(function (r) { if (!r.ok) throw new Error(); return r.blob(); })
        .then(function (b) { download(b, safeName() + ".pdf"); })
        .catch(function () { setStatus("Export failed – try again"); })
        .then(function () { btn.disabled = false; btn.textContent = old; });
    }
    next(0);
  });

  setFinger(fingerScrolls);
  buildPages();
  setTimeout(pageNo, 300);
  window.addEventListener("resize", function () { views.forEach(function (v) { if (v.active) activate(v); }); });
  setStatus("Saved");
})();
