import { mkdir, copyFile } from "node:fs/promises";
const root = new URL("../", import.meta.url);
await mkdir(new URL("vendor/", root), { recursive: true });
for (const name of ["three.module.js", "three.core.js"])
  await copyFile(
    new URL(`node_modules/three/build/${name}`, root),
    new URL(`vendor/${name}`, root),
  );
await copyFile(
  new URL("node_modules/three/LICENSE", root),
  new URL("vendor/THREE-LICENSE.txt", root),
);
console.log("Three.js 0.180.0 copied with MIT license; no CDN required.");
