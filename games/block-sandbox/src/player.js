import { isSolid } from "./blocks.js";
const HALF = 0.3,
  HEIGHT = 1.8,
  EPS = 0.00001;
export function intersectsSolid(world, pos) {
  for (
    let x = Math.floor(pos.x - HALF + EPS);
    x <= Math.floor(pos.x + HALF - EPS);
    x++
  )
    for (
      let y = Math.floor(pos.y + EPS);
      y <= Math.floor(pos.y + HEIGHT - EPS);
      y++
    )
      for (
        let z = Math.floor(pos.z - HALF + EPS);
        z <= Math.floor(pos.z + HALF - EPS);
        z++
      )
        if (isSolid(world.getBlock(x, y, z))) return true;
  return false;
}
export function safePosition(world, preferred) {
  if (
    preferred &&
    Number.isFinite(preferred.x) &&
    Number.isFinite(preferred.y) &&
    Number.isFinite(preferred.z)
  ) {
    const p = {
      x: Math.max(-255.69, Math.min(255.69, preferred.x)),
      y: Math.max(1, Math.min(94, preferred.y)),
      z: Math.max(-255.69, Math.min(255.69, preferred.z)),
    };
    if (!intersectsSolid(world, p)) return p;
    for (let y = Math.ceil(p.y); y < 94; y++) {
      const next = { x: p.x, y, z: p.z };
      if (
        !intersectsSolid(world, next) &&
        isSolid(world.getBlock(Math.floor(p.x), y - 1, Math.floor(p.z)))
      )
        return next;
    }
  }
  for (let r = 0; r < 100; r += 2)
    for (let dx = -r; dx <= r; dx += 2)
      for (let dz = -r; dz <= r; dz += 2) {
        if (r > 0 && Math.abs(dx) !== r && Math.abs(dz) !== r) continue;
        const x = dx + 0.5,
          z = dz + 0.5;
        for (let y = 85; y >= 1; y--) {
          const below = world.getBlock(dx, y - 1, dz);
          if (isSolid(below) && below !== 6 && below !== 5) {
            if (
              world.getBlock(dx, y, dz) === 0 &&
              world.getBlock(dx, y + 1, dz) === 0 &&
              !intersectsSolid(world, { x, y, z })
            )
              return { x, y, z };
            // 此列地表湿润或堵塞，试下一列；不能继续向下选中洞穴。
            break;
          }
        }
      }
  throw new Error("找不到安全出生点");
}
export class Player {
  constructor(world, position) {
    this.world = world;
    this.position = { ...position };
    this.velocity = { x: 0, y: 0, z: 0 };
    this.flying = false;
    this.grounded = false;
    this.swimming = false;
  }
  intersectsBlock(x, y, z) {
    const p = this.position;
    return (
      p.x + HALF > x &&
      p.x - HALF < x + 1 &&
      p.y + HEIGHT > y &&
      p.y < y + 1 &&
      p.z + HALF > z &&
      p.z - HALF < z + 1
    );
  }
  moveAxis(axis, amount) {
    if (!amount) return;
    const p = this.position;
    p[axis] += amount;
    let collision = false;
    const min = {
      x: Math.floor(p.x - HALF + EPS),
      y: Math.floor(p.y + EPS),
      z: Math.floor(p.z - HALF + EPS),
    };
    const max = {
      x: Math.floor(p.x + HALF - EPS),
      y: Math.floor(p.y + HEIGHT - EPS),
      z: Math.floor(p.z + HALF - EPS),
    };
    for (let x = min.x; x <= max.x; x++)
      for (let y = min.y; y <= max.y; y++)
        for (let z = min.z; z <= max.z; z++) {
          if (!isSolid(this.world.getBlock(x, y, z))) continue;
          collision = true;
          const boundary = { x, y, z }[axis];
          const target =
            amount > 0
              ? boundary - (axis === "y" ? HEIGHT : HALF) - EPS
              : boundary + 1 + (axis === "y" ? 0 : HALF) + EPS;
          p[axis] =
            amount > 0 ? Math.min(p[axis], target) : Math.max(p[axis], target);
        }
    if (collision) {
      this.velocity[axis] = 0;
      if (axis === "y" && amount < 0) this.grounded = true;
    }
  }
  update(dt, input, yaw) {
    dt = Math.min(0.1, Math.max(0, dt));
    const f = (input.forward ? 1 : 0) - (input.back ? 1 : 0),
      r = (input.right ? 1 : 0) - (input.left ? 1 : 0),
      len = Math.hypot(f, r) || 1;
    this.swimming =
      this.world.getBlock(
        Math.floor(this.position.x),
        Math.floor(this.position.y + 0.6),
        Math.floor(this.position.z),
      ) === 12;
    const speed = this.flying
      ? input.sprint
        ? 17
        : 8
      : this.swimming
        ? 2.8
        : input.sprint
          ? 7
          : 4.6;
    this.velocity.x = ((-Math.sin(yaw) * f + Math.cos(yaw) * r) / len) * speed;
    this.velocity.z = ((-Math.cos(yaw) * f - Math.sin(yaw) * r) / len) * speed;
    if (this.flying)
      this.velocity.y = ((input.jump ? 1 : 0) - (input.down ? 1 : 0)) * speed;
    else if (this.swimming)
      this.velocity.y = input.jump
        ? 3.5
        : Math.max(-3, this.velocity.y - 6 * dt);
    else {
      if (input.jump && this.grounded) this.velocity.y = 8.4;
      this.velocity.y = Math.max(-36, this.velocity.y - 24 * dt);
    }
    this.grounded = false;
    const n = Math.max(
      1,
      Math.ceil(
        (Math.max(
          Math.abs(this.velocity.x),
          Math.abs(this.velocity.y),
          Math.abs(this.velocity.z),
        ) *
          dt) /
          0.18,
      ),
    );
    for (let i = 0; i < n; i++)
      for (const axis of ["x", "z", "y"])
        this.moveAxis(axis, (this.velocity[axis] * dt) / n);
    if (this.position.y > 94) {
      this.position.y = 94;
      this.velocity.y = Math.min(0, this.velocity.y);
    }
  }
}
