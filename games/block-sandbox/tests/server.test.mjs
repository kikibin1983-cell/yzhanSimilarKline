import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
const { createGameServer, startServer } = await import("../server.mjs").catch(
  () => ({}),
);
test("local game server factory exists", () =>
  assert.equal(typeof createGameServer, "function"));
async function withServer(fn) {
  const server = createGameServer();
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  try {
    await fn(`http://127.0.0.1:${server.address().port}`);
  } finally {
    await new Promise((r) => server.close(r));
  }
}
test("identity endpoint distinguishes game from other local service", () =>
  withServer(async (url) => {
    const r = await fetch(url + "/__block_sandbox_health");
    assert.equal(r.status, 200);
    assert.equal((await r.json()).app, "block-sandbox");
  }));
test("actual page and local renderer resources serve with correct content types", () =>
  withServer(async (url) => {
    let r = await fetch(url + "/");
    assert.equal(r.status, 200);
    assert.match(await r.text(), /方块/);
    r = await fetch(url + "/vendor/three.module.js");
    assert.equal(r.status, 200);
    assert.match(r.headers.get("content-type"), /javascript/);
  }));
test("private files and traversal cannot be served", () =>
  withServer(async (url) => {
    for (const path of [
      "/package.json",
      "/server.mjs",
      "/.env",
      "/node_modules/three/package.json",
      "/src/%2e%2e%2fserver.mjs",
    ]) {
      const r = await fetch(url + path);
      assert.ok([403, 404].includes(r.status), path);
    }
  }));
test("missing asset returns 404 instead of HTML masquerading as JS", () =>
  withServer(async (url) => {
    const r = await fetch(url + "/src/no-such-module.js");
    assert.equal(r.status, 404);
  }));
test("duplicate launch reuses game service without starting another server", () =>
  withServer(async (url) => {
    const s = await startServer(Number(new URL(url).port), false);
    assert.equal(s.listening, false);
  }));
test("occupied port belonging to another app is rejected without stopping it", async () => {
  const other = http.createServer((req, res) => {
    res.setHeader("Content-Type", "application/json");
    res.end('{"app":"unrelated"}');
  });
  await new Promise((r) => other.listen(0, "127.0.0.1", r));
  try {
    await assert.rejects(
      startServer(other.address().port, false),
      /another application/,
    );
    assert.equal(other.listening, true);
  } finally {
    await new Promise((r) => other.close(r));
  }
});
