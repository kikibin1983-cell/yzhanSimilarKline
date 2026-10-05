import { BLOCKS, BUILDABLE } from "./blocks.js";
const $ = (id) => document.getElementById(id);
export class GameUI {
  constructor(game) {
    this.game = game;
    this.toastTimer = 0;
    this.setupInventory();
    const bind = (id, fn) =>
      $(id).addEventListener("click", () =>
        Promise.resolve()
          .then(fn)
          .catch((e) => this.toast(e.message)),
      );
    bind("continue", () => game.resume());
    bind("resume", () => game.resume());
    bind("to-home", () => game.home());
    bind("new-world", () => this.dialog("new-dialog"));
    bind("save", () => game.saveNow());
    bind("export", () => game.exportSave());
    for (const prefix of ["home", "pause"]) {
      bind(`${prefix}-settings`, () => this.openSettings());
      bind(`${prefix}-help`, () => this.dialog("help-dialog"));
    }
    bind("home-import", () => $("import-file").click());
    bind("random-seed", () => {
      $("seed").value = "青岚-" + Math.random().toString(36).slice(2, 8);
    });
    $("new-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const seed = $("seed").value.trim();
      if (!seed) return;
      const button = $("create");
      button.disabled = true;
      try {
        await game.newWorld(seed);
      } catch (e) {
        this.toast(e.message);
      } finally {
        button.disabled = false;
      }
    });
    $("import-file").addEventListener("change", async (event) => {
      const file = event.target.files[0];
      event.target.value = "";
      if (file)
        try {
          await game.importSave(file);
        } catch (e) {
          this.toast("导入失败：" + e.message);
        }
    });
    for (const el of document.querySelectorAll("[data-close]"))
      el.addEventListener("click", () => {
        const id = el.dataset.close;
        $(id).close();
        if (id === "inventory-dialog") game.resume();
      });
    for (const [id, key] of [
      ["view-distance", "viewDistance"],
      ["sensitivity", "sensitivity"],
      ["volume", "volume"],
      ["pixel-ratio", "pixelRatio"],
    ])
      $(id).addEventListener("input", () => {
        game.settings[key] = Number($(id).value);
        this.settingLabels();
        game.applySettings();
      });
    $("confirm-no").addEventListener("click", () => this.finishConfirm(false));
    $("confirm-yes").addEventListener("click", () => this.finishConfirm(true));
    $("confirm-dialog").addEventListener("cancel", (event) => {
      event.preventDefault();
      this.finishConfirm(false);
    });
  }
  icon(id) {
    const icon = document.createElement("span");
    icon.className = "block-icon";
    const tile = BLOCKS[id].tile;
    icon.style.backgroundPosition = `${((tile % 4) / 3) * 100}% ${(Math.floor(tile / 4) / 3) * 100}%`;
    return icon;
  }
  setupInventory() {
    for (const id of BUILDABLE) {
      const b = document.createElement("button");
      b.className = "block-choice";
      b.append(this.icon(id), document.createTextNode(BLOCKS[id].name));
      b.addEventListener("click", () => {
        this.game.slots[this.game.selected] = id;
        this.game.markChanged();
        this.hotbar();
        $("inventory-dialog").close();
        this.game.resume();
      });
      $("inventory-grid").append(b);
    }
  }
  hotbar() {
    const bar = $("hotbar");
    bar.replaceChildren();
    this.game.slots.forEach((id, i) => {
      const b = document.createElement("button");
      b.className = "slot" + (i === this.game.selected ? " active" : "");
      b.title = `${i + 1} ${BLOCKS[id].name}`;
      b.setAttribute("aria-label", b.title);
      const n = document.createElement("small");
      n.textContent = i + 1;
      b.append(n, this.icon(id));
      b.addEventListener("click", () => this.game.select(i));
      bar.append(b);
    });
  }
  toast(text) {
    $("toast").textContent = text;
    $("toast").classList.add("show");
    clearTimeout(this.toastTimer);
    this.toastTimer = setTimeout(
      () => $("toast").classList.remove("show"),
      3500,
    );
    if (this.game.onHome) $("home-status").textContent = text;
  }
  dialog(id) {
    if (this.game.hasWorld && !this.game.onHome) this.game.pause();
    if (!$(id).open) $(id).showModal();
  }
  confirm(message) {
    $("confirm-message").textContent = message;
    $("confirm-dialog").showModal();
    return new Promise((resolve) => {
      this.confirmResolve = resolve;
    });
  }
  finishConfirm(value) {
    $("confirm-dialog").close();
    this.confirmResolve?.(value);
    this.confirmResolve = null;
  }
  openSettings() {
    const s = this.game.settings;
    for (const [id, key] of [
      ["view-distance", "viewDistance"],
      ["sensitivity", "sensitivity"],
      ["volume", "volume"],
      ["pixel-ratio", "pixelRatio"],
    ])
      $(id).value = s[key];
    this.settingLabels();
    this.dialog("settings-dialog");
  }
  settingLabels() {
    $("sensitivity-value").textContent = Number($("sensitivity").value).toFixed(
      1,
    );
    $("volume-value").textContent =
      Math.round(Number($("volume").value) * 100) + "%";
  }
  home(status) {
    document.body.className = "";
    $("home").hidden = false;
    $("pause").hidden = true;
    $("hud").hidden = true;
    $("continue").disabled = !this.game.hasWorld;
    $("home-status").textContent = status;
  }
  play() {
    document.body.className = "playing";
    $("home").hidden = true;
    $("pause").hidden = true;
    $("hud").hidden = false;
    this.hotbar();
  }
  pause() {
    document.body.className = "playing paused";
    $("home").hidden = true;
    $("hud").hidden = false;
    $("pause").hidden = false;
  }
  status(text) {
    $("save-status").textContent = text;
    $("pause-status").textContent = text;
    if (this.game.onHome) $("home-status").textContent = text;
  }
  update(hit, fps, stats) {
    $("mode").textContent =
      `创造 · ${this.game.player.flying ? "飞行" : this.game.player.swimming ? "水中" : "步行"}`;
    $("target-label").textContent = hit
      ? BLOCKS[this.game.world.getBlock(hit.block.x, hit.block.y, hit.block.z)]
          .name
      : BLOCKS[this.game.slots[this.game.selected]].name;
    const p = this.game.player.position;
    $("info").textContent =
      `${Math.round(fps)} FPS · ${stats.triangles.toLocaleString()} 三角形\n坐标 ${p.x.toFixed(1)} / ${p.y.toFixed(1)} / ${p.z.toFixed(1)}\n区块 ${stats.chunks} · 生成队列 ${stats.pending}\n世界种子 ${this.game.world.seed}`;
  }
  fatal(message) {
    $("fatal-message").textContent = message;
    $("fatal").hidden = false;
  }
}
