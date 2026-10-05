import { World } from "./world.js";
import { Player, safePosition } from "./player.js";
import { raycast, editBlock } from "./interaction.js";
import {
  BrowserStorage,
  SaveQueue,
  createSnapshot,
  validateSave,
  DEFAULT_SETTINGS,
  MAX_IMPORT_BYTES,
} from "./storage.js";
import { DEFAULT_SLOTS } from "./blocks.js";
import { GameRenderer } from "./renderer.js";
import { GameUI } from "./ui.js";
import { GameAudio } from "./audio.js";
const canvas = document.getElementById("scene");
export class SandboxGame {
  constructor() {
    this.settings = { ...DEFAULT_SETTINGS };
    this.slots = [...DEFAULT_SLOTS];
    this.selected = 0;
    this.yaw = -0.55;
    this.pitch = -0.14;
    this.hasWorld = false;
    this.onHome = true;
    this.paused = true;
    this.keys = new Set();
    this.mouse = { left: false, right: false };
    this.changed = 0;
    this.saved = 0;
    this.generation = 0;
    this.storage = new BrowserStorage();
    this.queue = new SaveQueue((s) => this.storage.write(s));
    this.audio = new GameAudio();
    this.ui = new GameUI(this);
    this.ownsSave = true;
    this.badSave = false;
    this.transition = false;
    this.savePending = 0;
    this.lastEdit = 0;
  }
  async acquireSaveLock() {
    if (!navigator.locks) return;
    await new Promise((resolve) => {
      navigator.locks
        .request(
          "block-sandbox-editor",
          { ifAvailable: true },
          async (lock) => {
            this.ownsSave = Boolean(lock);
            resolve();
            if (lock)
              await new Promise((r) => {
                this.releaseLock = r;
              });
          },
        )
        .catch(() => {
          this.ownsSave = false;
          resolve();
        });
    });
  }
  async init() {
    await this.acquireSaveLock();
    let snapshot = null,
      error = "";
    try {
      const raw = await this.storage.read();
      if (raw) snapshot = validateSave(raw);
    } catch (e) {
      this.badSave = true;
      error =
        "存档未能读取：" + e.message + "。原存档已保留，可导入备份或确认新建。";
    }
    this.render = new GameRenderer(canvas, this.settings);
    this.initialise(snapshot, "青岚-2026");
    this.ui.home(
      error ||
        (!this.ownsSave
          ? "其他标签页正在使用存档，本页可游玩并导出备份。"
          : snapshot
            ? "找到你的世界，随时可以继续。"
            : "你的第一片世界，已经准备好了。"),
    );
    this.bindInput();
    this.lastTime = performance.now();
    this.frameCount = 0;
    this.fps = 0;
    this.fpsTime = this.lastTime;
    this.accumulator = 0;
    requestAnimationFrame((t) => this.frame(t));
    this.autosave = setInterval(() => {
      if (
        this.hasWorld &&
        this.changed !== this.saved &&
        !this.savePending &&
        !this.transition &&
        !this.badSave &&
        this.ownsSave
      )
        this.saveNow();
    }, 5000);
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        this.pause();
        if (this.hasWorld) this.saveNow();
      }
    });
    canvas.addEventListener("webglcontextlost", (event) => {
      event.preventDefault();
      this.pause();
      this.failed = true;
      this.ui.fatal(
        "图形连接中断。已保存的世界仍在；未保存的编辑可能丢失。请刷新页面恢复。",
      );
    });
    window.addEventListener("error", (event) => {
      if (!this.failed) {
        this.failed = true;
        this.pause();
        this.ui.fatal(
          "游戏发生错误：" + event.message + "。请先保留已有备份，然后刷新。",
        );
      }
    });
  }
  initialise(snapshot, seed) {
    this.generation++;
    this.clearInput();
    this.render.reset();
    this.world = new World(snapshot?.seed ?? seed, snapshot?.edits ?? []);
    this.player = new Player(
      this.world,
      safePosition(this.world, snapshot?.player.position),
    );
    this.yaw = snapshot?.player.yaw ?? -0.55;
    this.pitch = snapshot?.player.pitch ?? -0.14;
    this.player.flying = snapshot?.player.flying ?? false;
    this.slots = snapshot?.slots ? [...snapshot.slots] : [...DEFAULT_SLOTS];
    this.selected = snapshot?.selected ?? 0;
    this.settings = {
      ...DEFAULT_SETTINGS,
      ...(snapshot?.settings ?? this.settings),
    };
    this.applySettings(false);
    this.hasWorld = Boolean(snapshot);
    this.changed = 0;
    this.saved = 0;
    this.ui.hotbar();
    if (
      snapshot &&
      Math.abs(this.player.position.y - snapshot.player.position.y) > 0.01
    )
      this.ui.toast("原位置被方块堵住，已移动到安全位置。");
  }
  markChanged() {
    this.changed++;
    if (this.hasWorld)
      this.ui.status(
        this.ownsSave && !this.badSave
          ? "有新修改 · 自动保存中"
          : "内存世界 · 请导出备份",
      );
  }
  applySettings(mark = true) {
    this.audio.volume = this.settings.volume;
    this.render?.applySettings(this.settings);
    if (mark) this.markChanged();
  }
  select(i) {
    this.selected = (i + 9) % 9;
    this.markChanged();
    this.ui.hotbar();
  }
  clearInput() {
    this.keys.clear();
    this.mouse.left = this.mouse.right = false;
    this.accumulator = 0;
  }
  pause() {
    this.paused = true;
    this.clearInput();
    if (document.pointerLockElement === canvas) document.exitPointerLock();
    if (this.hasWorld && !this.onHome) this.ui.pause();
  }
  async resume() {
    if (!this.hasWorld) return;
    for (const d of document.querySelectorAll("dialog[open]")) d.close();
    this.audio.unlock();
    this.onHome = false;
    this.ui.play();
    this.clearInput();
    try {
      await canvas.requestPointerLock();
    } catch {
      this.pause();
      this.ui.toast("鼠标捕获未成功，请点击“继续探索”重试。");
    }
  }
  async home() {
    this.pause();
    await this.saveNow();
    this.onHome = true;
    this.ui.home("世界会留在这里。继续探索，或导出备份后新建。");
  }
  async newWorld(seed) {
    if (typeof seed !== "string" || !seed.trim() || seed.length > 128)
      throw new Error("请输入 1–128 字符的世界种子");
    if (this.transition) throw new Error("世界切换尚未完成，请稍后再试");
    this.transition = true;
    try {
      if (
        (this.hasWorld || this.badSave) &&
        !(await this.ui.confirm(
          "新世界将替换本机当前存档。若要保留建筑，请先取消并导出备份。",
        ))
      )
        return;
      this.pause();
      this.initialise(null, seed.trim());
      this.hasWorld = true;
      this.badSave = false;
      this.markChanged();
      document.getElementById("new-dialog").close();
      await this.saveNow(true);
      await this.resume();
    } finally {
      this.transition = false;
    }
  }
  async saveNow(allowTransition = false) {
    if (!this.hasWorld) return false;
    if (this.transition && allowTransition !== true) return false;
    if (!this.ownsSave || this.badSave) {
      this.ui.status("无法写入本地存档 · 可导出备份");
      return false;
    }
    const generation = this.generation,
      change = this.changed;
    this.savePending++;
    this.ui.status("正在保存…");
    try {
      await this.queue.enqueue(createSnapshot(this));
      if (generation === this.generation) {
        this.saved = change;
        this.ui.status(
          this.saved === this.changed
            ? "已保存 · " +
                new Date().toLocaleTimeString("zh-CN", {
                  hour: "2-digit",
                  minute: "2-digit",
                })
            : "有新修改 · 即将自动保存",
        );
      }
      return true;
    } catch (e) {
      if (generation === this.generation) {
        this.ui.status("保存失败 · 请导出备份");
        this.ui.toast("保存失败：" + e.message + "。你仍可导出当前世界。");
      }
      return false;
    } finally {
      this.savePending--;
    }
  }
  exportSave() {
    if (!this.hasWorld) return;
    const json = JSON.stringify(createSnapshot(this)),
      blob = new Blob([json], { type: "application/json" }),
      url = URL.createObjectURL(blob),
      a = document.createElement("a");
    a.href = url;
    a.download = `青岚方块-${this.world.seed.replace(/[^\p{L}\p{N}_-]/gu, "_").slice(0, 40)}-${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    this.ui.toast("备份已导出，请保留下载的 JSON 文件。");
  }
  async importSave(file) {
    if (!this.ownsSave)
      throw new Error("请先关闭另一个正在使用存档的游戏标签页，再刷新本页");
    if (file.size > MAX_IMPORT_BYTES) throw new Error("文件超过 20 MiB 限制");
    if (this.transition) throw new Error("世界切换尚未完成，请稍后再试");
    this.transition = true;
    try {
      const snapshot = validateSave(JSON.parse(await file.text()));
      if (
        (this.hasWorld || this.badSave) &&
        !(await this.ui.confirm(
          "导入备份将替换当前世界。请确认已备份当前建筑。",
        ))
      )
        return;
      this.pause();
      await this.queue.enqueue(snapshot); // 事务成功之前不能替换内存世界。
      this.initialise(snapshot);
      this.badSave = false;
      this.onHome = true;
      this.ui.home("备份已恢复。点击继续进入世界。");
      this.ui.toast("存档导入成功。");
    } finally {
      this.transition = false;
    }
  }
  bindInput() {
    document.addEventListener("pointerlockchange", () => {
      if (document.pointerLockElement === canvas) {
        this.clearInput();
        this.paused = false;
        this.onHome = false;
        this.ui.play();
      } else this.pause();
    });
    document.addEventListener("pointerlockerror", () => {
      this.pause();
      this.ui.toast("浏览器未允许捕获鼠标，请再次点击继续。");
    });
    window.addEventListener("blur", () => this.pause());
    document.addEventListener("keydown", (event) => {
      if (
        event.code === "Escape" &&
        this.hasWorld &&
        !this.onHome &&
        !document.querySelector("dialog[open]")
      ) {
        this.pause();
        return;
      }
      if (event.code === "F3") {
        event.preventDefault();
        document.getElementById("info").hidden =
          !document.getElementById("info").hidden;
        return;
      }
      if (this.paused || this.onHome) return;
      if (
        [
          "Space",
          "KeyW",
          "KeyA",
          "KeyS",
          "KeyD",
          "ControlLeft",
          "ControlRight",
          "ShiftLeft",
          "ShiftRight",
          "KeyE",
          "KeyF",
        ].includes(event.code)
      )
        event.preventDefault();
      if (event.code === "KeyE" && !event.repeat) {
        this.pause();
        this.ui.dialog("inventory-dialog");
        return;
      }
      if (event.code === "KeyF" && !event.repeat) {
        this.player.flying = !this.player.flying;
        this.player.velocity.y = 0;
        this.markChanged();
        this.ui.toast(
          this.player.flying
            ? "飞行已开启 · 空格上升 / Ctrl 下降"
            : "已切换步行",
        );
      }
      if (/^Digit[1-9]$/.test(event.code)) {
        this.select(Number(event.code.slice(5)) - 1);
        return;
      }
      this.keys.add(event.code);
    });
    document.addEventListener("keyup", (event) => this.keys.delete(event.code));
    document.addEventListener("mousemove", (event) => {
      if (this.paused || document.pointerLockElement !== canvas) return;
      this.yaw =
        (this.yaw - event.movementX * 0.0023 * this.settings.sensitivity) %
        (Math.PI * 2);
      this.pitch = Math.max(
        -1.49,
        Math.min(
          1.49,
          this.pitch - event.movementY * 0.0023 * this.settings.sensitivity,
        ),
      );
      if (event.movementX || event.movementY) this.markChanged();
    });
    canvas.addEventListener("mousedown", (event) => {
      if (this.paused || document.pointerLockElement !== canvas) return;
      this.audio.unlock();
      if (event.button === 0) this.mouse.left = true;
      if (event.button === 2) this.mouse.right = true;
      this.act(performance.now(), true);
    });
    document.addEventListener("mouseup", (event) => {
      if (event.button === 0) this.mouse.left = false;
      if (event.button === 2) this.mouse.right = false;
    });
    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
    canvas.addEventListener(
      "wheel",
      (event) => {
        if (this.paused) return;
        event.preventDefault();
        this.select(this.selected + (event.deltaY > 0 ? 1 : -1));
      },
      { passive: false },
    );
  }
  input() {
    return {
      forward: this.keys.has("KeyW"),
      back: this.keys.has("KeyS"),
      left: this.keys.has("KeyA"),
      right: this.keys.has("KeyD"),
      jump: this.keys.has("Space"),
      sprint: this.keys.has("ShiftLeft") || this.keys.has("ShiftRight"),
      down: this.keys.has("ControlLeft") || this.keys.has("ControlRight"),
    };
  }
  hit() {
    const p = this.player.position;
    return raycast(
      this.world,
      { x: p.x, y: p.y + 1.62, z: p.z },
      this.render.direction(),
      6,
    );
  }
  act(now, immediate = false) {
    if (
      this.paused ||
      (!this.mouse.left && !this.mouse.right) ||
      (!immediate && now - this.lastEdit < 180)
    )
      return;
    this.lastEdit = now;
    const placing = !this.mouse.left && this.mouse.right;
    if (
      editBlock(
        this.world,
        this.player,
        this.hit(),
        this.slots[this.selected],
        placing,
      )
    ) {
      this.markChanged();
      this.audio.play(placing);
    }
  }
  frame(now) {
    if (this.failed) return;
    try {
      const dt = Math.min((now - this.lastTime) / 1000, 0.1);
      this.lastTime = now;
      if (!this.paused && !this.onHome) {
        this.accumulator += dt;
        let steps = 0;
        const old = { ...this.player.position };
        while (this.accumulator >= 1 / 60 && steps < 6) {
          this.player.update(1 / 60, this.input(), this.yaw);
          this.accumulator -= 1 / 60;
          steps++;
        }
        if (
          Math.hypot(
            this.player.position.x - old.x,
            this.player.position.y - old.y,
            this.player.position.z - old.z,
          ) > 0.0001
        )
          this.markChanged();
      }
      this.render.updateChunks(this.world, this.player.position);
      this.render.draw(
        this.player,
        this.yaw,
        this.pitch,
        this.onHome,
        now / 1000,
      );
      const hit = !this.onHome ? this.hit() : null;
      this.render.aim(hit);
      if (!this.paused) this.act(now);
      this.frameCount++;
      if (now - this.fpsTime >= 1000) {
        this.fps = (this.frameCount * 1000) / (now - this.fpsTime);
        this.frameCount = 0;
        this.fpsTime = now;
      }
      this.ui.update(hit, this.fps, this.render.stats());
      requestAnimationFrame((t) => this.frame(t));
    } catch (e) {
      this.failed = true;
      this.pause();
      this.ui.fatal(
        "游戏运行中断：" + e.message + "。本地已保存的世界保留，可刷新恢复。",
      );
      console.error(e);
    }
  }
}
export const game = new SandboxGame();
game.init().catch((error) => {
  game.failed = true;
  game.ui.fatal(
    "无法初始化游戏：" +
      error.message +
      "。请使用支持 WebGL2 的桌面浏览器，并确认本地资源完整。",
  );
  console.error(error);
});
