/*
 * Brings a backup folder (on an external disk, say) up to date with the portal, in place:
 *   files/            the uploaded files the portal uses right now, same folders as in the portal
 *   _old_versions/    files the portal no longer uses (old versions of replaced certificates ...); optional
 *   database/         a dated copy of the database each time (the newest 10 are kept)
 *   code/code.zip     the code, only when it changed
 *   _removed/<date>/  files that were deleted in the portal are moved here, never thrown away
 *   _sync_state.json  what was saved last time, so the next update copies only what is new or changed
 *
 * The browser does the saving (File System Access API: Chrome or Edge). The folder handle is passed in, so the
 * logic can be tested with a pretend folder (see backup_sync.test.mjs).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.NeosBackupSync = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var STATE_FILE = "_sync_state.json";
  var KEEP_DATABASES = 10;

  function safeSegment(part) {
    var cleaned = String(part).replace(/[<>:"\\|?*\u0000-\u001f]/g, "_").replace(/[. ]+$/, "_");
    return cleaned === "" || cleaned === "." || cleaned === ".." ? "_" : cleaned;
  }

  function localPath(name) {
    return name.split("/").map(safeSegment);
  }

  async function getDir(root, segments, create) {
    var dir = root;
    for (var i = 0; i < segments.length; i++) dir = await dir.getDirectoryHandle(segments[i], { create: !!create });
    return dir;
  }

  async function readJson(dir, name) {
    try {
      var handle = await dir.getFileHandle(name);
      return JSON.parse(await (await handle.getFile()).text());
    } catch (e) {
      return null;
    }
  }

  async function writeText(dir, name, text) {
    var handle = await dir.getFileHandle(name, { create: true });
    var writable = await handle.createWritable();
    await writable.write(text);
    await writable.close();
  }

  async function sizeOf(root, segments) {
    try {
      var dir = await getDir(root, segments.slice(0, -1), false);
      var handle = await dir.getFileHandle(segments[segments.length - 1]);
      return (await handle.getFile()).size;
    } catch (e) {
      return -1;
    }
  }

  // Stream a response into a file. The browser writes to a temporary copy and swaps it in on close(), so a failed or
  // cancelled download never leaves a half-written file where a good one was.
  async function saveResponse(root, segments, response, onBytes) {
    var dir = await getDir(root, segments.slice(0, -1), true);
    var handle = await dir.getFileHandle(segments[segments.length - 1], { create: true });
    var writable = await handle.createWritable();
    var counter = new TransformStream({
      transform: function (chunk, controller) {
        if (onBytes) onBytes(chunk.length);
        controller.enqueue(chunk);
      },
    });
    await response.body.pipeThrough(counter).pipeTo(writable);
  }

  var CURRENT = "files", OLD = "_old_versions";
  function rootOf(file) { return file.current === false ? OLD : CURRENT; }

  // Decide, for every file in the portal, whether it is up to date, needs downloading, or only needs moving
  // (it was saved before, but is now an old version, or is in use again).
  async function makePlan(root, manifest, state, includeOld) {
    var known = state.files || {}, present = {}, todo = [], moves = [], skippedOld = 0, skippedOldBytes = 0;
    for (var i = 0; i < manifest.files.length; i++) {
      var f = manifest.files[i], want = rootOf(f), saved = known[f.name];
      present[f.name] = true;
      if (!includeOld && want === OLD && !saved) { skippedOld++; skippedOldBytes += f.size; continue; }   // old versions are not wanted
      if (saved && saved.sig === f.sig) {
        var at = saved.root || CURRENT;
        if (at === want) {
          if ((await sizeOf(root, [want].concat(localPath(f.name)))) === f.size) continue;     // up to date
        } else if ((await sizeOf(root, [at].concat(localPath(f.name)))) === f.size) {
          moves.push({ file: f, from: at, to: want });                                          // same file, new place
          continue;
        }
      }
      todo.push(f);
    }
    var removed = Object.keys(known).filter(function (name) { return !present[name]; });
    return { todo: todo, moves: moves, removed: removed, skippedOld: skippedOld, skippedOldBytes: skippedOldBytes };
  }

  async function moveFile(root, fromSegments, toSegments) {
    var from = await getDir(root, fromSegments.slice(0, -1), false);
    var handle = await from.getFileHandle(fromSegments[fromSegments.length - 1]);
    var file = await handle.getFile();
    var dir = await getDir(root, toSegments.slice(0, -1), true);
    var copy = await (await dir.getFileHandle(toSegments[toSegments.length - 1], { create: true })).createWritable();
    await file.stream().pipeTo(copy);
    await from.removeEntry(fromSegments[fromSegments.length - 1]);
  }

  async function pruneDatabases(dbDir) {
    var names = [];
    for await (var entry of dbDir.entries()) if (/^database-.*\.json$/.test(entry[0])) names.push(entry[0]);
    names.sort();
    var surplus = names.slice(0, Math.max(0, names.length - KEEP_DATABASES));
    for (var i = 0; i < surplus.length; i++) await dbDir.removeEntry(surplus[i]);
    return surplus.length;
  }

  var README =
    "ABC Operations Portal -- backup folder\n\n" +
    "files/            the uploaded files the portal uses right now, in the same folders as in the portal\n" +
    "_old_versions/    files the portal no longer uses: old versions of replaced certificates, leftovers of deleted records.\n" +
    "                  Safe to delete this folder; it is only filled when 'keep old versions' is ticked.\n" +
    "database/         dated copies of the whole database (database-YYYYMMDD-HHMM.json); restore the newest with\n" +
    "                  python manage.py migrate   and   python manage.py loaddata database-....json\n" +
    "code/code.zip     the application code (updated only when it changed)\n" +
    "_removed/         files that were deleted in the portal, kept here in case you need them\n" +
    "_sync_state.json  what was saved last time. Do not delete it, or the next update copies everything again.\n\n" +
    "The database copies contain staff details and password hashes: keep this disk private.\n";

  /*
   * opts: { root, api: {manifest(), file(name, signal), database(), code()}, onProgress(event), concurrency, signal }
   * Resolves with a summary; individual file failures are collected in `failed` and do not stop the update.
   */
  async function runSync(opts) {
    var root = opts.root, api = opts.api, emit = opts.onProgress || function () {};
    var concurrency = opts.concurrency || 3;
    var signal = opts.signal;

    emit({ type: "status", text: "Looking at what is in the folder…" });
    var includeOld = opts.includeOld !== false;
    var manifest = await api.manifest();
    var state = (await readJson(root, STATE_FILE)) || { files: {}, code_fingerprint: "" };
    if (!state.files) state.files = {};

    var work = await makePlan(root, manifest, state, includeOld);
    var totalBytes = work.todo.reduce(function (sum, f) { return sum + f.size; }, 0);
    emit({ type: "plan", todo: work.todo.length, bytes: totalBytes, moved: work.moves.length, removed: work.removed.length, total: manifest.files.length,
           oldCount: manifest.old_count || 0, oldBytes: manifest.old_bytes || 0, skippedOld: work.skippedOld, skippedOldBytes: work.skippedOldBytes });

    // Files that only changed place (replaced by a newer version, or in use again): moved, not downloaded.
    var moved = 0;
    for (var m = 0; m < work.moves.length; m++) {
      var move = work.moves[m], pathParts = localPath(move.file.name);
      try {
        await moveFile(root, [move.from].concat(pathParts), [move.to].concat(pathParts));
        state.files[move.file.name] = { sig: move.file.sig, size: move.file.size, root: move.to };
        moved++;
      } catch (error) {
        work.todo.push(move.file);                                          // could not move it: fetch it again instead
        totalBytes += move.file.size;
      }
    }

    var done = 0, doneBytes = 0, failed = [], cursor = 0, downloadedBytes = 0;
    async function worker() {
      while (cursor < work.todo.length) {
        if (signal && signal.aborted) return;
        var item = work.todo[cursor++];
        var counted = 0;
        try {
          emit({ type: "file", name: item.name, index: done + 1, of: work.todo.length });
          var response = await api.file(item.name, signal);
          var place = rootOf(item), segments = [place].concat(localPath(item.name));
          await saveResponse(root, segments, response, function (n) {
            counted += n; doneBytes += n;
            emit({ type: "bytes", doneBytes: doneBytes, totalBytes: totalBytes });
          });
          var actual = await sizeOf(root, segments);
          if (actual !== item.size) throw new Error("saved " + actual + " bytes, expected " + item.size);
          var before = state.files[item.name];
          if (before && (before.root || CURRENT) !== place) {                  // a stale copy in the other place
            try { var oldDir = await getDir(root, [before.root || CURRENT].concat(localPath(item.name)).slice(0, -1), false); await oldDir.removeEntry(localPath(item.name).slice(-1)[0]); } catch (e) { /* not there */ }
          }
          state.files[item.name] = { sig: item.sig, size: item.size, root: place };
          downloadedBytes += item.size;
          done++;
        } catch (error) {
          doneBytes -= counted;
          if (signal && signal.aborted) return;
          failed.push({ name: item.name, error: String(error && error.message || error) });
        }
      }
    }
    var workers = [];
    for (var w = 0; w < Math.min(concurrency, work.todo.length); w++) workers.push(worker());
    await Promise.all(workers);
    if (signal && signal.aborted) {
      await writeText(root, STATE_FILE, JSON.stringify(state));        // keep what was saved, so the next run continues from there
      throw new Error("Cancelled. What was already saved is kept.");
    }

    // Files that no longer exist in the portal at all: set aside, not thrown away.
    var removed = 0;
    if (work.removed.length) {
      emit({ type: "status", text: "Setting aside " + work.removed.length + " file(s) that were deleted in the portal…" });
      var stamp = manifest.stamp;
      for (var r = 0; r < work.removed.length; r++) {
        var name = work.removed[r], savedRoot = (state.files[name] && state.files[name].root) || CURRENT;
        try { await moveFile(root, [savedRoot].concat(localPath(name)), ["_removed", stamp, savedRoot].concat(localPath(name))); } catch (e) { /* already gone from the folder */ }
        delete state.files[name];
        removed++;
      }
    }

    // The database: a dated copy every time, because it is small and always complete.
    emit({ type: "status", text: "Saving the database…" });
    var databaseName = "database-" + manifest.stamp + ".json";
    await saveResponse(root, ["database", databaseName], await api.database(), null);
    var dbDir = await getDir(root, ["database"], true);
    await pruneDatabases(dbDir);

    // The code: only when it changed.
    var codeUpdated = false;
    if (manifest.code_fingerprint && manifest.code_fingerprint !== state.code_fingerprint) {
      emit({ type: "status", text: "Saving the code…" });
      await saveResponse(root, ["code", "code.zip"], await api.code(), null);
      state.code_fingerprint = manifest.code_fingerprint;
      codeUpdated = true;
    }

    state.last = { stamp: manifest.stamp, made: manifest.made };
    await writeText(root, STATE_FILE, JSON.stringify(state));
    await writeText(root, "README.txt", README);

    var summary = {
      total: manifest.files.length, downloaded: done, downloadedBytes: downloadedBytes, removed: removed, moved: moved, skippedOld: work.skippedOld, skippedOldBytes: work.skippedOldBytes,
      failed: failed, codeUpdated: codeUpdated, codeFingerprint: manifest.code_fingerprint, databaseFile: databaseName,
    };
    emit({ type: "finished", summary: summary });
    return summary;
  }

  return { runSync: runSync, localPath: localPath, safeSegment: safeSegment, STATE_FILE: STATE_FILE };
});
