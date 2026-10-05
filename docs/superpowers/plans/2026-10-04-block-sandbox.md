# Block Sandbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans inline; one independent final reviewer. The user approved the design and explicitly waived intermediate questions on 2026-10-04.

**Goal:** Deliver a playable local browser voxel sandbox and source-based learning guide.
**Architecture:** Deterministic world generation plus sparse edits, bounded chunk meshes, fixed-step collision, transactional browser saves. Plain ES modules keep game logic testable without graphics.
**Tech Stack:** Node.js built-in server/test runner, JavaScript, local Three.js, HTML/CSS, IndexedDB.
**Spec:** `docs/superpowers/specs/2026-10-04-block-sandbox-design.md`

## Global Constraints

- Project: `games/block-sandbox/`; existing stock app and data remain untouched.
- World: `-256 <= x,z < 256`, `0 <= y < 96`, horizontal chunk size 16.
- View distance: 2/3/4 chunks; middle default. Interaction reach: 6 blocks.
- Creative mode only; fixed water, indestructible bedrock, no Minecraft assets or compatible saves.
- Local server binds only `127.0.0.1:8765`; runtime has no external resource dependencies.
- Saves: IndexedDB, versioned snapshot, sparse edits including air, export/import <= 20 MiB.
- User authorized autonomous decisions and implementation; no additional design or plan approval gate.

## Review Focus

1. Fast movement and unloaded terrain must not let players cross solids (Task 2 collision substeps).
2. Concurrent saves/imports must not overwrite newer edits (Task 3 ordered writer and browser checks).
3. Tree generation at chunk boundaries must be independent of generation order (Task 1 determinism).
4. Pointer lock cancellation/focus loss must clear held keys and edits (Task 4 browser flow).
5. Wrong server on occupied port and malformed save must preserve unrelated service/current world (Task 5 server identity and Task 3 validators).

## Files and Interfaces

All paths below relative to `games/block-sandbox/`.

- `src/blocks.js`: `BLOCKS`, `isSolid(id)`, face texture metadata.
- `src/generation.js`: `Generator(seed).getBlock(x,y,z)`, `surfaceHeight(x,z)`.
- `src/world.js`: `World(seed,edits).getBlock(x,y,z)`, `setBlock(x,y,z,id)`, `loadChunk(cx,cz)`, `trimChunks(cx,cz,radius)`; `edits`, `dirty`.
- `src/meshing.js`: `meshChunk(world,cx,cz)` returns typed geometry groups `{opaque,transparent,water}`.
- `src/player.js`: `Player(world,position).update(dt,input,yaw)`, `position`, `velocity`, `flying`, `grounded`, `intersectsBlock(x,y,z)`; `safePosition(world,preferred)`.
- `src/interaction.js`: `raycast(world,origin,direction,maxDistance=6)`, `editBlock(world,player,hit,id,placing)`.
- `src/storage.js`: `validateSave(data)`, `createSnapshot(game)`, `SaveQueue(write).enqueue(snapshot)` and `BrowserStorage.read/write`.
- `src/renderer.js`, `src/ui.js`, `src/main.js`, `src/audio.js`: actual play loop, scene, DOM menu, user-gesture sound.
- `index.html`, `style.css`, `server.mjs`, `启动游戏.bat`, `scripts/vendor.mjs`, `package.json`, `package-lock.json`, `vendor/*`.
- `tests/*.test.mjs`, `README.md`, `LEARNING.md`, `VALIDATION.md` and evidence screenshots.

### Task 1: Deterministic world and chunk surfaces

Consumes block definitions; produces generator/world/mesh interfaces above.
- [x] Write tests for seed determinism, negative-coordinate edits, bedrock/bounds, tree chunk order, sparse air edits, solid occlusion and face counts from hand-built fixtures.
- [x] Run `node --test tests/world.test.mjs`; expected missing-feature failures.
- [x] Implement blocks, coordinate hashing/noise, caves/trees/water, lazy typed chunk data and sparse overrides, face-culling mesh data.
- [x] Run all available tests; expected zero failures. Commit only project files.

### Task 2: Physics and block interaction

Consumes `World.getBlock/setBlock`, `isSolid`; produces player and interaction interfaces.
- [x] Write wall/floor/ceiling, fast movement/flying/bounds, safe-spawn, ray distance/direction and placing-inside-player tests.
- [x] Run `node --test tests/physics.test.mjs`; expected missing behavior failures.
- [x] Implement fixed-step/substep axis collisions, movement, swimming/flying, safe spawn, voxel DDA and validated edits.
- [x] Run entire suite; expected zero failures. Commit tested modules.

### Task 3: Persistent saves and import validation

Consumes world edits and player/UI state; produces versioned snapshot/storage queue.
- [x] Write tests for schema/version/bounds/IDs, air-edit roundtrip, player/slot/settings validation, stale-write serialization and write-failure recovery.
- [x] Run `node --test tests/storage.test.mjs`; expected missing behavior failures.
- [x] Implement strict bounded save validation, IndexedDB transactions, defensive snapshot copy, ordered writes, read errors and file import size guard.
- [x] Run entire suite; expected zero failures. Commit storage tests and implementation.

### Task 4: Playable scene and interface

Consumes all Task 1–3 interfaces.
- [x] Add startup smoke expectations: local HTTP page/JS/vendor reachable, Chinese menu, create/continue/settings, actual WebGL world, HUD and save restore.
- [x] Verify startup smoke fails before creating app files.
- [x] Pin/install Three.js in project only; copy renderer and license to vendor with lockfile. Implement local pixel atlas, shaded chunk geometries, fog/sky/clouds, selected outline and bounded disposal.
- [x] Implement Chinese start/pause/settings/inventory dialogs, 9 slots, mouse/key inputs, lock/focus clear, simulation accumulator, incremental chunk work, audio, 5-second autosave, explicit save/export/import, failures without overwrites.
- [x] Run suite and browser smoke; record which actual user inputs automation can verify. Commit game files.

### Task 5: Launcher, delivery, learning and final validation

Consumes local game files and save/UI behavior.
- [x] Write HTTP tests for serving, traversal rejection, server identity, MIME, missing file and duplicate port handling; run RED.
- [x] Implement localhost-only static service/Windows launcher with script-relative paths and existing server identity check.
- [x] Run all Node tests, affected JS syntax checks and existing Python regression suite; record pre-existing failures if any.
- [x] Use real browser to inspect screenshots and replay edits/save/import/refresh; record performance and resource bounds.
- [x] Write README/LEARNING/VALIDATION and self-contained ZIP with vendored library, excluding node_modules and user data.
- [x] Independent final review against spec/plan. Fix important findings RED→GREEN; final full suite.
- [x] Commit only this project's artifacts, preserve original stock changes, show runnable entry and evidence.

## Plan Self Review

Interfaces have one spelling throughout. All spec sections map to world, physics, storage, scene/UI, delivery or learning tasks. Runtime dependencies stay local; security boundaries are local files/server and imports. Browser evidence remains distinct from logic-test evidence.
