"""从 sourcemap 还原原始 TypeScript 源码。

打包后的 JS 难读，但 .js.map 里的 sourcesContent 存着打包前的原始源码，
是理解 recorder 插件（快捷键 / IPC / 录音流程）最直接的入口。
"""
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

MAP = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parent.parent / "_asar_out" / "node_modules"
    / "@business" / "qwen_recorder_plugin" / "dist" / "chunks"
    / "runtime-Uq7IKc7t.js.map")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    Path(__file__).resolve().parent.parent / "_recorder_src")

with open(MAP, "r", encoding="utf-8") as f:
    m = json.load(f)

sources = m.get("sources", [])
contents = m.get("sourcesContent", [])
print(f"sourcemap 内源文件数: {len(sources)}")
print("=" * 70)

os.makedirs(OUT, exist_ok=True)
written = []
for i, src in enumerate(sources):
    content = contents[i] if i < len(contents) else None
    if not content:
        continue
    # 保留目录结构，去掉 ../ 前缀
    safe = src.replace("\\", "/").lstrip("./").replace("..", "_")
    safe = safe.replace("webpack://", "").lstrip("/")
    dest = os.path.join(OUT, safe.replace("/", os.sep))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as g:
        g.write(content)
    written.append((safe, len(content)))

for s, n in sorted(written, key=lambda x: -x[1]):
    print(f"  {n:>8}  {s}")
print(f"\n共写出 {len(written)} 个源文件到 {OUT}")
