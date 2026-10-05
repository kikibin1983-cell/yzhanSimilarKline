import test from "node:test";
import assert from "node:assert/strict";
const { validateSave, SaveQueue, createSnapshot } = await import(
  "../src/storage.js"
).catch(() => ({}));
const valid = () => ({
  formatVersion: 1,
  generatorVersion: 1,
  seed: "forest",
  savedAt: "2026-10-04T00:00:00.000Z",
  player: {
    position: { x: 0.5, y: 40, z: 0.5 },
    yaw: 0,
    pitch: 0,
    flying: false,
  },
  slots: [1, 2, 3, 4, 5, 7, 8, 9, 6],
  selected: 0,
  settings: { viewDistance: 3, sensitivity: 1, volume: 0.3, pixelRatio: 1.5 },
  edits: [["-1,10,-17", 0]],
});
test("save validator exists", () =>
  assert.equal(typeof validateSave, "function"));
test("valid snapshot preserves removed block as air", () =>
  assert.deepEqual(validateSave(valid()).edits, [["-1,10,-17", 0]]));
test("invalid versions never import silently", () => {
  for (const field of ["formatVersion", "generatorVersion"]) {
    const s = valid();
    s[field] = 99;
    assert.throws(() => validateSave(s), /存档/);
  }
});
test("invalid coordinates IDs and duplicate edits are rejected", () => {
  for (const entry of [
    ["256,10,0", 1],
    ["0,0,0", 0],
    ["0,10,0", 12],
    ["0,10,0", 999],
    ["oops", 1],
    ["0.5,10,0", 1],
  ]) {
    const s = valid();
    s.edits = [entry];
    assert.throws(() => validateSave(s), /存档/, String(entry));
  }
  const s = valid();
  s.edits.push([...s.edits[0]]);
  assert.throws(() => validateSave(s), /存档/);
});
test("malformed slots settings and nonfinite player are rejected", () => {
  let s = valid();
  s.slots = [1];
  assert.throws(() => validateSave(s), /存档/);
  s = valid();
  s.settings.viewDistance = 100;
  assert.throws(() => validateSave(s), /存档/);
  s = valid();
  s.player.position.x = NaN;
  assert.throws(() => validateSave(s), /存档/);
  s = valid();
  s.player.flying = "yes";
  assert.throws(() => validateSave(s), /存档/);
});
test("snapshot is a copy and does not change with later edits", () => {
  const g = {
    world: { seed: "forest", edits: new Map([["1,10,0", 0]]) },
    player: { position: { x: 0.5, y: 40, z: 0.5 }, flying: false },
    yaw: 0,
    pitch: 0,
    slots: [1, 2, 3, 4, 5, 7, 8, 9, 6],
    selected: 0,
    settings: { viewDistance: 3, sensitivity: 1, volume: 0.3, pixelRatio: 1.5 },
  };
  const s = createSnapshot(g);
  g.world.edits.set("1,10,0", 8);
  g.player.position.x = 5;
  assert.deepEqual(s.edits, [["1,10,0", 0]]);
  assert.equal(s.player.position.x, 0.5);
});
test("save queue writes snapshots in order even with asynchronous writes", async () => {
  const writes = [];
  const q = new SaveQueue(async (s) => {
    await new Promise((r) => setTimeout(r, s.n === 1 ? 20 : 0));
    writes.push(s.n);
  });
  const a = { n: 1 },
    p = q.enqueue(a);
  a.n = 9;
  await Promise.all([p, q.enqueue({ n: 2 })]);
  assert.deepEqual(writes, [1, 2]);
});
test("failed write does not poison next save", async () => {
  const writes = [];
  const q = new SaveQueue(async (s) => {
    if (s.n === 1) throw new Error("quota");
    writes.push(s.n);
  });
  await assert.rejects(q.enqueue({ n: 1 }), /quota/);
  await q.enqueue({ n: 2 });
  assert.deepEqual(writes, [2]);
});
