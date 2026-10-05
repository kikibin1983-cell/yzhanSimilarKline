const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
async function run() {
  const out = path.join(__dirname, "../output/playwright");
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({
    channel: "msedge",
    headless: true,
    args: ["--enable-unsafe-swiftshader"],
  });
  const ctx = await browser.newContext({
      viewport: { width: 1280, height: 720 },
    }),
    page = await ctx.newPage(),
    errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const g = (fn) =>
    page.evaluate(async (text) => {
      const { game } = await import("/src/main.js");
      return new Function("g", `return (${text})(g)`)(game);
    }, fn.toString());
  const waitGame = async (fn) => {
    const limit = Date.now() + 30000;
    while (!(await g(fn))) {
      assert.ok(Date.now() < limit, "game condition timed out");
      await page.waitForTimeout(50);
    }
  };
  try {
    await page.goto("http://127.0.0.1:8765");
    await page.waitForFunction(
      () =>
        document.getElementById("home-status").textContent !== "正在准备世界…",
    );
    await page.locator("#home-settings").click();
    await page.locator("#view-distance").selectOption("2");
    await page.locator('[data-close="settings-dialog"]').click();
    await page.locator("#new-world").click();
    await page.locator("#create").click();
    await waitGame((g) => g.hasWorld);
    assert.equal(
      await g((g) => g.settings.viewDistance),
      2,
      "new world preserves chosen settings",
    );
    await page.waitForFunction(
      () => document.pointerLockElement?.id === "scene",
    );
    await page.keyboard.press("Escape");
    // Real second page must not own write access to the same origin save.
    const other = await ctx.newPage();
    await other.goto("http://127.0.0.1:8765");
    await other.waitForFunction(
      () =>
        document.getElementById("home-status").textContent !== "正在准备世界…",
    );
    assert.equal(
      await other.evaluate(async () => {
        const { game } = await import("/src/main.js");
        return game.ownsSave;
      }),
      false,
    );
    await other.close();
    // Inject failure only at external storage boundary; observe actual game behavior.
    await g((g) => {
      g.originalWrite = g.storage.write.bind(g.storage);
      g.storage.write = async () => {
        throw new Error("quota simulation");
      };
      g.world.setBlock(2, 70, 2, 8);
      g.markChanged();
    });
    await page.locator("#save").click();
    await page.waitForFunction(() =>
      document.getElementById("save-status").textContent.includes("保存失败"),
    );
    assert.equal(await g((g) => g.world.getBlock(2, 70, 2)), 8);
    await g((g) => {
      g.storage.write = g.originalWrite;
      delete g.originalWrite;
    });
    await page.locator("#save").click();
    await page.waitForFunction(() =>
      document.getElementById("save-status").textContent.startsWith("已保存"),
    );
    // Delay actual imported write; ordinary saves and another replacement must not overtake it.
    const importData = await page.evaluate(async () => {
      const { game } = await import("/src/main.js");
      const { createSnapshot } = await import("/src/storage.js");
      const s = createSnapshot(game);
      s.seed = "race-import";
      return s;
    });
    const raceFile = path.join(out, "race-import.json");
    await fs.writeFile(raceFile, JSON.stringify(importData));
    await g((g) => {
      g.originalWrite = g.storage.write.bind(g.storage);
      g.writeSeeds = [];
      g.storage.write = async (s) => {
        if (s.seed === "race-import")
          await new Promise((r) => {
            g.releaseImport = r;
          });
        await g.originalWrite(s);
        g.writeSeeds.push(s.seed);
      };
    });
    await page.locator("#to-home").click();
    await page.locator("#home-import").click();
    await page.locator("#import-file").setInputFiles(raceFile);
    await page.locator("#confirm-yes").click();
    await waitGame((g) => Boolean(g.releaseImport));
    assert.equal(
      await g(async (g) => {
        try {
          await g.newWorld("must-not-replace");
          return false;
        } catch (e) {
          return e.message.includes("切换尚未完成");
        }
      }),
      true,
      "new-world replacement cannot reenter import",
    );
    assert.equal(
      await g(async (g) => {
        try {
          await g.importSave({ size: 1 });
          return false;
        } catch (e) {
          return e.message.includes("切换尚未完成");
        }
      }),
      true,
      "another import cannot reenter",
    );
    await g((g) => {
      g.pendingOrdinary = g.saveNow();
      return true;
    });
    await g((g) => g.releaseImport());
    await waitGame((g) => !g.transition);
    await g((g) => g.queue.tail);
    const skipped = await g((g) => g.pendingOrdinary);
    assert.equal(skipped, false, "ordinary save is blocked during import");
    assert.equal(
      await g(async (g) => (await g.storage.read()).seed),
      "race-import",
      "import cannot be overwritten by old-world snapshot",
    );
    await g((g) => {
      g.storage.write = g.originalWrite;
      delete g.originalWrite;
      delete g.releaseImport;
      delete g.writeSeeds;
    });
    // Corrupt stored data using actual IndexedDB, reload, and verify no auto-overwrite.
    await g(async (g) => {
      await g.queue.tail;
      await g.storage.write({ formatVersion: 99, note: "preserve-me" });
    });
    await page.reload();
    await page.waitForFunction(() =>
      document
        .getElementById("home-status")
        .textContent.includes("原存档已保留"),
    );
    await page.waitForTimeout(5100);
    assert.deepEqual(await g((g) => g.storage.read()), {
      formatVersion: 99,
      note: "preserve-me",
    });
    await page.locator("#new-world").click();
    await page.locator("#create").click();
    await page.locator("#confirm-no").click();
    assert.deepEqual(await g((g) => g.storage.read()), {
      formatVersion: 99,
      note: "preserve-me",
    });
    // Recover only after explicit in-game confirmation.
    await page.locator("#create").click();
    await page.locator("#confirm-yes").click();
    await waitGame((g) => g.hasWorld && !g.badSave);
    await page.waitForFunction(
      () => document.pointerLockElement?.id === "scene",
    );
    await page.keyboard.press("KeyF");
    await g((g) => {
      g.settings.viewDistance = 2;
      g.applySettings();
    });
    // Travel through distant regions; render and voxel caches must remain bounded.
    const sizes = [];
    for (const [x, z] of [
      [80, 80],
      [-160, 120],
      [200, -200],
      [0.5, 0.5],
    ]) {
      await page.evaluate(
        async (p) => {
          const { game } = await import("/src/main.js");
          game.player.position = { x: p.x, y: 70, z: p.z };
          game.markChanged();
        },
        { x, z },
      );
      await waitGame((g) => {
        const p = g.player.position;
        return (
          g.render.center ===
            `${Math.floor(p.x / 16)},${Math.floor(p.z / 16)},${g.settings.viewDistance}` &&
          g.render.stats().pending === 0
        );
      });
      const s = await g((g) => ({
        ...g.render.stats(),
        cachedChunks: g.world.chunks.size,
      }));
      assert.ok(s.chunks <= 25);
      assert.ok(s.cachedChunks <= 81);
      sizes.push(s);
    }
    const gpu = await g((g) => {
      const gl = g.render.renderer.getContext(),
        ext = gl.getExtension("WEBGL_debug_renderer_info");
      return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "unavailable";
    });
    await page.keyboard.press("Escape");
    assert.equal(await g((g) => Boolean(g.failed)), false);
    assert.deepEqual(errors, []);
    const result = {
      checks: [
        "new-world settings",
        "exclusive save lock",
        "write failure preserves memory",
        "save recovery",
        "import write race barrier",
        "corrupt save not overwritten",
        "cancel leaves corrupt save",
        "confirmed recovery",
        "bounded meshes and voxel caches",
      ],
      sizes,
      gpu,
      errors,
    };
    await fs.writeFile(
      path.join(out, "edge-report.json"),
      JSON.stringify(result, null, 2),
    );
    console.log(JSON.stringify(result, null, 2));
  } finally {
    await browser.close();
  }
}
run().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
