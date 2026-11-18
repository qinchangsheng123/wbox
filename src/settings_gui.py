# -*- coding: utf-8 -*-
"""WBOX 设置面板（极简内置 + 第三方皮肤，高 DPI 自适应）。

界面约定（2026-11-09 定版，2026-11-12 起改为插件驱动）：
  1. 主页面**只**保留「名称 + 开关」的功能行，行数由插件注册表决定：
     有几个 home=true 的插件就有几行，删掉插件目录那一行就消失。
     无编号、无详情小字、无状态行。
  2. 鼠标悬停在名称上显示该功能简介（ToolTip，文案来自 manifest.tip）。
  3. 右上角是齿轮图标，点开浏览器式弹出菜单（贴按钮展开的无标题栏浮层）：
     每行「图标 + 文字 + 右侧提示」，分组用 1px 分隔线，悬停高亮，
     点击外部 / Esc 收起；每个项目独占一行、纵向依次排列。
     开机自启、守护状态、启动/停止、重扫、日志、配置、主题、保存、关闭全在这里。
  4. 三个开关切换即时写回 config.json，守护 2 秒内热加载，不用点保存。
  5. 外观走皮肤层（src/skins.py）：`ui_skin` 选皮肤、`ui_appearance` 选明暗；
     老配置的 `ui_theme` 仍能读，按它推默认皮肤与明暗，升级不变脸。

关键：
  - 创建任何 Tk 窗口前启用 Per-Monitor DPI Awareness V2，否则 Windows 会把
    整个窗口当位图拉伸（150% 缩放下文字发虚）。
  - 字体用「像素尺寸 + tk scaling = DPI/72」，DPI 变化时显式重设。
  - 控件全部用 tk 原生控件 + Canvas 自绘（开关、图标），不依赖 ttk 主题，
    这样纯黑主题下不会出现系统绘制的白底方块。
"""
import ctypes
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import time
import tkinter as tk
import core_defaults
import guard_beat
import plugin_api
import plugin_host
import plugin_package
import skins
import tray_icon
from ctypes import wintypes
from tkinter import filedialog
from tkinter import font as tkfont
from tkinter import messagebox

if os.environ.get('ABG_BASE'):
    BASE = os.path.abspath(os.environ['ABG_BASE'])
