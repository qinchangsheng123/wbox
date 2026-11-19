# -*- coding: utf-8 -*-
"""面板的配置读写与系统状态查询（视图模型层，不含界面代码）。

三条口径与守护保持一字不差：
  1. 配置合并三段：内核 DEFAULTS -> 各插件 DEFAULT_CONFIG -> 用户 config.json
     （plugin_host.merge_defaults，纯函数，不改入参）。
  2. 开关写回只改自己那一段，绝不把插件其它参数抹掉（with_defaults）。
  3. 守护状态只信心跳管道（guard_beat.read_status），不兜底查进程列表。

2026-11 审查项 ⑨：从 settings_gui.py 拆出。App 只负责画，判断都在这里。
"""
import json
import os
import shutil
import subprocess

from tkinter import font as tkfont

import core_defaults
import guard_beat
import panel_env
import plugin_api
import plugin_host
from win_env import NOWIN, is_admin, ps_run

BASE = panel_env.BASE
CFG = panel_env.CFG
LOG = panel_env.LOG
PID_FILE = panel_env.PID_FILE
STOP_FILE = panel_env.STOP_FILE
STATE_FILE = panel_env.STATE_FILE
PLUGINS_DIR = panel_env.PLUGINS_DIR
TASK = panel_env.TASK
TASKLIST = panel_env.TASKLIST
UI_SIZE_ZOOM = panel_env.UI_SIZE_ZOOM
DEFAULT_UI_SIZE = panel_env.DEFAULT_UI_SIZE

# ---- 配置读写 ----
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


def ui_size_from_cfg(cfg=None):
    """配置里的 ui_size -> 三档之一，坏值/缺键一律回落默认「小」。

    读配置失败不能拦住面板启动：外观是纯展示偏好，拿不到就当默认，
    和 skins.resolve() 对坏皮肤的处理口径一致。
    """
    if not isinstance(cfg, dict):
        try:
            cfg = load_cfg()
        except Exception:
            cfg = {}
    key = str(cfg.get('ui_size') or '').strip().lower()
    return key if key in UI_SIZE_ZOOM else DEFAULT_UI_SIZE



# ---- 三段合并 / 点路径 / 开关读写 ----
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



# ---- 守护状态查询 ----
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
