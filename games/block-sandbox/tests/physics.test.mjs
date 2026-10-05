import test from "node:test";
import assert from "node:assert/strict";
import { World } from "../src/world.js";
const { Player, safePosition } = await import("../src/player.js").catch(
  () => ({}),
);
const { raycast, editBlock } = await import("../src/interaction.js").catch(
  () => ({}),
);
const flat = {
  getBlock(x, y, z) {
    return y <= 0 ? 3 : 0;
  },
};
test("physics exposes a player controller", () =>
  assert.equal(typeof Player, "function"));
test("gravity lands at the floor without sinking", () => {
  const p = new Player(flat, { x: 0.5, y: 5, z: 0.5 });
  for (let i = 0; i < 180; i++) p.update(1 / 60, {}, 0);
  assert.ok(Math.abs(p.position.y - 1) < 0.005);
  assert.equal(p.grounded, true);
});
test("fast flight cannot tunnel through a one block wall", () => {
  const w = {
    getBlock(x, y, z) {
      return y <= 0 || (z === -2 && y < 8) ? 3 : 0;
    },
  };
  const p = new Player(w, { x: 0.5, y: 2, z: 0.5 });
  p.flying = true;
  for (let i = 0; i < 30; i++)
    p.update(1 / 30, { forward: true, sprint: true }, 0);
  assert.ok(p.position.z >= -0.7 - 0.001);
});
test("jump hits ceiling without entering solid", () => {
  const w = {
    getBlock(x, y, z) {
      return y <= 0 || y === 3 ? 3 : 0;
    },
  };
  const p = new Player(w, { x: 0.5, y: 1, z: 0.5 });
  p.grounded = true;
  for (let i = 0; i < 20; i++) p.update(1 / 60, { jump: i === 0 }, 0);
  assert.ok(p.position.y + 1.8 <= 3.001);
});
test("diagonal movement does not exceed straight movement", () => {
  const a = new Player(flat, { x: 0.5, y: 10, z: 0.5 }),
    b = new Player(flat, { x: 0.5, y: 10, z: 0.5 });
  a.flying = b.flying = true;
  a.update(0.1, { forward: true }, 0);
  b.update(0.1, { forward: true, right: true }, 0);
  assert.ok(
    Math.abs(
      Math.hypot(b.position.x - 0.5, b.position.z - 0.5) -
        Math.abs(a.position.z - 0.5),
    ) < 0.001,
  );
});
test("safe spawn repairs saved position inside terrain", () => {
  const pos = safePosition(flat, { x: 0.5, y: 0, z: 0.5 });
  assert.equal(pos.y, 1);
});
test("new world seed 23 starts in dry space, not underwater or in a cave", () => {
  const w = new World("23"),
    p = safePosition(w);
  assert.equal(
    w.getBlock(Math.floor(p.x), Math.floor(p.y), Math.floor(p.z)),
    0,
  );
  assert.equal(
    w.getBlock(Math.floor(p.x), Math.floor(p.y + 1.6), Math.floor(p.z)),
    0,
  );
  assert.ok(p.y >= w.generator.surfaceHeight(Math.floor(p.x), Math.floor(p.z)));
});
test("restoring an existing underwater player preserves legitimate position", () => {
  const w = new World("23"),
    p = { x: 0.5, y: 23, z: 0.5 };
  assert.deepEqual(safePosition(w, p), p);
});
test("raycast returns first block and correct adjacent placement", () => {
  const w = {
    getBlock(x, y, z) {
      return x === 3 && y === 2 && z === 0 ? 3 : 0;
    },
  };
  const h = raycast(w, { x: 0.5, y: 2.5, z: 0.5 }, { x: 1, y: 0, z: 0 }, 6);
  assert.deepEqual(h.block, { x: 3, y: 2, z: 0 });
  assert.deepEqual(h.adjacent, { x: 2, y: 2, z: 0 });
  assert.equal(
    raycast(w, { x: 0.5, y: 2.5, z: 0.5 }, { x: 1, y: 0, z: 0 }, 2),
    null,
  );
});
test("raycast works in negative directions and skips fixed water", () => {
  const w = {
    getBlock(x, y, z) {
      return x === -2 ? 3 : x === -1 ? 12 : 0;
    },
  };
  const h = raycast(w, { x: 0.5, y: 4.5, z: 0.5 }, { x: -1, y: 0, z: 0 });
  assert.deepEqual(h.block, { x: -2, y: 4, z: 0 });
  assert.equal(h.distance, 1.5);
});
test("placing a block inside player is rejected without mutating world", () => {
  let writes = 0;
  const w = {
    getBlock: () => 0,
    setBlock() {
      writes++;
      return true;
    },
  };
  const p = new Player(w, { x: 0.5, y: 1, z: 0.5 });
  const h = { block: { x: 0, y: 0, z: 0 }, adjacent: { x: 0, y: 1, z: 0 } };
  assert.equal(editBlock(w, p, h, 1, true), false);
  assert.equal(writes, 0);
});
