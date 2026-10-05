// Optional QA utility: uses an existing Playwright installation, never a runtime dependency.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs/promises");
const path = require("node:path");
const assert = require("node:assert/strict");
async function main() {
  const out = path.join(__dirname, "../output/playwright");
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({
    channel: "msedge",
    headless: true,
    args: [
      "--enable-webgl",
      "--ignore-gpu-blocklist",
      "--enable-unsafe-swiftshader",
    ],
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    acceptDownloads: true,
  });
  const page = await context.newPage(),
    errors = [],
    external = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("request", (r) => {
    if (
      !r.url().startsWith("http://127.0.0.1:8765") &&
      !r.url().startsWith("blob:") &&
      !r.url().startsWith("data:")
    )
      external.push(r.url());
  });
  const game = (fn) =>
    page.evaluate(async (text) => {
      const { game } = await import("/src/main.js");
      return new Function("game", `return (${text})(game)`)(game);
    }, fn.toString());
  const waitGame = async (fn) => {
    const limit = Date.now() + 30000;
    while (!(await game(fn))) {
      assert.ok(Date.now() < limit, "game condition timed out");
      await page.waitForTimeout(50);
    }
  };
  const check = async () => {
    const fatal = page.locator("#fatal");
    if (await fatal.isVisible()) throw new Error(await fatal.innerText());
  };
  try {
    await page.goto("http://127.0.0.1:8765");
    await page.waitForFunction(
      () =>
        document.getElementById("home-status").textContent !== "正在准备世界…",
    );
    await check();
    await waitGame((g) => g.render.stats().pending === 0);
    await page.screenshot({ path: path.join(out, "01-home.png") });
    console.log("HOME", await page.locator("#home").innerText());
    await page.locator("#new-world").click();
    await page.locator("#seed").fill("青岚-2026");
    await page.locator("#create").click();
    await waitGame((g) => g.hasWorld);
    await check();
    if (await page.locator("#pause").isVisible())
      await page.locator("#resume").click();
    await page.waitForFunction(
      () => document.pointerLockElement === document.getElementById("scene"),
    );
    await waitGame((g) => g.render.stats().pending === 0);
    const before = await game((g) => ({ ...g.player.position }));
    await page.keyboard.down("KeyW");
    await page.waitForTimeout(500);
    await page.keyboard.up("KeyW");
    const after = await game((g) => ({ ...g.player.position }));
    assert.ok(
      Math.hypot(after.x - before.x, after.z - before.z) > 0.5,
      "real keyboard movement",
    );
    await page.keyboard.press("KeyF");
    assert.equal(await game((g) => g.player.flying), true);
    await page.keyboard.down("Space");
    await page.waitForTimeout(500);
    await page.keyboard.up("Space");
    assert.ok(
      (await game((g) => g.player.position.y)) > after.y + 1,
      "flight ascent",
    );
    // Aim at a controlled nearby fixture, then use actual mouse input for both edits.
    await game((g) => {
      const p = g.player.position,
        x = Math.floor(p.x),
        y = Math.floor(p.y + 1.62),
        z = Math.floor(p.z) - 3;
      g.world.setBlock(x, y, z, 8);
      g.yaw = 0;
      g.pitch = 0;
      g.markChanged();
      return true;
    });
    await page.waitForTimeout(200);
    const hit = await game((g) => g.hit());
    assert.ok(hit, "aimed block exists");
    await page.mouse.down({ button: "left" });
    await page.mouse.up({ button: "left" });
    assert.equal(
      await page.evaluate(async (p) => {
        const { game } = await import("/src/main.js");
        return game.world.getBlock(p.x, p.y, p.z);
      }, hit.block),
      0,
      "actual left click removal",
    );
    // A new fixture provides a deterministic adjacent placement target.
    const target = await game((g) => {
      const p = g.player.position,
        cell = {
          x: Math.floor(p.x),
          y: Math.floor(p.y + 1.62),
          z: Math.floor(p.z) - 3,
        };
      g.world.setBlock(cell.x, cell.y, cell.z, 8);
      return cell;
    });
    await page.waitForTimeout(200);
    const placeHit = await game((g) => g.hit());
    await page.mouse.down({ button: "right" });
    await page.mouse.up({ button: "right" });
    const pos = placeHit.adjacent;
    assert.equal(
      await page.evaluate(async (p) => {
        const { game } = await import("/src/main.js");
        return game.world.getBlock(p.x, p.y, p.z);
      }, pos),
      1,
      "actual right click placement",
    );
    await game((g) => {
      g.player.position = { x: 16.5, y: 54, z: 14.5 };
      g.yaw = 0.3;
      g.pitch = -0.42;
      g.markChanged();
    });
    await waitGame(
      (g) => g.render.center === "1,0,3" && game.render.stats().pending === 0,
    );
    await page.screenshot({ path: path.join(out, "02-game.png") });
    await page.keyboard.press("KeyE");
    await page.screenshot({ path: path.join(out, "04-blocks.png") });
    await page.locator(".block-choice").filter({ hasText: "砖块" }).click();
    assert.equal(await game((g) => g.slots[g.selected]), 8);
    await page.waitForFunction(
      () => document.pointerLockElement === document.getElementById("scene"),
    );
    await page.keyboard.press("Escape");
    await page.waitForTimeout(100);
    console.log(
      "ESC STATE",
      await game((g) => ({
        paused: g.paused,
        onHome: g.onHome,
        lock: document.pointerLockElement?.id,
        dialogs: [...document.querySelectorAll("dialog[open]")].map(
          (d) => d.id,
        ),
      })),
    );
    assert.equal(
      await page.locator("#pause").isVisible(),
      true,
      "Escape opens pause",
    );
    await page.locator("#save").click();
    await page.waitForFunction(() =>
      document.getElementById("save-status").textContent.startsWith("已保存"),
    );
    const saved = await game((g) => ({
      seed: g.world.seed,
      edits: [...g.world.edits],
      position: { ...g.player.position },
      slots: [...g.slots],
    }));
    const downloadPromise = page.waitForEvent("download");
    await page.locator("#export").click();
    const download = await downloadPromise;
    const backup = path.join(out, "qa-backup.json");
    await download.saveAs(backup);
    await page.screenshot({ path: path.join(out, "03-pause.png") });
    await page.reload();
    await page.waitForFunction(
      () => !document.getElementById("continue").disabled,
    );
    await check();
    const restored = await game((g) => ({
      seed: g.world.seed,
      edits: [...g.world.edits],
      position: { ...g.player.position },
      slots: [...g.slots],
    }));
    assert.deepEqual(restored, saved, "refresh restores full save");
    await page.locator("#home-settings").click();
    await page.locator("#view-distance").selectOption("2");
    await page.locator('[data-close="settings-dialog"]').click();
    await page.locator("#home-import").click();
    await page.locator("#import-file").setInputFiles(backup);
    await page.locator("#confirm-yes").click();
    await waitGame((g) => g.settings.viewDistance === 3);
    assert.equal(
      await game((g) => g.settings.viewDistance),
      3,
      "import restored old settings",
    );
    // Invalid imports must not replace the current world.
    const bad = path.join(out, "invalid.json");
    await fs.writeFile(bad, JSON.stringify({ formatVersion: 99 }));
    await page.locator("#import-file").setInputFiles(bad);
    await page.waitForTimeout(300);
    assert.equal(await game((g) => g.world.seed), saved.seed);
    assert.match(
      await page.locator("#home-status").innerText(),
      /导入失败/,
      "import error is visible on home screen",
    );
    await context.setOffline(true);
    await page.locator("#continue").click();
    await page.waitForTimeout(2000);
    await check();
    const stats = await game((g) => ({
      ...g.render.stats(),
      fps: g.fps,
      renderer: g.render.renderer
        .getContext()
        .getParameter(g.render.renderer.getContext().RENDERER),
    }));
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    const report = {
      checks: [
        "Chinese menu",
        "WebGL scene",
        "pointer lock",
        "WASD movement",
        "flight",
        "left/right mouse edits",
        "inventory",
        "save/export",
        "reload restore",
        "valid import",
        "invalid import preserves world",
        "offline play",
      ],
      stats,
      errors,
      external,
      fixture: target,
    };
    await fs.writeFile(
      path.join(out, "browser-report.json"),
      JSON.stringify(report, null, 2),
    );
    console.log(JSON.stringify(report, null, 2));
  } catch (error) {
    await page.screenshot({ path: path.join(out, "failure.png") });
    console.error("BROWSER FAIL", error);
    console.error("PAGE ERRORS", errors);
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}
main().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