elif getattr(sys, 'frozen', False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    # 源码调试时 src/ 里没有配置，按 src -> 项目根 -> app/ 顺序找真正的运行目录，
    # 这样 --self-check / --layout-check 直接跑源码也能读到完整 config 与图标。
    _up = os.path.dirname(BASE)
    for _cand in (BASE, _up, os.path.join(_up, 'app')):
        if os.path.isfile(os.path.join(_cand, 'config.json')):
            BASE = _cand
            break

CFG = os.path.join(BASE, 'config.json')
LOG = os.path.join(BASE, 'guard.log')
PID_FILE = os.path.join(BASE, 'guard.pid')
STOP_FILE = os.path.join(BASE, 'stop.flag')
SELFCHECK = os.path.join(BASE, 'settings_selfcheck.txt')
LAYOUTCHECK = os.path.join(BASE, 'layout_check.txt')
ICON_LIGHT = os.path.join(BASE, 'icon-light.ico')
ICON_DARK = os.path.join(BASE, 'icon-dark.ico')
TASK = 'WBOX'
VERSION = core_defaults.VERSION   # 单一来源在 core_defaults，此处 re-export 供面板与测试
# 插件目录与 plugins_state.json 的位置必须与守护**完全一致**（两边读同一份注册表、
# 同一份运行期状态），所以解析逻辑抽到 plugin_host.resolve_plugins_dir() 里，
# 这里只调用它，绝不各写一份（两份规则迟早会分叉，症状是"面板能看到插件、守护看不到"）。
PLUGINS_DIR = plugin_host.resolve_plugins_dir(BASE)
STATE_FILE = os.path.join(BASE, 'plugins_state.json')
# 系统命令一律用 SystemRoot\System32 下的绝对路径：面板可能以管理员身份运行，
# 裸名 tasklist / powershell 会按 PATH（含当前目录）解析，存在同目录劫持风险。
SYS32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
PS = os.path.join(SYS32, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
TASKLIST = os.path.join(SYS32, 'tasklist.exe')
NOWIN = 0x08000000
BASE_W = 640                 # standard 档基准窗口宽度（逻辑像素，设计稿 640×332）
PLAIN_W = 480                # v2.10.0：极简档收紧到 480，去掉两侧大片空白
DPI_MODE = 'unknown'
READONLY = False             # 配置目录不可写且无法提权时为 True：功能项全部置灰

# 颜色不再写死：全部来自 src/skins.py 的 token（见 docs/SKIN-SPEC.md）。
# 旧代码里用的是 11 个历史键名（bg/fg/line/hover/inv…），这里只做一层映射，
# 新代码请直接用 app.tok['fill_2'] 这类 token 名，别再往 _legacy 里加键。
LEGACY_KEYS = ('bg', 'fg', 't2', 't3', 'line', 'border', 'hover', 'active',
               'inv', 'invfg', 'tipbg', 'tipfg')


def legacy_colors(tok, appearance='light'):
    """26 个 token -> 面板历史代码用的 11 个键（外加一个语义 danger）。"""
    return {
        'bg': tok['glass'], 'fg': tok['fg'], 't2': tok['t2'], 't3': tok['t3'],
        'line': tok['hair'], 'border': tok['hair_strong'],
        'hover': tok['fill_2'], 'active': tok['press'],
        'inv': tok['on'], 'invfg': tok['on_knob'],
        'tipbg': tok['fg'], 'tipfg': tok['glass'],
        't4': tok['t4'],                       # 第四级文字（禁用 / 极弱提示）
        'hair': tok['hair'], 'fill': tok['fill'], 'press': tok['press'],
        'inset': tok['inset'], 'line_strong': tok['hair_strong'],
        # 皮肤 token 里刻意没有彩色（零彩色是品牌约定），但「卸载 / 删除」
        # 这类破坏性操作需要一个可识别的红，单独给一个语义色，不进皮肤。
        'danger': '#B3261E' if appearance == 'light' else '#FF8A80',
    }


def _compat_themes():
    """THEMES['light'/'dark'] 的兼容壳：老代码、老测试按明暗两套色板取色。

    真正的光源是 skins.py——这里只把默认皮肤的 light/dark 两套 token 按旧键名
    摆好，避免每个调用点都改成 app.tok[...]。新代码请直接用 app.tok。
    """
    base = skins.get(skins.DEFAULT_ID) or skins.builtin_skins()[-1]
    return {k: legacy_colors(base.tokens(k), k) for k in ('light', 'dark')}


THEMES = _compat_themes()

# 主页面与齿轮菜单的**唯一**数据来源是插件注册表（plugins\*\plugin.json）。
# v1 的 FEATURES 常量（写死三个功能的名字/图标/简介）已删除：删掉一个插件目录，
# 面板就该少一行、不报错、不留空行，而不是继续显示一个点不动的幽灵功能。
#
# 插件加载期状态 -> 菜单右侧提示。degraded 不走这张表：它带异常次数，另算。
PLUGIN_STATUS_HINT = {
    plugin_host.STATUS_OK: '正常',
    plugin_host.STATUS_DEGRADED: '运行异常',
    plugin_host.STATUS_BAD_MANIFEST: '未能加载',
    plugin_host.STATUS_LOAD_FAILED: '未能加载',
    plugin_host.STATUS_TOO_NEW: '需要更新',
    plugin_host.STATUS_CONFLICT: '名称重复',
    'disabled': '已关闭',
}


# 菜单里"内核自带"的行数（不含「已安装插件」标题、每个插件一行、以及
# 「打开插件目录」/「浏览插件库」那两行）。v2.3.0 比 v2.2.0 多一行「插件开发指南」，11 -> 12：
#   守护状态 / 启动守护 / 停止守护 / 开机自启 / 重新扫描游戏 / 查看日志 /
#   编辑配置文件 / 插件开发指南 / 外观与皮肤 / 保存设置 / 关闭菜单
# v2.7.0：原「浅色主题 / 深色主题」两行合成「外观与皮肤…」一行，12 -> 11。
# v2.9.0：「启动与守护」补设计稿两行「启动后收进托盘 / 游戏结束提示」，11 -> 13。
# 测试与滚动区高度都从这里取，别再各写一份数字。
CORE_MENU_ROWS = 13


def menu_row_count(entries):
    """弹出菜单的行数（不含分隔线与版本小字）。

    必须由注册表算出来，不能写死：删一个插件目录菜单就矮一行，写死会让滚动区
    多出一段空白。式子里的固定行是：

      CORE_MENU_ROWS  —— 内核自带的行，含「插件开发指南」（口径见该常量注释）
      1               —— 「已安装插件」分组标题
      len(entries)    —— 每个插件一行
      2               —— 「打开插件目录」+ v2.11.3 新增的「浏览插件库」

    注意「插件开发指南」属于 CORE_MENU_ROWS，不在插件分组里（v2.3.0 定稿时的
    取舍：指南讲的是"怎么写插件"，但对着的是所有用户，放在内核区更靠前）。
    """
    return CORE_MENU_ROWS + 1 + len(entries or ()) + 2


def plugin_hint(entry, state=None, switch=None):
    """菜单里某一行插件右侧的提示文本：「状态 · 已启用 / 已关闭」。

    state 是守护写进 plugins_state.json 的那条记录（运行期状态），可为 None：
      - 加载期就不是 ok 的插件（manifest 坏 / 导入失败 / api 太新…）以加载期为准，
        因为它的代码根本没跑起来，运行期记录没有意义（也可能是上一版的残留）。
      - 加载期 ok 但守护报过 degraded / error_count>0，运行期状态优先于「正常」。

    switch 是这个插件开关的当前值（True/False），None = 调用方不要求显示：
      - 只有真加载起来的插件才追加启用态。manifest 坏 / 导入失败 / api 太新的插件
        开关毫无意义（主页面也没有它的开关行），写成「已禁用」会让人以为"拨一下就好"。
      - 未提权且 manifest.requires_admin 为真时再追加「需要管理员」：这类插件不是坏了，
        是权限不够，两件事得在同一行里同时看见（否则用户只会看到一个「正常」+ 不工作）。
    """
    if entry.status != plugin_host.STATUS_OK:
        text = PLUGIN_STATUS_HINT.get(entry.status, entry.status or '未知')
    else:
        text = '正常'
        if isinstance(state, dict):
            errs = state.get('error_count')
            if isinstance(errs, bool) or not isinstance(errs, int):
                errs = 0
            if state.get('status') == plugin_host.STATUS_DEGRADED or errs > 0:
                text = ('运行异常 %d 次' % errs) if errs else '运行异常'
    # 插件版本号不进界面（文案规范 §1）：用户只需要状态，不需要 v2.0.0。
    parts = [text]
    if isinstance(switch, bool) and entry.status in plugin_host.USABLE_STATUS:
        parts.append('已启用' if switch else '已关闭')
    if getattr(entry, 'requires_admin', False) and not is_admin():
        parts.append('需管理员')
    return ' · '.join(parts)


def read_plugin_states():
    """读守护写的 plugins_state.json，返回 {id: 记录}。

    面板只读不写（这份文件是守护的产物，面板跟着写会把运行期异常状态覆盖掉）。
    文件不存在 / JSON 坏 / 结构不认识一律返回 {}：插件状态面板打不开是本末倒置。
    """
    try:
        with open(STATE_FILE, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    records = data.get('plugins') if isinstance(data, dict) else None
    if not isinstance(records, list):
        return {}
    out = {}
    for rec in records:
        if isinstance(rec, dict) and isinstance(rec.get('id'), str) and rec['id']:
            out[rec['id']] = rec
    return out


# ---------------- DPI ----------------

def enable_dpi_awareness():
    """必须在创建 Tk 窗口之前调用。返回实际生效的模式。"""
    global DPI_MODE
    u = ctypes.windll.user32
    try:
        f = u.SetProcessDpiAwarenessContext
        f.argtypes = [ctypes.c_void_p]
        f.restype = wintypes.BOOL
        if f(ctypes.c_void_p((1 << 64) - 4)):       # PER_MONITOR_AWARE_V2 = -4
            DPI_MODE = 'PerMonitorV2'
            return DPI_MODE
    except Exception:
        pass
    try:
        f = ctypes.windll.shcore.SetProcessDpiAwareness
        f.argtypes = [ctypes.c_int]
        f.restype = ctypes.c_long
        if f(2) == 0:                                # PER_MONITOR_DPI_AWARE
            DPI_MODE = 'PerMonitor'
            return DPI_MODE
    except Exception:
        pass
    try:
        if u.SetProcessDPIAware():
            DPI_MODE = 'System'
            return DPI_MODE
    except Exception:
        pass
    # 到这里也可能是 exe manifest 已声明感知（此时调用会被系统拒绝，但 DPI 已正确）
    DPI_MODE = 'AlreadySetByManifest'
    return DPI_MODE


def system_dpi():
    u = ctypes.windll.user32
    try:
        f = u.GetDpiForSystem
        f.restype = wintypes.UINT
        d = int(f())
        if d >= 72:
            return d
    except Exception:
        pass
    try:
        h = u.GetDC(0)
        d = int(ctypes.windll.gdi32.GetDeviceCaps(h, 88))
        u.ReleaseDC(0, h)
        if d >= 72:
            return d
    except Exception:
        pass
    return 96


def window_dpi(hwnd):
    try:
        f = ctypes.windll.user32.GetDpiForWindow
        f.argtypes = [wintypes.HWND]
        f.restype = wintypes.UINT
        d = int(f(hwnd))
        if d >= 72:
            return d
    except Exception:
        pass
    return system_dpi()


class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [('x', wintypes.LONG), ('y', wintypes.LONG)]


def work_area():
    """屏幕工作区（排除任务栏），返回 left, top, width, height。"""
    try:
        r = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        pass
    u = ctypes.windll.user32
    return 0, 0, int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1))


# ---------------- 配置与系统状态 ----------------

def load_cfg():
    with open(CFG, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def save_cfg(cfg):
    """写配置。先留一份 .bak 再覆盖：面板一保存就是全量重写，写坏了没有回滚点。

    .bak 只在本次保存前已存在 config.json 时生成，且只保留上一版（不累积）。
    """
    if os.path.exists(CFG):
        try:
            shutil.copy2(CFG, CFG + '.bak')
        except OSError:
            pass
    tmp = CFG + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CFG)


def is_admin():
    """当前进程是否已提权。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def file_writable(path):
    """真开一次追加模式来判可写：os.access 在 UAC 虚拟化 / 继承 ACL 下会误报。"""
    try:
        with open(path, 'a', encoding='utf-8'):
            pass
        return True
    except Exception:
        return False


def dir_writable(path):
    """配置文件还不存在时，退而探测所在目录能否新建文件。"""
    probe = os.path.join(path, '.abg_write_probe.tmp')
    try:
        with open(probe, 'a', encoding='utf-8'):
            pass
        try:
            os.remove(probe)
        except OSError:
            pass
        return True
    except Exception:
        return False


def can_write_config():
    """面板能否把设置写进 config.json（决定是否给功能项置灰）。

    保存走的是"建 config.json.tmp → os.replace"，既需要**文件**可写，也需要
    **目录**可新建文件。只测文件追加写会在"文件被单独授权、目录仍受限"时误判
    为可写，用户改完开关保存失败且无提示（2026-11-11 审计发现）。
    """
    if not os.path.isdir(BASE):
        return False
    if os.path.exists(CFG) and not file_writable(CFG):
        return False
    return dir_writable(BASE)


def elevate_settings(args):
    """配置写不进去且当前不是管理员时自我提权重开面板。

    返回 True 表示已经把提权副本拉起来（本进程应立即退出）。返回 False 表示
    用户拒绝了 UAC（ShellExecuteW ≤ 32，含取消）或本次已是 --elevated 副本，
    调用方应以"置灰只读"模式正常打开面板。
    """
    if '--elevated' in args:
        return False            # 已经提权过一次仍然不可写：不再递归弹 UAC
    params = '--settings --elevated'
    if not getattr(sys, 'frozen', False):
        # 源码调试时 sys.executable 是 python.exe，只传开关会变成"python --settings"，
        # 提权副本启动即报错退出；必须把入口脚本一起带上。
        # python -c / -m 调试时 sys.argv[0] 是 '-c'/'pkg.mod'，不是可执行脚本路径，
        # 这种情况回退到本文件旁边的 abg_main.py（源码树里一定存在）。
        script = os.path.abspath(sys.argv[0] or '')
        if not (os.path.isfile(script) and script.lower().endswith('.py')):
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'abg_main.py')
        params = '"%s" %s' % (script, params)
    try:
        sh = ctypes.windll.shell32
        sh.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.LPCWSTR, ctypes.c_int]
        sh.ShellExecuteW.restype = ctypes.c_ssize_t
        rc = sh.ShellExecuteW(None, 'runas', sys.executable, params, BASE, 1)
    except Exception:
        return False
    return bool(rc and rc > 32)


def load_config_raw():
    """读 config.json 的原始内容；不存在 / 解析失败时返回 {}。

    面板不能因为"配置文件还没生成"就打不开：缺配置时下面按内核默认值显示，
    与守护首次启动的行为一致（守护那边同样回落 {} 再走默认值）。
    """
    try:
        return load_cfg()
    except (OSError, ValueError):
        return {}


def plugin_entries(log_func=None):
    """面板用的插件注册表（只读发现 + 加载，绝不写 plugins_state.json）。

    用 plugin_host.load_registry 而不是 discover：面板要拿 plugin 实例上的
    gui_state / gui_toggle 默认实现，discover 只解析 manifest、不 import 代码。
    """
    return plugin_host.load_registry(
        BASE, plugins_dir=PLUGINS_DIR, log_func=log_func or (lambda m: None))


def effective_cfg(cfg=None, entries=None):
    """三段合并后的配置：内核默认值 -> 各插件 DEFAULT_CONFIG -> 用户 config.json。

    口径必须与守护完全一致（design\\plugin-arch.md 4.3）：面板显示「开」而守护
    因为缺段按默认值跑「关」，是最招人烦的一类老坑。合并本身由
    plugin_host.merge_defaults() 完成，纯函数，不改任何入参。
    """
    cfg = load_config_raw() if cfg is None else cfg
    if entries is None:
        entries = plugin_entries()
    return plugin_host.merge_defaults(core_defaults.DEFAULTS, entries, cfg)


def with_defaults(cfg, key, defaults):
    """写回前把功能段补齐，避免只写一个 enabled 把其它参数抹掉。"""
    seg = dict(defaults)
    if isinstance(cfg.get(key), dict):
        seg.update(cfg[key])
    cfg[key] = seg
    return seg


def cfg_get_path(cfg, key, default=None):
    """按点路径取值：'ace_throttle.enabled' -> cfg['ace_throttle']['enabled']。"""
    cur = cfg
    for part in key.split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def cfg_set_path(cfg, key, value):
    """按点路径写值，父段不存在就建。

    只写用户改的那一个叶子键，**不把整段默认值固化进 config.json**：
    段内其它键继续走插件的默认值，插件升级默认值（如弹窗保护名单）时
    对老配置依然生效。这正是 with_defaults() 的相反取舍——那个用于开关，
    开关要保证"关掉一整段"时其它键还在；这里是单项写入，不缺段就不补。
    """
    parts = key.split('.')
    cur = cfg
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return value


def plugin_switch_state(entry, cfg):
    """读某个插件开关的当前值。

    优先用插件自己的 `gui_state()`（比如 ace 是「清理 + 限速两段都为真才算开」，
    这条语义只有它自己知道）；插件没继承基类或实现抛异常时，按 manifest.toggle
    声明的路径全部为真来兜底——面板不能因为一个坏插件就整页打不开。
    """
    fn = getattr(entry.plugin, 'gui_state', None)
    if callable(fn):
        try:
            return bool(fn(cfg))
        except Exception:
            pass
    paths = [p for p in entry.toggle if isinstance(p, str) and p]
    if not paths:
        return False
    for path in paths:
        if not bool(plugin_api.cfg_get_path(cfg, path, False)):
            return False
    return True


def plugin_switch_write(entry, cfg, on):
    """把开关写进 cfg（内存里），落盘由调用方统一 save_cfg()。

    同样优先用插件的 `gui_toggle()`（ace 要同时写 ace_cleanup.enabled 与
    ace_throttle.enabled 两处），异常/缺失时按 manifest.toggle 逐条写。
    返回 True 表示至少写入了一个路径。
    """
    fn = getattr(entry.plugin, 'gui_toggle', None)
    if callable(fn):
        try:
            return bool(fn(cfg, bool(on)))
        except Exception:
            pass
    wrote = False
    for path in [p for p in entry.toggle if isinstance(p, str) and p]:
        if plugin_api.cfg_set_path(cfg, path, bool(on)):
            wrote = True
    return wrote


def ps_run(cmd, timeout=25):
    r = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-Command', cmd],
                       creationflags=NOWIN, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr).decode('utf-8', 'replace').strip()


def guard_state():
    if not os.path.isfile(PID_FILE):
        return '未运行'
    try:
        with open(PID_FILE, 'r', encoding='ascii') as f:
            pid = int(f.read().strip())
    except Exception:
        return '未运行'
    if pid == os.getpid():
        return '未运行'
    try:
        r = subprocess.run([TASKLIST, '/FI', 'PID eq %d' % pid, '/FO', 'CSV', '/NH'],
                           creationflags=NOWIN, stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=10)
        out = r.stdout.decode('utf-8', 'replace')
        if str(pid) in out and ('WBOX' in out or 'pythonw' in out):
            return '运行中 (pid %d)' % pid
        return '未运行（pid 文件残留）'
    except Exception:
        return '状态未知'


# 心跳管道里会话白话对照表：面板只说人话，不透出 idle/grace 这类内部值。
SESSION_TEXT = {
    'idle': '空闲待命',
    'active': '游戏中',
    'grace': '游戏刚退出',
}


def short_guard_state(probe=False):
    """菜单右侧只放两三个字的状态提示，长 pid 不挤占名称列。

    v2.5.0 起状态来源是守护的心跳管道（连得上 = 真活着，比 guard.pid 文件
    可靠——pid 文件只能证明"曾经写过"）。读不到就明说「守护没在运行」，
    不再兜底去查进程列表：心跳挂了本身就是要让用户看见的状态。

    v2.11.3：心跳里带进程事件通道状态。事件不可用时不是「守护坏了」，
    但优化确实没生效，所以状态行追加「轮询兜底」——"进程在"和"全功能在"
    必须能一眼分开（旧版守护没有该字段，按老文案显示，不误报降级）。
    """
    if probe:
        return '（自检）'
    st = guard_beat.read_status()
    if isinstance(st, dict) and st.get('pid'):
        parts = ['运行中']
        text = SESSION_TEXT.get(str(st.get('session') or ''), '')
        if text:
            parts.append(text)
        ev = st.get('events')
        if isinstance(ev, dict) and ev.get('mode') == 'poll':
            parts.append('轮询兜底')
        return ' · '.join(parts)
    return '守护没在运行'


def guard_diagnostics(st=None):
    """设置页用：(进程事件, 插件健康, 补充说明)。

    值只放短词（等宽小字列不能撑开），需要解释的整句走 note 单独一行换行显示。
    心跳读不到时三样都返回「未运行」——不编造"看起来正常"的值。
    """
    if st is None:
        st = guard_beat.read_status()
    if not (isinstance(st, dict) and st.get('pid')):
        return '未运行', '未运行', ''
    note = ''
    ev = st.get('events')
    if isinstance(ev, dict):
        mode = ev.get('mode')
        if mode == 'event':
            ev_text = '事件驱动'
        elif mode == 'poll':
            ev_text = '轮询兜底'
            note = str(ev.get('hint') or '进程事件监听不可用，当前使用轮询')
            wakes = ev.get('wakes')
            if isinstance(wakes, int) and wakes > 0:
                note += '（已用事件唤醒 %d 次）' % wakes
        else:
            ev_text = '未上报'
    else:
        ev_text = '未上报'
    plugs = st.get('plugins') if isinstance(st.get('plugins'), list) else []
    bad = [p for p in plugs
           if isinstance(p, dict) and p.get('status') not in ('ok', 'degraded')]
    deg = [p for p in plugs
           if isinstance(p, dict) and p.get('status') == 'degraded']
    if bad:
        p_text = '%d 个未加载' % len(bad)
    elif deg:
        p_text = '%d 个运行异常' % len(deg)
    elif plugs:
        p_text = '全部正常'
    else:
        p_text = '无插件'
    return ev_text, p_text, note


def autostart_state():
    try:
        rc, out = ps_run("(Get-ScheduledTask -TaskName '%s').Settings.Enabled" % TASK)
        return rc == 0 and out.strip().lower() == 'true'
    except Exception:
        return False


def set_autostart(on):
    verb = 'Enable-ScheduledTask' if on else 'Disable-ScheduledTask'
    return ps_run("%s -TaskName '%s'" % (verb, TASK))


def start_guard():
    return ps_run("Start-ScheduledTask -TaskName '%s'" % TASK)


def pick_family(root):
    try:
        fam = set(tkfont.families(root))
    except Exception:
        fam = set()
    for name in ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'):
        if name in fam:
            return name
    return 'TkDefaultFont'


def walk(widget):
    for child in widget.winfo_children():
        yield child
        for sub in walk(child):
            yield sub


# ---------------- 窗口图标（任务管理器 / 任务栏 / Alt+Tab） ----------------
# Tk 的 wm iconbitmap 只保证大图标；任务管理器「进程」列表、任务栏预览
# 与 Alt+Tab 取的是窗口 ICON_SMALL，不显式设置会回退成 Tk 默认羽毛图标。
WM_SETICON = 0x0080
WM_GETICON = 0x007F
ICON_SMALL, ICON_BIG = 0, 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
_HICONS = {}


def _user32_calls():
    u = ctypes.windll.user32
    load = u.LoadImageW
    load.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                     ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    load.restype = ctypes.c_void_p
    send = u.SendMessageW
    send.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                     ctypes.c_size_t, ctypes.c_ssize_t]
    send.restype = ctypes.c_ssize_t
    return send, load


def win_hwnd(win):
    """取顶层 frame 的 HWND：任务栏与任务管理器读它，而不是客户区子窗口。"""
    try:
        frame = win.wm_frame()
        if frame:
            return int(frame, 16)
    except Exception:
        pass
    return int(win.winfo_id())


def set_window_icons(win, path):
    """同时设置 ICON_SMALL / ICON_BIG 为设计图标，返回 (small_ok, big_ok)。"""
    if not os.path.isfile(path):
        return False, False
    try:
        send, load = _user32_calls()
        u = ctypes.windll.user32
        hwnd = win_hwnd(win)
        small_size = u.GetSystemMetrics(49) or 16      # SM_CXSMICON
        big_size = u.GetSystemMetrics(11) or 32        # SM_CXICON
        ok = []
        for which, size in ((ICON_SMALL, small_size), (ICON_BIG, big_size)):
            key = (path, size)
            h = _HICONS.get(key)
            if not h:
                h = load(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
                if h:
                    _HICONS[key] = h
            if h:
                send(hwnd, WM_SETICON, which, h)
                ok.append(which)
        return ICON_SMALL in ok, ICON_BIG in ok
    except Exception:
        return False, False


def get_window_icons(win):
    """自检用：读回窗口当前的大小图标句柄是否都已设置。"""
    try:
        send, _ = _user32_calls()
        hwnd = win_hwnd(win)
        return (bool(send(hwnd, WM_GETICON, ICON_SMALL, 0)),
                bool(send(hwnd, WM_GETICON, ICON_BIG, 0)))
    except Exception:
        return False, False


# ---------------- 自绘控件 ----------------

def round_rect(cv, x0, y0, x1, y1, r, **kw):
    """Canvas 没有原生圆角矩形：采样四段真圆弧，smooth=False 折线填充。

    v2.8.0 修正：原来靠 `smooth=True` 让 Tk 自己插值，样条会把直边也一起拉弯，
    卡片底边于是变成一条断断续续的虚线（1px 描边在 150% 缩放下尤其明显）。
    现在显式采样圆弧、直边保持真正的直线，描边才连续。
    """
    r = max(0.0, min(float(r), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if r <= 0:
        return cv.create_polygon(x0, y0, x1, y0, x1, y1, x0, y1, **kw)
    n = 8                      # 每角 8 段：20px 圆角上肉眼已经看不出折线
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90.0), (x1 - r, y1 - r, 0.0),
                       (x0 + r, y1 - r, 90.0), (x0 + r, y0 + r, 180.0)):
        for i in range(n + 1):
            a = math.radians(a0 + 90.0 * i / n)
            pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return cv.create_polygon(pts, smooth=False, **kw)


# ---------------- 程序化抗锯齿（极简档） ----------------
# 光栅化单一来源在 src/sprite.py：安装器极简界面与面板共用同一套实现与缓存。
from sprite import (SS as _SS, COV_CACHE as _COV_CACHE,
                    coverage as _coverage, cov_cached as _cov_cached,
                    sprite as _sprite, round_rect_pts as _rr_pts)


def mix_hex(c1, c2, p):
    """两个不透明 #RRGGBB 按 p 线性插值（Tk 没有 CSS transition，颜色自己混）。"""
    if p <= 0:
        return c1
    if p >= 1:
        return c2
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return '#%02X%02X%02X' % tuple(
        int(round(x + (y - x) * p)) for x, y in zip(a, b))


def _cb(t, a, b):
    u = 1 - t
    return 3 * a * u * u * t + 3 * b * u * t * t + t * t * t


def ease(x):
    """设计稿唯一动效曲线 cubic-bezier(.22,.61,.36,1)。

    牛顿法解 x(t)=x 得 t 再求 y(t)；6 轮足够收敛到 1e-4 以内。
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    t = x
    for _ in range(6):
        u = 1 - t
        fx = _cb(t, .22, .36) - x
        dx = 3 * .22 * u * u + 6 * (.36 - .22) * u * t + 3 * (1 - .36) * t * t
        if dx <= 1e-6:
            break
        t = min(1.0, max(0.0, t - fx / dx))
    return _cb(t, .61, 1.0)


def client_area_animation():
    """系统「在窗口中显示动画」开关（SPI_GETCLIENTAREAANIMATION=0x1042）。

    关掉 = 设计稿的 prefers-reduced-motion：所有动效时长归 0。
    极简档本来就零动画，这个开关只影响玻璃档。
    """
    try:
        v = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(v), 0)
        return bool(v.value)
    except Exception:
        return True


class Switch(tk.Canvas):
    """40×22 胶囊开关，纯 Canvas 自绘，双主题通用。"""

    def __init__(self, master, app, value=False, command=None):
        self.app = app
        self.value = bool(value)
        self.command = command
        self.enabled = True
        self._anim_knob = None      # 动效中的把手位置 0..1（None = 按 value 画）
        self._anim_fill = None      # 动效中的轨道色（None = 按 value 取 token）
        # 尺寸跟皮肤走：玻璃 46×28（iOS 规格）、极简 36×18（少画一半像素）。
        perf = getattr(getattr(app, 'skin', None), 'performance', 'standard')
        self.w = app.px(46 if perf == 'standard' else 36)
        self.h = app.px(28 if perf == 'standard' else 18)
        self.bg = app.colors['bg']
        self._focused = False
        tk.Canvas.__init__(self, master, width=self.w, height=self.h,
                           bg=self.bg, highlightthickness=0, bd=0,
                           cursor='hand2', takefocus=1)
        self.bind('<Button-1>', self.toggle)
        self.bind('<Return>', self.toggle)
        self.bind('<space>', self.toggle)
        self.bind('<FocusIn>', self._focus_in, add='+')
        self.bind('<FocusOut>', self._focus_out, add='+')
        self.draw()

    def _focus_in(self, ev=None):
        self._focused = True
        self.draw()

    def _focus_out(self, ev=None):
        self._focused = False
        self.draw()

    def toggle(self, ev=None):
        if not self.enabled:
            return
        old = self.value
        self.value = not old
        ms = self.app.anim_ms(240)
        if ms <= 0:
            self.draw()
        else:
            # 设计稿：把手 240ms、轨道 220ms，同一条 ease-out 曲线
            frm, to = self._track_fill(old), self._track_fill(self.value)
            self.app.tween(('swk', id(self)), ms,
                           lambda p: self._set_knob(1 - p if old else p),
                           done=lambda: self._set_knob(None))
            self.app.tween(('swt', id(self)), self.app.anim_ms(220),
                           lambda p: self._set_fill(mix_hex(frm, to, p)),
                           done=lambda: self._set_fill(None))
        if self.command:
            self.command()

    def _track_fill(self, value):
        c = self.app.colors
        tok = getattr(self.app, 'tok', {})
        if not self.enabled:
            return c['line'] if value else self.bg
        if value:
            return tok.get('on', c['inv'])
        return tok.get('switch_off', c['line'])

    def _set_knob(self, p):
        self._anim_knob = p
        try:
            self.draw()
        except Exception:
            pass

    def _set_fill(self, color):
        self._anim_fill = color
        try:
            self.draw()
        except Exception:
            pass

    def _stop_anim(self):
        self.app.cancel_tween(('swk', id(self)))
        self.app.cancel_tween(('swt', id(self)))
        self._anim_knob = None
        self._anim_fill = None

    def set_enabled(self, on):
        """置灰：不再响应点击，纯灰阶绘制（只读模式用于"需要管理员权限"）。"""
        self.enabled = bool(on)
        try:
            self.configure(cursor='hand2' if self.enabled else 'arrow',
                           takefocus=1 if self.enabled else 0)
        except Exception:
            pass
        self._stop_anim()
        self.draw()

    def set_value(self, value):
        self.value = bool(value)
        self._stop_anim()
        self.draw()

    def set_bg(self, color):
        self.bg = color
        try:
            self.configure(bg=color)
        except Exception:
            pass
        self.draw()

    def draw(self):
        self.delete('all')
        c = self.app.colors
        w, h = self.w, self.h
        tok = getattr(self.app, 'tok', {})
        off = tok.get('switch_off', c['line'])
        if self.enabled:
            if self.value:
                fill, knob, line = tok.get('on', c['inv']), tok.get('on_knob', c['invfg']), ''
            else:
                # 关闭态不要再描一圈主文字色：iOS 的关是「灰轨道 + 白钮」，
                # 描边会让它看起来像个没填色的按钮，比开态还抢眼。
                fill, knob, line = off, tok.get('knob', '#FFFFFF'), ''
        else:
            # 置灰：全部用中性灰阶，不出现可点击的反色
            fill, line, knob = (c['line'] if self.value else self.bg), c['line'], c['t3']
        if self._anim_fill is not None:
            fill = self._anim_fill
        pad = self.app.px(2)
        k = max(self.app.px(6), h - 2 * pad)     # 把手直径 = 高度减两倍内边距
        kp = self._anim_knob if self._anim_knob is not None else (
            1.0 if self.value else 0.0)
        x0 = pad + (w - 2 * pad - k) * kp
        if not getattr(self.app, 'glass', True):
            # 极简档：程序化抗锯齿小图，胶囊/把手边缘按覆盖率混色，无阶梯。
            layers = [(_cov_cached(('swt', w, h), w, h,
                                   [(0, h / 2.0, h / 2.0, w - h / 2.0, h / 2.0,
                                     h / 2.0)]), fill)]
            if line:
                layers.append((_cov_cached(('swl', w, h), w, h,
                                           [(3, h / 2.0, h / 2.0, w - h / 2.0,
                                             h / 2.0, h / 2.0 - 0.5, 0.5)]), line))
            layers.append((_cov_cached(('swk', w, h, k, int(round(x0))), w, h,
                                       [(1, x0 + k / 2.0, (h - k) / 2.0 + k / 2.0,
                                         k / 2.0)]), knob))
            self._img = _sprite(self, w, h, layers, self.bg)
            self.create_image(0, 0, anchor='nw', image=self._img)
        else:
            if line:
                round_rect(self, 0, 0, w, h, h // 2, fill=fill, outline=line,
                           width=max(1, self.app.px(1)))
            else:
                round_rect(self, 0, 0, w, h, h // 2, fill=fill, outline='')
            y0 = (h - k) // 2
            self.create_oval(x0, y0, x0 + k, y0 + k, fill=knob, outline='')
        if self._focused:
            # 开态轨道是实心底，焦点环反色才看得见；关态用 fg。
            # 环贴着轨道外沿画（Canvas 画不出控件外偏移，见 App._ring 注释）。
            col = c['bg'] if (self.enabled and self.value) else c['fg']
            self.create_rectangle(0.5, 0.5, w - 0.5, h - 0.5,
                                  outline=col, width=1, tags='focusring')


class Glyph(tk.Canvas):
    """统一图标（v2.2.0）：品牌盾唯一实心，其余全线性。

    不画背景色、不用背景色伪造镂空；几何与底色解耦，hover 只换底色。
    24x24 设计坐标（品牌盾 64x64），统一光学框 20x20、安全边距 2，
    线宽 round(1.8 * size/24)，方端尖角，双主题只翻 token。
    """

    def __init__(self, master, app, kind, size=None, ink=None):
        self.app = app
        self.kind = kind
        self.s = size or app.px(22)
        self.bg = app.colors['bg']
        self.dim = False
        # 显式指定墨色：标题栏的加号/箭头/窗口控制不能用默认的 t2，
        # 设计稿里它们分别是 fg / t3 / t3 三档。
        self.ink = ink
        # 极简档走程序化抗锯齿光栅（见 _coverage 注释）；standard 档矢量。
        self._raster = getattr(getattr(app, 'skin', None), 'performance',
                               'standard') == 'minimal'
        tk.Canvas.__init__(self, master, width=self.s, height=self.s,
                           bg=self.bg, highlightthickness=0, bd=0)
        self.draw()

    def set_dim(self, on):
        self.dim = bool(on)
        self.draw()

    def p(self, *vals):
        unit = self.s / (100.0 if self.kind == 'mark' else 24.0)
        return [v * unit for v in vals]

    def set_bg(self, color):
        self.bg = color
        try:
            self.configure(bg=color)
        except Exception:
            pass
        # 几何不依赖底色，重绘只为刷新 Canvas；不再有「镂空填背景色」。
        self.draw()

    def draw(self):
        self.delete('all')
        c = self.app.colors
        ink = c['fg'] if self.kind == 'mark' else c['t2']
        if self.ink:
            ink = c.get(self.ink, ink)
        if getattr(self, 'dim', False):
            ink = c['t3']
        lw_f = max(1.0, self.p(1.8)[0])   # 光栅用浮点线宽，AA 自己消化小数
        lw = max(1, int(round(lw_f)))
        u = self.s / (100.0 if self.kind == 'mark' else 24.0)
        raster = getattr(self, '_raster', False)
        groups = []                      # 光栅模式：[(prims, ink 色)] 依序叠色
        prims = []

        def P(pts):
            return [v * u for v in pts]

        def flush():
            if prims:
                groups.append((list(prims), ink))
                del prims[:]

        def L(pts):
            q = P(pts)
            if raster:
                for i in range(0, len(q) - 2, 2):
                    prims.append((0, q[i], q[i + 1], q[i + 2], q[i + 3], lw_f / 2))
            else:
                self.create_line(*q, fill=ink, width=lw)

        def P0(pts):
            q = P(pts)
            if raster:
                n = len(q)
                for i in range(0, n, 2):
                    j = (i + 2) % n
                    prims.append((0, q[i], q[i + 1], q[j], q[j + 1], lw_f / 2))
            else:
                self.create_polygon(*q, fill='', outline=ink, width=lw)

        def O(pts):
            q = P(pts)
            if raster:
                prims.append((2, (q[0] + q[2]) / 2, (q[1] + q[3]) / 2,
                              (q[2] - q[0]) / 2, lw_f / 2))
            else:
                self.create_oval(*q, fill='', outline=ink, width=lw)

        def OF(pts, color):
            q = P(pts)
            if raster:
                flush()
                groups.append(([(1, (q[0] + q[2]) / 2, (q[1] + q[3]) / 2,
                                 (q[2] - q[0]) / 2)], color))
            else:
                self.create_oval(*q, fill=color, outline='')

        def F(pts):
            q = P(pts)
            if raster:
                flush()
                groups.append(([(4, [(q[i], q[i + 1])
                                     for i in range(0, len(q), 2)])], ink))
            else:
                self.create_polygon(*q, fill=ink, outline='')

        def ARC(pts, start, extent):
            q = P(pts)
            if raster:
                cx, cy = (q[0] + q[2]) / 2, (q[1] + q[3]) / 2
                r = (q[2] - q[0]) / 2
                n = 24
                prev = None
                for i in range(n + 1):
                    a = math.radians(start + extent * i / float(n))
                    pt = (cx + r * math.cos(a), cy - r * math.sin(a))
                    if prev is not None:
                        prims.append((0, prev[0], prev[1], pt[0], pt[1], lw_f / 2))
                    prev = pt
            else:
                self.create_arc(*q, start=start, extent=extent,
                                style='arc', outline=ink, width=lw)

        k = self.kind
        if k == 'mark':
            # v2.8.0：品牌标记换成大写 W 负形（design/wbox-preview.html #w-glyph）。
            # 与旧盾牌的区别：5 笔封口多边形，16 px 下仍然认得出，也不和
            # 「ACE 防护」的盾牌图标撞形。
            # 坐标已按 1.4 倍放大到画布 5~95：面板里没有方块容器，
            # 原尺寸（18~82）在 16~22 px 上会显得又小又空。
            F((5.2, 19.2, 19.2, 19.2, 33.2, 66.8, 50, 36,
               66.8, 66.8, 80.8, 19.2, 94.8, 19.2,
               80.8, 80.8, 66.8, 80.8, 50, 58.4,
               33.2, 80.8, 19.2, 80.8))
        elif k == 'dot':
            # 唯一微实心例外：16px 下线性小圆会退化成空心环。
            OF((10, 10, 14, 14), c['t3'])
        elif k == 'plane':
            P0((2, 11, 22, 3, 15, 21, 11.5, 14.5))
            L((11.5, 14.5, 22, 3))
        elif k == 'pulse':
            P0((3, 5, 21, 5, 21, 19, 3, 19))
            L((5.5, 14.5, 9, 14.5, 11, 9.5, 13.5, 17, 15.5, 12.5, 19, 12.5))
        elif k == 'shield':
            P0((12, 2, 21, 6, 21, 13, 12, 22, 3, 13, 3, 6))
            L((8, 12, 11, 15, 16, 9))
        elif k == 'block':
            P0((2, 7, 15.5, 7, 15.5, 20, 2, 20))
            L((2, 11, 15.5, 11))
            L((17, 3, 22, 8))
            L((22, 3, 17, 8))
        elif k == 'link':
            # 旧版双向箭头，仅为兼容仍在 manifest 里写 link 的第三方插件。
            P0((3, 6.2, 16, 6.2, 16, 3.8, 21.5, 8, 16, 12.2, 16, 9.8, 3, 9.8))
            P0((21, 14.2, 8, 14.2, 8, 11.8, 2.5, 16, 8, 20.2, 8, 17.8, 21, 17.8))
        elif k == 'gear':
            O((9, 9, 15, 15))
            for i in range(6):
                a = -math.pi / 2 + i * math.pi / 3
                L((12 + 6 * math.cos(a), 12 + 6 * math.sin(a),
                   12 + 9 * math.cos(a), 12 + 9 * math.sin(a)))
        elif k == 'play':
            P0((8, 5, 19, 12, 8, 19))
        elif k == 'stop':
            P0((7, 7, 17, 7, 17, 17, 7, 17))
        elif k == 'boot':
            O((4, 4, 20, 20))
            L((12, 2, 12, 10))
        elif k == 'rescan':
            ARC((3, 3, 21, 21), 30, 300)
            L((18, 3, 21, 6, 18, 9))
        elif k == 'log':
            P0((4, 3, 18, 3, 18, 21, 4, 21))
            for yy in (8, 12, 16):
                L((7, yy, 15, yy))
        elif k == 'cfg':
            for yy in (7, 12, 17):
                L((3, yy, 21, yy))
            for box in ((8, 5, 11, 9), (14, 10, 17, 14), (6, 15, 9, 19)):
                O(box)
        elif k == 'sun':
            O((9, 9, 15, 15))
            for i in range(8):
                a = i * math.pi / 4
                L((12 + 8 * math.cos(a), 12 + 8 * math.sin(a),
                   12 + 10.5 * math.cos(a), 12 + 10.5 * math.sin(a)))
        elif k == 'moon':
            P0((16, 3, 9, 6, 6, 12, 9, 18, 16, 21, 11.5, 12))
        elif k == 'check':
            L((4, 12, 9.5, 17.5, 20, 6))
        elif k == 'close':
            L((5, 5, 19, 19))
            L((19, 5, 5, 19))
        elif k == 'plus':
            L((12, 4, 12, 20))
            L((4, 12, 20, 12))
        elif k == 'chev':
            # 字标右侧的下拉箭头：菜单展开时翻成向上
            if getattr(self, 'flip', False):
                L((7, 15, 12, 9.5, 17, 15))
            else:
                L((7, 9.5, 12, 15, 17, 9.5))
        elif k == 'chev_r':
            L((9.5, 6, 15.5, 12, 9.5, 18))
        elif k == 'min':
            L((6, 12.5, 18, 12.5))
        elif k == 'max':
            P0((6.5, 6.5, 17.5, 6.5, 17.5, 17.5, 6.5, 17.5))
        elif k == 'restore':
            P0((8.5, 5.5, 18.5, 5.5, 18.5, 15.5, 8.5, 15.5))
            L((5.5, 8.5, 5.5, 18.5, 15.5, 18.5))
        elif k == 'trash':
            L((4, 6, 20, 6))
            L((9, 6, 9, 4, 15, 4, 15, 6))
            P0((6, 6, 18, 6, 17, 21, 7, 21))
            L((10, 10, 10, 17))
            L((14, 10, 14, 17))
        elif k == 'folder':
            P0((3, 7, 9, 7, 11, 9, 21, 9, 21, 19, 3, 19))
        elif k == 'admin':
            P0((6, 11, 18, 11, 18, 21, 6, 21))
            L((9, 11, 9, 8, 10, 5, 14, 5, 15, 8, 15, 11))
        elif k == 'tray':
            # 收进托盘：下箭头落进开口槽
            L((12, 3, 12, 13))
            L((8, 9.5, 12, 13.5, 16, 9.5))
            L((3, 13, 3, 19, 21, 19, 21, 13))
        elif k == 'bell':
            L((12, 3, 12, 5))
            ARC((5, 5, 19, 17), 0, 180)
            L((5, 11, 5, 15, 3, 17, 21, 17, 19, 15, 19, 11))
            L((10, 20, 14, 20))
        else:
            # 未知 kind 兜底成点，绝不留空白方块。
            OF((10, 10, 14, 14), c['t3'])
        if raster:
            flush()
            key = (self.kind, self.s, bool(getattr(self, 'flip', False)))
            covs = _COV_CACHE.get(key)
            if covs is None:
                covs = [_coverage(self.s, self.s, g[0]) for g in groups]
                _COV_CACHE[key] = covs
            self._img = _sprite(self, self.s, self.s,
                                list(zip(covs, [g[1] for g in groups])), self.bg)
            self.create_image(0, 0, anchor='nw', image=self._img)

class ToolTip(object):
    """悬停名称 320ms 后弹出反色简介；移开、点击、切主题立即收起。"""

    def __init__(self, app, widget, text):
        self.app = app
        self.widget = widget
        self.text = text
        self.tip = None
        self.job = None
        widget.bind('<Enter>', self.schedule)
        widget.bind('<Leave>', self.hide)
        widget.bind('<ButtonPress>', self.hide)

    def schedule(self, ev=None):
        self.cancel()
        self.job = self.widget.after(320, self.show)

    def cancel(self):
        if self.job:
            try:
                self.widget.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def show(self):
        if self.tip or not self.widget.winfo_exists():
            return
        c = self.app.colors
        tip = tk.Toplevel(self.app.root)
        tip.wm_overrideredirect(True)
        try:
            tip.wm_attributes('-topmost', True)
        except Exception:
            pass
        frm = tk.Frame(tip, bg=c['tipbg'], highlightthickness=1,
                       highlightbackground=c['line'])
        frm.pack()
        tk.Label(frm, text=self.text, justify='left', anchor='w',
                 bg=c['tipbg'], fg=c['tipfg'], font=self.app.f_small,
                 wraplength=self.app.px(320), padx=self.app.px(10),
                 pady=self.app.px(8)).pack()
        tip.update_idletasks()
        x, y = self.widget.winfo_pointerxy()
        x += self.app.px(14)
        y += self.app.px(16)
        w, h = tip.winfo_width(), tip.winfo_height()
        sw, sh = tip.winfo_screenwidth(), tip.winfo_screenheight()
        if x + w > sw - 8:
            x = max(8, sw - w - 8)
        if y + h > sh - 8:
            y = max(8, sh - h - 8)
        tip.wm_geometry('+%d+%d' % (x, y))
        self.tip = tip
        ms = self.app.anim_ms(180)
        if ms > 0:
            def step(p):
                try:
                    tip.attributes('-alpha', p)
                except Exception:
                    pass
            try:
                tip.attributes('-alpha', 0.0)
                self.app.tween(('tip', id(tip)), ms, step)
            except Exception:
                pass

    def hide(self, ev=None):
        self.cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


# ---------------- 界面 ----------------

class App(object):
    def __init__(self, root, probe=False, dpi_override=None):
        self.root = root
        self.probe = probe
        # 只读模式：config.json 写不进去（典型：装在 Program Files 且拒绝了 UAC）
        # 时三个功能开关、停止守护、重扫游戏库、开机自启全部置灰。
        self.readonly = bool(READONLY) and not probe
        # 插件管理（＋ / 垃圾桶）要写 plugins\\ 与安装根目录（热重载标记），
        # 权限口径单独判：config.json 可写但 plugins\\ 被收紧时按钮也要置灰。
        self.plugins_ro = self.readonly or not (
            dir_writable(BASE) and plugin_package.plugins_writable(PLUGINS_DIR))
        self.dpi = int(dpi_override) if dpi_override else window_dpi(root.winfo_id())
        self._job = None
        self._dpi_lock = False
        self.settings_win = None          # 浏览器式弹出菜单（overrideredirect）
        self.settings_outer = None
        self.settings_canvas = None
        self.settings_inner = None
        self._menu_window_id = None
        self.gear_wrap = None
        self.gear_btn = None
        self._menu_was_pressed = False
        # 每插件设置弹框（与上面的齿轮菜单是两套浮层，互斥打开）
        self.plugin_win = None
        self.plugin_body = None
        self.plugin_canvas = None
        self._plugin_window_id = None
        self._plugin_expanded = False
        self.plugin_entry = None
        self.plugin_anchor = None
        self.plugin_gears = {}
        self._plugin_was_pressed = False
        self._setting_switches = {}
        self.status_label = None
        self.tooltips = []
        self.switches = {}
        self.theme_buttons = {}

        # 显示名与系统标识统一 WBOX（v2.11.0 全量改名：exe / 安装目录 /
        # 计划任务 / 注册表键 / 管道 / mutex / 备份目录）；旧 Wingman 安装
        # 由安装器 migrate_legacy 迁移，面板只认新标识。
        root.title('WBOX')
        self.apply_scaling()

        self.family = pick_family(root)
        # 负号 = 像素尺寸。Tk 会缓存点数字体的像素度量，只改 tk scaling 时字号不变，
        # 所以这里由 resize_fonts() 按 DPI 显式重设，保证 150%/200% 下字也等比放大。
        self.f_title = tkfont.Font(root, family=self.family, weight='bold')
        self.f_head = tkfont.Font(root, family=self.family, weight='bold')
        # 行名用中等字重：设计稿 .nm 是 500，Tk 只有 400/700 两档，
        # 700 在小字号上比设计稿重一档，整屏看着发黑，所以行名走 400。
        self.f_row = tkfont.Font(root, family=self.family)
        self.f_body = tkfont.Font(root, family=self.family)
        self.f_small = tkfont.Font(root, family=self.family)
        self.f_wordmark = tkfont.Font(root, family=self.family, weight='bold')
        # 注册表必须在读配置之前建好：三段合并要用各插件的 DEFAULT_CONFIG，
        # 主页面行数与菜单分组也全靠它（顺序反了就会拿不到插件默认值）。
        self.entries_all = plugin_entries()
        self.runtime_state = read_plugin_states()
        # 主页面只显示「加载成功 + 作者声明 home」的插件；失败的插件只在齿轮菜单
        # 里露一行状态，不占主页面（半成品功能开关比没有更糟）。
        self.entries = [e for e in self.entries_all
                        if e.status == plugin_host.STATUS_OK and e.home]
        self.entries.sort(key=lambda e: (e.order, e.id))

        cfg = effective_cfg(entries=self.entries_all)
        # 皮肤与外观：resolve() 内部处理老配置（只有 ui_theme）的迁移，
        # 找不到 skin.json 时回退 plain，绝不让面板变成白板。
        self.skin, self.theme = skins.resolve(cfg, base=BASE, log=self.note)
        # 明暗偏好与解析结果分开存：选「跟随系统」时落盘的是 system，
        # 而 self.theme 永远是解析后的 light/dark（控件配色只认这两个）。
        pref = str(cfg.get('ui_appearance') or '').lower()
        self.appearance_pref = pref if pref in ('system', 'light', 'dark') \
            else self.theme
        # v2.10.0 迁移：老配置显式写过 liquid-glass 的，内置已移除会回退
        # plain；把解析结果写回一次，免得每次启动都走「不存在→回退」日志。
        if not self.readonly and not probe \
                and cfg.get('ui_skin') not in (None, self.skin.id):
            self.save_appearance()
        self.tok = self.skin.tokens(self.theme)
        self.colors = legacy_colors(self.tok, self.theme)
        # 两档皮肤走同一套骨架，差异只在材质与交互密度：
        # glass=True 圆角卡片 + 浮层设置；False 方角直排 + 行内展开 + 同窗换页。
        self.glass = self.skin.performance == 'standard'
        self.pane = 'list'                # 极简档：list / set 同窗两页
        self.inline_open = None           # 极简档：哪一行展开了内联设置
        self.inline_full = None           # 极简档：哪一行摊开了全部设置项
        self.board_full_h = 0             # 列表内容全高，溢出滚动的基准
        self.set_canvas = None            # 极简设置页的滚动容器（仅溢出时滚）
        self.set_inner = None
        self.skin_canvas = None           # 皮肤面板的滚动容器（仅溢出时滚）
        self.skin_window_id = None
        self.win_w_pref = BASE_W if self.glass else PLAIN_W  # 最大化按钮会改它
        self.custom_chrome = False
        self._drag = None
        self._maximized = False
        self._normal_geo = None
        self._tweens = {}
        self._motion_on = client_area_animation()
        self._menu_fade = None
        self._dialog_win = None
        self._dialog_picks = []
        self._menu_rows = []
        self.resize_fonts()

        # 开关状态一个插件一个：键就是插件 id，值是插件自己算出来的初始状态。
        self.vars = {}
        for entry in self.entries:
            self.vars[entry.id] = tk.BooleanVar(
                root, value=plugin_switch_state(entry, cfg))
        self.boot_var = tk.BooleanVar(root, value=(True if probe else autostart_state()))
        self.boot_initial = bool(self.boot_var.get())
        # 托盘两项是面板级偏好（不是插件键），默认关：收托盘/弹气泡都改变
        # 用户可见行为，opt-in 比 opt-out 安全。图标存在性不在此列（v2.11.2
        # 起常驻，见 apply_tray_pref）。设计稿 wbox-skins.html 设置页。
        self.tray_var = tk.BooleanVar(root, value=bool(cfg.get('start_in_tray')))
        self.notify_var = tk.BooleanVar(
            root, value=bool(cfg.get('notify_game_end')))
        self.tray = None
        self._last_session = ''

        self.main = tk.Frame(root, bg=self.colors['bg'],
                             padx=self.px(self.pad_x()), pady=self.px(self.pad_y()))
        self.main.pack(fill='both', expand=True)
        self.apply_chrome()
        self.apply_icon(root)
        self.build_main()

        root.minsize(self.px(430), self.px(200))
        root.resizable(False, False)
        root.bind('<Configure>', self.on_configure)
        # 点击主窗口任意空白处 / Esc 收起弹出菜单（齿轮自身在 ButtonRelease 处理）
        root.bind('<Button-1>', self._menu_outside, add='+')

        def _esc(e):
            # 收最上面那层浮层；都收起后焦点回字标（close_* 里做）
            if getattr(self, 'skin_win', None) and self.skin_win.winfo_exists():
                self.close_skin_settings()
            elif getattr(self, 'plugin_win', None) and self.plugin_win.winfo_exists():
                self.close_plugin_settings()
            else:
                self.close_settings()
        root.bind('<Escape>', _esc, add='+')
        self.place()

        if not probe:
            self.apply_tray_pref()
            self.refresh()
        root.bind('<Destroy>', lambda e: self._tray_shutdown(), add='+')

    # ---- 尺寸 ----
    def pad_x(self):
        # v2.8.0 起主容器不再留边：标题栏与列表各自带内边距（设计稿的
        # 28/8 与 8/6 是两层，交给一处算会把两档皮肤的间距搅在一起）。
        return 0

    def pad_y(self):
        return 0

    def px(self, value):
        return max(1, int(round(value * self.scale)))

    def apply_scaling(self):
        self.scale = self.dpi / 96.0
        self.root.tk.call('tk', 'scaling', self.dpi / 72.0)

    def resize_fonts(self):
        # 字号基准来自皮肤 metrics.font（玻璃 15 / 极简 13），
        # 标题与辅助字按它派生，避免每档皮肤各写一套固定像素。
        skin = getattr(self, 'skin', None)
        base = skin.metrics.get('font', 15) if skin is not None else 15
        # 字标单独一档：玻璃 22 / 极简 15。它要压得住整屏，跟着正文字号
        # 等比放大会在极简皮肤里显得头重脚轻。
        word = 22 if (skin is not None and skin.performance == 'standard') else 15
        # 极简档辅助字只比正文小 1/2 px：v2.9.0 的 -2/-3 派生把「设置」等
        # 入口压到 10px，用户实测偏小；standard 档保持设计稿派生。
        minimal = skin is not None and skin.performance == 'minimal'
        for f, size_px in ((self.f_title, int(round(base * 1.5))),
                           (self.f_head, base),
                           (self.f_row, base),
                           (self.f_body, max(10, base - (1 if minimal else 2))),
                           (self.f_small, max(9, base - (2 if minimal else 3))),
                           (self.f_wordmark, word)):
            try:
                f.configure(size=-self.px(size_px))     # 负数 = 像素
            except Exception:
                pass

    def relayout(self):
        """新版没有需要按窗口宽度换行的静态文本，保留接口给 apply_dpi。"""
        return

    def popup_colors(self, key='pop_base'):
        """浮层用的配色：底色换成 menu_base / pop_base。

        主窗口底（glass）与浮层底必须分开：两者同色时浮层像"窗口的一部分"，
        用户分不清哪块是可以点掉的浮层。设计稿里就是 .72 / .97 两档。
        """
        c = dict(self.colors)
        c['bg'] = self.tok.get(key, c['bg'])
        return c

    def note(self, msg):
        """皮肤回退这类"用户可能想知道、但不该被弹框打断"的事件，只写日志。"""
        try:
            with open(os.path.join(BASE, 'guard.log'), 'a', encoding='utf-8') as f:
                f.write('%s [skin] %s\n' % (
                    __import__('time').strftime('%Y-%m-%d %H:%M:%S'), msg))
        except Exception:
            pass

    def dbg(self, msg):
        """开发期定位用：设了 ABG_DBG 才写日志，正常使用零开销。"""
        if not os.environ.get('ABG_DBG'):
            return
        try:
            with open(os.path.join(BASE, 'gui_debug.log'), 'a', encoding='utf-8') as f:
                f.write('%s dpi=%s scale=%.3f win=%dx%d %s\n' % (
                    __import__('time').strftime('%H:%M:%S'), self.dpi, self.scale,
                    self.root.winfo_width(), self.root.winfo_height(), msg))
        except Exception:
            pass

    def place(self):
        lx, ly, sw, sh = work_area()
        self.dbg('place start area=%d,%d,%d,%d mode=%s sdpi=%d wdpi=%d' % (
            lx, ly, sw, sh, DPI_MODE, system_dpi(), window_dpi(self.root.winfo_id())))
        w = self.content_width()
        self.root.geometry('%dx100+%d+%d' % (w, lx + max(0, (sw - w) // 2), ly))
        self.root.update_idletasks()
        self.relayout()
        self.root.update_idletasks()
        # 高度完全由内容决定：标题栏 + 列表（+ 极简档展开的设置块）。
        # 量之前先把容器恢复成全高，否则上一次溢出裁剪后的高度会骗过 reqheight。
        bd = getattr(self, 'board', None)
        if bd is not None and bd.winfo_exists() and self.board_full_h:
            bd.configure(height=self.board_full_h)
        if (self.set_canvas is not None and self.set_canvas.winfo_exists()
                and self.set_inner is not None):
            self.set_canvas.configure(height=self.set_inner.winfo_reqheight())
        self.root.update_idletasks()
        need = self.root.winfo_reqheight()
        if getattr(self, '_maximized', False):
            h, x, y = sh, lx, ly
        else:
            h = min(need, sh - self.px(40))
            x = lx + max(0, (sw - w) // 2)
            y = ly + max(0, (sh - h) // 2)
        self.root.geometry('%dx%d+%d+%d' % (w, h, x, y))
        self.root.update_idletasks()
        self.apply_window_region()
        self._fit_overflow(need, h)
        self.dbg('place end w=%d h=%d need=%d' % (w, h, need))

    def _fit_overflow(self, need, h):
        """内容比窗口高时滚轮滚，不硬截（屏幕高度是硬上限，设计稿的
        「高度随内容」只在不超屏时成立）。列表与极简设置页各走自己的容器。"""
        over = max(0, need - h)
        bd = getattr(self, 'board', None)
        if bd is not None and bd.winfo_exists() and self.board_full_h:
            bd.configure(height=max(self.px(60), self.board_full_h - over))
            bd.configure(scrollregion=(0, 0, bd.winfo_reqwidth(),
                                       self.board_full_h))
            self._bind_wheel(bd, bd)
        cv, inner = self.set_canvas, self.set_inner
        if cv is not None and cv.winfo_exists() and inner is not None:
            cw = max(1, cv.winfo_width())
            cv.itemconfigure(self._set_window_id, width=cw)
            cv.configure(scrollregion=(0, 0, cw, inner.winfo_reqheight()))
            self._bind_wheel(cv, inner)

    def apply_dpi(self, dpi):
        self.dpi = int(dpi)
        self.apply_scaling()
        self.resize_fonts()
        self.root.minsize(self.px(430), self.px(200))
        self.rebuild()

    def on_configure(self, ev):
        if ev.widget is not self.root:
            return
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
        self._job = self.root.after(180, self._after_resize)

    def _after_resize(self):
        self._job = None
        self.relayout()
        if self.probe or self._dpi_lock:
            return
        try:
            new = window_dpi(self.root.winfo_id())
        except Exception:
            return
        self.dbg('after_resize wdpi=%d' % new)
        if new != self.dpi:
            self._dpi_lock = True
            try:
                self.apply_dpi(new)
            finally:
                self._dpi_lock = False
            self.dbg('apply_dpi -> %d' % new)

    # ---- 主题 / 图标 ----
    def apply_icon(self, win):
        p = ICON_DARK if self.theme == 'dark' else ICON_LIGHT
        if os.path.isfile(p):
            try:
                win.iconbitmap(p)
            except Exception:
                pass
        # wm_frame() 要等窗口被 map 之后才有值；补两次延迟，确保系统标题栏
        # 与任务管理器用的大小图标都落在顶层 frame 上，而不是客户区子窗口。
        def refresh(w=win):
            self.apply_titlebar(w)
            set_window_icons(w, ICON_DARK if self.theme == 'dark' else ICON_LIGHT)

        try:
            win.after(60, refresh)
            win.after(260, refresh)
        except Exception:
            pass

    def apply_titlebar(self, win):
        """Windows 系统标题栏跟随主题：纯黑主题启用 DWM 沉浸式深色模式。

        系统标题栏不能直接用 Tk 改色，用 DWMWA_USE_IMMERSIVE_DARK_MODE（新系统
        20 / 旧系统 19）切到深色；失败就保留系统默认，不影响功能。
        """
        try:
            frame = win.wm_frame()
            hwnd = int(frame, 16) if frame else win.winfo_id()
            f = ctypes.windll.dwmapi.DwmSetWindowAttribute
            f.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_int), ctypes.c_uint32]
            f.restype = ctypes.c_long
            val = ctypes.c_int(1 if self.theme == 'dark' else 0)
            for attr in (20, 19):
                f(ctypes.c_void_p(hwnd), attr, ctypes.byref(val), 4)
            # 切换后强制重画非客户区，否则部分系统上标题栏要等一次 resize 才刷新
            u = ctypes.windll.user32
            u.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_uint32]
            u.RedrawWindow(ctypes.c_void_p(hwnd), None, None,
                           0x0001 | 0x0400 | 0x0100)   # INVALIDATE|FRAME|UPDATENOW
        except Exception:
            pass

    def save_appearance(self):
        """外观单独落盘。

        不放进 save_features：用户拨一个功能开关时，配置里不该多出 ui_skin /
        ui_appearance 两条改动——那是切换外观的副作用，会让「只改了这一项」
        的断言（以及用户对比配置 diff 时的判断）失真。
        """
        if self.readonly:
            return
        try:
            cfg = load_cfg()
            cfg['ui_skin'] = self.skin.id
            cfg['ui_appearance'] = self.appearance_pref
            cfg['ui_theme'] = self.theme      # 老字段留一份快照，降级不突变
            save_cfg(cfg)
        except Exception:
            self.note('外观未能保存到 config.json')

    def apply_skin(self, skin, appearance=None, persist=True):
        """换皮肤 / 换明暗的唯一入口。

        每次都重建控件：Tk 控件一旦创建就记住自己的 bg，改配色只改
        Frame 那一层会留下旧色的方块（历史坑）。重建一次的成本是几十毫秒，
        用户点一下皮肤能接受。persist=False 供 layout-check 这类
        「只换骨架不落盘」的自检路径用。
        """
        self.skin = skin
        if appearance in ('light', 'dark', 'system'):
            self.appearance_pref = appearance
            self.theme = skins.detect_appearance() if appearance == 'system' \
                else appearance
        self.tok = skin.tokens(self.theme)
        self.colors = legacy_colors(self.tok, self.theme)
        # 换皮肤会换整套交互骨架（圆角卡片 / 行内展开 / 同窗换页），
        # 这三样必须在重建前跟着皮肤走，否则极简档会画成玻璃的样子。
        self.glass = skin.performance == 'standard'
        self.win_w_pref = BASE_W if self.glass else PLAIN_W
        self.pane = 'list'
        self.inline_open = None
        self.resize_fonts()
        # 只读模式下外观只在本进程内生效，不弹"需要管理员权限"打断切换
        if persist:
            self.save_appearance()
        self.apply_icon(self.root)
        self.apply_tray_pref()
        self.rebuild()

    def set_theme(self, name):
        """兼容旧调用：只换明暗，皮肤不变。system = 跟随系统应用主题。"""
        if name not in ('light', 'dark', 'system') or name == self.appearance_pref:
            return
        self.apply_skin(self.skin, name)

    def set_skin(self, skin_id):
        if skin_id == self.skin.id:
            return
        skin = skins.get(skin_id, base=BASE, log=self.note)
        if skin is None:
            self.note('皮肤 %s 不存在，忽略切换' % skin_id)
            return
        self.apply_skin(skin, self.appearance_pref)

    def rebuild(self):
        """切主题 / 改 DPI 时按新令牌重建所有控件，避免残留旧配色的方块。"""
        # 皮肤可能被外部直接换掉（截图脚本、测试替身），骨架标志在这里重新取一次，
        # 不然极简档会套着玻璃的圆角卡片重绘。
        self.glass = self.skin.performance == 'standard'
        self.close_plugin_settings()
        self.hide_tips()
        try:
            self.root.configure(bg=self.colors['bg'])
            self.main.configure(bg=self.colors['bg'],
                                padx=self.px(self.pad_x()),
                                pady=self.px(self.pad_y()))
        except Exception:
            pass
        for w in list(self.main.winfo_children()):
            w.destroy()
        self.tooltips = []
        self.switches = {}
        self.build_main()
        if self.settings_win and self.settings_win.winfo_exists():
            for w in list(self.settings_inner.winfo_children()):
                w.destroy()
            c = self.colors
            self.settings_win.configure(bg=c['line'])
            self.settings_outer.configure(bg=c['line'])
            self.settings_canvas.configure(bg=c['bg'])
            self.settings_inner.configure(bg=c['bg'])
            self.build_settings(self.settings_inner)
            self.place_menu()
        if getattr(self, 'skin_win', None) and self.skin_win.winfo_exists():
            self.refresh_skin_list()
        self.place()
        self.relayout()

    def hide_tips(self):
        for t in self.tooltips:
            t.hide()
        self.tooltips = []

    # ---- 动效：单 after 链插值（设计稿时长 + 唯一曲线） ----
    def anim_ms(self, ms):
        """玻璃档才动；系统关动画 / probe 自检一律 0ms（直接跳终态）。"""
        if self.probe or not self.glass or not self._motion_on:
            return 0
        return ms

    def cancel_tween(self, key):
        job = self._tweens.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    def tween(self, key, ms, step, done=None):
        """step(p) 收 eased 进度。同 key 重开先 cancel 旧链：hover 进出交替、
        开关连点都不会留两条链抢同一个控件。"""
        self.cancel_tween(key)
        if ms <= 0:
            step(1.0)
            if done:
                done()
            return
        t0 = time.monotonic()

        def tick():
            p = (time.monotonic() - t0) * 1000.0 / ms
            if p >= 1.0:
                self._tweens.pop(key, None)
                step(1.0)
                if done:
                    done()
                return
            step(ease(p))
            self._tweens[key] = self.root.after(16, tick)
        tick()

    def _pop_in(self, win, slide=0):
        """浮层入场 200ms 淡入；菜单另加 translateY(-6px) 落位。

        只做入场：退场立即 destroy——淡出链会让 Toplevel 残骸多活一百多毫秒，
        撞重建的「无残留浮层」断言，也撞点外部收起的全局判定。
        """
        ms = self.anim_ms(200)
        if ms <= 0 or not win.winfo_exists():
            return
        win.update_idletasks()
        x, y = win.winfo_x(), win.winfo_y()
        try:
            win.attributes('-alpha', 0.0)
        except Exception:
            return

        def step(p):
            try:
                win.attributes('-alpha', p)
                if slide:
                    win.geometry('+%d+%d' % (x, y - int(round(slide * (1 - p)))))
            except Exception:
                pass

        def done():
            try:
                win.geometry('+%d+%d' % (x, y))
                win.attributes('-alpha', 1.0)
            except Exception:
                pass
        self.tween(('pop', id(win)), ms, step, done)

    # ---- 通用控件 ----
    # v1.2.0 起界面只剩「齿轮图标按钮 + 自绘菜单行」，旧版 tk.Button 工厂已删除：
    # 三类交互全部由 Glyph / Switch / menu_row 承接，避免两套控件风格并存。
    def sep(self, master, pady=0):
        tk.Frame(master, height=1, bg=self.colors['line']).pack(
            fill='x', pady=pady)

    def hover(self, widgets, on, base=None, press=False):
        base = base or self.colors['bg']
        target = self.colors['press'] if press else (
            self.colors['hover'] if on else base)
        ms = self.anim_ms(120 if press else 160)
        key = ('hover', id(widgets[0]))
        if ms <= 0:
            self.cancel_tween(key)
            self._paint_bg(widgets, target)
            return
        cur = self._bg_of(widgets[0]) or base
        self.tween(key, ms,
                   lambda p: self._paint_bg(widgets, mix_hex(cur, target, p)))

    def _bg_of(self, w):
        try:
            return w.cget('bg')
        except Exception:
            return None

    def _paint_bg(self, widgets, color):
        for w in widgets:
            if isinstance(w, (Switch, Glyph)):
                w.set_bg(color)
            else:
                try:
                    w.configure(bg=color)
                except Exception:
                    pass

    def bind_hover(self, row, widgets, base=None):
        allw = (row,) + tuple(widgets)

        def enter(ev, ws=widgets):
            self.hover(ws, True, base)

        def leave(ev, ws=widgets):
            self.hover(ws, False, base)

        def press(ev, ws=widgets):
            self.hover(ws, True, base, press=True)

        def release(ev, ws=widgets):
            self.hover(ws, True, base)

        for w in allw:
            # add='+'：名称上已经绑了 ToolTip 的 Enter/Leave，不能被覆盖
            w.bind('<Enter>', enter, add='+')
            w.bind('<Leave>', leave, add='+')
            w.bind('<ButtonPress>', press, add='+')
            w.bind('<ButtonRelease>', release, add='+')

    # ---- 窗口外壳：自绘标题栏（设计稿 v3.4.0 方案 4） ----
    def content_width(self):
        """客户区宽度（像素）：基准 640 逻辑像素，屏幕窄了才收。"""
        _, _, sw, _ = work_area()
        if getattr(self, '_maximized', False):
            return sw
        want = int(round(self.win_w_pref * self.scale))
        return min(want, max(self.px(320), sw - self.px(30)))

    def apply_chrome(self):
        """把系统标题栏换掉，整行交给自绘。

        方案 4 要求「字标 + 加号 + 窗口控制」同处一行 48px，系统标题栏做不到。
        overrideredirect 之后系统不再给标题栏、圆角、阴影，代价自己补：
          * 圆角 = SetWindowRgn(CreateRoundRectRgn)，半径取皮肤 radius.window
            （极简档半径 0，一次 API 都不调，保持"方角零开销"）
          * 任务栏 = WS_EX_APPWINDOW，否则 overrideredirect 窗口最小化等于消失；
            wrapper 诞生于 map，风格位要在 <Map> 钩子里重补（apply_appwindow）
          * 拖动 = 标题栏上跟鼠标，窗口尺寸由内容定，没有可缩放的余地
        阴影不做：DWM 只给带 frame 的窗口投影，硬要就得拦 WndProc，
        收益一个像素级观感、代价是整条窗口消息链，不划算。
        """
        if self.skin.chrome != 'custom':
            return
        try:
            self.root.overrideredirect(True)
        except Exception:
            return
        self.custom_chrome = True
        self.apply_appwindow()
        self.root.bind('<Map>', lambda e: self.apply_appwindow(refresh=True),
                       add='+')

    def apply_appwindow(self, refresh=False):
        """任务栏按钮：overrideredirect 的 wrapper 在 map 时才被 Tk 创建并带上
        WS_EX_TOOLWINDOW（init 时 wm_frame() 落回客户区子窗口，风格位加错对象），
        所以 <Map> 之后必须对 wrapper 重补：清 TOOLWINDOW、加 APPWINDOW。
        可见路径实测（probe_tb_deploy）：shell 在 wrapper 首次 show 时就按
        TOOLWINDOW 缓存了排除决定，Map 里改位 + FRAMECHANGED 不够，必须
        hide/show 刷新一次才出按钮；alpha=0 探针路径改位即够（hook_bits 截图），
        两条路径都覆盖才稳。withdraw/deiconify 会清键盘焦点（焦点 None 时 Tk
        丢弃键事件、菜单 Esc 全链路失效），刷新前记住焦点、刷新后 focus_force
        回填——focus_set 在非前景进程不生效，force 才落得住（bisect 实测）。
        iconic 时不刷新，等下次 show 自然重判，避免把最小化按钮刷没。"""
        try:
            u = ctypes.windll.user32
            hwnd = win_hwnd(self.root)
            u.GetWindowLongW.restype = ctypes.c_long
            ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), -20)      # GWL_EXSTYLE
            want = (ex | 0x00040000) & ~0x00000080   # +APPWINDOW -TOOLWINDOW
            if want == ex:
                return
            u.SetWindowLongW(ctypes.c_void_p(hwnd), -20, ctypes.c_long(want))
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                           0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
            if refresh and self.root.state() == 'normal':
                try:
                    focus = self.root.focus_get()
                except Exception:
                    focus = None
                self.root.withdraw()
                self.root.deiconify()
                try:
                    (focus if focus is not None else self.root).focus_force()
                except Exception:
                    pass
        except Exception:
            pass

    def apply_window_region(self):
        """圆角窗口：Tk 只能画方形窗口，圆角靠 Windows 区域裁。"""
        try:
            u = ctypes.windll.user32
            g = ctypes.windll.gdi32
            hwnd = win_hwnd(self.root)
            r = int(round(float(self.skin.radius.get('window') or 0) * self.scale))
            if r <= 0:
                u.SetWindowRgn(ctypes.c_void_p(hwnd), None, True)
                return
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if w < 2 or h < 2:
                return
            rgn = g.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
            u.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
        except Exception:
            pass

    def _drag_start(self, ev):
        self._drag = (ev.x_root - self.root.winfo_x(),
                      ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        d = getattr(self, '_drag', None)
        if not d:
            return
        self.root.geometry('+%d+%d' % (ev.x_root - d[0], ev.y_root - d[1]))

    def on_close(self):
        try:
            self.close_settings()
        except Exception:
            pass
        if self.tray is not None and bool(self.tray_var.get()):
            # 「启动后收进托盘」：关窗 = 收进托盘，守护与面板进程都继续活着
            self.root.withdraw()
            return
        self.root.destroy()

    def on_min(self):
        """最小化：overrideredirect 窗口 iconify() 无效，先装回系统外壳再最小化，
        重新映射后把自绘外壳装回去。"""
        if not self.custom_chrome:
            self.root.iconify()
            return
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.after(100, self._restore_chrome)

    def on_max(self):
        """最大化 = 铺满工作区（留任务栏，不是全屏）；再点还原最大化前的尺寸位置。"""
        lx, ly, sw, sh = work_area()
        if getattr(self, '_maximized', False):
            self._maximized = False
            self.rebuild()
            geo = getattr(self, '_normal_geo', None)
            if geo:
                try:
                    self.root.geometry(geo)
                    self.root.update_idletasks()
                    self.apply_window_region()
                    hh = int(geo.split('+')[0].split('x')[1])
                    self._fit_overflow(self.root.winfo_reqheight(), hh)
                except Exception:
                    pass
            kind = 'max'
        else:
            self._normal_geo = self.root.geometry()
            self._maximized = True
            self.rebuild()
            kind = 'restore'
        g = getattr(self, 'max_wrap', None)
        if g is not None and getattr(g, 'glyph', None) is not None:
            g.glyph.kind = kind
            g.glyph.draw()

    def _restore_chrome(self):
        if not self.custom_chrome:
            return
        try:
            if self.root.state() == 'iconic':
                self.root.after(100, self._restore_chrome)
                return
        except Exception:
            pass
        try:
            self.root.overrideredirect(True)
            self.apply_appwindow()
            self.apply_window_region()
        except Exception:
            pass

    # ---- 标题栏控件 ----
    def chrome_button(self, master, kind, command, tip):
        """窗口控制：34×26 命中区，悬停一层圆角底（极简档方角）。"""
        c = self.colors
        glass = self.skin.performance == 'standard'
        w, h = self.px(34), self.px(26)
        cv = tk.Canvas(master, width=w, height=h, bg=c['bg'],
                       highlightthickness=0, bd=0, cursor='hand2')
        g = Glyph(cv, self, kind, size=self.px(12 if not glass else 11), ink='t3')
        g.set_bg(c['bg'])
        cv.create_window(w // 2, h // 2, window=g)
        base = c['bg']
        hot = c['hover']

        def paint(on, col=None):
            col = col or (hot if on else base)
            try:
                cv.delete('hl')
                g.set_bg(col)
                if col != base:
                    r = self.px(8) if glass else 0
                    if r:
                        round_rect(cv, 1, 1, w - 1, h - 1, r, fill=col,
                                   outline='', tags='hl')
                    else:
                        cv.create_rectangle(1, 1, w - 1, h - 1, fill=col,
                                            outline='', tags='hl')
                    cv.tag_lower('hl')
            except Exception:
                pass

        def fade(to, ms):
            frm = g.bg
            self.tween(('chr', id(cv)), ms,
                       lambda p: paint(True, mix_hex(frm, to, p)))

        for wid in (cv, g):
            wid.bind('<Enter>', lambda e: fade(hot, self.anim_ms(160)), add='+')
            wid.bind('<Leave>', lambda e: fade(base, self.anim_ms(160)), add='+')
            wid.bind('<ButtonPress>',
                     lambda e: fade(self.colors['press'], self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease>', lambda e: fade(hot, self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease-1>', lambda e: command(), add='+')
        self._keyboard(cv, command)
        cv.glyph = g
        self.tooltips.append(ToolTip(self, cv, tip))
        return cv

    def round_button(self, master, size, glyph_kind, glyph_size, command, tip):
        """圆形按钮（加号）：fill 底 + 字形，悬停换 fill_2。极简档只留字形与方底。"""
        c = self.colors
        glass = self.skin.performance == 'standard'
        cv = tk.Canvas(master, width=size, height=size, bg=c['bg'],
                       highlightthickness=0, bd=0)
        g = Glyph(cv, self, glyph_kind, size=glyph_size, ink='fg')
        # 极简档也要读得出「这是个按钮」：浅灰圆角方底 + 悬停加深，
        # 不再是一枚裸字形漂在标题栏上（v2.10.0 打磨）。
        base = self.tok['fill'] if glass else self.tok['fill_2']
        hot = self.tok['fill_2'] if glass else self.tok['fill']
        g.set_bg(base)
        cv.create_window(size // 2, size // 2, window=g)

        def paint(on, col=None):
            col = col or (hot if on else base)
            try:
                cv.delete('bg')
                if glass:
                    cv.create_oval(0, 0, size, size, fill=col, outline='',
                                   tags='bg')
                else:
                    round_rect(cv, 0, 0, size, size, self.px(6), fill=col,
                               outline='', tags='bg')
                cv.tag_lower('bg')
                g.set_bg(col)
            except Exception:
                pass

        paint(False)

        def fade(to, ms):
            frm = g.bg
            self.tween(('rnd', id(cv)), ms,
                       lambda p: paint(True, mix_hex(frm, to, p)))

        for wid in (cv, g):
            wid.bind('<Enter>', lambda e: fade(hot, self.anim_ms(160)), add='+')
            wid.bind('<Leave>', lambda e: fade(base, self.anim_ms(160)), add='+')
            wid.bind('<ButtonPress>',
                     lambda e: fade(self.colors['press'], self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease>', lambda e: fade(hot, self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease-1>', lambda e: command(), add='+')
        self._keyboard(cv, command)
        cv.glyph = g        # 调用方按 kind 找图标（测试与自检都靠它）
        self.tooltips.append(ToolTip(self, cv, tip))
        return cv

    # ---- 主页面 ----
    def build_main(self):
        """标题栏 + 内容区。内容区按皮肤分两套：

        * 玻璃档：圆角卡片 + 每行行尾一枚滑块，点开 Toplevel 设置浮层；
        * 极简档：方角列表 + 行内展开（不新建窗口），「设置」在同窗换页。

        两套都从插件注册表生成，行数据来源只有一个。
        """
        self.plugin_gears = {}
        self.build_titlebar(self.main)
        if self.pane == 'set' and not self.glass:
            self.build_set_view(self.main)
        else:
            self.build_list_view(self.main)
        # Tab 顺序照文案规范 §4：＋ → 字标（菜单入口）→ 各行开关 → 各行行尾。
        # 设置页没有开关行，链收成「面包屑 → 字标 → 页内可点行」。
        chain = []
        if self.pane != 'set' and getattr(self, 'add_wrap', None) is not None:
            chain.append(self.add_wrap)
        if not self.glass and getattr(self, 'pane_btn', None) is not None:
            chain.append(self.pane_btn)
        chain.append(self.brand_wrap)
        if self.pane == 'set' and not self.glass:
            chain.extend(getattr(self, '_set_focusables', []))
        else:
            chain.extend(self.switches.values())
            chain.extend(self.plugin_gears.values())
        self._set_chain(self.root, chain)

    def build_titlebar(self, master):
        """一行装下字标（=菜单入口）、加号、窗口控制。

        间距照 design/wbox-preview.html：玻璃档左右 28/8、字标热区 34 高、
        加号 32 圆、窗口控制 34×26 且用发丝线隔开；极简档 16/6、字标 15。
        """
        c = self.colors
        glass = self.glass
        tb_h = self.px(self.skin.metrics.get('titlebar', 48))
        bar = tk.Frame(master, bg=c['bg'], height=tb_h)
        bar.pack(fill='x')
        bar.pack_propagate(False)
        self.titlebar = bar

        # 右起：关闭、发丝线、最大化、最小化（设计稿 34×26 三枚）
        ctrls = tk.Frame(bar, bg=c['bg'])
        ctrls.pack(side='right', padx=(0, self.px(6 if glass else 4)))
        self.chrome_button(ctrls, 'close', self.on_close,
                           '关闭面板（守护在后台继续运行）').pack(side='right')
        tk.Frame(ctrls, bg=c['line'], width=self.px(1)).pack(
            side='right', fill='y', pady=self.px(9), padx=self.px(4))
        self.max_wrap = self.chrome_button(ctrls, 'max', self.on_max,
                                           '最大化 / 还原窗口')
        self.max_wrap.pack(side='right')
        self.chrome_button(ctrls, 'min', self.on_min,
                           '最小化').pack(side='right')

        # 加号：唯一动作按钮（装插件）。设置页里不出现——设计稿在 set 页把
        # .p-add 一起 display:none，装插件跟"改设置"不是一件事。
        size = self.px(32 if glass else 26)
        self.add_wrap = self.round_button(
            bar, size, 'plus', self.px(17 if glass else 15),
            self.on_add_plugin,
            '安装插件：选一个插件文件，装好后立即生效。')
        if self.pane != 'set':
            self.add_wrap.pack(side='right',
                               padx=(0, self.px(9 if glass else 6)))
        self.add_btn = getattr(self.add_wrap, 'glyph', self.add_wrap)
        self.add_wrap.configure(
            cursor='hand2' if not self.plugins_ro else 'arrow')

        if not glass:
            # 极简档没有菜单浮层：标题栏一枚纯文字「设置」，点它同窗换页；
            # 进设置页后换成「← 设置」面包屑（设计稿 p-back / p-crumb）。
            if self.pane == 'set':
                back = tk.Label(bar, text='←', bg=c['bg'], fg=c['t2'],
                                font=self.f_body, padx=self.px(8),
                                cursor='hand2')
                back.pack(side='left', padx=(self.px(6), 0))
                back.bind('<ButtonRelease-1>', lambda e: self.toggle_pane())
                back.bind('<Enter>', lambda e: back.configure(bg=c['hover']),
                          add='+')
                back.bind('<Leave>', lambda e: back.configure(bg=c['bg']),
                          add='+')
                self._keyboard(back, self.toggle_pane)
                self.pane_btn = back
            else:
                txt = tk.Label(bar, text='设置', bg=c['bg'], fg=c['t2'],
                               font=self.f_body, padx=self.px(9), cursor='hand2')
                txt.pack(side='right', padx=(0, self.px(4)))
                txt.bind('<ButtonRelease-1>', lambda e: self.toggle_pane())
                txt.bind('<Enter>', lambda e: txt.configure(bg=c['hover']),
                         add='+')
                txt.bind('<Leave>', lambda e: txt.configure(bg=c['bg']), add='+')
                self._keyboard(txt, self.toggle_pane)
                self.pane_btn = txt

        # 左：字标（整块热区 = 菜单入口）
        self.brand_wrap = tk.Frame(bar, bg=c['bg'], padx=self.px(8),
                                   pady=self.px(4), cursor='hand2')
        self.brand_wrap.pack(side='left', padx=(self.px(20 if glass else 8), 0))
        brand = tk.Label(self.brand_wrap, text='WBOX', bg=c['bg'], fg=c['fg'],
                         font=self.f_wordmark, cursor='hand2')
        brand.pack(side='left')
        self.gear_wrap, self.gear_btn = self.brand_wrap, brand
        branders = [self.brand_wrap, brand]
        if glass:
            self.chev = Glyph(self.brand_wrap, self, 'chev', size=self.px(12),
                              ink='t3')
            self.chev.set_bg(c['bg'])
            self.chev.flip = bool(self.settings_win and
                                  self.settings_win.winfo_exists())
            self.chev.pack(side='left', padx=(self.px(5), 0))
            branders.append(self.chev)
        for w in branders:
            w.bind('<ButtonRelease-1>', lambda e: self.on_brand())
            w.bind('<Enter>', lambda e: self.hover(branders, True), add='+')
            w.bind('<Leave>', lambda e: self.hover(branders, False), add='+')
            w.bind('<Button-1>', self._drag_start, add='+')
            w.bind('<B1-Motion>', self._drag_move, add='+')
        self._keyboard(self.brand_wrap, self.on_brand)
        bar.bind('<Button-1>', self._drag_start, add='+')
        bar.bind('<B1-Motion>', self._drag_move, add='+')
        if not glass and self.pane == 'set':
            tk.Label(bar, text='设置', bg=c['bg'], fg=c['t3'],
                     font=self.f_body).pack(side='left', padx=(self.px(6), 0))
        self.tooltips.append(ToolTip(
            self, brand,
            ('菜单：守护状态、开机自启、日志、配置、外观与皮肤。' if glass
             else '设置：外观与皮肤、启动与守护、日志目录。')))

    def on_brand(self):
        if self.glass:
            self.toggle_settings()
        else:
            self.toggle_pane()

    def build_list_view(self, master):
        """插件列表。玻璃 = 圆角卡片；极简 = 方角直排。"""
        c = self.colors
        glass = self.glass
        r_card = self.px(self.skin.radius.get('card', 20)) if glass else 0
        self.row_h = self.px(self.skin.metrics.get('row', 52))
        self.r_row = self.px(self.skin.radius.get('row', 14)) if glass else 0
        body_px = self.px(8) if glass else 0
        card_pad = self.px(6) if glass else 0
        board_w = self.content_width() - 2 * body_px
        row_w = board_w - 2 * card_pad
        self.row_base = self.tok['inset'] if glass else c['bg']

        board = tk.Canvas(master, bg=c['bg'], highlightthickness=0, bd=0,
                          width=board_w, height=self.row_h)
        board.pack(fill='x', padx=body_px,
                   pady=(self.px(4) if glass else 0, self.px(8) if glass else 0))
        self.board = board

        y = card_pad
        for i, entry in enumerate(self.entries):
            row, row_h = self.make_row(board, entry, i, card_pad, row_w, y)
            y += self.row_h
            if not glass and self.inline_open == entry.id:
                y += self.make_inline(board, entry, card_pad, row_w, y)

        board_h = y + card_pad
        board.configure(height=board_h)
        self.board_full_h = board_h
        if glass:
            round_rect(board, 0.5, 0.5, board_w - 0.5, board_h - 0.5, r_card,
                       fill=self.row_base, outline=c['line'])
            board.tag_lower('all')

        if not self.entries:
            box = tk.Frame(master, bg=c['bg'])
            box.pack(fill='x', pady=self.px(14))
            tk.Label(box, text='还没有安装任何插件\n'
                               '主程序不附带插件，从插件库下载后用右上角 ＋ 安装',
                     bg=c['bg'], fg=c['t3'], font=self.f_body,
                     anchor='w', justify='left').pack(fill='x')
            btns = tk.Frame(box, bg=c['bg'])
            btns.pack(anchor='w', pady=(self.px(10), 0))
            self.capsule_button(btns, '安装插件', self.on_add_plugin).pack(
                side='left')
            self.capsule_button(btns, '插件库', self.on_plugin_repo,
                                primary=False).pack(side='left',
                                                    padx=(self.px(8), 0))
        self._bind_wheel(board, board)

    def capsule_button(self, master, text, command, primary=True):
        """整宽胶囊之外的单枚胶囊按钮（空态引导 / 皮肤安装入口 / 弹框共用）。"""
        c = self.colors
        h = self.px(34 if self.glass else 30)
        fill = c['fg'] if primary else c['fill']
        fg = c['bg'] if primary else c['fg']
        cv = tk.Canvas(master, bg=c['bg'], highlightthickness=0, bd=0,
                       height=h, cursor='hand2')
        lbl = tk.Label(cv, text=text, bg=fill, fg=fg, font=self.f_body)
        cv.update_idletasks()
        w = lbl.winfo_reqwidth() + self.px(36)
        cv.configure(width=w)
        r = (h // 2) if self.glass else 0
        round_rect(cv, 0.5, 0.5, w - 0.5, h - 0.5, r, fill=fill,
                   outline='', tags='cap')
        cv.create_window(w // 2, h // 2, window=lbl)
        for wdg in (cv, lbl):
            wdg.bind('<ButtonRelease-1>', lambda e: command())
        # 焦点环画在实心底上时用反色，否则 fg 环叠 fg 底看不见
        self._keyboard(cv, command, ring_color=(c['bg'] if primary else c['fg']))
        return cv

    # ---- 键盘可达与焦点环（文案规范 §4） ----
    def _ring(self, cv, on, color=None):
        """Canvas 焦点环：1px、距边 2px。Tk 画不出控件外偏移，偏移向内实现；
        hover 只删自己的底色 tag，不碰 focusring，所以环不会被悬停盖掉。"""
        try:
            cv.delete('focusring')
            if on:
                w, h = int(cv.cget('width')), int(cv.cget('height'))
                ins = self.px(2)
                cv.create_rectangle(
                    ins + 0.5, ins + 0.5, w - ins - 0.5, h - ins - 0.5,
                    outline=color or self.colors['fg'], width=1,
                    tags='focusring')
        except Exception:
            pass

    def _keyboard(self, widget, command, ring_color=None):
        """Enter / Space 激活 + 焦点可见。Canvas 画环，Frame/Label 用 1px 外框。"""
        try:
            widget.configure(takefocus=1)
        except Exception:
            pass
        widget.bind('<Return>', lambda e: command(), add='+')
        widget.bind('<space>', lambda e: command(), add='+')
        if isinstance(widget, tk.Canvas):
            widget.bind('<FocusIn>',
                        lambda e: self._ring(widget, True, ring_color), add='+')
            widget.bind('<FocusOut>', lambda e: self._ring(widget, False), add='+')
        else:
            def fin(e):
                try:
                    widget.configure(highlightthickness=1,
                                     highlightbackground=self.colors['fg'],
                                     highlightcolor=self.colors['fg'])
                except Exception:
                    pass

            def fout(e):
                try:
                    widget.configure(highlightthickness=0)
                except Exception:
                    pass
            widget.bind('<FocusIn>', fin, add='+')
            widget.bind('<FocusOut>', fout, add='+')

    def _set_chain(self, win, widgets):
        """显式 Tab 链：Tk 默认遍历序是创建序，排不出设计稿要求的
        「＋ → 字标 → 各行开关 → 各行行尾」，所以自己接管 Tab / Shift+Tab。"""
        win._focus_chain = [w for w in widgets if w is not None]
        win.bind('<Tab>', lambda e: self._tab_step(win, 1), add='+')
        # Windows Tk 没有 ISO_Left_Tab 这个 keysym，Shift+Tab 走 Shift-Tab
        try:
            win.bind('<ISO_Left_Tab>', lambda e: self._tab_step(win, -1), add='+')
        except tk.TclError:
            win.bind('<Shift-Tab>', lambda e: self._tab_step(win, -1), add='+')

    def _tab_step(self, win, step):
        chain = [w for w in getattr(win, '_focus_chain', [])
                 if w.winfo_exists()]
        if not chain:
            return 'break'
        cur = win.focus_get()
        i = chain.index(cur) if cur in chain else (-1 if step > 0 else 0)
        chain[(i + step) % len(chain)].focus_set()
        return 'break'

    def _focus_brand(self):
        """浮层收起后焦点还给字标（菜单入口），不留「焦点掉进虚空」。"""
        w = getattr(self, 'brand_wrap', None)
        if w is not None and w.winfo_exists():
            try:
                w.focus_set()
            except Exception:
                pass

    def dialog(self, title, body, buttons, extra=None):
        """自绘模态弹框：动词按钮、焦点默认落在最后一个（安全）按钮、Esc = 安全按钮。

        buttons = [(文字, 返回值), ...]：第一枚是主动作（最右、实心），
        其余是安全动作。Enter 只触发当前焦点按钮，绝不默认触发危险动作。
        messagebox 的「确定 / 是」改不成动词，所以确认框一律走这里。
        """
        c = self.colors
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg=c['line'])
        try:
            win.attributes('-topmost', True)
        except Exception:
            pass
        if self.probe:
            win.attributes('-alpha', 0.0)
        inner = tk.Frame(win, bg=c['bg'], padx=self.px(20), pady=self.px(16))
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        tk.Label(inner, text=title, bg=c['bg'], fg=c['fg'], font=self.f_row,
                 anchor='w').pack(fill='x')
        tk.Label(inner, text=body, bg=c['bg'], fg=c['t2'], font=self.f_body,
                 anchor='w', justify='left',
                 wraplength=self.px(340)).pack(fill='x', pady=(self.px(8), 0))
        if extra:
            tk.Label(inner, text=extra, bg=c['bg'], fg=c['t3'],
                     font=self.f_small, anchor='w', justify='left',
                     wraplength=self.px(340)).pack(fill='x', pady=(self.px(8), 0))
        foot = tk.Frame(inner, bg=c['bg'])
        foot.pack(fill='x', pady=(self.px(16), 0))
        result = [buttons[-1][1]]

        def pick(val):
            result[0] = val
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        picks = []
        for i, (text, val) in enumerate(buttons):
            b = self.capsule_button(foot, text, lambda v=val: pick(v),
                                    primary=(i == 0))
            b.pack(side='right', padx=(self.px(8), 0))
            picks.append(b)
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        par = self.parent()
        x = par.winfo_rootx() + max(0, (par.winfo_width() - w) // 2)
        y = par.winfo_rooty() + max(0, (par.winfo_height() - h) // 2)
        win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        win.bind('<Escape>', lambda e: pick(buttons[-1][1]))
        self._set_chain(win, picks)
        self._dialog_win = win
        self._dialog_picks = picks
        win.grab_set()
        picks[-1].focus_set()
        win.wait_window()
        self._dialog_win = None
        self._dialog_picks = []
        return result[0]

    def confirm(self, title, body, ok_text, extra=None):
        """L2 危险确认：标题带对象、正文讲后果与可逆、按钮用具体动词。"""
        return bool(self.dialog(title, body, [(ok_text, True), ('取消', False)],
                                extra=extra))

    def _error(self, title, body, can_retry=False):
        """错误弹框：人话 + 下一步，不给裸「确定」。返回用户是否选了「重试」。"""
        buttons = ([('重试', True), ('关闭', False)] if can_retry
                   else [('关闭', False)])
        return bool(self.dialog(title, body, buttons)) and can_retry

    def make_row(self, board, entry, i, card_pad, row_w, y):
        """一行：图标 / 名称 / 开关 / 行尾入口。行自己是一块 Canvas，
        圆角悬停底板画在它上面，子控件用 create_window 摆位。"""
        c = self.colors
        glass = self.glass
        rh = self.row_h
        row = tk.Canvas(board, bg=self.row_base, highlightthickness=0, bd=0,
                        width=row_w, height=rh)
        board.create_window(card_pad, y, window=row, anchor='nw')
        row.w = row_w

        if i and glass:
            # 分隔线不画到图标下面：左起 50、右到距边 12（设计稿 .row + .row::before）
            row.create_line(self.px(50), 0.5, row_w - self.px(12), 0.5,
                            fill=c['line'])
        elif i:
            row.create_line(0, 0.5, row_w, 0.5, fill=c['line'])

        icon_px = self.px(self.skin.metrics.get('icon', 22))
        pad_l = self.px(14)
        gap = self.px(14 if glass else 10)
        icon = Glyph(row, self, entry.icon, size=icon_px)
        icon.set_bg(self.row_base)
        row.create_window(pad_l, rh // 2, window=icon, anchor='w')

        sw_w = self.px(46 if glass else 36)
        tail = self.px(30 if glass else 22)
        tail_gap = self.px(4)
        name_x = pad_l + icon_px + gap
        name_w = max(self.px(40), row_w - name_x - tail - tail_gap
                     - sw_w - self.px(24))
        name = tk.Label(row, text=entry.name, bg=self.row_base, fg=c['fg'],
                        font=self.f_row, anchor='w')
        row.create_window(name_x, rh // 2, window=name, anchor='w',
                          width=name_w, height=rh)
        if entry.tip:
            self.tooltips.append(ToolTip(self, name, entry.tip))

        sw = Switch(row, self, value=self.vars[entry.id].get(),
                    command=self.on_toggle)
        sw.set_bg(self.row_base)
        row.create_window(row_w - self.px(10) - tail - tail_gap,
                          rh // 2, window=sw, anchor='e')
        if self.readonly:
            sw.set_enabled(False)
        self.switches[entry.id] = sw

        if glass:
            gw = tk.Canvas(row, width=tail, height=tail, bg=self.row_base,
                           highlightthickness=0, bd=0, cursor='hand2')
            gear = Glyph(gw, self, 'cfg', size=self.px(17))
            gear.set_bg(self.row_base)
            gw.create_window(tail // 2, tail // 2, window=gear)
            row.create_window(row_w - self.px(10), rh // 2, window=gw, anchor='e')
            self.plugin_gears[entry.id] = gw
            self.tooltips.append(ToolTip(
                self, gw, '设置「%s」：调整这个插件的运行方式，\n卸载也在这里。'
                % entry.name))
            for w in (gw, gear):
                w.bind('<ButtonRelease-1>',
                       lambda e, ent=entry: self.on_plugin_settings(ent))
            self._keyboard(gw, lambda ent=entry: self.on_plugin_settings(ent))
        else:
            more = Glyph(row, self, 'chev_r', size=self.px(14))
            more.set_bg(self.row_base)
            row.create_window(row_w - self.px(12), rh // 2, window=more,
                              anchor='e', )
            self.plugin_gears[entry.id] = row
            row.configure(cursor='hand2')
            row.bind('<ButtonRelease-1>',
                     lambda e, ent=entry: self.toggle_inline(ent))
            self._keyboard(row, lambda ent=entry: self.toggle_inline(ent))
            # v2.10.0 修「点不开」：行面大部分被子控件盖住（Tk 事件不穿透
            # create_window），只绑 row 时点名称/图标/右箭头全无响应。
            # 开关除外——它有自己的 toggle，不能连带展开。
            for w in (icon, name, more):
                w.configure(cursor='hand2')
                w.bind('<ButtonRelease-1>',
                       lambda e, ent=entry: self.toggle_inline(ent))

        kids = [icon, name, sw]
        self.bind_row_hover(row, kids, (gear if glass else None))
        return row, rh

    def bind_row_hover(self, row, kids, tail):
        """整行悬停：画一层圆角底板，子控件底色同步跟上（Tk 没有真透明）。"""
        c = self.colors
        rh = row.winfo_reqheight() or self.row_h
        w = getattr(row, 'w', 0) or row.winfo_reqwidth()

        def paint(on):
            row.delete('hl')
            col = c['hover'] if on else self.row_base
            if on:
                if self.r_row:
                    round_rect(row, 1, 1, w - 1, rh - 1, self.r_row,
                               fill=col, outline='', tags='hl')
                else:
                    row.create_rectangle(1, 1, w - 1, rh - 1, fill=col,
                                         outline='', tags='hl')
                row.tag_lower('hl')
            for k in kids:
                if k is not None:
                    try:
                        k.set_bg(col)
                    except AttributeError:
                        k.configure(bg=col)
            if tail is not None:
                tail.configure(bg=col)
                try:
                    for sub in tail.winfo_children():
                        sub.set_bg(col)
                except Exception:
                    pass

        targets = [row] + [k for k in kids if k is not None]
        for t in targets:
            t.bind('<Enter>', lambda e: paint(True), add='+')
            t.bind('<Leave>', lambda e: paint(False), add='+')

    # ---- 极简档：行内展开 + 同窗换页 ----
    INLINE_MAX = 3          # 行内先摊前 3 项，「全部 N 项设置…」再摊全量

    def toggle_inline(self, entry):
        opening = self.inline_open != entry.id
        self.inline_open = entry.id if opening else None
        if not opening or self.inline_full != entry.id:
            self.inline_full = None
        self.close_plugin_settings()
        self.rebuild()

    def toggle_inline_full(self, entry):
        self.inline_full = None if self.inline_full == entry.id else entry.id
        self.rebuild()

    def make_inline(self, board, entry, card_pad, row_w, y):
        """行内展开：设置项直接摊在这行底下，不新建 Toplevel。

        极简档的性能账在这里：少一个窗口句柄、少一套 z 序、展开收合零动画，
        代价是列表会跳动（设计稿明确接受）。
        """
        bg = self.tok['fill_2'] if self.glass else self.tok['fill']
        c = self.colors
        box = tk.Frame(board, bg=bg)
        # 展开块缩进到名称列（图标 14 + 图标 22 + 间距 14 ≈ 40），
        # 与设计稿 .p-expand padding-left:40px 对齐，视觉上归属上面那一行。
        indent = tk.Frame(box, bg=bg)
        indent.pack(fill='both', expand=True, padx=(self.px(26), 0))
        cfg = effective_cfg(entries=self.entries_all)
        specs = list(getattr(entry, 'settings', None) or [])
        # 展开块的底色不是窗口底：让所有子控件工厂读到的 bg 暂时跟着换，
        # 否则 int/choice/path 这几类控件会画出白色方块。
        old_bg = self.colors['bg']
        self.colors['bg'] = bg
        try:
            full = self.inline_full == entry.id
            show = specs if full else specs[:self.INLINE_MAX]
            for spec in show:
                self.build_setting_row(indent, entry, spec, cfg, bg=bg)
        finally:
            self.colors['bg'] = old_bg
        if len(specs) > self.INLINE_MAX:
            txt = ('收起额外设置' if full else '全部 %d 项设置…' % len(specs))
            ml = tk.Label(indent, text=txt, bg=bg, fg=c['t2'],
                          font=self.f_small, anchor='w', cursor='hand2',
                          padx=self.px(2), pady=self.px(2))
            ml.pack(fill='x', padx=self.px(14), pady=(0, self.px(4)))
            ml.bind('<Button-1>',
                    lambda e, ent=entry: self.toggle_inline_full(ent))
        if not specs:
            tk.Label(indent, text='这个插件没有可调项', bg=bg, fg=c['t3'],
                     font=self.f_small, anchor='w').pack(
                fill='x', padx=self.px(14), pady=self.px(8))
        # 来源一行：装完之后也能看见"这插件是谁给的、要碰什么"。
        # 未声明发布者时明说来源不明——插件是完全信任的代码，这句话比版本号重要。
        pub = (getattr(entry, 'publisher', '') or '').strip()
        perms = list(getattr(entry, 'permissions', None) or ())
        src_parts = ['发布者：' + (pub or '未声明（来源不明）')]
        if perms:
            labels = [plugin_package.PERMISSION_LABELS.get(p, p + '（未登记）')
                      for p in perms[:4]]
            src_parts.append('权限：' + '、'.join(labels))
        src = tk.Label(indent, text=' · '.join(src_parts), bg=bg, fg=c['t3'],
                       font=self.f_small, anchor='w', justify='left',
                       wraplength=max(self.px(220), row_w - self.px(80)))
        src.pack(fill='x', padx=self.px(14), pady=(0, self.px(2)))
        rm = tk.Label(indent, text='卸载此插件', bg=bg, fg=c['danger'],
                      font=self.f_body, anchor='w', cursor='hand2',
                      padx=self.px(2), pady=self.px(4))
        rm.pack(fill='x', padx=self.px(14), pady=(self.px(2), self.px(8)))
        rm.bind('<ButtonRelease-1>',
                lambda e, ent=entry: self.on_delete_plugin(ent))
        self._keyboard(rm, lambda ent=entry: self.on_delete_plugin(ent))
        box.update_idletasks()
        hh = max(self.px(30), box.winfo_reqheight())
        board.create_window(card_pad, y, window=box, anchor='nw',
                            width=row_w, height=hh)
        return hh

    def toggle_pane(self):
        self.pane = 'set' if self.pane == 'list' else 'list'
        self.inline_open = None
        self.rebuild()

    def build_set_view(self, master):
        """极简档的设置页：同一个窗口换内容，不叠浮层。

        尺寸照 design/wbox-skins.html 的 .p-set：分组头 24 高、行固定 36 高、
        行内边距 0 12 0 14、值用等宽小字。行高必须写死而不是靠 pady 撑——
        靠 pady 撑出来的行只有 27 左右，整页会显得比列表页挤。
        v2.9.0：套 Canvas 视口，矮屏溢出时滚轮可达（设计稿「不建滚动容器」
        指不叠独立滚动窗，视口在不超屏时与整块 Frame 无异）。
        """
        c = self.colors
        bg = c['bg']
        outer = tk.Frame(master, bg=bg)
        outer.pack(fill='both', expand=True)
        # 设置页也套一层 Canvas 视口：不超屏时和整块 Frame 无异（窗口按内容长），
        # 超屏时滚轮可达，避免「窗口高度按内容长」在矮屏上把关于组截掉。
        cv = tk.Canvas(outer, bg=bg, highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        wrap = tk.Frame(cv, bg=bg)
        self.set_canvas = cv
        self.set_inner = wrap
        self._set_window_id = cv.create_window(0, 0, window=wrap, anchor='nw')
        pad_l = self.px(14)
        pad_r = self.px(12)
        self._set_focusables = []
        cfg = effective_cfg(entries=self.entries_all)
        try:
            mono = tkfont.Font(root=self.root, family='Consolas',
                               size=-self.px(11))
        except Exception:
            mono = self.f_small

        def group(title):
            box = tk.Frame(wrap, bg=bg, height=self.px(24))
            box.pack(fill='x')
            box.pack_propagate(False)
            tk.Label(box, text=title, bg=bg, fg=c['t3'], font=self.f_small,
                     anchor='sw', padx=pad_l, pady=self.px(3)).pack(
                fill='both', expand=True)
            # 分组头下面**不画**分隔线：设计稿里 .p-grp 没有 border，
            # 每一行自带 border-bottom，多画这一条会变成"头-线-行-线"的杂音。

        def row_box():
            row = tk.Frame(wrap, bg=bg, height=self.px(36))
            row.pack(fill='x')
            row.pack_propagate(False)
            return row

        def line(text, value='', command=None):
            row = row_box()
            lb = tk.Label(row, text=text, bg=bg, fg=c['fg'], font=self.f_body,
                          anchor='w', padx=pad_l)
            lb.pack(side='left', fill='y')
            val = tk.Label(row, text=value, bg=bg, fg=c['t3'],
                           font=mono, anchor='e', padx=pad_r)
            val.pack(side='right', fill='y')
            if command:
                for w in (row, lb, val):
                    w.configure(cursor='hand2')
                    w.bind('<ButtonRelease-1>', lambda e: command())
                    w.bind('<Enter>', lambda e: [x.configure(bg=c['hover'])
                                                 for x in (row, lb, val)], add='+')
                    w.bind('<Leave>', lambda e: [x.configure(bg=bg)
                                                 for x in (row, lb, val)], add='+')
                self._keyboard(row, command)
                self._set_focusables.append(row)
            tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
            return row

        group('外观')
        for item in skins.catalog(BASE):
            if item['reason'] == 'api':
                line('○ ' + item['name'], '需要更新')
                continue
            sk_ = item['skin']
            cur = sk_.id == self.skin.id
            line(('● ' if cur else '○ ') + sk_.name,
                 '当前' if cur else sk_.perf_label,
                 lambda i=sk_.id: self.set_skin(i))
        line('从文件安装皮肤…', '', self.on_install_skin)

        group('启动与守护')
        boot_line = row_box()
        tk.Label(boot_line, text='开机自动启动', bg=bg, fg=c['fg'],
                 font=self.f_body, anchor='w', padx=pad_l).pack(
            side='left', fill='y')
        bsw = Switch(boot_line, self, value=self.boot_var.get(),
                     command=self.on_boot_toggle)
        bsw.set_bg(bg)
        bsw.pack(side='right', padx=pad_r)
        self._set_focusables.append(bsw)
        tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
        for key, text, var, cmd in (
                ('tray', '启动后收进托盘', self.tray_var, self.on_tray_toggle),
                ('notify', '游戏结束提示', self.notify_var,
                 self.on_notify_toggle)):
            trow = row_box()
            tk.Label(trow, text=text, bg=bg, fg=c['fg'], font=self.f_body,
                     anchor='w', padx=pad_l).pack(side='left', fill='y')
            tsw = Switch(trow, self, value=bool(var.get()), command=cmd)
            tsw.set_bg(bg)
            tsw.pack(side='right', padx=pad_r)
            self.switches[key] = tsw
            self._set_focusables.append(tsw)
            tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
        line('守护状态', short_guard_state())
        # v2.11.3：把"守护活着"拆成两件可核对的事——进程事件通道是否在工作、
        # 插件是否都加载起来了。两者都不影响守护进程存在，但都影响功能是否生效。
        ev_text, pl_text, ev_note = guard_diagnostics()
        line('进程事件', ev_text)
        line('插件健康', pl_text)
        if ev_note:
            tk.Label(wrap, text=ev_note, bg=bg, fg=c['t3'], font=self.f_small,
                     anchor='w', justify='left',
                     wraplength=self.px(250)).pack(
                fill='x', padx=pad_l, pady=(0, self.px(4)))

        group('内核')
        line('游戏检测间隔', '%d 毫秒' % int(cfg.get('poll_idle_ms') or 2000))
        line('日志保留', '30 天')

        group('关于')
        line('WBOX', VERSION)
        line('打开日志目录', '', self.on_log)
        line('编辑配置文件', '', self.on_cfg)
        # 窗口按内容长高，最后一行贴着窗口边会看起来像被裁掉：留一条呼吸位
        tk.Frame(wrap, bg=bg, height=self.px(10)).pack(fill='x')

    # ---- 二级设置：浏览器式弹出菜单 ----
    def toggle_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.close_settings()
        else:
            self.open_settings()

    def _menu_outside(self, ev):
        """点主窗口空白处收起菜单；字标自己交给 ButtonRelease-1 的 toggle。"""
        if getattr(self, 'skin_win', None) and self.skin_win.winfo_exists():
            if ev.widget not in (self.brand_wrap, self.gear_btn):
                self.close_skin_settings()
        if not (self.settings_win and self.settings_win.winfo_exists()):
            return
        if ev.widget is self.gear_wrap or ev.widget is self.gear_btn:
            return
        self.close_settings()

    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.place_menu()
            return
        self.close_plugin_settings()
        c = self.colors
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)        # 无系统标题栏，才像浏览器菜单
        win.configure(bg=c['line'])
        win.resizable(False, False)
        # 不能用 transient：Windows 会把无标题栏浮层压到 owner 窗口后面，
        # 菜单会被主窗口盖住。改成纯 topmost，靠全局左键判定负责收起。
        try:
            win.wm_attributes('-topmost', True)
        except Exception:
            pass
        if self.probe:                       # 布局自检时不要闪一个窗口到桌面上
            win.attributes('-alpha', 0.0)
        c = self.popup_colors('menu_base')
        outer = tk.Frame(win, bg=c['line'], padx=1, pady=1)
        outer.pack(fill='both', expand=True)
        cv = tk.Canvas(outer, bg=c['bg'], highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        inner = tk.Frame(cv, bg=c['bg'])
        self._menu_window_id = cv.create_window((0, 0), window=inner, anchor='nw')
        self.settings_win = win
        self.settings_outer = outer
        self.settings_canvas = cv
        self.settings_inner = inner
        # 每次打开菜单都重读一次守护写的运行期状态：面板可能已经开了几个小时，
        # 期间某个插件被降级/自愈了，菜单里的提示要跟着变。
        self.runtime_state = read_plugin_states()
        self.build_settings(inner)
        self.place_menu()
        win.bind('<Escape>', lambda e: self.close_settings())
        self.refresh_status()
        self._menu_was_pressed = False
        win.after(120, self._menu_watch)
        if getattr(self, 'chev', None) is not None and self.chev.winfo_exists():
            self.chev.flip = True
            self.chev.draw()
        self._pop_in(win, slide=self.px(6))
        # 菜单打开焦点移入首项（文案规范 §4）；Esc 关闭后焦点还给字标
        chain = getattr(win, '_focus_chain', [])
        if chain:
            try:
                chain[0].focus_set()
            except Exception:
                pass

    def _menu_watch(self):
        """全局左键：按在菜单 / 齿轮之外就收起（浏览器菜单的点击外部关闭）。

        Tk 绑定只能收到本进程窗口的点击，这里用 GetAsyncKeyState +
        GetCursorPos 读全局状态，跨进程点击其它程序同样生效；只在"新一次按下"
        的瞬间判断位置，不干扰菜单内的开关与拖动。
        """
        win = self.settings_win
        if not (win and win.winfo_exists()):
            return
        try:
            u = ctypes.windll.user32
            u.GetAsyncKeyState.argtypes = [ctypes.c_int]
            u.GetAsyncKeyState.restype = ctypes.c_short
            pressed = bool(int(u.GetAsyncKeyState(0x01)) & 0x8000)
            if pressed and not self._menu_was_pressed and not self.probe:
                u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
                pt = POINT()
                u.GetCursorPos(ctypes.byref(pt))
                x, y = pt.x, pt.y
                pad = self.px(4)
                mx, my = win.winfo_rootx(), win.winfo_rooty()
                in_menu = (mx - pad <= x <= mx + win.winfo_width() + pad and
                           my - pad <= y <= my + win.winfo_height() + pad)
                g = self.gear_wrap
                in_gear = False
                if g and g.winfo_exists():
                    gx, gy = g.winfo_rootx(), g.winfo_rooty()
                    in_gear = (gx <= x <= gx + g.winfo_width() and
                               gy <= y <= gy + g.winfo_height())
                if not in_menu and not in_gear:
                    self.close_settings()
                    return
            self._menu_was_pressed = pressed
        except Exception:
            pass
        try:
            win.after(120, self._menu_watch)
        except Exception:
            pass

    def place_menu(self):
        """贴齿轮右下方展开；下方不够改向上，仍不够就限高并启用滚轮。"""
        win = self.settings_win
        inner, cv = self.settings_inner, self.settings_canvas
        if not (win and inner and cv and win.winfo_exists()):
            return
        # 重排会打断入场链：先把透明度落回 1，别留半透明菜单
        self.cancel_tween(('pop', id(win)))
        if not self.probe:
            try:
                win.attributes('-alpha', 1.0)
            except Exception:
                pass
        win.update_idletasks()
        lx, ly, sw, sh = work_area()
        w = min(self.px(310), max(self.px(260), sw - self.px(20)))
        inner_w = w - 2
        cv.itemconfigure(self._menu_window_id, width=inner_w)
        win.update_idletasks()
        need_h = inner.winfo_reqheight() + 2
        # 设计稿 max-height:560：超了就让菜单自己滚，别让一屏菜单把桌面顶满
        need_h = min(need_h, self.px(560))
        gear = (self.gear_wrap if self.gear_wrap and
                self.gear_wrap.winfo_exists() else self.root)
        gx, gy = gear.winfo_rootx(), gear.winfo_rooty()
        gw, gh = gear.winfo_width(), gear.winfo_height()
        # 左沿对齐字标热区（设计稿 v3.4：「浮层从字标正下方拉出，左沿对齐字标」）。
        # 之前是右沿对齐，字标一挪菜单就跑到窗口外面去。
        x = max(lx + self.px(8), min(gx, lx + sw - w - self.px(8)))
        below = gy + gh + self.px(6)
        avail_below = ly + sh - below - self.px(8)
        avail_above = gy - ly - self.px(8)
        if need_h <= avail_below:
            y, h = below, need_h
        elif need_h <= avail_above:
            y, h = gy - need_h - self.px(6), need_h
        elif need_h <= (ly + sh - self.px(8)) - (ly + self.px(8)):
            # 上下都放不下一整块，但屏幕本身放得下：把菜单上抬到刚好完整显示，
            # 允许它盖住主窗口一部分。截断比遮挡更糟——截断会让最后几项
            #（保存设置 / 关闭菜单）直接消失，用户以为菜单没有这些功能。
            h = need_h
            y = max(ly + self.px(8), min(below, ly + sh - self.px(8) - need_h))
        elif avail_below >= avail_above:
            y, h = below, max(self.px(160), avail_below)
        else:
            h = max(self.px(160), avail_above)
            y = ly + self.px(8)
        h = min(h, max(self.px(160), sh - self.px(16)))
        y = max(ly + self.px(8), min(y, ly + sh - h - self.px(8)))
        win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        try:
            win.lift()
            win.wm_attributes('-topmost', True)
        except Exception:
            pass
        cv.configure(scrollregion=(0, 0, inner_w, inner.winfo_reqheight()))
        self._bind_wheel(cv, inner)
        # 与插件浮层同一口径：内容高度可能在 place 之后才 settling（字体度量 /
        # 换行），scrollregion 不跟随就会差几像素滚不到底。
        inner.bind('<Configure>', lambda e: cv.configure(
            scrollregion=(0, 0, inner_w, inner.winfo_reqheight())))
        self._menu_fade_update(win, cv, w, h)

    def _menu_fade_update(self, win, cv, w, h):
        """菜单底部 34px 渐隐（设计稿 .m-fade）：内容还能滚时才出现。

        Tk 控件没有逐像素 alpha，渐隐用 stipple 抖动带模拟四档覆盖率；
        载体 Canvas 的底色设成 magic 色并给菜单窗开 -transparentcolor，
        抖动的「洞」才真的透下去而不是露出一块 magic 色方块。
        """
        old = getattr(self, '_menu_fade', None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
            self._menu_fade = None
        inner = self.settings_inner
        if self.probe or inner is None or not inner.winfo_exists():
            return
        if inner.winfo_reqheight() <= h - 2:
            return
        magic = '#010203'
        try:
            win.wm_attributes('-transparentcolor', magic)
        except Exception:
            return
        fh = self.px(34)
        f = tk.Canvas(win, bg=magic, highlightthickness=0, bd=0, height=fh)
        f.place(x=1, y=h - fh - 1, width=w - 2, height=fh)
        bg = cv.cget('bg')
        bh = fh / 5.0
        for i, stip in enumerate(('gray12', 'gray25', 'gray50', 'gray75')):
            f.create_rectangle(0, i * bh, w, (i + 1) * bh, fill=bg,
                               outline='', stipple=stip)
        f.create_rectangle(0, 4 * bh, w, fh, fill=bg, outline='')
        self._bind_wheel(cv, f)
        self._menu_fade = f

    def _bind_wheel(self, cv, inner):
        def scroll(ev):
            step = int(-ev.delta / 120) or (-1 if ev.delta > 0 else 1)
            cv.yview_scroll(step, 'units')
            return 'break'
        for w in (cv, inner) + tuple(walk(inner)):
            try:
                w.bind('<MouseWheel>', scroll)
            except Exception:
                pass

    def close_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            try:
                self.settings_win.destroy()
            except Exception:
                pass
        self.settings_win = None
        self.settings_outer = None
        self.settings_canvas = None
        self.settings_inner = None
        self._menu_window_id = None
        self._menu_fade = None
        self.status_label = None
        self.theme_buttons = {}
        self.switches.pop('boot', None)
        self.switches.pop('tray', None)
        self.switches.pop('notify', None)
        if getattr(self, 'chev', None) is not None:
            try:
                self.chev.flip = False
                self.chev.draw()
            except Exception:
                pass
        self._focus_brand()

    def menu_row(self, master, text, icon, hint='', command=None,
                 switch=False, mark=False, close_after=True, disabled=False,
                 switch_key=None, switch_var=None, switch_cmd=None):
        """浏览器菜单行：图标 + 名称 + 右侧提示/开关/勾选，整行悬停与点击。

        disabled=True 用于只读模式：文字与图标降到最弱灰阶、不绑点击（不改变行高
        与排布，所以布局与文案与正常模式完全一致）。
        """
        c = self.colors
        row = tk.Frame(master, bg=c['bg'])
        row.pack(fill='x', pady=(self.px(1), 0))
        row._menu_cmd = None
        row._menu_switch = None
        if getattr(self, '_menu_rows', None) is not None:
            self._menu_rows.append(row)
        g = Glyph(row, self, icon, size=self.px(16))
        g.pack(side='left', padx=(self.px(14), self.px(10)))
        lab = tk.Label(row, text=text, bg=c['bg'],
                       fg=(c['t3'] if disabled else c['fg']),
                       font=self.f_body, anchor='w')
        lab.pack(side='left', fill='x', expand=True, ipady=self.px(8))
        widgets = [g, lab]
        if disabled:
            g.set_dim(True)
        row.hint_label = None
        right = None
        if switch:
            var = self.boot_var if switch_var is None else switch_var
            cmd = self.on_boot_toggle if switch_cmd is None else switch_cmd
            right = Switch(row, self, value=bool(var.get()), command=cmd)
            if disabled:
                right.set_enabled(False)
            self.switches[switch_key or 'boot'] = right
            row._menu_switch = right
        elif mark:
            right = Glyph(row, self, 'check', size=self.px(14))
            if disabled:
                right.set_dim(True)
        if right is not None:
            right.pack(side='right', padx=(self.px(8), self.px(12)))
            widgets.append(right)
        elif hint:
            h = tk.Label(row, text=hint, bg=c['bg'], fg=c['t3'],
                         font=self.f_small, anchor='e')
            h.pack(side='right', padx=(self.px(8), self.px(14)))
            widgets.append(h)
            row.hint_label = h
        if disabled:
            return row
        self.bind_hover(row, widgets)
        if command:
            def run(ev=None):
                if close_after:
                    self.close_settings()
                command()
            for w in (row,) + tuple(widgets):
                w.bind('<Button-1>', run)
            row._menu_cmd = run
            self._keyboard(row, run)
        return row

    def menu_sep(self, master):
        tk.Frame(master, height=1, bg=self.colors['line']).pack(
            fill='x', pady=(self.px(7), self.px(7)))

    def plugin_switch_live(self, entry, cfg=None):
        """菜单提示用的「开关当前值」。

        优先读面板上那个开关的实时值：用户拨了开关但还没点保存时，菜单里的
        「已启用 / 已禁用」必须跟着变（磁盘上的 cfg 只有保存后才更新）。
        没有开关行的插件（home=False / 加载失败）回落读配置计算。
        """
        var = self.vars.get(entry.id) if getattr(self, 'vars', None) else None
        if var is not None:
            try:
                return bool(var.get())
            except Exception:
                pass
        if cfg is None:
            cfg = effective_cfg(entries=self.entries_all)
        return plugin_switch_state(entry, cfg)

    def build_settings(self, inner):
        """浏览器式菜单：每项一行 / 依次排列，分组只用 1px 分隔线。"""
        c = self.colors
        ro = self.readonly
        self._menu_rows = []
        tk.Frame(inner, height=self.px(6), bg=c['bg']).pack(fill='x')

        def action(text, icon, command, hint='', disabled=False):
            return self.menu_row(inner, text, icon, hint=hint, command=command,
                                 disabled=disabled)

        # 守护。只读模式下"停止守护"要写 stop.flag、"重扫游戏库"要写 flag 文件，
        # 都落在不可写的安装目录里，所以与开机自启一起置灰；
        # "启动守护"只是触发计划任务、不写配置，保持可用。
        st = self.menu_row(inner, '守护状态', 'dot',
                           hint=('需要管理员权限' if ro
                                 else short_guard_state(self.probe)))
        self.status_label = st.hint_label
        if ro:
            # 置灰必配解释（文案规范 §4）：为什么灰 + 怎么办，两行里的第二行
            tk.Label(inner, text='右键程序图标，以管理员身份重新打开后即可修改',
                     bg=c['bg'], fg=c['t3'], font=self.f_small,
                     anchor='w', justify='left').pack(
                         fill='x', padx=self.px(14))
        action('启动守护', 'play', self.on_start)
        action('停止守护', 'stop', self.on_stop, disabled=ro)
        self.menu_row(inner, '开机自启', 'boot', switch=True, disabled=ro)
        self.menu_row(inner, '启动后收进托盘', 'tray', switch=True,
                      switch_key='tray', switch_var=self.tray_var,
                      switch_cmd=self.on_tray_toggle, disabled=ro)
        self.menu_row(inner, '游戏结束提示', 'bell', switch=True,
                      switch_key='notify', switch_var=self.notify_var,
                      switch_cmd=self.on_notify_toggle, disabled=ro)

        # 维护
        self.menu_sep(inner)
        action('重新扫描游戏', 'rescan', self.on_rescan, disabled=ro)
        action('查看日志', 'log', self.on_log)
        action('编辑配置文件', 'cfg', self.on_cfg)

        # 插件：标题 + 每个插件一行 + 打开插件目录，全部由注册表生成。
        # 加载失败的插件也留一行（右侧写「加载失败」），用户才知道有东西没跑起来；
        # 但主页面不显示它们（见 build_main）。
        self.menu_sep(inner)
        total = len(self.entries_all)
        self.menu_row(inner, '已安装插件', 'dot',
                      hint=('共 %d 个' % total) if total else '还没有安装插件')
        hint_cfg = effective_cfg(entries=self.entries_all)
        for entry in self.entries_all:
            hint = plugin_hint(entry, self.runtime_state.get(entry.id),
                               self.plugin_switch_live(entry, hint_cfg))
            r = self.menu_row(inner, entry.name, entry.icon, hint=hint,
                              command=lambda d=entry.dir: self.on_plugin_dir(d))
            # 异常态给自救 Tooltip，不弹框（文案规范 §2.1）
            tip = None
            if hint.startswith('未能加载'):
                tip = '这个插件没能加载。可以在齿轮菜单里查看日志了解原因。'
            elif hint.startswith('运行异常'):
                tip = '这个插件最近运行不太稳定。可以关掉再打开，或查看日志。'
            if tip:
                labs = [w for w in r.winfo_children() if isinstance(w, tk.Label)]
                if labs:
                    self.tooltips.append(ToolTip(self, labs[0], tip))
        action('打开插件目录', 'folder', self.on_plugins_dir)
        # v2.11.3：本体不带插件。官方插件在独立仓库分发，这里给一条直达入口，
        # 免得用户只看到一个空面板不知道去哪儿拿。
        action('浏览插件库', 'link', self.on_plugin_repo,
               hint='官方插件下载')
        # v2.3.0：随安装包分发的《插件开发指南》。文案刻意不用技术词
        # （不写 .md / .html / docs\），用户看到的就是"指南"两个字。
        action('插件开发指南', 'log', self.on_guide,
               hint='写插件看这一份')

        # 外观：v2.7.0 起收成一行。原来「浅色主题 / 深色主题」两行只够切明暗，
        # 皮肤多了以后两行装不下，改成打开独立的皮肤面板（右侧带当前皮肤名）。
        self.menu_sep(inner)
        action('外观与皮肤…', 'sun' if self.theme == 'light' else 'moon',
               self.open_skin_settings,
               hint='%s · %s%s' % (
                   self.skin.name,
                   '跟随系统 ' if self.appearance_pref == 'system' else '',
                   '浅色' if self.theme == 'light' else '深色'))

        # 收尾
        self.menu_sep(inner)
        action('保存设置', 'check', lambda: self.on_save(True))
        action('关闭菜单', 'close', self.close_settings, hint='Esc')
        self.sep(inner, pady=(self.px(7), 0))
        tk.Label(inner, text='v%s · 改动立即生效' % VERSION, bg=c['bg'],
                 fg=c['t3'], font=self.f_small).pack(
                     anchor='w', padx=self.px(14),
                     pady=(self.px(2), self.px(10)))
        # 菜单 Tab 链：自上而下，开关行 focus 落在开关本身，其余落在整行
        chain = []
        for r in self._menu_rows:
            if getattr(r, '_menu_switch', None) is not None:
                chain.append(r._menu_switch)
            elif getattr(r, '_menu_cmd', None) is not None:
                chain.append(r)
        if self.settings_win is not None:
            self._set_chain(self.settings_win, chain)

    # ---- 外观与皮肤 ----
    def open_skin_settings(self):
        """皮肤面板：卡片列所有皮肤 + 明暗分段 + 安装/卸载。

        与齿轮菜单、插件设置共享同一条规矩：无标题栏浮层、topmost、点外部收起。
        点皮肤立即换（不等「保存」），换完面板留在原地——用户可以连着试两套再决定。
        内容超屏时走 Canvas 视口滚轮，不截断（v2.9.0 对齐设计稿）。
        """
        if getattr(self, 'skin_win', None) and self.skin_win.winfo_exists():
            self.place_skin_settings()
            return
        self.close_settings()
        self.close_plugin_settings()
        c = self.colors
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        win.configure(bg=c['line'])
        win.resizable(False, False)
        try:
            win.wm_attributes('-topmost', True)
        except Exception:
            pass
        if self.probe:
            try:
                win.attributes('-alpha', 0.0)
            except Exception:
                pass
        c = self.popup_colors('pop_base')
        outer = tk.Frame(win, bg=c['line'], padx=1, pady=1)
        outer.pack(fill='both', expand=True)
        cv = tk.Canvas(outer, bg=c['bg'], highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        body = tk.Frame(cv, bg=c['bg'])
        self.skin_win, self.skin_body, self.skin_outer = win, body, outer
        self.skin_canvas = cv
        self.skin_window_id = cv.create_window(0, 0, window=body, anchor='nw')
        self.build_skin_list(body)
        self.place_skin_settings()
        win.bind('<Escape>', lambda e: self.close_skin_settings())
        self._pop_in(win)

    def _draw_skin_thumb(self, cv, st, w, h):
        """112×63 迷你窗口缩略图：全部用该皮肤自己的 token 画，不用截图。"""
        s = w / 112.0
        cv.create_rectangle(0, 0, w, h, fill=st['glass'], width=0)
        for i in range(3):
            x = (10 + i * 8) * s
            cv.create_oval(x - 2 * s, 6 * s, x + 2 * s, 10 * s,
                           fill=st['t3'], width=0)
        round_rect(cv, w - 30 * s, 5 * s, w - 10 * s, 11 * s, 3 * s,
                   fill=st['fill_2'], width=0)
        cv.create_line(0, 17 * s, w, 17 * s, fill=st['hair'])
        for i, y in enumerate((25, 41)):
            yy = y * s
            round_rect(cv, 10 * s, yy, w * 0.5, yy + 7 * s, 3 * s,
                       fill=st['t2'] if i == 0 else st['t3'], width=0)
            on = i == 0
            tx0, tx1 = w - 34 * s, w - 10 * s
            round_rect(cv, tx0, yy - 1 * s, tx1, yy + 9 * s, 5 * s,
                       fill=st['on'] if on else st['switch_off'], width=0)
            kx = tx1 - 5 * s if on else tx0 + 5 * s
            cv.create_oval(kx - 4 * s, yy, kx + 4 * s, yy + 8 * s,
                           fill=st['on_knob'] if on else st['knob'], width=0)

    def build_skin_list(self, body):
        c = self.colors
        chain = []
        tk.Frame(body, height=self.px(6), bg=c['bg']).pack(fill='x')
        tk.Label(body, text='外观', bg=c['bg'], fg=c['t3'], font=self.f_small,
                 anchor='w').pack(fill='x', padx=self.px(14))
        for item in skins.catalog(BASE, log=self.note):
            chain.append(self._skin_card(body, item))
        tk.Label(body, text='明暗', bg=c['bg'], fg=c['t3'], font=self.f_small,
                 anchor='w').pack(fill='x', padx=self.px(14),
                                  pady=(self.px(8), 0))
        seg = tk.Frame(body, bg=c['bg'])
        seg.pack(fill='x', padx=self.px(14), pady=(self.px(4), self.px(6)))
        for key, label in (('system', '跟随系统'), ('light', '浅色'),
                           ('dark', '深色')):
            chain.append(self.capsule_button(
                seg, label, lambda k=key: self.on_pick_appearance(k),
                primary=(self.appearance_pref == key)))
            chain[-1].pack(side='left', padx=(0, self.px(6)))
        self.sep(body)

        act = tk.Frame(body, bg=c['bg'], padx=self.px(14), pady=self.px(8))
        act.pack(fill='x')
        install = tk.Label(act, text='安装皮肤（.wbskin）…', bg=c['bg'],
                           fg=c['t2'], font=self.f_small, cursor='hand2')
        install.pack(side='left')
        for ev, col in (('<Enter>', c['fg']), ('<Leave>', c['t2'])):
            install.bind(ev, lambda e, x=col: e.widget.configure(fg=x))
        install.bind('<Button-1>', lambda e: self.on_install_skin())
        self._keyboard(install, self.on_install_skin)
        chain.append(install)
        # 内置皮肤在磁盘上没有目录，不给「卸载」；用户装的才给
        if not self.skin.builtin:
            unins = tk.Label(act, text='卸载此皮肤', bg=c['bg'], fg=c['danger'],
                             font=self.f_small, cursor='hand2')
            unins.pack(side='right')
            unins.bind('<Button-1>', lambda e: self.on_uninstall_skin())
            self._keyboard(unins, self.on_uninstall_skin)
            chain.append(unins)
        if getattr(self, 'skin_win', None) is not None:
            self._set_chain(self.skin_win, chain)

    def _skin_card(self, body, item):
        """一张皮肤卡：缩略图 + 名称/性能pill + 来源·版本·描述 + 当前勾。

        reason='api' 的卡画「需要更新」pill、缩略图灰化、点了不生效
        （SKIN-SPEC §10：api 过新的皮肤要露出来告诉用户升级，不能装死）。
        """
        c = self.colors
        skin = item['skin']
        too_new = item['reason'] == 'api'
        on = skin is not None and skin.id == self.skin.id
        row = tk.Frame(body, bg=c['bg'],
                       cursor='arrow' if too_new else 'hand2')
        row.pack(fill='x', padx=self.px(10), pady=self.px(3))
        card = tk.Frame(row, bg=c['bg'], padx=self.px(10), pady=self.px(8),
                        highlightbackground=c['fg'] if on else c['hair'],
                        highlightcolor=c['fg'] if on else c['hair'],
                        highlightthickness=self.px(2) if on else 1)
        card.pack(fill='x')
        tw, th = self.px(112), self.px(63)
        thumb = tk.Canvas(card, width=tw, height=th, bg=c['bg'],
                          highlightthickness=0, bd=0)
        thumb.pack(side='left')
        if skin is not None:
            st = skin.tokens(self.theme)
        else:
            st = dict(self.tok)
            st.update(fg=st['t3'], t2=st['t4'], t3=st['t4'],
                      on=st['switch_off'], on_knob=st['knob'])
        self._draw_skin_thumb(thumb, st, tw, th)
        if on:
            Glyph(card, self, 'check', size=self.px(16)).pack(
                side='right', padx=(self.px(6), 0))
        txt = tk.Frame(card, bg=c['bg'])
        txt.pack(side='left', fill='both', expand=True, padx=(self.px(12), 0))
        head = tk.Frame(txt, bg=c['bg'])
        head.pack(fill='x')
        tk.Label(head, text=item['name'], bg=c['bg'],
                 fg=c['fg'] if not too_new else c['t3'],
                 font=self.f_body, anchor='w').pack(side='left')
        # 性能标签是硬要求（SKIN-SPEC 5）：用户有权知道这套皮肤贵不贵
        pill = '需要更新' if too_new else skin.perf_label
        tk.Label(head, text=pill, bg=c['fill'],
                 fg=c['danger'] if too_new else c['t2'],
                 font=self.f_small, padx=self.px(6), pady=self.px(1)).pack(
                     side='left', padx=(self.px(8), 0))
        if too_new:
            sub = '要求 api %d，当前面板只支持 api %d · 更新面板后可用' % (
                item['api'], skins.API_VERSION)
        else:
            sub = '%s · v%s · %s' % ('内置' if item['builtin'] else '第三方',
                                    item['version'], item['description'])
        tk.Label(txt, text=sub, bg=c['bg'], fg=c['t3'], font=self.f_small,
                 anchor='w', justify='left', wraplength=self.px(330)).pack(
                     fill='x', pady=(self.px(4), 0))
        if too_new:
            def reject(e, it=item):
                self.note('皮肤 %s 要求 api %d（当前 %d），未启用'
                          % (it['id'], it['api'], skins.API_VERSION))
            for w in (row, card, thumb, txt, head) + tuple(head.winfo_children()):
                w.bind('<Button-1>', reject)
            self._keyboard(row, lambda it=item: reject(None, it))
            return row
        for w in (row, card, thumb, txt, head) + tuple(head.winfo_children()):
            w.bind('<Button-1>', lambda e, d=item['id']: self.on_pick_skin(d))
        self._keyboard(row, lambda d=item['id']: self.on_pick_skin(d))
        if not on:
            card.bind('<Enter>', lambda e: card.configure(
                highlightbackground=c['t3']))
            card.bind('<Leave>', lambda e: card.configure(
                highlightbackground=c['hair']))
        return row

    def on_pick_skin(self, skin_id):
        self.set_skin(skin_id)
        self.refresh_skin_list()

    def on_pick_appearance(self, key):
        self.set_theme(key)
        self.refresh_skin_list()

    def refresh_skin_list(self):
        """换完皮肤后重建面板内容：勾要跟着走，配色也是新的。"""
        body = getattr(self, 'skin_body', None)
        if not (body and body.winfo_exists()):
            return
        for w in list(body.winfo_children()):
            w.destroy()
        self.build_skin_list(body)
        try:
            self.skin_outer.configure(bg=self.colors['line'])
            self.skin_win.configure(bg=self.colors['line'])
            if self.skin_canvas is not None:
                self.skin_canvas.configure(bg=self.colors['bg'])
        except Exception:
            pass
        self.place_skin_settings()

    def on_install_skin(self):
        while True:
            path = filedialog.askopenfilename(
                title='选择皮肤文件', parent=self.parent(),
                filetypes=[('皮肤包', '*.wbskin'), ('所有文件', '*.*')])
            if not path:
                return
            try:
                skin = skin_package.install_zip(path, BASE, overwrite=False,
                                                log=self.note)
                break
            except Exception as e:
                # 拒绝原因已由 skin_package 记进日志（skin.reject reason=…）
                if not self.dialog('未能安装',
                                   '皮肤没能安装：%s\n'
                                   '请确认文件完整后重试，或换一个文件。' % e,
                                   [('重新选择', True), ('关闭', False)]):
                    return
        self.set_skin(skin.id)
        self.refresh_skin_list()
        self.dialog('已安装', '皮肤「%s」已装好并启用。' % skin.name,
                    [('好', True)])

    def on_uninstall_skin(self):
        if self.skin.builtin:
            return
        if not self.confirm('卸载皮肤',
                            '卸载「%s」后外观会回到极简。要卸载吗？' % self.skin.name,
                            '卸载'):
            return
        sid = self.skin.id
        while True:
            try:
                skin_package.remove_skin(sid, BASE)
                break
            except Exception as e:
                self.note('卸载皮肤失败：%r' % e)
                if not self._error('未能卸载',
                                   '皮肤没能卸载：%s\n请稍后重试，详情可查看日志。' % e,
                                   can_retry=True):
                    return
        self.set_skin(skins.FALLBACK_ID)
        self.refresh_skin_list()

    def place_skin_settings(self):
        """贴字标下方、左沿对齐展开；内容超屏改滚轮，不截断卡片。"""
        win = getattr(self, 'skin_win', None)
        body = getattr(self, 'skin_body', None)
        cv = getattr(self, 'skin_canvas', None)
        if not (win and body and win.winfo_exists()):
            return
        win.update_idletasks()
        lx, ly, sw, sh = work_area()
        w = min(self.px(600), max(self.px(260), sw - self.px(20)))
        need_h = body.winfo_reqheight() + 2
        anchor = self.brand_wrap if (self.brand_wrap and
                                     self.brand_wrap.winfo_exists()) else self.root
        ax, ay = anchor.winfo_rootx(), anchor.winfo_rooty()
        x = max(lx + self.px(8), min(ax, lx + sw - w - self.px(8)))
        below = ay + anchor.winfo_height() + self.px(4)
        avail = max(self.px(160), ly + sh - below - self.px(8))
        h = min(need_h, self.px(560), avail)
        win.geometry('%dx%d+%d+%d' % (w, h, x, below))
        if cv is not None and self.skin_window_id is not None:
            cv.itemconfigure(self.skin_window_id, width=w - 2)
            win.update_idletasks()
            cv.configure(scrollregion=(0, 0, w - 2, body.winfo_reqheight()))
            self._bind_wheel(cv, body)
        try:
            win.lift()
            win.wm_attributes('-topmost', True)
        except Exception:
            pass

    def close_skin_settings(self):
        win = getattr(self, 'skin_win', None)
        if win is not None:
            self.cancel_tween(('pop', id(win)))
        self.skin_win = None
        self.skin_canvas = None
        self.skin_window_id = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        self._focus_brand()

    def on_uninstall_from_popup(self, entry):
        """浮层里的卸载：先收浮层，再走原来的确认流程（含备份）。"""
        self.close_plugin_settings()
        self.on_delete_plugin(entry)

    # ---- 行为 ----
    def parent(self):
        # 菜单是无标题栏浮层，动作项点击后会先收起；弹框一律挂主窗口
        return self.root

    def refresh_status(self):
        if not (self.status_label and self.status_label.winfo_exists()):
            return
        if self.readonly:
            try:
                self.status_label.configure(text='需要管理员权限')
            except Exception:
                pass
            return
        try:
            self.status_label.configure(text=short_guard_state(self.probe))
        except Exception:
            pass

    def refresh(self):
        self.refresh_status()
        self._tray_watch()
        if not self.probe:
            self.root.after(2000, self.refresh)

    def save_features(self, show_msg=False, quiet=False):
        """把各插件开关 + 主题写回 config.json。

        开关的落点由插件自己的 gui_toggle() 决定（ace 一个开关写两处），
        面板不再知道 `afterburner_enabled` / `ace_*` 这些具体键名。
        必须用 load_cfg() 读回整份再写：只写开关会抹掉路径、RTSS 名单等参数
        （test_throttle_state.py 第 [9] 组盯着这条）。
        """
        if self.readonly:
            if not quiet:
                self.dialog('需要管理员权限',
                            '这项改动需要管理员权限。\n'
                            '请右键程序图标，以管理员身份重新打开后再试。',
                            [('知道了', True)])
            return False
        try:
            cfg = load_cfg()
            for entry in self.entries:
                plugin_switch_write(entry, cfg, bool(self.vars[entry.id].get()))
            save_cfg(cfg)
            if show_msg:
                self.dialog('已保存', '设置已保存，无需重启。', [('好', True)])
            return True
        except Exception as e:
            self.note('保存设置失败：%r' % e)
            if self._error('未能保存',
                           '设置没能保存。请检查是否有写入权限后重试，详情可查看日志。',
                           can_retry=True):
                return self.save_features(show_msg=show_msg, quiet=quiet)
            return False

    def on_toggle(self):
        # 记录点击前的状态：写盘失败必须把开关视觉状态回滚，
        # 不允许出现"开关动了但 config.json 没变"的假象。
        before = {k: bool(v.get()) for k, v in self.vars.items()}
        for key, sw in self.switches.items():
            if key in self.vars:
                self.vars[key].set(sw.value)
        if self.save_features():
            return
        for key, value in before.items():
            self.vars[key].set(value)
            sw = self.switches.get(key)
            if sw is not None:
                sw.set_value(value)

    # ---- 托盘与游戏结束提示（v2.9.0，设计稿设置页「启动与守护」） ----
    def on_tray_toggle(self):
        sw = self.switches.get('tray')
        val = bool(sw.value) if sw is not None else bool(self.tray_var.get())
        self.tray_var.set(val)
        if self.probe:
            return
        if not self.write_setting(None, {'key': 'start_in_tray'}, val):
            self.tray_var.set(not val)
            if sw is not None:
                sw.set_value(not val)
            return
        self.apply_tray_pref()

    def on_notify_toggle(self):
        sw = self.switches.get('notify')
        val = bool(sw.value) if sw is not None else bool(self.notify_var.get())
        self.notify_var.set(val)
        if self.probe:
            return
        if not self.write_setting(None, {'key': 'notify_game_end'}, val):
            self.notify_var.set(not val)
            if sw is not None:
                sw.set_value(not val)

    def apply_tray_pref(self):
        """托盘图标存在性默认常驻：面板进程活着就该在通知区有一枚 WBOX，
        关窗即退出时随进程摘除。「启动后收进托盘」只决定关窗行为（收托盘 vs
        退出）与气泡前提，不再决定图标有没有——v2.11.1 前图标也 opt-in，
        用户默认看不到任何托盘存在，被当成没做。"""
        if self.probe:
            return
        icon = ICON_DARK if self.theme == 'dark' else ICON_LIGHT
        if self.tray is None:
            t = tray_icon.TrayIcon(icon, tip='WBOX')
            if t.start():
                self.tray = t
                self.root.after(200, self._tray_pump)
        else:
            self.tray.set_icon(icon)
        if not bool(self.tray_var.get()) and self.root.state() == 'withdrawn':
            self.root.deiconify()

    def _tray_shutdown(self):
        t, self.tray = self.tray, None
        if t is not None:
            t.stop()

    def _tray_pump(self):
        t = self.tray
        if t is None:
            return
        try:
            while True:
                cmd = t.commands.get_nowait()[0]
                if cmd == tray_icon.CMD_OPEN:
                    self.tray_open()
                elif cmd == tray_icon.CMD_EXIT:
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(200, self._tray_pump)

    def tray_open(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _tray_watch(self):
        """游戏结束气泡：心跳 active->grace/idle 且窗口正收在托盘里才弹。

        窗口可见时状态行已经写着「游戏中/游戏刚退出」，再弹气泡是打扰；
        但会话跟踪不能停，否则收托盘那一刻会拿陈旧 prev 误报。
        """
        if self.probe or self.tray is None or not bool(self.notify_var.get()):
            return
        st = guard_beat.read_status()
        cur = ''
        if isinstance(st, dict) and st.get('pid'):
            cur = str(st.get('session') or '')
        prev, self._last_session = self._last_session, cur
        if prev == 'active' and cur in ('grace', 'idle') \
                and self.root.state() == 'withdrawn':
            self.tray.balloon('游戏结束', '游戏已全部退出，守护回到空闲待命。')

    def on_boot_toggle(self):
        sw = self.switches.get('boot')
        if not sw:
            return
        val = bool(sw.value)
        self.boot_var.set(val)
        if not self.probe:
            while True:
                rc, out = set_autostart(val)
                if rc == 0:
                    break
                self.note('设置开机自启失败：%s' % out)
                if not self._error('未能设置开机自启',
                                   '开机自动运行没能设置成功。可以再试一次，'
                                   '或稍后在齿轮菜单里重新勾选。',
                                   can_retry=True):
                    sw.set_value(self.boot_initial)
                    self.boot_var.set(self.boot_initial)
                    return
            self.boot_initial = val
        self.save_features()

    def on_save(self, show_msg=True):
        if not self.save_features(show_msg=False):
            return
        boot = bool(self.boot_var.get())
        msg = '设置已保存，无需重启。'
        if boot != self.boot_initial and not self.probe:
            rc, out = set_autostart(boot)
            if rc == 0:
                self.boot_initial = boot
                msg += '\n开机自启已' + ('开启' if boot else '关闭') + '。'
            else:
                self.note('设置开机自启失败：%s' % out)
                msg += '\n开机自启没能改成功，可以再试一次。'
        if show_msg:
            self.dialog('已保存', msg, [('好', True)])

    def on_start(self):
        while True:
            try:
                rc, out = start_guard()
            except Exception as e:
                rc, out = 1, repr(e)
            if rc == 0:
                # 成功不打断（文案规范 §2.3）：hint 变「运行中」就够了
                self.refresh_status()
                return
            self.note('启动守护失败：%s' % out)
            if not self._error('未能启动',
                               '后台守护没能启动。可以重试一次，或查看日志了解原因。',
                               can_retry=True):
                return

    def on_stop(self):
        # 影响面大（所有插件暂停），L2 二次确认，按钮用具体动词
        if not self.confirm('停止守护？',
                            '停止后所有插件都会暂停工作，直到你再次启动。要停止吗？',
                            '停止'):
            return
        while True:
            try:
                with open(STOP_FILE, 'w', encoding='ascii') as f:
                    f.write('stop')
            except Exception as e:
                self.note('停止守护失败：%r' % e)
                if self._error('未能停止',
                               '后台守护没能停止。可以重试一次，或查看日志了解原因。',
                               can_retry=True):
                    continue
                return
            self.refresh_status()
            return

    def _open_failed(self, what, e):
        """打开文件/目录失败：人话 + 下一步，原始异常只进日志。返回是否重试。"""
        self.note('打开%s失败：%r' % (what, e))
        return self._error('打开失败',
                           '%s没能打开。可以重试一次，详情可查看日志。' % what,
                           can_retry=True)

    def on_log(self):
        while True:
            try:
                if not os.path.exists(LOG):
                    open(LOG, 'a', encoding='utf-8').close()
                os.startfile(LOG)
                return
            except Exception as e:
                if not self._open_failed('日志', e):
                    return

    def on_guide(self):
        """打开随安装包分发的《插件开发指南》。

        指南是自包含的单个网页（样式内嵌、不联网），双击即可用系统默认浏览器
        打开，所以这里直接 os.startfile，不拉起任何浏览器参数。
        文件缺失说明安装目录被裁剪过（或跑的是源码而非安装版），提示重装即可，
        不把路径里的技术细节写到界面上（铁律 13：界面上不出现目录名与扩展名）。
        """
        path = os.path.join(BASE, 'docs', '插件开发指南.html')
        if not os.path.isfile(path):
            self.dialog('指南缺失',
                        '这台机器上没有找到插件开发指南。'
                        '重新安装一次就能拿到。', [('好', True)])
            return
        while True:
            try:
                os.startfile(path)
                return
            except Exception as e:
                if not self._open_failed('指南', e):
                    return

    def on_cfg(self):
        while True:
            try:
                os.startfile(CFG)
                return
            except Exception as e:
                if not self._open_failed('设置文件', e):
                    return

    def on_plugin_dir(self, path):
        """打开某个插件的目录。只读模式下同样允许：只是浏览，不写任何配置。"""
        while True:
            try:
                if not os.path.isdir(path):
                    os.makedirs(path)
                os.startfile(path)
                return
            except Exception as e:
                if not self._open_failed('插件文件夹', e):
                    return

    def on_plugins_dir(self):
        """打开插件目录；第一次用时目录可能还不存在，先建出来。"""
        self.on_plugin_dir(PLUGINS_DIR)

    def on_plugin_repo(self):
        """打开官方插件库（GitHub 仓库）。

        本体 v2.11.3 起不带插件，装好程序是"空面板"；这里把下载入口放在
        与「打开插件目录」相邻的位置，用户不必去翻 README。
        """
        url = core_defaults.PLUGIN_REPO
        while True:
            try:
                os.startfile(url)      # ShellExecute：交给默认浏览器
                self.note('已打开插件库：%s' % url)
                return
            except Exception as e:
                if not self._open_failed('插件库页面', e):
                    return

    def on_rescan(self):
        """通知守护立即强制重扫（缓存删不掉也不影响，守护会带 force=True）。"""
        while True:
            try:
                failed = []
                for name in ('games_cache.json', 'rescan_games.flag'):
                    p = os.path.join(BASE, name)
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            failed.append(name)
                with open(os.path.join(BASE, 'rescan_games.flag'), 'w', encoding='ascii') as f:
                    f.write('1')
                msg = ('游戏扫描已开始，列表会自动更新。\n'
                       '详情可查看日志。')
                if failed:
                    msg += '\n\n部分旧缓存没能清掉，不影响这次扫描。'
                self.dialog('已开始', msg, [('好', True)])
                return
            except Exception as e:
                self.note('重新扫描游戏失败：%r' % e)
                if not self._error('扫描未完成',
                                   '游戏扫描没能完成。可以重试，或查看日志了解原因。',
                                   can_retry=True):
                    return

    # ---- 插件管理（v2.1.0）：＋ 装 zip 包 / 垃圾桶卸插件 / 通知守护热重载 ----
    def _plugins_readonly_notice(self):
        self.dialog('需要管理员权限',
                    '这项改动需要管理员权限。\n'
                    '请右键程序图标，以管理员身份重新打开后再试。',
                    [('知道了', True)])

    def on_add_plugin(self):
        """选一个规范 .zip 插件包，校验通过后安装并让守护下一轮重载。"""
        if self.probe:
            return
        if self.plugins_ro:
            self._plugins_readonly_notice()
            return
        while True:
            path = filedialog.askopenfilename(
                parent=self.parent(), title='选择插件文件',
                filetypes=[('插件文件', '*.zip'), ('所有文件', '*.*')])
            if not path:
                return
            try:
                info = plugin_package.inspect_zip(path)
                break
            except Exception as e:
                self.note('插件包校验失败：%r' % e)
                # 「重新选择」回到文件框，「取消」结束——不给裸「确定」
                if not self.dialog('这个文件不能用',
                                   '选中的文件不是有效的插件文件。'
                                   '请确认文件完整后重试，或换一个文件。',
                                   [('重新选择', True), ('取消', False)]):
                    return
        overwrite = False
        # 装之前把"这是谁做的、要碰什么、包有没有被换过"摆给用户看。
        # 插件运行在管理员进程内，这一屏是用户唯一能判断来源的机会。
        if not self.confirm('安装这个插件？',
                            plugin_package.describe_install(info),
                            '安装'):
            return
        if os.path.isdir(os.path.join(PLUGINS_DIR, info['id'])):
            overwrite = self.confirm(
                '已安装过这个插件',
                '同名插件已经装过了。\n'
                '继续安装会先备份旧版本，之后仍可找回。要继续吗？',
                '覆盖安装')
            if not overwrite:
                return
        while True:
            try:
                result = plugin_package.install_zip(
                    path, PLUGINS_DIR, base=BASE, overwrite=overwrite)
                plugin_package.notify_reload(BASE)
                break
            except Exception as e:
                self.note('安装插件失败：%r' % e)
                if not self._error('安装未完成',
                                   '插件没能安装。请确认文件完整后重试，详情可查看日志。',
                                   can_retry=True):
                    return
        self.reload_registry()
        msg = '插件已安装，现在就能在列表里启用它。'
        if result['replaced']:
            msg += '\n\n旧版本已备份，之后可以找回。'
        self.dialog('安装完成', msg, [('好', True)])

    # ---- 每插件设置弹框 ----
    def on_plugin_settings(self, entry):
        """点插件行的滑块：同一插件再点一次收起，换一个插件则切换内容。

        v2.7.0：没有 settings 的插件也开浮层——浮层里至少还有「卸载此插件」。
        原来这里弹一句「没有可调项」就结束，用户想删这个插件还得去翻插件目录。
        """
        if self.probe:
            return
        win = self.plugin_win
        if win and win.winfo_exists():
            same = (self.plugin_entry is not None
                    and self.plugin_entry.id == entry.id)
            self.close_plugin_settings()
            if same:
                return
        self.open_plugin_settings(entry)

    def open_plugin_settings(self, entry):
        """按 manifest.settings 生成浮层。与齿轮菜单互斥，不叠两层浮层。"""
        c = self.colors
        # 同一插件因写盘重开（选完路径要按新值重画）时保留「已摊全量」状态，
        # 否则用户点一次 rp-more、选一次路径就被打回前 3 项。
        keep = (self.plugin_entry is not None
                and self.plugin_entry.id == entry.id
                and self._plugin_expanded)
        # 只允许一层：先前已经有插件浮层（比如路径选完要按新值重画）就先拆掉，
        # 否则旧窗会变成没人认领的孤儿 Toplevel，重建时也不会被回收。
        if self.plugin_win is not None and self.plugin_win.winfo_exists():
            self.close_plugin_settings()
        self.close_settings()
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        win.configure(bg=c['line'])
        win.resizable(False, False)
        try:
            win.wm_attributes('-topmost', True)
        except Exception:
            pass
        if self.probe:
            try:
                win.attributes('-alpha', 0.0)
            except Exception:
                pass
        c = self.popup_colors('pop_base')
        outer = tk.Frame(win, bg=c['line'], padx=1, pady=1)
        outer.pack(fill='both', expand=True)
        # 内容放在 Canvas 视口里：装得下时和整块 Frame 无异，装不下时滚轮滚，
        # 不再把浮层底部（卸载 / 关闭）硬截掉。
        cv = tk.Canvas(outer, bg=c['bg'], highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        self.plugin_win = win
        self.plugin_canvas = cv
        self.plugin_entry = entry
        self.plugin_anchor = self.plugin_gears.get(entry.id)
        self._plugin_expanded = bool(keep)
        self._fill_plugin_body(entry)
        win.bind('<Escape>', lambda e: self.close_plugin_settings())
        self._plugin_was_pressed = False
        win.after(120, self._plugin_watch)
        self._pop_in(win)

    def _fill_plugin_body(self, entry, expanded=None):
        """浮层内容：默认前 3 项 + 「全部 N 项设置…」（设计稿 rp-more），
        点过之后摊全量；卸载与脚注始终在最底，靠滚动可达。"""
        if expanded is not None:
            self._plugin_expanded = bool(expanded)
        cv = self.plugin_canvas
        if self.plugin_body is not None and self.plugin_body.winfo_exists():
            self.plugin_body.destroy()
        c = self.popup_colors('pop_base')
        body = tk.Frame(cv, bg=c['bg'])
        self.plugin_body = body
        self._plugin_window_id = cv.create_window(0, 0, window=body, anchor='nw')
        self._setting_switches = {}

        head = tk.Frame(body, bg=c['bg'])
        head.pack(fill='x', padx=self.px(14), pady=(self.px(12), self.px(10)))
        Glyph(head, self, entry.icon, size=self.px(20)).pack(side='left')
        tk.Label(head, text=entry.name, bg=c['bg'], fg=c['fg'],
                 font=self.f_head).pack(side='left', padx=(self.px(10), 0))
        # 版本号靠右（设计稿 .rp-head：名称左、版本右，等宽字体小一号）
        tk.Label(head, text='v%s' % (entry.version or ''), bg=c['bg'],
                 fg=c['t3'], font=self.f_small).pack(side='right')
        self.sep(body)

        # 显示值一律走三段合并后的配置：面板显示「开」而守护按默认值跑「关」
        # 是这套体系里最招人烦的老坑，这里同一个口径。
        cfg = effective_cfg(entries=self.entries_all)
        specs = list(entry.settings or [])
        show = specs if self._plugin_expanded else specs[:3]
        for spec in show:
            self.build_setting_row(body, entry, spec, cfg)
            self.sep(body)
        if len(specs) > 3:
            more = tk.Frame(body, bg=c['bg'], cursor='hand2')
            more.pack(fill='x', padx=self.px(7), pady=(0, self.px(2)))
            txt = ('收起额外设置' if self._plugin_expanded
                   else '全部 %d 项设置…' % len(specs))
            ml = tk.Label(more, text=txt, bg=c['bg'], fg=c['t2'],
                          font=self.f_small, anchor='w')
            ml.pack(side='left', padx=self.px(8), pady=self.px(5))
            for w in (more, ml):
                w.bind('<Button-1>',
                       lambda e: self._fill_plugin_body(
                           entry, not self._plugin_expanded))
                w.bind('<Enter>', lambda e: [x.configure(bg=c['hover'])
                                             for x in (more, ml)], add='+')
                w.bind('<Leave>', lambda e: [x.configure(bg=c['bg'])
                                             for x in (more, ml)], add='+')
            self.sep(body)

        # 卸载沉到浮层底部（方案 4）：行尾不再单独挂垃圾桶，
        # 破坏性操作离手指远一点，误触概率也低。
        if not self.plugins_ro:
            del_row = tk.Frame(body, bg=c['bg'], cursor='hand2')
            del_row.pack(fill='x', padx=self.px(14),
                         pady=(self.px(4), self.px(2)))
            del_lbl = tk.Label(del_row, text='卸载此插件', bg=c['bg'],
                               fg=c['danger'], font=self.f_body, anchor='w')
            del_lbl.pack(fill='x')
            for w in (del_row, del_lbl):
                w.bind('<Button-1>',
                       lambda e, ent=entry: self.on_uninstall_from_popup(ent))
            self._keyboard(del_lbl,
                           lambda ent=entry: self.on_uninstall_from_popup(ent))
            self.sep(body)

        foot = tk.Frame(body, bg=c['bg'])
        foot.pack(fill='x', padx=self.px(14), pady=(self.px(9), self.px(12)))
        tk.Label(foot, text='改动立即生效', bg=c['bg'], fg=c['t3'],
                 font=self.f_small).pack(side='left')
        if self.readonly:
            tk.Label(foot, text='需要管理员权限', bg=c['bg'], fg=c['t3'],
                     font=self.f_small).pack(side='right')
        else:
            close = tk.Label(foot, text='关闭', bg=c['bg'], fg=c['t2'],
                             font=self.f_small, cursor='hand2')
            close.pack(side='right')
            close.bind('<Button-1>', lambda e: self.close_plugin_settings())
            close.bind('<Enter>', lambda e: close.configure(fg=c['fg']))
            close.bind('<Leave>', lambda e: close.configure(fg=c['t2']))

        self.place_plugin_settings()

    def place_plugin_settings(self):
        """右边缘贴齿轮、向下展开；下面放不开就向上，仍不行就截到屏幕内。"""
        win = self.plugin_win
        body = self.plugin_body
        if not (win and body and win.winfo_exists()):
            return
        win.update_idletasks()
        lx, ly, sw, sh = work_area()
        # 设计稿 .row-pop width:296
        w = min(self.px(296), max(self.px(240), sw - self.px(24)))
        need_h = body.winfo_reqheight() + 2
        a = self.plugin_anchor
        row = None
        if a is not None and a.winfo_exists():
            # 浮层贴的是「整行」的下沿（设计稿 top:calc(100% - 4px)），
            # 挂到行尾滑块上会从行中间长出来，压住同行的名称。
            row = a.master if isinstance(a.master, tk.Canvas) else None
            ax, ay = (row or a).winfo_rootx(), (row or a).winfo_rooty()
            aw, ah = (row or a).winfo_width(), (row or a).winfo_height()
        else:
            ax, ay = self.root.winfo_rootx(), self.root.winfo_rooty()
            aw, ah = 0, 0
        x = min(ax + aw - w, lx + sw - w - self.px(8))
        x = max(lx + self.px(8), x)
        below = ay + ah - self.px(4)
        avail_below = ly + sh - below - self.px(8)
        if need_h <= avail_below:
            y, h = below, need_h
        else:
            avail_above = ay - ly - self.px(8)
            if need_h <= avail_above:
                y, h = ay - need_h - self.px(6), need_h
            elif avail_below >= avail_above:
                y, h = below, max(self.px(140), avail_below)
            else:
                h = max(self.px(140), avail_above)
                y = ay - h - self.px(6)
        h = min(h, max(self.px(140), sh - self.px(16)))
        y = max(ly + self.px(8), min(y, ly + sh - h - self.px(8)))
        win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        cv = self.plugin_canvas
        if cv is not None and cv.winfo_exists():
            cv.itemconfigure(self._plugin_window_id, width=w - 2)
            win.update_idletasks()
            cv.configure(scrollregion=(0, 0, w - 2, body.winfo_reqheight()))
            self._bind_wheel(cv, body)
            # 路径胶囊的换行在拿到最终宽度后才发生（Configure），内容高度会
            # 晚于本次 place 变化；scrollregion 不跟着更新就会滚不到底。
            body.bind('<Configure>', lambda e: cv.configure(
                scrollregion=(0, 0, w - 2, body.winfo_reqheight())))
        try:
            win.lift()
            win.wm_attributes('-topmost', True)
        except Exception:
            pass

    def _plugin_watch(self):
        """全局左键：按在弹框 / 齿轮之外就收起（与齿轮菜单同一套判定）。"""
        win = self.plugin_win
        if not (win and win.winfo_exists()):
            return
        try:
            u = ctypes.windll.user32
            u.GetAsyncKeyState.argtypes = [ctypes.c_int]
            u.GetAsyncKeyState.restype = ctypes.c_short
            pressed = bool(int(u.GetAsyncKeyState(0x01)) & 0x8000)
            if pressed and not self._plugin_was_pressed and not self.probe:
                u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
                pt = POINT()
                u.GetCursorPos(ctypes.byref(pt))
                x, y = pt.x, pt.y
                pad = self.px(4)
                mx, my = win.winfo_rootx(), win.winfo_rooty()
                inside = (mx - pad <= x <= mx + win.winfo_width() + pad and
                          my - pad <= y <= my + win.winfo_height() + pad)
                in_anchor = False
                a = self.plugin_anchor
                if a is not None and a.winfo_exists():
                    ax, ay = a.winfo_rootx(), a.winfo_rooty()
                    in_anchor = (ax <= x <= ax + a.winfo_width() and
                                 ay <= y <= ay + a.winfo_height())
                if not inside and not in_anchor:
                    self.close_plugin_settings()
                    return
            self._plugin_was_pressed = pressed
        except Exception:
            pass
        try:
            win.after(120, self._plugin_watch)
        except Exception:
            pass

    def close_plugin_settings(self):
        win = self.plugin_win
        if win is not None:
            self.cancel_tween(('pop', id(win)))
        self.plugin_win = None
        self.plugin_body = None
        self.plugin_canvas = None
        self._plugin_window_id = None
        self.plugin_entry = None
        self.plugin_anchor = None
        if win is not None:
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
        self._focus_brand()

    def build_setting_row(self, master, entry, spec, cfg, bg=None):
        """一行设置：左标题（可带小字说明）+ 右控件。

        bg 给极简档的行内展开用：展开块底色和浮层不同，控件必须跟着换，
        否则会出现一块白底方片压在灰底上。
        """
        c = dict(self.colors)
        if bg:
            c['bg'] = bg
        ro = self.readonly
        typ = spec['type']
        if typ == 'path':
            # 路径行单独排版：主标题一行，路径做成整行灰底胶囊放在下面。
            # 设计稿 .rp-col 就是这个结构——296 宽的单行里塞不下路径 + 选择按钮。
            row = tk.Frame(master, bg=c['bg'])
            row.pack(fill='x', padx=self.px(14), pady=(self.px(9), self.px(9)))
            tk.Label(row, text=spec['label'], bg=c['bg'],
                     fg=(c['t3'] if ro else c['fg']), font=self.f_body,
                     anchor='w').pack(anchor='w')
            if spec.get('hint'):
                tk.Label(row, text=spec['hint'], bg=c['bg'], fg=c['t3'],
                         font=self.f_small, anchor='w', justify='left',
                         wraplength=self.px(230)).pack(anchor='w')
            pill = tk.Frame(row, bg=c['inset'])
            pill.pack(fill='x', pady=(self.px(6), 0))
            cur = str(cfg_get_path(cfg, spec['key'], '') or '')
            shown = cur if cur else '留空 → 自动查找'
            btn = tk.Label(pill, text='选择', bg=c['inset'],
                           fg=(c['t3'] if ro else c['t2']), font=self.f_small,
                           cursor=('arrow' if ro else 'hand2'))
            # 按钮先 pack 占住右侧：pack 的空腔按顺序切，长路径后 pack 才能
            # 拿到剩余宽度；反过来长路径会把按钮顶出胶囊（240dpi 实测越界）。
            btn.pack(side='right', padx=self.px(8))
            lab = tk.Label(pill, text=shown, bg=c['inset'], fg=c['t2'],
                           font=self.f_small, anchor='w', justify='left')
            lab.pack(side='left', fill='x', expand=True,
                     padx=self.px(8), pady=self.px(4))

            def _wrap_path(e):
                lab.configure(wraplength=max(
                    self.px(40),
                    e.width - btn.winfo_reqwidth() - self.px(32)))
            pill.bind('<Configure>', _wrap_path)
            if not ro:
                # 和弹框里其它控件（＋/−、选项）一致用按下即触发：
                # 这是一枚小字按钮，没有"误拖"风险，等到松开会显得迟滞。
                btn.bind('<Button-1>', lambda e: self._pick_path(entry, spec))
            return row
        row = tk.Frame(master, bg=c['bg'])
        row.pack(fill='x', padx=self.px(14), pady=(self.px(9), self.px(9)))
        left = tk.Frame(row, bg=c['bg'])
        left.pack(side='left', fill='x', expand=True)
        tk.Label(left, text=spec['label'], bg=c['bg'],
                 fg=(c['t3'] if ro else c['fg']), font=self.f_body,
                 anchor='w', justify='left',
                 wraplength=self.px(160)).pack(anchor='w')
        if spec.get('hint'):
            tk.Label(left, text=spec['hint'], bg=c['bg'], fg=c['t3'],
                     font=self.f_small, anchor='w', justify='left',
                     wraplength=self.px(160)).pack(anchor='w')
        right = tk.Frame(row, bg=c['bg'])
        right.pack(side='right', padx=(self.px(10), 0))
        if typ == 'bool':
            cur = bool(cfg_get_path(cfg, spec['key'], False))
            sw = Switch(right, self, value=cur,
                        command=lambda spec=spec: self.on_setting_bool(
                            entry, spec, self.switch_of(spec['key'])))
            sw.set_bg(c['bg'])
            sw.pack()
            if ro:
                sw.set_enabled(False)
            self._setting_switches[spec['key']] = sw
        elif typ == 'int':
            self.build_setting_int(right, entry, spec, cfg, ro)
        elif typ == 'choice':
            self.build_setting_choice(right, entry, spec, cfg, ro)
        elif typ == 'path':
            self.build_setting_path(right, entry, spec, cfg, ro)
        return row

    def switch_of(self, key):
        return self._setting_switches.get(key)

    def on_setting_bool(self, entry, spec, sw):
        if sw is None:
            return
        value = bool(sw.value)
        if not self.write_setting(entry, spec, value):
            sw.set_value(not value)

    def build_setting_int(self, master, entry, spec, cfg, ro):
        c = self.colors
        try:
            cur = int(cfg_get_path(cfg, spec['key'], spec['min']))
        except (TypeError, ValueError):
            cur = spec['min']
        cur = max(spec['min'], min(spec['max'], cur))
        unit = spec.get('unit') or ''
        # 用单元素列表持有当前值：闭包里要能改，又不引入 nonlocal
        state = [cur]
        var = tk.StringVar(master, value=('%d %s' % (cur, unit)).strip())
        ink = c['t3'] if ro else c['fg']
        minus = tk.Label(master, text='−', bg=c['bg'], fg=ink, font=self.f_head,
                         padx=self.px(6), cursor=('arrow' if ro else 'hand2'))
        minus.pack(side='left')
        tk.Label(master, textvariable=var, bg=c['bg'], fg=ink,
                 font=self.f_body, width=5).pack(side='left')
        plus = tk.Label(master, text='+', bg=c['bg'], fg=ink, font=self.f_head,
                        padx=self.px(6), cursor=('arrow' if ro else 'hand2'))
        plus.pack(side='left')

        def step(delta):
            new = max(spec['min'], min(spec['max'], state[0] + delta))
            if new == state[0]:
                return
            if self.write_setting(entry, spec, new):
                state[0] = new
                var.set(('%d %s' % (new, unit)).strip())

        if not ro:
            minus.bind('<Button-1>', lambda e: step(-spec.get('step', 1)))
            plus.bind('<Button-1>', lambda e: step(spec.get('step', 1)))

    def build_setting_choice(self, master, entry, spec, cfg, ro):
        c = self.colors
        cur = str(cfg_get_path(cfg, spec['key'], spec['options'][0][0]))
        buttons = {}

        def paint(btn, on):
            btn.configure(bg=(c['fg'] if on else c['bg']),
                          fg=(c['bg'] if on else c['t2']))

        def pick(value):
            if ro:
                return
            if self.write_setting(entry, spec, value):
                for v, b in buttons.items():
                    paint(b, v == value)

        for value, label in spec['options']:
            b = tk.Label(master, text=label, font=self.f_small,
                         padx=self.px(8), pady=self.px(3),
                         cursor=('arrow' if ro else 'hand2'))
            b.pack(side='left', padx=(self.px(3), 0))
            buttons[value] = b
            paint(b, value == cur)
            if not ro:
                b.bind('<Button-1>', lambda e, v=value: pick(v))

    def _pick_path(self, entry, spec):
        """选文件 → 落盘 → 重画浮层（路径胶囊的宽度随内容变，重建最省心）。"""
        if self.readonly:
            return
        try:
            path = filedialog.askopenfilename(
                parent=self.plugin_win or self.root,
                title='选择「%s」' % spec['label'],
                filetypes=[('程序', '*.exe'), ('所有文件', '*.*')])
        except Exception:
            return
        if not path:
            return
        if self.write_setting(entry, spec, path.replace('/', '\\')):
            if self.plugin_win and self.plugin_win.winfo_exists():
                saved = self.plugin_anchor
                self.open_plugin_settings(entry)
                self.plugin_anchor = saved

    def build_setting_path(self, master, entry, spec, cfg, ro):
        c = self.colors
        cur = str(cfg_get_path(cfg, spec['key'], '') or '')
        shown = os.path.basename(cur) if cur else '自动查找'
        lab = tk.Label(master, text=shown, bg=c['bg'], fg=c['t3'],
                       font=self.f_small)
        lab.pack(side='left', padx=(0, self.px(8)))
        btn = tk.Label(master, text='选择', bg=c['bg'],
                       fg=(c['t3'] if ro else c['t2']), font=self.f_small,
                       cursor=('arrow' if ro else 'hand2'))
        btn.pack(side='left')

        def choose():
            if ro:
                return
            try:
                path = filedialog.askopenfilename(
                    parent=self.root, title='选择「%s」' % spec['label'],
                    filetypes=[('程序', '*.exe'), ('所有文件', '*.*')])
            except Exception:
                return
            if not path:
                return
            if self.write_setting(entry, spec, path.replace('/', '\\')):
                lab.configure(text=os.path.basename(path) or path)

        if not ro:
            btn.bind('<Button-1>', lambda e: choose())

    def write_setting(self, entry, spec, value):
        """单项写盘：只改这个叶子键，其它键保持不动。失败要把控件状态回滚。"""
        if self.readonly:
            return False
        try:
            cfg = load_cfg()
            cfg_set_path(cfg, spec['key'], value)
            save_cfg(cfg)
            return True
        except Exception as e:
            self.note('保存设置失败：%r' % e)
            self._error('未能保存',
                        '设置没能保存。请检查是否有写入权限后重试，详情可查看日志。')
            return False

    def on_delete_plugin(self, entry):
        """把插件目录移进备份区（不硬删），再通知守护热重载。"""
        if self.probe:
            return
        if self.plugins_ro:
            self._plugins_readonly_notice()
            return
        extra = None
        if entry.id in plugin_package.BUILTIN_IDS:
            extra = '这是随程序附带的内置插件，卸载后需重新安装程序才能恢复。'
        if not self.confirm('卸载「%s」？' % entry.name,
                            '卸载后这个插件会停止工作，文件会先备份而不是直接删除，'
                            '之后可以找回。要卸载吗？',
                            '卸载', extra=extra):
            return
        while True:
            try:
                plugin_package.remove_plugin(entry.id, PLUGINS_DIR, base=BASE)
                plugin_package.notify_reload(BASE)
                break
            except Exception as e:
                self.note('卸载插件失败：%r' % e)
                if not self._error('未能卸载',
                                   '插件没能卸载，可能正在被占用。请稍后重试，详情可查看日志。',
                                   can_retry=True):
                    return
        self.reload_registry()
        self.dialog('已卸载', '插件已卸载并备份，之后可以找回，列表已更新。',
                    [('好', True)])

    def reload_registry(self):
        """装/卸之后重读注册表：主页面行数、菜单分组与开关都跟着变。

        不整体重启面板进程，只重建控件；vars 必须重建成新的 BooleanVar，
        否则旧开关被 destroy 后仍引用旧变量，拨动时写不到 config.json。
        """
        self.hide_tips()
        self.close_settings()
        self.close_plugin_settings()
        self.entries_all = plugin_entries()
        self.runtime_state = read_plugin_states()
        self.entries = [e for e in self.entries_all
                        if e.status == plugin_host.STATUS_OK and e.home]
        self.entries.sort(key=lambda e: (e.order, e.id))
        cfg = effective_cfg(entries=self.entries_all)
        self.vars = {}
        for entry in self.entries:
            self.vars[entry.id] = tk.BooleanVar(
                self.root, value=plugin_switch_state(entry, cfg))
        self.rebuild()


# ---------------- 无窗口自检 / 布局检查 ----------------

def self_check():
    enable_dpi_awareness()
    dpi = system_dpi()
    wa = work_area()
    lines = [
        'OK',
        'BASE=%s' % BASE,
        'DPI_MODE=%s' % DPI_MODE,
        'real_dpi=%d (96 means still virtualized => blurry)' % dpi,
        'scale=%.2f' % (dpi / 96.0),
        'work_area=%dx%d' % (wa[2], wa[3]),
        'frozen=%s' % getattr(sys, 'frozen', False),
        'exe_bytes=%d' % (os.path.getsize(sys.executable) if getattr(sys, 'frozen', False) else 0),
        # 装在 Program Files 时这里通常是 False：面板要么提权，要么功能置灰
        'cfg_writable=%s' % can_write_config(),
        'elevated=%s' % is_admin(),
    ]
    try:
        entries = plugin_entries()
        cfg = effective_cfg(entries=entries)
        lines.append('AB=%s ACE=%s THROTTLE=%s' % (
            cfg.get('afterburner_enabled'),
            cfg['ace_cleanup'].get('enabled'),
            cfg['ace_throttle'].get('enabled')))
        lines.append('theme=%s' % cfg.get('ui_theme', 'light'))
        # 主页面显示的功能行 = 加载成功且 home=true 的插件，名字取自 manifest
        lines.append('features=%s' % '|'.join(
            e.name for e in entries
            if e.status == plugin_host.STATUS_OK and e.home))
        lines.append('second_level=wordmark_menu+skin_panel')
        _skin, _appearance = skins.resolve(cfg, base=BASE)
        lines.append('skin=%s/%s perf=%s' % (
            _skin.id, _appearance, _skin.perf_label))
        lines.append('menu_style=browser_popup rows=%d' % menu_row_count(entries))
        # 注册表全景：id:状态，自检时一眼能看出哪个插件没加载起来
        lines.append('plugins=%s' % (','.join(
            '%s:%s' % (e.id, e.status) for e in entries) or 'none'))
        lines.append('plugins_dir=%s exists=%s' % (
            PLUGINS_DIR, os.path.isdir(PLUGINS_DIR)))
        lines.append('plugins_state=%s' % os.path.isfile(STATE_FILE))
        lines.append('menu_entry=label:WBOX')
        lines.append('plugin_actions=add:plus,settings:cfg,remove:in_popup edit=%s'
                     % plugin_package.plugins_writable(PLUGINS_DIR))
        lines.append('plugin_package=%s' % plugin_package.FORMAT_ID)
        lines.append('icons=%s' % ','.join(
            '%s:%s' % (n, os.path.isfile(p)) for n, p in
            (('light', ICON_LIGHT), ('dark', ICON_DARK))))
    except Exception as e:
        lines.append('CFG_FAIL=%r' % (e,))
    lines.append('guard=%s' % guard_state())
    try:
        r = tk.Tk()
        r.geometry('1x1+0+0')
        r.attributes('-alpha', 0.0)
        r.update()
        fam = pick_family(r)
        f = tkfont.Font(r, family=fam, size=10)
        r.tk.call('tk', 'scaling', dpi / 72.0)
        lines.append('tkinter=%s font=%s font_px=%d' % (tk.TkVersion, fam, f.metrics('linespace')))
        set_window_icons(r, ICON_LIGHT)
        wi = get_window_icons(r)
        lines.append('window_icons=small:%s,big:%s' % wi)
        lines.append('icon_files=%s' % ','.join(
            '%s:%s' % (n, os.path.isfile(p)) for n, p in
            (('light', ICON_LIGHT), ('dark', ICON_DARK))))
        r.destroy()
        lines.append('TK_WINDOW_OK=True')
    except Exception as e:
        lines.append('TK_WINDOW_FAIL=%r' % (e,))
    with open(SELFCHECK, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def layout_check():
    """模拟 96/120/144/168/192/240 DPI，检查控件是否溢出窗口。"""
    enable_dpi_awareness()
    lines = ['OK', 'DPI_MODE=%s' % DPI_MODE]
    try:
        root = tk.Tk()
        root.attributes('-alpha', 0.0)
        app = App(root, probe=True)

        def overflow(base):
            rx, ry = base.winfo_rootx(), base.winfo_rooty()
            w, h = base.winfo_width(), base.winfo_height()
            bad = []
            for c in walk(base):
                try:
                    x = c.winfo_rootx() - rx
                    y = c.winfo_rooty() - ry
                    cw, ch = c.winfo_width(), c.winfo_height()
                    if cw > 1 and ch > 1 and (x + cw > w + 2 or y + ch > h + 2):
                        bad.append('%s(%d,%d,%d,%d)' % (
                            c.winfo_class(), x, y, cw, ch))
                except Exception:
                    pass
            return w, h, bad

        lx, ly, sw_area, sh_area = work_area()

        def scan():
            for dpi in (96, 120, 144, 168, 192, 240):
                app.close_settings()
                app.apply_dpi(dpi)
                root.update_idletasks()
                root.update()
                w, h, bad = overflow(root)
                # 弹出菜单按内部 Frame 检查逐行项目是否越界，并确认浮层
                # 完整落在屏幕工作区（超高时由 Canvas 滚轮承接，不裁内容）。
                app.open_settings()
                root.update_idletasks()
                root.update()
                iw, ih, ibad = overflow(app.settings_inner)
                m = app.settings_win
                mx, my = m.winfo_rootx(), m.winfo_rooty()
                mw, mh = m.winfo_width(), m.winfo_height()
                in_area = (mx >= lx - 2 and my >= ly - 2 and
                           mx + mw <= lx + sw_area + 2 and
                           my + mh <= ly + sh_area + 2)
                lines.append(
                    'dpi=%-3d scale=%.2f win=%dx%d overflow=%d menu=%dx%d '
                    'items=%dx%d moverflow=%d in_area=%s font_px=%-3d plugins=%d %s'
                    % (dpi, dpi / 96.0, w, h, len(bad), mw, mh, iw, ih,
                       len(ibad), in_area, app.f_body.metrics('linespace'),
                       len(app.entries), ';'.join((bad + ibad)[:3])))
                for e in app.entries:
                    if e.status != plugin_host.STATUS_OK or not e.settings:
                        continue
                    app.open_plugin_settings(e)
                    root.update_idletasks()
                    root.update()
                    ph = app.plugin_win.winfo_height()
                    need = app.plugin_body.winfo_reqheight() + 2
                    _, _, pbad = overflow(app.plugin_body)
                    lines.append('plugin_settings=%s dpi=%d items=%d need=%d '
                                 'win_h=%d clipped=%s overflow=%d'
                                 % (e.id, dpi, len(e.settings), need, ph,
                                    need > ph + 2, len(pbad)))
                    app.close_plugin_settings()

        lines.append('pass skin=%s' % app.skin.id)
        scan()
        # v2.10.0 内置只有极简档；standard 骨架（第三方皮肤仍走）也要量：
        # 换一枚临时 standard 皮肤再扫一遍，persist=False 不写生产配置。
        if app.skin.performance != 'standard':
            tok = dict.fromkeys(skins.TOKENS, '#808080')
            tok.update(fg='#000000', glass='#FFFFFF', on='#000000',
                       on_knob='#FFFFFF', knob='#FFFFFF')
            std = skins.from_dict({
                'id': 'layout-std', 'name': '自检标准档', 'version': '1.0.0',
                'api': 1, 'engine': 'flat', 'performance': 'standard',
                'chrome': 'custom',
                'metrics': {'row': 52, 'font': 15, 'icon': 22, 'titlebar': 48},
                'radius': {'window': 26, 'card': 20, 'row': 14},
                'light': tok})
            app.apply_skin(std, persist=False)
            lines.append('pass skin=layout-std')
            scan()
        app.close_settings()
        root.destroy()
    except Exception as e:
        lines[0] = 'FAIL'
        lines.append('ERROR=%r' % (e,))
    with open(LAYOUTCHECK, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    global READONLY
    args = sys.argv[1:]
    if '--self-check' in args:
        self_check()
        return
    if '--layout-check' in args:
        layout_check()
        return
    # 默认目录装在 Program Files 下时，普通用户改不了 config.json：先自我提权重开；
    # 用户拒绝 UAC（或提权后仍不可写）才退回"功能置灰 + 状态行提示需要管理员权限"。
    if not can_write_config():
        if not is_admin() and elevate_settings(args):
            return 0
        READONLY = True
    enable_dpi_awareness()
    # 上一次安装（换入过程中断电/被杀）留下的现场先收干净：面板可能正是断电
    # 之后第一个启动的东西，不能等下一次装插件才处理。失败静默，只影响"能不能
    # 自动挽回"，不影响面板打开。
    try:
        plugin_package.recover(PLUGINS_DIR, base=BASE, log=lambda m: None)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    main()
