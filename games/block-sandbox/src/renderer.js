import * as THREE from "../vendor/three.module.js";
import { meshChunk } from "./meshing.js";
import { chunkKey } from "./blocks.js";
export function makeAtlas() {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const ctx = canvas.getContext("2d"),
    colors = [
      "#7d9e42",
      "#876545",
      "#8d6848",
      "#8b908a",
      "#dac68c",
      "#775132",
      "#b48e54",
      "#497639",
      "#b9935a",
      "#af6855",
      "#bad9d5",
      "#858a83",
      "#878b82",
      "#619daf",
      "#484d49",
      "#dcd0b3",
    ];
  for (let tile = 0; tile < 16; tile++) {
    const ox = (tile % 4) * 16,
      oy = Math.floor(tile / 4) * 16;
    ctx.fillStyle = colors[tile];
    ctx.fillRect(ox, oy, 16, 16);
    for (let y = 0; y < 16; y++)
      for (let x = 0; x < 16; x++) {
        const n = ((x * 43 + y * 71 + tile * 131) ^ ((x + y + tile) * 17)) % 11;
        ctx.fillStyle = n < 4 ? "#ffffff14" : "#00000014";
        ctx.fillRect(ox + x, oy + y, n === 0 ? 2 : 1, 1);
      }
    if (tile === 1) {
      ctx.fillStyle = "#69933e";
      ctx.fillRect(ox, oy, 16, 3);
      for (let x = 0; x < 16; x += 3) ctx.fillRect(ox + x, oy + 3, 2, 2);
    }
    if (tile === 5) {
      ctx.fillStyle = "#39291666";
      for (const x of [2, 6, 11, 14]) ctx.fillRect(ox + x, oy, 1, 16);
    }
    if (tile === 6) {
      ctx.strokeStyle = "#705232";
      for (const s of [3, 7, 11])
        ctx.strokeRect(ox + 8 - s / 2, oy + 8 - s / 2, s, s);
    }
    if (tile === 8) {
      ctx.fillStyle = "#65452b77";
      for (const y of [3, 7, 11, 15]) ctx.fillRect(ox, oy + y, 16, 1);
      ctx.fillRect(ox + 5, oy, 1, 4);
      ctx.fillRect(ox + 11, oy + 8, 1, 4);
    }
    if (tile === 9) {
      ctx.fillStyle = "#d4c3a4";
      for (const y of [0, 5, 10, 15]) ctx.fillRect(ox, oy + y, 16, 1);
      for (let y = 0; y < 15; y += 5)
        ctx.fillRect(ox + (y % 10 ? 3 : 10), oy + y, 1, 5);
    }
    if (tile === 10) {
      ctx.strokeStyle = "#ddecde";
      ctx.strokeRect(ox + 0.5, oy + 0.5, 15, 15);
      ctx.fillStyle = "#e9f3e7";
      ctx.fillRect(ox + 4, oy + 3, 2, 3);
      ctx.fillRect(ox + 7, oy + 7, 1, 3);
    }
    if (tile === 11 || tile === 12) {
      ctx.fillStyle = tile === 11 ? "#2f3432" : "#d4af87";
      for (const [x, y] of [
        [2, 4],
        [9, 2],
        [6, 11],
        [12, 10],
      ])
        ctx.fillRect(ox + x, oy + y, 2, 2);
    }
    if (tile === 13) {
      ctx.fillStyle = "#aad3d64d";
      for (const y of [4, 10]) ctx.fillRect(ox + 2, oy + y, 8, 1);
    }
  }
  return canvas;
}
export class GameRenderer {
  constructor(canvas, settings) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false,
      powerPreference: "high-performance",
    });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color("#b5d2d1");
    this.camera = new THREE.PerspectiveCamera(73, 1, 0.05, 260);
    this.camera.rotation.order = "YXZ";
    this.scene.add(new THREE.HemisphereLight(0xf0f4df, 0x74806c, 2.0));
    const sun = new THREE.DirectionalLight(0xfff0c6, 2.1);
    sun.position.set(-40, 80, 35);
    this.scene.add(sun);
    const atlas = makeAtlas();
    document.documentElement.style.setProperty(
      "--atlas",
      `url(${atlas.toDataURL()})`,
    );
    const texture = new THREE.CanvasTexture(atlas);
    texture.magFilter = texture.minFilter = THREE.NearestFilter;
    texture.generateMipmaps = false;
    texture.colorSpace = THREE.SRGBColorSpace;
    this.materials = {
      opaque: new THREE.MeshLambertMaterial({ map: texture }),
      transparent: new THREE.MeshLambertMaterial({
        map: texture,
        transparent: true,
        opacity: 0.53,
        depthWrite: false,
      }),
      water: new THREE.MeshLambertMaterial({
        map: texture,
        transparent: true,
        opacity: 0.65,
        depthWrite: false,
        side: THREE.DoubleSide,
      }),
    };
    this.meshes = new Map();
    this.pending = [];
    this.center = "";
    this.outline = new THREE.LineSegments(
      new THREE.EdgesGeometry(new THREE.BoxGeometry(1.006, 1.006, 1.006)),
      new THREE.LineBasicMaterial({
        color: 0xffffcf,
        transparent: true,
        opacity: 0.9,
      }),
    );
    this.outline.visible = false;
    this.scene.add(this.outline);
    this.clouds = new THREE.Group();
    const cloudMat = new THREE.MeshLambertMaterial({
      color: 0xfff9e8,
      transparent: true,
      opacity: 0.82,
    });
    for (let i = 0; i < 16; i++) {
      const c = new THREE.Mesh(
        new THREE.BoxGeometry(9 + (i % 3) * 4, 1.4, 4 + (i % 4)),
        cloudMat,
      );
      c.position.set(
        (i % 4) * 38 - 62,
        64 + (i % 3) * 3,
        Math.floor(i / 4) * 41 - 60,
      );
      this.clouds.add(c);
    }
    this.scene.add(this.clouds);
    this.applySettings(settings);
    window.addEventListener("resize", () => this.resize());
  }
  applySettings(settings) {
    this.settings = { ...settings };
    this.scene.fog = new THREE.Fog(
      "#b5d2d1",
      settings.viewDistance * 16 * 0.62,
      settings.viewDistance * 16 * 1.22,
    );
    this.resize();
    this.center = "";
  }
  resize() {
    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, this.settings.pixelRatio),
    );
    this.renderer.setSize(innerWidth, innerHeight, false);
    this.camera.aspect = innerWidth / innerHeight;
    this.camera.updateProjectionMatrix();
  }
  disposeChunk(key) {
    const group = this.meshes.get(key);
    if (!group) return;
    for (const mesh of group.children) mesh.geometry.dispose();
    this.scene.remove(group);
    this.meshes.delete(key);
  }
  reset() {
    for (const key of [...this.meshes.keys()]) this.disposeChunk(key);
    this.pending = [];
    this.center = "";
  }
  updateChunks(world, pos) {
    const cx = Math.floor(pos.x / 16),
      cz = Math.floor(pos.z / 16),
      r = this.settings.viewDistance,
      center = `${cx},${cz},${r}`;
    if (this.center !== center) {
      this.center = center;
      this.pending = [];
      for (let z = cz - r; z <= cz + r; z++)
        for (let x = cx - r; x <= cx + r; x++)
          if (
            x >= -16 &&
            x < 16 &&
            z >= -16 &&
            z < 16 &&
            !this.meshes.has(chunkKey(x, z))
          )
            this.pending.push({ x, z, d: (x - cx) ** 2 + (z - cz) ** 2 });
      this.pending.sort((a, b) => a.d - b.d);
      for (const key of [...this.meshes.keys()]) {
        const [x, z] = key.split(",").map(Number);
        if (Math.abs(x - cx) > r || Math.abs(z - cz) > r)
          this.disposeChunk(key);
      }
    }
    // 用户编辑优先重建；单帧分摊地形，防止一次性加载全部世界。
    for (const key of world.dirty) {
      const [x, z] = key.split(",").map(Number);
      if (
        this.meshes.has(key) &&
        !this.pending.some((p) => p.x === x && p.z === z)
      )
        this.pending.unshift({ x, z, d: 0 });
    }
    const start = performance.now();
    let count = 0;
    while (this.pending.length && count < 2 && performance.now() - start < 9) {
      const { x, z } = this.pending.shift(),
        key = chunkKey(x, z);
      this.disposeChunk(key);
      const data = meshChunk(world, x, z),
        group = new THREE.Group();
      for (const kind of ["opaque", "transparent", "water"]) {
        const d = data[kind];
        if (!d.positions.length) continue;
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute(
          "position",
          new THREE.BufferAttribute(d.positions, 3),
        );
        geometry.setAttribute(
          "normal",
          new THREE.BufferAttribute(d.normals, 3),
        );
        geometry.setAttribute("uv", new THREE.BufferAttribute(d.uvs, 2));
        geometry.computeBoundingSphere();
        group.add(new THREE.Mesh(geometry, this.materials[kind]));
      }
      this.meshes.set(key, group);
      this.scene.add(group);
      world.dirty.delete(key);
      count++;
    }
    world.trimChunks(cx, cz, r + 2);
    for (const key of world.dirty) {
      if (!this.meshes.has(key)) world.dirty.delete(key);
    }
  }
  aim(hit) {
    this.outline.visible = Boolean(hit);
    if (hit)
      this.outline.position.set(
        hit.block.x + 0.5,
        hit.block.y + 0.5,
        hit.block.z + 0.5,
      );
  }
  direction() {
    const d = new THREE.Vector3();
    this.camera.getWorldDirection(d);
    return { x: d.x, y: d.y, z: d.z };
  }
  draw(player, yaw, pitch, preview, time) {
    const p = player.position;
    if (preview) {
      const angle = 0.6 + Math.sin(time * 0.025) * 0.07;
      this.camera.position.set(
        p.x + Math.cos(angle) * 25,
        p.y + 17,
        p.z + Math.sin(angle) * 25,
      );
      this.camera.lookAt(p.x - 5, p.y - 1, p.z - 9);
      this.outline.visible = false;
    } else {
      this.camera.position.set(p.x, p.y + 1.62, p.z);
      this.camera.rotation.set(pitch, yaw, 0, "YXZ");
    }
    this.clouds.position.x = Math.sin(time * 0.01) * 5;
    this.clouds.position.z = Math.floor(p.z / 80) * 80;
    this.renderer.render(this.scene, this.camera);
  }
  stats() {
    return {
      chunks: this.meshes.size,
      triangles: this.renderer.info.render.triangles,
      geometries: this.renderer.info.memory.geometries,
      pending: this.pending.length,
    };
  }
}
