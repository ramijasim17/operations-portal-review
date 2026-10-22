// Tests for core/static/core/js/backup_sync.js with an in-memory folder.   Run:  node --test accounts/js_tests/
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const Sync = require("../../core/static/core/js/backup_sync.js");
const enc = new TextEncoder();
const dec = new TextDecoder();

function concat(chunks) {
  const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0));
  let at = 0;
  for (const c of chunks) { out.set(c, at); at += c.length; }
  return out;
}

class FakeWritable extends WritableStream {          // like the browser's: written to a temporary copy, swapped in on close
  constructor(commit) {
    const chunks = [];
    super({
      write(chunk) { chunks.push(typeof chunk === "string" ? enc.encode(chunk) : chunk); },
      close() { commit(concat(chunks)); },
    });
  }
  async write(data) { const w = this.getWriter(); await w.write(data); w.releaseLock(); }
  async close() { const w = this.getWriter(); await w.close(); }
}

class FakeFileHandle {
  constructor(entry) { this.entry = entry; }
  async getFile() {
    const data = this.entry.data;
    return { size: data.length, text: async () => dec.decode(data), stream: () => new Blob([data]).stream() };
  }
  async createWritable() { return new FakeWritable((bytes) => { this.entry.data = bytes; }); }
}

class FakeDir {
  constructor() { this.children = new Map(); }
  async getDirectoryHandle(name, { create } = {}) {
    let child = this.children.get(name);
    if (!child) { if (!create) throw new Error("NotFoundError"); child = new FakeDir(); this.children.set(name, child); }
    if (!(child instanceof FakeDir)) throw new Error("TypeMismatchError");
    return child;
  }
  async getFileHandle(name, { create } = {}) {
    let child = this.children.get(name);
    if (!child) { if (!create) throw new Error("NotFoundError"); child = { data: new Uint8Array(0) }; this.children.set(name, child); }
    if (child instanceof FakeDir) throw new Error("TypeMismatchError");
    return new FakeFileHandle(child);
  }
  async removeEntry(name) { if (!this.children.delete(name)) throw new Error("NotFoundError"); }
  async *entries() { for (const [n, c] of this.children) yield [n, c instanceof FakeDir ? c : new FakeFileHandle(c)]; }
}

function read(root, path) {            // the text of a file in the fake folder, or null
  let node = root;
  for (const part of path.split("/")) {
    node = node instanceof FakeDir ? node.children.get(part) : undefined;
    if (!node) return null;
  }
  return node instanceof FakeDir ? "[dir]" : dec.decode(node.data);
}
function list(root, prefix = "") {
  const out = [];
  for (const [name, child] of root.children) {
    if (child instanceof FakeDir) out.push(...list(child, `${prefix}${name}/`)); else out.push(prefix + name);
  }
  return out.sort();
}

function makeServer(files, extra = {}) {
  const server = { files: { ...files }, code: "fingerprint-1", stamp: "20261010-0900", fail: new Set(), served: [], ...extra };
  server.api = {
    manifest: async () => ({
      stamp: server.stamp, made: "2026-10-10T09:00:00+03:00", code_fingerprint: server.code,
      files: Object.entries(server.files).map(([name, f]) => ({ name, size: f.text.length, sig: `${f.text.length}:${f.version}`, current: f.current !== false })),
    }),
    file: async (name) => {
      server.served.push(name);
      if (server.fail.has(name)) throw new Error("network error");
      return new Response(server.files[name].text);
    },
    database: async () => new Response(JSON.stringify([{ model: "x" }])),
    code: async () => new Response("CODE-ZIP"),
  };
  return server;
}

const events = [];
const sync = (root, server, extra = {}) => Sync.runSync({ root, api: server.api, onProgress: (e) => events.push(e), ...extra });

