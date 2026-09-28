"""在千问客户端二进制/资源里搜字符串，找「录音纪要」的深链路由或 IPC 入口。

用法: python scan_qianwen.py <关键词1> <关键词2> ...
默认搜 qianwen:// 深链与录音相关标志。
"""
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP = Path(os.path.expandvars(r"%LOCALAPPDATA%")) / "Programs" / "QianwenApp"

TARGETS = [
    os.path.join(APP, "qianwen.exe"),
    os.path.join(APP, "qianwen_proxy.exe"),
    os.path.join(APP, "4.3.5.248", "resources.pak"),
    os.path.join(APP, "4.3.5.248", "Resources", "webui.pak"),
    os.path.join(APP, "4.3.5.248", "Resources", "quantum_apps", "qwen-light-apps.asar"),
    os.path.join(APP, "4.3.5.248", "Resources", "quantum_apps", "cowork-agent-host.asar"),
    os.path.join(APP, "4.3.5.248", "Resources", "qianwen_shell", "shell_ffi.dll"),
    os.path.join(APP, "4.3.5.248", "Resources", "qianwen_shell", "QianwenShellEmbedded.dll"),
]

CHUNK = 8 * 1024 * 1024
OVERLAP = 4096


def iter_strings(data: bytes, minlen: int = 5):
    """从字节里抽可打印 ASCII 串。"""
    for m in re.finditer(rb"[\x20-\x7e]{%d,}" % minlen, data):
        yield m.group().decode("ascii", "replace")


def iter_utf16(data: bytes, minlen: int = 4):
    """抽 UTF-16LE 里的 ASCII 串（exe/dll 的宽字符串，路由名多在这里）。"""
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % minlen, data):
        try:
            yield m.group().decode("utf-16-le", "replace")
        except Exception:
            continue


def iter_utf8_cjk(data: bytes, minlen: int = 2):
    """UTF-8 里含中文的串（asar/pak 的界面文案是 UTF-8）。"""
    text = data.decode("utf-8", "ignore")
    for m in re.finditer(r"[\u4e00-\u9fff][\u4e00-\u9fff\w\s:：/\.\-]{0,60}", text):
        yield m.group()


def scan_file(path: str, needles: list[str]) -> list[tuple[str, str]]:
    hits = []
    if not os.path.exists(path):
        return hits
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        offset = 0
        carry = b""
        while offset < size:
            data = f.read(CHUNK)
            if not data:
                break
            buf = carry + data
            for s in list(iter_strings(buf)) + list(iter_utf16(buf)) + list(iter_utf8_cjk(buf)):
                low = s.lower()
                for n in needles:
                    if n in low:
                        hits.append((s[:400], path))
                        break
            carry = buf[-OVERLAP:]
            offset += len(data)
    return hits


def main():
    needles = [a.lower() for a in sys.argv[1:]] or [
        "qianwen://", "ai-record", "ai_record", "录音纪要", "recording",
        "startrecord", "globalrecord",
    ]
    print(f"搜关键词: {needles}\n" + "=" * 70)
    seen = set()
    for t in TARGETS:
        if not os.path.exists(t):
            print(f"[跳过] 不存在: {t}")
            continue
        print(f"\n### {os.path.basename(t)}  ({os.path.getsize(t)/1048576:.1f} MB)")
        hits = scan_file(t, needles)
        uniq = 0
        for s, _ in hits:
            key = s.strip()[:200]
            if key in seen:
                continue
            seen.add(key)
            uniq += 1
            if uniq <= 40:
                print("   ", s.strip()[:300])
        print(f"    -> 命中 {len(hits)} 条，去重后新增 {uniq} 条")


if __name__ == "__main__":
    main()
