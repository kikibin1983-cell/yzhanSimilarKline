import http from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { spawn } from "node:child_process";
const ROOT = path.dirname(fileURLToPath(import.meta.url));
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
};
export function createGameServer() {
  return http.createServer(async (req, res) => {
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("X-Content-Type-Options", "nosniff");
    try {
      if (req.method !== "GET" && req.method !== "HEAD") {
        res.writeHead(405);
        res.end();
        return;
      }
      const p = decodeURIComponent(
        new URL(req.url, "http://localhost").pathname,
      );
      if (p === "/__block_sandbox_health") {
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify({ app: "block-sandbox", version: "1.0.0" }));
        return;
      }
      const name = p === "/" ? "index.html" : p.slice(1);
      const allowed =
        ["index.html", "style.css", "favicon.svg"].includes(name) ||
        /^src\/[a-z-]+\.js$/.test(name) ||
        /^vendor\/(three\.(module|core)\.js|THREE-LICENSE\.txt)$/.test(name);
      if (!allowed) {
        res.writeHead(403);
        res.end("Forbidden");
        return;
      }
      const body = await readFile(path.join(ROOT, name));
      res.writeHead(200, {
        "Content-Type": TYPES[path.extname(name)] ?? "application/octet-stream",
      });
      res.end(req.method === "HEAD" ? undefined : body);
    } catch (error) {
      res.writeHead(error.code === "ENOENT" ? 404 : 400);
      res.end(error.code === "ENOENT" ? "Not found" : "Bad request");
    }
  });
}
function openBrowser(url) {
  if (process.platform === "win32")
    spawn("rundll32", ["url.dll,FileProtocolHandler", url], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
    }).unref();
  else
    spawn(process.platform === "darwin" ? "open" : "xdg-open", [url], {
      detached: true,
      stdio: "ignore",
    }).unref();
}
export async function startServer(port = 8765, open = false) {
  const server = createGameServer(),
    url = `http://127.0.0.1:${port}`;
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  }).catch(async (error) => {
    if (error.code !== "EADDRINUSE") throw error;
    try {
      const r = await fetch(url + "/__block_sandbox_health", {
        signal: AbortSignal.timeout(1500),
      });
      if ((await r.json()).app === "block-sandbox") {
        if (open) openBrowser(url);
        console.log("Game is already running: " + url);
        return;
      }
    } catch {}
    throw new Error(
      `Port ${port} is occupied by another application. Close it or use --port=8766 (different port has separate browser saves).`,
    );
  });
  if (server.listening) {
    console.log(
      `Block Sandbox: ${url}\nKeep this process running while playing. Ctrl+C stops the server.`,
    );
    if (open) openBrowser(url);
  }
  return server;
}
if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const value = process.argv.find((a) => a.startsWith("--port=")),
    port = value ? Number(value.split("=")[1]) : 8765;
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    console.error("Invalid port");
    process.exitCode = 1;
  } else
    startServer(port, process.argv.includes("--open")).catch((e) => {
      console.error(e.message);
      process.exitCode = 1;
    });
}