test("the first update copies everything, the database and the code", async () => {
  const root = new FakeDir();
  const server = makeServer({ "asset_certs/91D.pdf": { text: "certificate", version: 1 }, "sgs_reports/log.txt": { text: "gauge log", version: 1 } });
  const result = await sync(root, server);
  assert.equal(result.downloaded, 2);
  assert.equal(result.failed.length, 0);
  assert.equal(read(root, "files/asset_certs/91D.pdf"), "certificate");
  assert.equal(read(root, "files/sgs_reports/log.txt"), "gauge log");
  assert.equal(read(root, "database/database-20261010-0900.json"), '[{"model":"x"}]');
  assert.equal(read(root, "code/code.zip"), "CODE-ZIP");
  assert.ok(read(root, "README.txt"));
  assert.ok(JSON.parse(read(root, "_sync_state.json")).files["asset_certs/91D.pdf"]);
});

test("an update with nothing new downloads no files and not the code again", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/one.pdf": { text: "one", version: 1 } });
  await sync(root, server);
  server.served.length = 0;
  server.stamp = "20261010-1000";
  const result = await sync(root, server);
  assert.equal(result.downloaded, 0);
  assert.equal(result.codeUpdated, false);
  assert.deepEqual(server.served, []);
  assert.ok(read(root, "database/database-20261010-1000.json"));                // the database is always saved
});

test("new and changed files are downloaded; deleted ones are set aside, not thrown away", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/keep.pdf": { text: "keep", version: 1 }, "a/edit.pdf": { text: "v1", version: 1 }, "a/gone.pdf": { text: "bye", version: 1 } });
  await sync(root, server);
  server.served.length = 0;
  server.stamp = "20261011-0900";
  server.files["a/edit.pdf"] = { text: "version two", version: 2 };
  server.files["b/new.pdf"] = { text: "new one", version: 1 };
  delete server.files["a/gone.pdf"];
  const result = await sync(root, server);
  assert.deepEqual(server.served.sort(), ["a/edit.pdf", "b/new.pdf"]);          // keep.pdf is not downloaded again
  assert.equal(result.downloaded, 2);
  assert.equal(result.removed, 1);
  assert.equal(read(root, "files/a/edit.pdf"), "version two");
  assert.equal(read(root, "files/b/new.pdf"), "new one");
  assert.equal(read(root, "files/a/gone.pdf"), null);
  assert.equal(read(root, "_removed/20261011-0900/files/a/gone.pdf"), "bye");
  assert.equal(JSON.parse(read(root, "_sync_state.json")).files["a/gone.pdf"], undefined);
});

test("a file that is missing or the wrong size in the folder is fetched again", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/one.pdf": { text: "one one", version: 1 }, "a/two.pdf": { text: "two two", version: 1 } });
  await sync(root, server);
  (await (await root.getDirectoryHandle("files")).getDirectoryHandle("a")).children.get("one.pdf").data = enc.encode("trunc");
  await (await root.getDirectoryHandle("files")).getDirectoryHandle("a").then((d) => d.removeEntry("two.pdf"));
  server.served.length = 0;
  const result = await sync(root, server);
  assert.deepEqual(server.served.sort(), ["a/one.pdf", "a/two.pdf"]);
  assert.equal(read(root, "files/a/one.pdf"), "one one");
  assert.equal(result.downloaded, 2);
});

test("one failing file does not stop the rest, and the next update picks it up", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/ok.pdf": { text: "fine", version: 1 }, "a/bad.pdf": { text: "broken", version: 1 } });
  server.fail.add("a/bad.pdf");
  const result = await sync(root, server);
  assert.equal(result.downloaded, 1);
  assert.equal(result.failed.length, 1);
  assert.equal(result.failed[0].name, "a/bad.pdf");
  assert.equal(read(root, "files/a/ok.pdf"), "fine");
  assert.equal(JSON.parse(read(root, "_sync_state.json")).files["a/bad.pdf"], undefined);
  server.fail.clear(); server.served.length = 0;
  const again = await sync(root, server);
  assert.deepEqual(server.served, ["a/bad.pdf"]);
  assert.equal(again.failed.length, 0);
  assert.equal(read(root, "files/a/bad.pdf"), "broken");
});

