# 千问会议自动录音

Windows 小工具：**检测到你在开会（Teams / Zoom / 微信 / 腾讯会议 / 飞书 / 钉钉等），自动唤起千问客户端的「录音纪要」开始录音。**

不用改千问、不用调 API、不做任何破解——它只是替你按一下千问自己的录音快捷键。

---

## 它解决什么问题

开会时经常忘记录音，等想起来会已经开一半了。这个工具常驻后台，盯着"你是不是在通话"，一旦开始就自动帮你开录。

判断依据是 **Windows 音频会话（WASAPI）**：通话时那个应用必然持有**活动的麦克风会话**。这比看窗口标题靠谱得多——

- 微信新版通话跑在 `WeChatAppEx.exe` 子进程里，窗口标题抓不准；
- Zoom / Teams 常驻后台，光看"进程在不在"会误判成一直在开会。

---

## 工作原理

```
① 轮询音频会话，看目标应用有没有在占用麦克风
        ↓ 有（且持续 N 秒，防抖）
② 检查千问在不在跑 —— 不在就先把它拉起来，并等它加载完录音插件
        ↓
③ 发送千问的录音快捷键（右 Ctrl + /）
        ↓
④ 几秒后回头确认"真的在录了吗"，没录上就自动补发（最多重试 2 次）
        ↓
⑤ 录音结束后，把音频自动复制到指定备份目录
```

几个设计上的取舍：

- **防抖**：通话必须持续 `start_debounce_sec` 秒才触发，避免微信弹个语音消息就开录。
- **防重**：同一机制反查"千问是不是已经在录音"，正在录就不重复触发。
- **冷却**：两次触发之间有最小间隔，避免连续误触。
- **先拉起再按键**：千问没启动时，光发快捷键是对着空气按。所以会先启动它，
  等 12 秒让录音插件把键盘钩子挂上，再发键。

---

## 环境要求

