"""Build a local-only, allowlisted game ZIP; no user saves or dependency caches."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
files = [root / name for name in (
    "index.html", "style.css", "favicon.svg", "server.mjs", "启动游戏.bat",
    "README.md", "LEARNING.md", "VALIDATION.md", "package.json", "package-lock.json", ".gitignore",
)]
for folder, patterns in {"src": ["*.js"], "vendor": ["*.js", "*.txt"],
                         "tests": ["*.mjs"], "scripts": ["*.mjs", "*.cjs", "build-release.py"],
                         "evidence": ["*.png", "*-report.json"]}.items():
    for pattern in patterns:
        files.extend(sorted((root / folder).glob(pattern)))
manifest = {"app": "青岚方块", "version": "1.0.0", "sha256": {}}
for file in files:
    if not file.is_file():
        raise SystemExit(f"Missing required delivery file: {file}")
    manifest["sha256"][file.relative_to(root).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
output = root / "releases" / "青岚方块-1.0.0.zip"
output.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for file in files:
        archive.write(file, "青岚方块/" + file.relative_to(root).as_posix())
    archive.writestr("青岚方块/SHA256-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
with zipfile.ZipFile(output) as archive:
    assert archive.testzip() is None
    assert not any("node_modules/" in n or ".npm-cache/" in n or "qa-backup" in n or n.endswith(".env") for n in archive.namelist())
print(json.dumps({"zip": str(output), "files": len(files), "bytes": output.stat().st_size,
                  "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))