test("file names that the disk cannot hold are saved under a safe name and still tracked by the original", async () => {
  const root = new FakeDir();
  const server = makeServer({ 'wa_files/What? "report": v2.pdf': { text: "odd", version: 1 } });
  await sync(root, server);
  assert.deepEqual(list(root).filter((p) => p.startsWith("files/")), ["files/wa_files/What_ _report__ v2.pdf"]);
  server.served.length = 0;
  await sync(root, server);
  assert.deepEqual(server.served, []);                                              // recognised next time
});

test("cancelling keeps what was saved and the next update continues from there", async () => {
  const root = new FakeDir();
  const files = {};
  for (let i = 0; i < 8; i++) files[`f/${i}.txt`] = { text: `file ${i}`, version: 1 };
  const server = makeServer(files);
  const controller = new AbortController();
  let calls = 0;
  const inner = server.api.file;
  server.api.file = async (name, signal) => { if (++calls === 3) controller.abort(); return inner(name, signal); };
  await assert.rejects(sync(root, server, { signal: controller.signal, concurrency: 1 }), /Cancelled/);
  const saved = Object.keys(JSON.parse(read(root, "_sync_state.json")).files).length;
  assert.ok(saved >= 1 && saved < 8, `saved ${saved}`);
  server.api.file = inner; server.served.length = 0;
  const result = await sync(root, server);
  assert.equal(server.served.length, 8 - saved);
  assert.equal(result.failed.length, 0);
});

test("only the newest 10 database copies are kept", async () => {
  const root = new FakeDir();
  const server = makeServer({});
  for (let day = 10; day < 25; day++) { server.stamp = `202610${day}-0900`; await sync(root, server); }
  const copies = list(root).filter((p) => p.startsWith("database/"));
  assert.equal(copies.length, 10);
  assert.equal(copies[0], "database/database-20261015-0900.json");
  assert.equal(copies.at(-1), "database/database-20261024-0900.json");
});

test("the code is saved again only when its fingerprint changes", async () => {
  const root = new FakeDir();
  const server = makeServer({});
  await sync(root, server);
  server.code = "fingerprint-2";
  const result = await sync(root, server);
  assert.equal(result.codeUpdated, true);
  assert.equal((await sync(root, server)).codeUpdated, false);
});

test("a name is turned into safe folder segments", () => {
  assert.deepEqual(Sync.localPath("a/b:c/d?.pdf"), ["a", "b_c", "d_.pdf"]);
  assert.deepEqual(Sync.localPath("x/../y"), ["x", "_", "y"]);
});

test("a replaced certificate: the old file leaves the main folder without being downloaded again, the new one comes in", async () => {
  const root = new FakeDir();
  const server = makeServer({ "asset_certs/C3125A.pdf": { text: "first certificate", version: 1 } });
  await sync(root, server);
  // the portal keeps the old file and stores the replacement under a new name
  server.files["asset_certs/C3125A.pdf"].current = false;
  server.files["asset_certs/C3125A_xYz12.pdf"] = { text: "second certificate", version: 1 };
  server.served.length = 0;
  const result = await sync(root, server);
  assert.deepEqual(server.served, ["asset_certs/C3125A_xYz12.pdf"]);                // only the new one is downloaded
  assert.equal(result.moved, 1);
  assert.equal(read(root, "files/asset_certs/C3125A_xYz12.pdf"), "second certificate");
  assert.equal(read(root, "files/asset_certs/C3125A.pdf"), null);                    // the main folder holds only what is in use
  assert.equal(read(root, "_old_versions/asset_certs/C3125A.pdf"), "first certificate");
  server.served.length = 0;
  assert.equal((await sync(root, server)).downloaded, 0);                            // and the next update is quiet
  assert.deepEqual(server.served, []);
});

