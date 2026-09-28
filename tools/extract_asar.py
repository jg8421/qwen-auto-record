"""解析 Electron asar 包，列出并提取与「录音纪要」相关的文件。

asar 结构：
  16 字节头 (4 + headerPickleSize + headerStringSize + jsonLen) + JSON 目录 + 拼接的文件内容
"""
import json
import os
import re
import struct
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ASAR = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "Programs" / "QianwenApp"
    / "4.3.5.248" / "Resources" / "quantum_apps" / "qwen-light-apps.asar")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else (
    Path(__file__).resolve().parent.parent / "_asar_out")


def read_asar(path):
    with open(path, "rb") as f:
        head = f.read(16)
        header_size = struct.unpack("<I", head[4:8])[0]
        json_size = struct.unpack("<I", head[12:16])[0]
        f.seek(16)
        header = json.loads(f.read(json_size).decode("utf-8"))
        base = 8 + header_size
    return header, base


def walk(node, prefix=""):
    """产出 (相对路径, 条目)。"""
    for name, item in node.get("files", {}).items():
        p = f"{prefix}/{name}" if prefix else name
        if "files" in item:
            yield from walk(item, p)
        else:
            yield p, item


def main():
    header, base = read_asar(ASAR)
    entries = list(walk(header))
    print(f"asar 内文件总数: {len(entries)}")

    patterns = [a.lower() for a in sys.argv[1:]] or ["ai-recorder", "recorder", "record"]
    matched = [(p, e) for p, e in entries if any(x in p.lower() for x in patterns)]
    print(f"匹配 {matched.__len__()} 个文件\n" + "=" * 70)
    for p, e in matched[:200]:
        print(f"  {e.get('size', 0):>9}  {p}")

    os.makedirs(OUT, exist_ok=True)
    with open(ASAR, "rb") as f:
        for p, e in matched:
            if "offset" not in e:
                continue
            f.seek(base + int(e["offset"]))
            blob = f.read(int(e["size"]))
            dest = os.path.join(OUT, p.replace("/", os.sep))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as g:
                g.write(blob)
    print(f"\n已提取到 {OUT}")


if __name__ == "__main__":
    main()
