"""非侵入式验证：千问到底注册了哪些全局快捷键。

原理：RegisterHotKey 对同一组「修饰键+主键」是全局唯一的。
若千问已注册 Ctrl+/，我们再去注册同一个组合就会失败（错误 1409
ERROR_HOTKEY_ALREADY_REGISTERED）。因此无需真的按快捷键、
也就不会弹出录音窗口或开始录音，就能判定快捷键是否存在。

已知对照组：Alt+Space（千问唤起）、Alt+Shift+A（截图）。
"""
import ctypes
import sys
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK = {
    "space": 0x20, "a": 0x41, "r": 0x52, "q": 0x51, "s": 0x53, "d": 0x44,
    "z": 0x5A, "1": 0x31, "/": 0xBF, "?": 0xBF, ".": 0xBE, ",": 0xBC,
    "m": 0x4D, "n": 0x4E, "v": 0x56,
}

ERROR_HOTKEY_ALREADY_REGISTERED = 1409


def try_register(mods: int, vk: int) -> tuple[bool, int]:
    """尝试注册；返回 (是否成功, 错误码)。成功则立刻注销，不留痕迹。"""
    # 用一个只属于本进程的假窗口句柄（HWND_MESSAGE 风格不需要真窗口，
    # 传 NULL 亦可，注册仍会与全局冲突检测）
    ok = user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk)
    if ok:
        user32.UnregisterHotKey(None, 1)
        return True, 0
    return False, ctypes.get_last_error()


CANDIDATES = [
    ("Alt+Space      【已知：唤起】", MOD_ALT, "space"),
    ("Alt+Shift+A    【已知：截图】", MOD_ALT | MOD_SHIFT, "a"),
    ("Ctrl+/         【用户所说：录音】", MOD_CONTROL, "/"),
    ("Ctrl+Alt+/     ", MOD_CONTROL | MOD_ALT, "/"),
    ("Alt+/          ", MOD_ALT, "/"),
    ("Ctrl+Shift+/   ", MOD_CONTROL | MOD_SHIFT, "/"),
    ("Ctrl+.         ", MOD_CONTROL, "."),
    ("Ctrl+Alt+R     ", MOD_CONTROL | MOD_ALT, "r"),
    ("Ctrl+Alt+Q     ", MOD_CONTROL | MOD_ALT, "q"),
    ("Ctrl+Shift+R   ", MOD_CONTROL | MOD_SHIFT, "r"),
    ("Ctrl+Alt+Space ", MOD_CONTROL | MOD_ALT, "space"),
    ("Win+Shift+R    ", MOD_WIN | MOD_SHIFT, "r"),
    # 反例：几乎不可能被注册，用来证明测试方法本身有效
    ("Ctrl+Alt+Shift+F9 【反例：应可注册】", MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x78),
]

print("=" * 66)
print("全局快捷键占用探测（被占用 = 该应用已注册）")
print("=" * 66)

occupied, free = [], []
for label, mods, key in CANDIDATES:
    vk = VK.get(key, key if isinstance(key, int) else None)
    ok, err = try_register(mods, vk)
    if ok:
        free.append(label)
        print(f"  [可注册 · 未被占用] {label}")
    else:
        tag = "已被占用" if err == ERROR_HOTKEY_ALREADY_REGISTERED else f"失败(err={err})"
        occupied.append(label)
        print(f"  [{tag}] {label}")

print()
print("判决：Ctrl+/ 是否被占用 ->",
      "是（说明确实是千问注册的录音快捷键）"
      if any("Ctrl+/" in x for x in occupied) else
      "否（Ctrl+/ 可能不是全局快捷键）")