test("with 'keep old versions' off, leftovers are not copied, but a file you already hold is still moved aside", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/in-use.pdf": { text: "in use", version: 1 }, "a/was-ok.pdf": { text: "was ok", version: 1 },
                              "a/leftover.pdf": { text: "leftover", version: 1, current: false } });
  await sync(root, server, { includeOld: false });
  assert.deepEqual(list(root).filter((p) => p.startsWith("files/") || p.startsWith("_old_versions/")), ["files/a/in-use.pdf", "files/a/was-ok.pdf"]);
  server.files["a/was-ok.pdf"].current = false;                                      // replaced in the portal
  server.served.length = 0;
  await sync(root, server, { includeOld: false });
  assert.deepEqual(server.served, []);
  assert.equal(read(root, "_old_versions/a/was-ok.pdf"), "was ok");
  assert.equal(read(root, "files/a/was-ok.pdf"), null);
  assert.equal(read(root, "_old_versions/a/leftover.pdf"), null);
});

test("with 'keep old versions' on, leftovers are copied once into _old_versions", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/in-use.pdf": { text: "in use", version: 1 }, "a/leftover.pdf": { text: "leftover", version: 1, current: false } });
  await sync(root, server, { includeOld: true });
  assert.equal(read(root, "files/a/in-use.pdf"), "in use");
  assert.equal(read(root, "_old_versions/a/leftover.pdf"), "leftover");
  assert.equal(read(root, "files/a/leftover.pdf"), null);
});

test("a file that is in use again goes back to the main folder", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/x.pdf": { text: "xx", version: 1, current: false } });
  await sync(root, server, { includeOld: true });
  server.files["a/x.pdf"].current = true;
  server.served.length = 0;
  const result = await sync(root, server);
  assert.equal(result.moved, 1);
  assert.deepEqual(server.served, []);
  assert.equal(read(root, "files/a/x.pdf"), "xx");
  assert.equal(read(root, "_old_versions/a/x.pdf"), null);
});

test("if the saved file has gone missing, a move turns into a download", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/y.pdf": { text: "yy", version: 1 } });
  await sync(root, server);
  await (await root.getDirectoryHandle("files")).getDirectoryHandle("a").then((d) => d.removeEntry("y.pdf"));
  server.files["a/y.pdf"].current = false;
  server.served.length = 0;
  const result = await sync(root, server);
  assert.deepEqual(server.served, ["a/y.pdf"]);
  assert.equal(read(root, "_old_versions/a/y.pdf"), "yy");
  assert.equal(result.failed.length, 0);
});

test("an old version that is later deleted from the portal altogether is set aside, not thrown away", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/old.pdf": { text: "old", version: 1, current: false }, "a/keep.pdf": { text: "keep", version: 1 } });
  await sync(root, server, { includeOld: true });
  delete server.files["a/old.pdf"];
  server.stamp = "20261012-0900";
  const result = await sync(root, server, { includeOld: true });
  assert.equal(result.removed, 1);
  assert.equal(read(root, "_old_versions/a/old.pdf"), null);
  assert.equal(read(root, "_removed/20261012-0900/_old_versions/a/old.pdf"), "old");
});

test("skipped old versions are counted, so a skip is never silent", async () => {
  const root = new FakeDir();
  const server = makeServer({ "a/use.pdf": { text: "in use", version: 1 }, "a/o1.pdf": { text: "old one", version: 1, current: false }, "a/o2.pdf": { text: "old two", version: 1, current: false } });
  const result = await sync(root, server, { includeOld: false });
  assert.equal(result.skippedOld, 2);
  assert.equal(result.skippedOldBytes, "old one".length + "old two".length);
  assert.equal((await sync(root, server, { includeOld: true })).skippedOld, 0);
});