- **Windows 10 / 11**
- **Python 3.9+**
- [千问 PC 客户端](https://www.qianwen.com/)（已登录）
- 依赖：`pystray`、`Pillow`、`pycaw`、`comtypes`、`psutil`
  （`audio_capture.py` 另需 `soundcard`、`numpy`、`websocket-client`）

---

## 安装

```powershell
git clone https://github.com/<你的用户名>/qwen-auto-record.git
cd qwen-auto-record

# 一键：建环境 + 装依赖 + 注册开机自启
powershell -NoProfile -ExecutionPolicy Bypass -File "安装与开机自启.ps1"
```

脚本会做三件事：

1. 在 `%LOCALAPPDATA%\QwenAutoRecord\venv` 建虚拟环境并装依赖
   （**故意不放在项目目录**：venv 有几千个小文件，放进 OneDrive 之类的同步盘会又慢又容易坏）
2. 注册计划任务 `QwenAutoRecord`，**登录时自动启动**
3. 打印核对结果

> 想取消开机自启：
> `Unregister-ScheduledTask -TaskName QwenAutoRecord -Confirm:$false`

---

## 使用

| 操作 | 方式 |
|---|---|
| 启动 | 双击 `启动自动录音.bat` |
| 停止 | 双击 `停止自动录音.bat`，或右键托盘图标 → 退出 |
| 控制面板 | <http://127.0.0.1:8765/>（或右键托盘图标） |
| 看状态 | 托盘图标颜色：绿=监听中，红=录音中，灰=已暂停 |

**托盘右键菜单**：打开控制面板 / 立即录音一次 / 暂停或恢复监听 / 打开日志 / 打开录音文件夹 / 退出。

**控制面板**能看当前状态、各应用的进程与麦克风占用、历史录音、运行日志，也能直接改配置（勾选启用哪些应用、改快捷键与时间参数），保存即生效。

---

## 配置

首次运行会生成 `config.json`（可参考 `config.example.json`）：

```jsonc
{
  "apps": {
    "teams":  { "enabled": true,  "label": "Teams",
                "processes": ["ms-teams.exe", "teams.exe"] },
    "zoom":   { "enabled": true,  "label": "Zoom",
                "processes": ["zoom.exe"] },
    "wechat": { "enabled": true,  "label": "微信",
                "processes": ["weixin.exe", "wechatappex.exe"] }
  },

  "trigger": { "method": "hotkey", "hotkey": "rightctrl+/" },

  "recorder_process": "qianwen.exe",

  "poll_interval_sec": 2.0,       // 轮询间隔
  "start_debounce_sec": 3.0,      // 通话需持续多久才触发（防抖）
  "retrigger_cooldown_sec": 30.0, // 两次触发的最小间隔

  // 千问没启动时先拉起它
  "launcher": {
    "auto_launch": true,
    "exe": "",                    // 留空 = 自动查找
    "wait_ready_sec": 12,         // 启动后等多久再发键（机器慢就加大）
    "retry_hotkey": 2,            // 没录上时最多补发几次
    "retry_delay_sec": 7
  },

  // 把录完的音频复制到备份目录
  "archive": { "enabled": true, "dir": "", "min_age_sec": 60, "interval_sec": 120 }
}
```

---

## 两个值得记下的实现坑

如果你要改这个项目（或者做类似的 Windows 自动化），这两条能省你几个小时。

### 1. `SendInput` 的 `INPUT` 结构体在 x64 上必须恰好 40 字节

只放 `KEYBDINPUT`、或用小尺寸 padding，会得到 `sizeof(INPUT) == 32`。
此时 `SendInput` **返回 0 且不设置 `LastError`** —— 完全静默失败，
你的日志会一直说"已发送快捷键"，实际一个键都没发出去。

```python
class _INPUTunion(ctypes.Union):
    # 必须带上 MOUSEINPUT：x64 上 INPUT 的真实大小由它决定
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
if n != 1:                      # ★ 一定要检查返回值
    raise OSError(f"SendInput 失败（返回 {n}）")
```

### 2. 对着「低级键盘钩子」要发**扫描码**，发虚拟键没用

千问的录音快捷键是宿主用 `WH_KEYBOARD_LL` 那类钩子实现的，钩子看到的是**扫描码**。
发虚拟键（`wVk=0xA3` + `wVk=0xBF`）它毫无反应；换成扫描码立刻就行：

```python
# 右 Ctrl：扫描码 0x1D，且必须带 EXTENDED 标志（否则会被当成左 Ctrl）
KEYBDINPUT(wVk=0, wScan=0x1D,
           dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY)
# '/'
KEYBDINPUT(wVk=0, wScan=0x35, dwFlags=KEYEVENTF_SCANCODE)
```

### 排查思路：拿一个"已知能用"的热键做对照

怀疑注入通路有问题时，别猜。找一个**确定注册过的**全局热键发一次
（比如千问的唤起键 `Alt+Space`）：

- 有反应 → 注入通路没问题，问题在该键的编码上；
- 没反应 → 再看会话隔离、权限、UIPI 这些。

一次就能把排查范围砍掉一大半。

---

## 常用调试命令

```powershell
$py = "$env:LOCALAPPDATA\QwenAutoRecord\venv\Scripts\python.exe"

& $py qwen_auto_record.py --once            # 看当前检测状态
& $py qwen_auto_record.py --list-sessions   # 看哪些进程在占用麦克风
& $py qwen_auto_record.py --test-trigger    # 立刻触发一次录音
& $py verify_trigger.py                     # 验证注入通路 + 是否真的开录
& $py tools\probe_hotkey.py                 # 探测某热键是否已被注册
& $py tools\probe_sessions.py               # 看音频会话明细
```

---

## 已知限制

1. **停止录音仍需手动。** 快捷键只负责"唤起"，不是开关；再按一次只是把窗口调出来。
   通话结束后要在千问窗口点"结束"。而且录音器如果还开着，下一场不会新建会话。
2. **静音通话不会触发** —— 没有麦克风会话。这是有意的，避免"微信开着就录"。
3. **只在 Windows 上测过**（Windows 11 + 千问 4.3.5.248）。
4. **微信的进程名**可能随版本变化。如果漏检，用 `--list-sessions` 在通话中看真实进程名，补进 `config.json`。
5. 录音文件存在千问自己的目录里：
   `%APPDATA%\Qianwen\qianwen-ai-record\<时间戳>\录音文件.webm`
   转写和纪要在**千问云端**，本地只有音频。

---

## 合规与免责

**请务必先确认合法性，再使用本工具。**

- 本工具是**静默录音**：对方不会收到任何提示。部分国家和地区要求
  录音前取得**所有参与者同意**，否则可能违法。
- 请同时确认符合你所在**机构/公司的相关规定**。很多公司对会议录音有明确要求。
- 建议做法：会前一句"我记个纪要"，让对方知情。

本工具只是自动化了"你自己按一下快捷键"这个动作，**不修改千问客户端、不绕过任何授权**。
使用者需自行承担合规责任。

---

## 目录结构

```
engine.py               核心：通话检测 / 触发 / 归档 / 可暂停的监听器
tray_app.py             入口：托盘图标 + 本地控制面板（HTTP）
qwen_auto_record.py     命令行入口（调试用）
panel.html              控制面板前端
config.example.json     配置样例
安装与开机自启.ps1       建环境 + 装依赖 + 注册开机自启
启动自动录音.bat         后台启动
停止自动录音.bat         停止
verify_trigger.py       验证"发键 → 真的开录"这条链路
audio_capture.py        备选方案：自己录音（系统回放+麦克风双路混音，带 AGC）
tools/                  诊断小工具（热键探测、音频会话探测、asar 解包等）
```

---

## License

MIT
