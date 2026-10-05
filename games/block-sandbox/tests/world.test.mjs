import test from "node:test";
import assert from "node:assert/strict";
const { World } = await import("../src/world.js").catch(() => ({}));
const { Generator } = await import("../src/generation.js").catch(() => ({}));
const { meshChunk } = await import("../src/meshing.js").catch(() => ({}));
test("world APIs exist for actual generation and edits", () =>
  assert.equal(typeof World, "function"));
test("same seed generates identical chunks regardless of request order", () => {
  const a = new Generator("test"),
    b = new Generator("test");
  const first = a.generateChunk(-1, 0);
  a.generateChunk(0, 0);
  b.generateChunk(0, 0);
  assert.deepEqual(first, b.generateChunk(-1, 0));
  assert.notDeepEqual(first, new Generator("different").generateChunk(-1, 0));
});
test("direct generation agrees with chunk data including tree edges", () => {
  const g = new Generator("forest");
  for (const cx of [-1, 0, 1]) {
    const data = g.generateChunk(cx, 0);
    for (const x of [0, 1, 14, 15])
      for (const z of [0, 7, 15])
        for (let y = 0; y < 60; y++)
          assert.equal(
            data[(y * 16 + z) * 16 + x],
            g.getBlock(cx * 16 + x, y, z),
            `${cx},${x},${y},${z}`,
          );
  }
});
test("negative coordinates preserve removed blocks across chunk eviction", () => {
  const w = new World("test");
  const original = w.getBlock(-1, 10, -17);
  assert.notEqual(original, 0);
  assert.equal(w.setBlock(-1, 10, -17, 0), true);
  w.trimChunks(10, 10, 1);
  assert.equal(w.getBlock(-1, 10, -17), 0);
  assert.equal(w.edits.get("-1,10,-17"), 0);
  assert.equal(w.setBlock(-1, 10, -17, original), true);
  assert.equal(w.edits.size, 0);
});
test("world boundaries and bedrock cannot be edited", () => {
  const w = new World("test");
  assert.equal(w.setBlock(0, 0, 0, 0), false);
  assert.equal(w.setBlock(-257, 40, 0, 1), false);
  assert.equal(w.setBlock(256, 40, 0, 1), false);
  assert.equal(w.setBlock(0, 96, 0, 1), false);
  assert.equal(w.setBlock(0, 40, 0, 999), false);
  assert.equal(w.getBlock(256, 30, 0), 13);
});
test("chunk boundary edit invalidates both adjacent meshes", () => {
  const w = new World("test");
  w.dirty.clear();
  w.setBlock(15, 70, 0, 8);
  assert.ok(w.dirty.has("0,0"));
  assert.ok(w.dirty.has("1,0"));
});
test("mesh culls internal solid faces using hand checked block fixture", () => {
  const fixture = {
    getBlock(x, y, z) {
      return y === 2 && z === 2 && (x === 2 || x === 3) ? 3 : 0;
    },
  };
  const m = meshChunk(fixture, 0, 0);
  assert.equal(m.opaque.positions.length, 10 * 6 * 3);
  assert.equal(m.opaque.normals.length, m.opaque.positions.length);
  assert.equal(m.opaque.uvs.length, 10 * 6 * 2);
});
test("empty chunk mesh has no geometry", () => {
  assert.equal(
    meshChunk({ getBlock: () => 0 }, 0, 0).opaque.positions.length,
    0,
  );
});
