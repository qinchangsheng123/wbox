# -*- coding: utf-8 -*-
"""WBOX - 启动游戏时拉起 MSI Afterburner，游戏全部退出后关闭它。

设计要点：
  1. 只关闭"本次由本脚本启动"的小飞机；用户自己开的小飞机不动。
  2. 只关闭"游戏期间新增"的 RTSS 进程；原本常驻的 RTSS 不动。
  3. 白名单 = 配置里的进程名 + 游戏目录自动发现（排除平台/反作弊/工具 exe）。
  4. 配置热加载，改完 config.json 保存即可生效，不用重启守护。
  5. 单实例（命名互斥体），重复双击启动脚本不会起第二个守护。
  6. 游戏退出后清理 ACE 残留：清理逻辑已内建（ace_native.py），四道闸门原样保留，
     不再依赖 PowerShell 脚本与 SYSTEM 计划任务。
"""
import atexit
import copy
import ctypes
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes

import config_schema
import core_defaults
import events
import game_lib
import plugin_api
import plugin_host
import plugin_package

# 打包成 exe 后 __file__ 指向临时解压目录，配置/日志必须跟着 exe 本体走
if getattr(sys, 'frozen', False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isfile(os.path.join(BASE, 'config.json')):
        _up = os.path.dirname(BASE)
        if os.path.isfile(os.path.join(_up, 'config.json')):
            BASE = _up
CONFIG_PATH = os.path.join(BASE, 'config.json')
LOG_PATH = os.path.join(BASE, 'guard.log')
PID_PATH = os.path.join(BASE, 'guard.pid')
STOP_PATH = os.path.join(BASE, 'stop.flag')
# 面板装/卸插件后写这个标记，守护在会话 idle 时重建插件宿主（文件名由
# plugin_package 定义，两边共用一份常量，避免拼写分叉）。
RELOAD_FLAG_PATH = os.path.join(BASE, plugin_package.RELOAD_FLAG)

# 插件目录 = 安装根下的 plugins\；源码调试时回退到仓库根的 plugins\。
# 解析规则与面板共用同一份实现（plugin_host.resolve_plugins_dir），绝不各写一遍：
# 两处规则一旦分叉，症状就是"面板能看到插件、守护看不到"。
PLUGINS_DIR = plugin_host.resolve_plugins_dir(BASE)

CREATE_NO_WINDOW = 0x08000000
INVALID_HANDLE = ctypes.c_void_p(-1).value

# 提权进程（守护由计划任务以 HIGHEST 启动）里不能靠 PATH 解析外部命令：
# 当前目录与用户可写目录可能排在 System32 之前，裸名 taskkill 会被同名 exe 劫持。
# 所有系统命令一律用 SystemRoot\System32 下的绝对路径，不写字面 C:\Windows。
SYS32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
TASKKILL = os.path.join(SYS32, 'taskkill.exe')
TASKLIST = os.path.join(SYS32, 'tasklist.exe')
POWERSHELL = os.path.join(SYS32, 'WindowsPowerShell', 'v1.0', 'powershell.exe')

# v2.5.0-③ 事件驱动：事件通道健康时轮询降为低频兜底（只改「什么时候跑一轮」，
# 闸门与插件的判定逻辑一行不动）。插件用 request_fast_poll 登记的快节拍
# （弹窗监视期）是功能时延不是兜底，绝不放宽。
EVENT_IDLE_FACTOR = 15          # 空闲 2s -> 30s
EVENT_FAST_FACTOR = 5           # 会话中 0.4s -> 2.0s（游戏退出有事件兜住）
EVENT_IDLE_MIN = 5.0
EVENT_FAST_MIN = 1.0

AB_NAME = 'msiafterburner.exe'
# RTSS 组件进程名。这两个常量留在内核里，但只有 afterburner 插件会用到
# （通过 ctx.import_core('guard_core') 取）——放在这里是为了不把 Win32 模块
# 拆成两份，内核自身不再做任何 RTSS 判定。
RTSS_NAMES = {
    'rtss.exe', 'rtsshooksloader.exe', 'rtsshooksloader64.exe',
    'encoderserver.exe', 'encoderserver64.exe',
    'desktopoverlayhost.exe', 'desktopoverlayhost64.exe',
    'desktopoverlayhostloader.exe',
}

# 内核默认值统一放在 core_defaults（唯一来源），这里只做同名 re-export：
# 老代码与测试仍然用 guard_core.DEFAULTS / ACE_THROTTLE_DEFAULTS 等，
# 且拿到的是同一个对象（不是副本），避免出现两份默认值口径。
DEFAULTS = core_defaults.DEFAULTS
ACE_CLEANUP_DEFAULTS = core_defaults.ACE_CLEANUP_DEFAULTS
POPUP_DEFAULTS = core_defaults.POPUP_DEFAULTS
ACE_THROTTLE_DEFAULTS = core_defaults.ACE_THROTTLE_DEFAULTS

# ---------- Win32 进程枚举（无第三方依赖，Python 3.8 可用） ----------
class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', wintypes.WCHAR * 260),
    ]


k32 = ctypes.WinDLL('kernel32', use_last_error=True)
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32FirstW.restype = wintypes.BOOL
k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.restype = wintypes.BOOL
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.CloseHandle.restype = wintypes.BOOL
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                           wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD)]
k32.QueryFullProcessImageNameW.restype = wintypes.BOOL

# ---------- 进程资源控制（ACE 限速用；纯调度参数，不写内存、不注入、不碰驱动） ----------
#
# 实测结论（2026-11-08）：
#   SetPriorityClass(PROCESS_MODE_BACKGROUND_BEGIN) 只对"自身进程"有效，跨进程一律
#   返回 ERROR_INVALID_PARAMETER(87)，所以不能靠它一次性降 CPU+IO+内存优先级。
#   跨进程可行的两条通道：SetPriorityClass(IDLE) 改 CPU，NtSetInformationProcess
#   (ProcessIoPriority) 改磁盘 I/O —— 后者才是真正压住扫盘的关键。
PROCESS_SET_INFORMATION = 0x0200
PROCESS_QUERY_INFORMATION = 0x0400
IDLE_PRIORITY_CLASS = 0x00000040
NORMAL_PRIORITY_CLASS = 0x00000020
PROCESS_IO_PRIORITY = 33
IO_HINTS = {0: 'very-low', 1: 'low', 2: 'normal', 3: 'high', 4: 'critical'}
PRIORITY_NAMES = {
    0x00000040: 'idle', 0x00004000: 'below-normal', 0x00000020: 'normal',
    0x00008000: 'above-normal', 0x00000080: 'high', 0x00000100: 'realtime',
}
k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
k32.SetPriorityClass.restype = wintypes.BOOL
k32.GetPriorityClass.argtypes = [wintypes.HANDLE]
k32.GetPriorityClass.restype = wintypes.DWORD
k32.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
k32.SetProcessAffinityMask.restype = wintypes.BOOL

ntdll = ctypes.WinDLL('ntdll')
ntdll.NtSetInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_ulong,
                                          ctypes.c_void_p, ctypes.c_ulong]
ntdll.NtSetInformationProcess.restype = ctypes.c_long
ntdll.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_ulong,
                                            ctypes.c_void_p, ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_ulong)]
ntdll.NtQueryInformationProcess.restype = ctypes.c_long


def _open_for_set(pid):
    return k32.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_INFORMATION, False, pid)


# ---------- SeDebugPrivilege ----------
# 实测（2026-11-08）：ACE 的驱动会拦截对 sguard64/sguardsvc64 的句柄打开。
# 同一个进程，带 SeDebugPrivilege 的调用方可以改优先级；不带的一律 ERROR_ACCESS_DENIED(5)。
# 计划任务启动的守护令牌里该特权默认是"存在但禁用"，所以必须显式启用。
class _LUID(ctypes.Structure):
    _fields_ = [('LowPart', wintypes.DWORD), ('HighPart', wintypes.LONG)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Luid', _LUID), ('Attributes', wintypes.DWORD)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [('PrivilegeCount', wintypes.DWORD),
                ('Privileges', _LUID_AND_ATTRIBUTES * 1)]


DEBUG_PRIVILEGE_STATE = None


def enable_debug_privilege():
    """启用 SeDebugPrivilege；结果缓存，重复调用不重复设置。"""
    global DEBUG_PRIVILEGE_STATE
    if DEBUG_PRIVILEGE_STATE is not None:
        return DEBUG_PRIVILEGE_STATE
    try:
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                         ctypes.POINTER(wintypes.HANDLE)]
        k32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                   ctypes.POINTER(_LUID)]
        advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
        advapi32.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL,
                                                   ctypes.POINTER(_TOKEN_PRIVILEGES),
                                                   wintypes.DWORD, wintypes.LPVOID,
                                                   wintypes.LPVOID]
        advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
        h = wintypes.HANDLE()
        if not k32.OpenProcessToken(k32.GetCurrentProcess(), 0x0028, ctypes.byref(h)):
            DEBUG_PRIVILEGE_STATE = False
            return False
        try:
            luid = _LUID()
            if not advapi32.LookupPrivilegeValueW(None, 'SeDebugPrivilege', ctypes.byref(luid)):
                DEBUG_PRIVILEGE_STATE = False
                return False
            tp = _TOKEN_PRIVILEGES()
            tp.PrivilegeCount = 1
            tp.Privileges[0].Luid = luid
            tp.Privileges[0].Attributes = 0x00000002   # SE_PRIVILEGE_ENABLED
            ctypes.set_last_error(0)
            ok = advapi32.AdjustTokenPrivileges(h, False, ctypes.byref(tp), 0, None, None)
            DEBUG_PRIVILEGE_STATE = bool(ok) and ctypes.get_last_error() == 0
            return DEBUG_PRIVILEGE_STATE
        finally:
            k32.CloseHandle(h)
    except Exception:
        DEBUG_PRIVILEGE_STATE = False
        return False


def priority_class(pid):
    """返回 (数值, 可读名)；取不到返回 (None, 'unavailable')。"""
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return None, 'unavailable'
    try:
        v = int(k32.GetPriorityClass(h))
    finally:
        k32.CloseHandle(h)
    if not v:
        return None, 'unavailable'
    return v, PRIORITY_NAMES.get(v & 0x00FFFFFF, hex(v))


def io_priority(pid):
    """返回 I/O 优先级提示值（0=very-low … 2=normal），取不到返回 None。"""
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return None
    try:
        v = ctypes.c_ulong(0xFF)
        n = ctypes.c_ulong(0)
        st = ntdll.NtQueryInformationProcess(h, PROCESS_IO_PRIORITY,
                                             ctypes.byref(v), 4, ctypes.byref(n))
        return int(v.value) if st == 0 else None
    finally:
        k32.CloseHandle(h)


def _set_io_priority(h, hint):
    v = ctypes.c_ulong(hint)
    return ntdll.NtSetInformationProcess(h, PROCESS_IO_PRIORITY,
                                         ctypes.byref(v), 4) == 0


def throttle_process(pid, io_hint=0, bind_last_core=False):
    """把进程压成低 CPU + 低磁盘 I/O 优先级，减少扫盘抢盘。

    只改调度参数：不写对方内存、不注入 DLL、不加载驱动，随时可 restore_process 还原。
    返回 (是否至少改成功一项, 说明)。
    """
    h = _open_for_set(pid)
    if not h:
        err = ctypes.get_last_error()
        hint = '，需 SeDebugPrivilege' if err == 5 else ''
        return False, '打不开进程（错误码 %d%s）' % (err, hint)
    parts, fails = [], []
    try:
        if k32.SetPriorityClass(h, IDLE_PRIORITY_CLASS):
            parts.append('CPU=空闲')
        else:
            fails.append('CPU 失败(%d)' % ctypes.get_last_error())
        if _set_io_priority(h, io_hint):
            parts.append('磁盘IO=%s' % IO_HINTS.get(io_hint, str(io_hint)))
        else:
            fails.append('磁盘IO 失败')
        if bind_last_core:
            cores = os.cpu_count() or 1
            if k32.SetProcessAffinityMask(h, ctypes.c_size_t(1 << (cores - 1))):
                parts.append('亲和性=末核')
            else:
                fails.append('亲和性失败')
        return bool(parts), '，'.join(parts + fails)
    finally:
        k32.CloseHandle(h)


def restore_process(pid):
    """还原 CPU 与 I/O 优先级。进程已退出时返回 False，属正常。"""
    h = _open_for_set(pid)
    if not h:
        return False
    try:
        ok_cpu = bool(k32.SetPriorityClass(h, NORMAL_PRIORITY_CLASS))
        ok_io = _set_io_priority(h, 2)
        return ok_cpu or ok_io
    finally:
        k32.CloseHandle(h)


def process_path(pid):
    """取进程 exe 完整路径；受保护进程取不到就返回 None。"""
    h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if h is None or h == 0:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(32768)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        k32.CloseHandle(h)


def snapshot():
    """返回 {pid: 进程名小写}；**取进程表失败时返回 None**。

    None 与空的 {} 语义完全不同：{} 表示"系统里真的一个进程都没看到"（正常不会
    发生），None 表示"这一轮根本没读到进程表"。调用方必须把 None 当成"未知"，
    绝不能当成"游戏已经退出"——否则一次快照抖动就会误关小飞机、误清 ACE。
    """
    result = {}
    h = k32.CreateToolhelp32Snapshot(2, 0)
    if h is None or h == 0 or h == INVALID_HANDLE:
        return None
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(h, ctypes.byref(pe))
        while ok:
            result[int(pe.th32ProcessID)] = pe.szExeFile.lower()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = k32.Process32NextW(h, ctypes.byref(pe))
    finally:
        k32.CloseHandle(h)
    return result


def run_hidden(args):
    try:
        return subprocess.run(args, creationflags=CREATE_NO_WINDOW,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=15).returncode
    except Exception:
        return -1


def run_capture(args, timeout=20):
    """跑命令并取回 stdout（PowerShell ACL 查询这类需要读结果的场景）。"""
    try:
        p = subprocess.run(args, creationflags=CREATE_NO_WINDOW,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout)
        return (p.stdout or b'').decode('mbcs', 'replace').strip()
    except Exception:
        return ''


def shell_execute_runas(path):
    try:
        sh = ctypes.WinDLL('shell32')
        sh.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.LPCWSTR, ctypes.c_int]
        sh.ShellExecuteW.restype = ctypes.c_ssize_t
        return sh.ShellExecuteW(None, 'runas', path, None,
                                os.path.dirname(path), 1)
    except Exception:
        return 0


def log(msg):
    line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        limit = LOG_MAX_KB * 1024
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > limit:
            old = LOG_PATH + '.1'
            if os.path.exists(old):
                os.remove(old)
            os.replace(LOG_PATH, old)
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


LOG_MAX_KB = 512
MUTEX_HANDLE = None


def acquire_single_instance():
    """已有实例在跑就返回 False，静默退出。"""
    global MUTEX_HANDLE
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    MUTEX_HANDLE = kernel.CreateMutexW(None, False, 'Local\\WBOXSingleton')
    return ctypes.get_last_error() != 183


class Guard(object):
    """内核守护：进程快照 / 会话状态机 / 配置热加载 / 主循环。

    功能动作全部在 plugins 目录里（afterburner / ace / popup），本类只负责四件事：
      1. 每轮取一次进程表，算出「哪些游戏在跑」（`None` 与 `{}` 必须区分开）；
      2. 推进会话状态机 `idle -> active -> grace -> idle`；
      3. 在迁移点分发 `on_session_*` / `on_tick`；
      4. 配置热加载与三段默认值合并（内核 -> 插件 -> 用户）。

    这样删掉任意插件、甚至清空 plugins 目录，主循环都照跑（零插件可跑是硬约束）。
    """

    def __init__(self, host=None):
        self.cfg = dict(DEFAULTS)
        self.cfg_issues = []
        self._cfg_issue_sig = None
        self.session_seq = 0
        self.cfg_loaded = False
        self.cfg_mtime = None
        self.watch = set()
        self.last_scan = 0.0
        # ---- 会话状态机（跨插件共享的唯一时间轴，插件只读）----
        self.session_state = plugin_api.STATE_IDLE
        self.session_started_at = 0.0
        self.grace_until = 0.0
        self.session_resumed = False
        self.last_games = []
        self.tick_seq = 0
        self.close_retries = 0        # ctx.retry_close 累计次数（自检/日志用）
        self._retry_requested = False # 本轮 on_session_close 里插件是否要求重试
        self._close_dispatch = False  # 是否正在分发 on_session_close（retry_close 的闸门）
        self._tick_now = 0.0
        self._tick_procs = None
        self.path_cache = {}
        self.lib = {'games': [], 'sources': {}}
        self.lib_exes = set()       # 游戏库识别出的主 exe 名（小写）
        self.lib_dirs = []          # 游戏库识别出的安装目录（小写，按长度倒序）
        self.lib_scan_at = 0.0
        self.running = True
        # v2.5.0-③：进程事件唤醒器（proc_events.ProcEventWatcher）。默认 None =
        # 纯轮询；main() 里 attach 成功才挂上。挂上后 run() 的一轮等待从
        # sleep(delay) 换成「等唤醒事件或超时」，判定逻辑不变。
        self.ev = None
        # 进程表快照失败计数：连续 3 次才记日志并降级，1~2 次抖动只跳过本轮
        self.snap_fails = 0
        self.snap_noted = False     # tick 降级日志只打一次（成功一轮后复位）
        self.last_procs = {}        # 上一轮成功取到的进程表（快照持续失败时降级用）
        self.last_tick_at = None    # 上一轮 tick 的墙钟，用于检测系统时间跳变
        self._cleaned = False
        # ---- 插件宿主 ----
        # log 用 lambda 转发到模块级 log()：这样测试里替换 guard_core.log
        # 依然能捕获插件日志，不必重建宿主。
        # host 参数是离线测试接缝（注入假插件），生产路径一律 None。
        if host is None:
            host = plugin_host.PluginHost(
                BASE, lambda m: log(m), cfg=self.cfg, plugins_dir=PLUGINS_DIR)
            self.host = host
            self._install_providers()
            # 构造期只发现 + import + on_load，不写 plugins_state.json
            # （面板/测试里 new 一个 Guard 不该产生运行期文件）
            host.load_all(None, write_state=False)
        else:
            self.host = host
            self._install_providers()
            host.cfg = self.cfg

    # ---------- 对外只读视图 ----------
    @property
    def session(self):
        """v1 的 `session` 布尔：会话是否进行中（含宽限期）。只读，避免插件/旧脚本写坏。"""
        return self.session_state != plugin_api.STATE_IDLE

    def session_view(self, games=None):
        """给插件看的会话快照（只读，每次新建一个）。"""
        state = self.session_state
        return plugin_api.SessionView(
            state=state,
            games=self.last_games if games is None else games,
            started_at=self.session_started_at if state != plugin_api.STATE_IDLE else 0.0,
            grace_until=self.grace_until if state == plugin_api.STATE_GRACE else 0.0,
            resumed=self.session_resumed)

    def make_tick(self, now, procs, names, games):
        """本轮数据包：procs/names/games 都是本轮唯一的一份，插件只读。"""
        return plugin_api.Tick(seq=self.tick_seq, now=now, procs=procs, names=names,
                               games=games, session=self.session_view(games))

    def _install_providers(self):
        """把内核能力注入宿主，插件通过 ctx.xxx() 间接调用（插件拿不到 Guard）。

        这里全部用属性查找而不是提前绑定函数对象：测试替换
        `guard.read_procs` / `guard.games_present` 之后插件立刻跟着变。
        """
        self.host.providers.update({
            'procs': lambda: self._tick_procs,
            'snapshot': snapshot,
            'process_path': self.cached_path,
            'refresh_paths': self.refresh_paths,
            'games_present': self.games_present,
            'watch_names': lambda: set(self.watch),
            'session': lambda: self.session_view(self.last_games),
            'now': lambda: self._tick_now or time.time(),
            'retry_close': self.retry_close,
        })

    def cached_path(self, pid):
        """复用内核 path_cache 取进程 exe 路径（缓存没有才现查）。"""
        hit = self.path_cache.get(pid)
        return hit[1] if hit else process_path(pid)

    # ---------- 配置 ----------
    def scan_roots(self):
        """扫描游戏库，把游戏主 exe 名加进监控集合。"""
        global LOG_MAX_KB
        found = set()
        cfg = self.cfg
        dir_kw = [k.lower() for k in cfg.get('exclude_dir_keywords', [])]
        name_kw = [k.lower() for k in cfg.get('exclude_name_keywords', [])]
        min_size = float(cfg.get('min_game_exe_mb', 0.2)) * 1048576
        max_depth = int(cfg.get('max_scan_depth', 5))
        for root in cfg.get('game_roots', []):
            if not root or not os.path.isdir(root):
                continue
            cut = len(root)
            for dp, dn, fn in os.walk(root):
                rel = dp[cut:].strip('\\/')
                depth = rel.count(os.sep) + 1 if rel else 0
                if depth >= max_depth:
                    dn[:] = []
                    continue
                dn[:] = [d for d in dn if any(k in d.lower() for k in dir_kw)]
                for name in fn:
                    if not name.lower().endswith('.exe'):
                        continue
                    low = name.lower()
                    if any(k in low for k in name_kw):
                        continue
                    full = os.path.join(dp, name)
                    try:
                        if os.path.getsize(full) < min_size:
                            continue
                    except OSError:
                        continue
                    found.add(low)
        self.last_scan = time.time()
        return found

    def refresh_paths(self, procs):
        """缓存 pid -> (进程名, exe 路径)，只对新增/复用的 pid 查一次。"""
        cache = self.path_cache
        for pid in list(cache):
            if pid not in procs:
                del cache[pid]
        for pid, name in procs.items():
            hit = cache.get(pid)
            if hit is None or hit[0] != name:
                cache[pid] = (name, process_path(pid))

    def path_is_game(self, name, path):
        """按安装路径兜底：装在游戏库里、且不是平台/反作弊/工具的 exe 都算游戏。"""
        if not path:
            return False
        low = path.lower()
        for kw in self.cfg.get('exclude_dir_keywords', []):
            if kw.lower() in low:
                return False
        low_name = name.lower()
        for kw in self.cfg.get('exclude_name_keywords', []):
            if kw.lower() in low_name:
                return False
        # 1) 平台清单识别出的游戏目录（Steam/Epic/WeGame/GOG/Ubisoft）
        for d in self.lib_dirs:
            if low.startswith(d + '\\'):
                return True
        # 2) 手填的游戏库根目录（装在哪都能兜住）
        for root in self.cfg.get('game_roots', []):
            if not root:
                continue
            if low.startswith(root.lower().rstrip('\\/') + '\\'):
                return True
        return False

    def reload_config(self):
        """配置热加载：mtime 变了才做。

        三段合并（文档 4.3）：内核默认值 -> 各插件 `DEFAULT_CONFIG` -> 用户 JSON。
        插件对象一律复用，**不重建宿主**——重建会丢掉 ACE 排队、小飞机所有权等
        运行期状态，等于中途把功能重启一次。
        """
        try:
            mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            mtime = None
        if self.cfg_loaded and mtime == self.cfg_mtime:
            return
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8-sig') as f:
                data = json.load(f)
        except Exception as e:
            if self.cfg_loaded:
                log('配置读取失败，沿用上次配置: %r' % (e,))
                self.cfg_mtime = mtime
                return
            # 首次就读不到（config.json 还没写出来）时不能什么都不配：那样所有插件
            # 都收不到 on_config，三个功能会静默全关，比 v1「按默认值跑」更糟。
            log('配置读取失败，先按内核默认值运行: %r' % (e,))
            data = {}
        if not isinstance(data, dict):
            log('配置格式不对（顶层必须是对象），沿用上次配置')
            self.cfg_mtime = mtime
            return
        prev_loaded = self.cfg_loaded
        prev_lib = self.cfg.get('game_lib')
        prev_roots = self.cfg.get('game_roots')
        # ① 段级迁移（ace_guard -> ace_cleanup 这类）必须在合并默认值之前跑。
        # v2.13.0：迁移前留快照，迁移后比对并备份原文件 —— 升级后"设置看着
        # 保存了但行为没变"时，能直接看到哪几个键被搬走、原件在哪。
        data = self._migrate_with_record(data)
        # ② 三段合并；merge_defaults 是纯函数，不会改 DEFAULTS 也不会改 data
        cfg = plugin_host.merge_defaults(DEFAULTS, self.host.registry, data)
        # v2.13.0：统一校验（类型/范围/枚举/列表元素）。修正能修正的，回退
        # 修正不了的，并把每一条 {字段, 原值, 回退值, 原因} 写进日志与
        # config_issues.json —— 面板/自检/诊断包都从这里取。
        base_defaults = plugin_host.merge_defaults(DEFAULTS, self.host.registry, {})
        cfg, self.cfg_issues = config_schema.validate(
            cfg, defaults=base_defaults, entries=self.host.registry)
        self._note_config_issues(self.cfg_issues, write_state=True)
        self.cfg = cfg
        self.host.cfg = cfg
        self.cfg_mtime = mtime
        self.cfg_loaded = True
        global LOG_MAX_KB
        LOG_MAX_KB = int(cfg.get('log_max_kb', 512))
        # 平台开关或游戏库根改了就必须真重扫，不能继续吃 6 小时缓存
        lib_changed = prev_loaded and (
            prev_lib != cfg.get('game_lib') or prev_roots != cfg.get('game_roots'))
        self.refresh_lib(force=bool(lib_changed))
        auto = self.scan_roots()
        watch = set(n.lower() for n in cfg.get('game_processes', []))
        watch |= auto
        watch |= self.lib_exes
        watch -= set(n.lower() for n in cfg.get('exclude_processes', []))
        self.watch = watch
        events.emit('config', code='CONFIG_LOADED', plugins=len(self.host.plugins),
                    msg='监控 %d 个游戏进程名' % len(watch))
        log('配置已加载：手工名单 %d 项，目录发现 %d 项，游戏库 %d 个，合计监控 %d 个游戏进程名'
            % (len(cfg.get('game_processes', [])), len(auto),
               len(self.lib.get('games', [])), len(watch)))
        # ③ 通知插件；每个插件会收到自己那份合并结果（宿主负责切分）
        self.host.dispatch('on_config', cfg)

    # ------------------------------------------------------ 配置迁移记录
    @staticmethod
    def _flat_diff(before, after, prefix=''):
        """把两层 dict 压成点路径差异：返回 (added, removed, changed)。"""
        added, removed, changed = [], [], []
        if not isinstance(before, dict):
            before = {}
        if not isinstance(after, dict):
            after = {}
        for key in after:
            path = '%s.%s' % (prefix, key) if prefix else key
            if key not in before:
                added.append(path)
            elif isinstance(after[key], dict) and isinstance(before[key], dict):
                a, r, c = Guard._flat_diff(before[key], after[key], path)
                added.extend(a)
                removed.extend(r)
                changed.extend(c)
            elif after[key] != before[key]:
                changed.append(path)
        for key in before:
            if key not in after:
                removed.append('%s.%s' % (prefix, key) if prefix else key)
        return added, removed, changed

    def _migrate_with_record(self, data):
        """执行插件迁移并留下可回溯记录（备份 + 差异 + 恢复路径）。"""
        if not isinstance(data, dict):
            return data
        before = copy.deepcopy(data)
        try:
            self.host.dispatch('migrate', data)
        except Exception as e:      # dispatch 本身吞异常；这里兜底防御
            log('配置迁移异常，沿用迁移前的配置：%r' % (e,))
            return before
        if not isinstance(data, dict):
            log('配置迁移把顶层改坏了，沿用迁移前的配置')
            return before
        if data == before:
            return data
        added, removed, changed = self._flat_diff(before, data)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup = ''
        try:
            if os.path.isfile(CONFIG_PATH):
                backup = os.path.join(
                    BASE, 'config.json.bak-%s-%s' % (core_defaults.VERSION, stamp))
                shutil.copy2(CONFIG_PATH, backup)
        except OSError as e:
            log('迁移前备份 config.json 失败：%r' % (e,))
        log('配置迁移完成：新增 %d 项、删除 %d 项、改写 %d 项%s'
            % (len(added), len(removed), len(changed),
               ('；原文件已备份为 %s' % os.path.basename(backup)) if backup else
               '；原文件备份失败，回退请手动恢复'))
        if removed:
            log('  迁移删除的旧键：%s' % ', '.join(removed[:12]))
        if changed:
            log('  迁移改写的键：%s' % ', '.join(changed[:12]))
        events.emit('config', code='CONFIG_MIGRATED', added=len(added),
                    removed=len(removed), changed=len(changed),
                    msg=', '.join((added + changed)[:6]))
        self._record_migration({'at': stamp, 'version': core_defaults.VERSION,
                                'backup': backup, 'added': added,
                                'removed': removed, 'changed': changed})
        return data

    def _record_migration(self, record):
        """把迁移记录追加进 config_migrations.json（保留最近 10 次）。"""
        path = os.path.join(BASE, 'config_migrations.json')
        items = []
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get('items'), list):
                items = raw['items']
        except (OSError, ValueError):
            items = []
        items.append(record)
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'version': core_defaults.VERSION,
                           'items': items[-10:]}, f, ensure_ascii=False, indent=1)
        except OSError as e:
            log('写 config_migrations.json 失败：%r' % (e,))

    def _note_config_issues(self, issues, write_state=False):
        """配置校验结果：日志去重 + 落盘（config_issues.json）。

        同一批问题只记一次，避免每次热加载都刷同样的行；问题消失时记一行
        「已恢复正常」并清空状态文件。
        """
        sig = config_schema.signature(issues)
        if sig == getattr(self, '_cfg_issue_sig', None):
            return
        changed = sig != getattr(self, '_cfg_issue_sig', None)
        self._cfg_issue_sig = sig
        if not issues:
            if changed:
                log('配置校验：所有字段有效')
            return
        log('配置校验发现 %d 处问题，已按下面采用值继续运行：%s'
            % (len(issues), config_schema.describe(issues)))
        events.emit('config', code='CONFIG_INVALID', count=len(issues),
                    msg=config_schema.describe(issues, limit=3))
        if write_state:
            try:
                with open(os.path.join(BASE, 'config_issues.json'), 'w',
                          encoding='utf-8') as f:
                    json.dump({'version': core_defaults.VERSION,
                               'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                               'items': issues}, f, ensure_ascii=False, indent=1)
            except OSError as e:
                log('写 config_issues.json 失败：%r' % (e,))

    def refresh_lib(self, force=False):
        """刷新游戏库识别结果（默认用 6 小时缓存；force=True 强制重扫）。"""
        gl = self.cfg.get('game_lib') or {}
        if not gl.get('enabled', True):
            self.lib_exes, self.lib_dirs = set(), []
            self.lib = {'games': [], 'sources': {}}
            return
        try:
            self.lib = game_lib.load(self.cfg, BASE, log, force=force)
        except Exception as e:
            log('游戏库识别失败: %r' % (e,))
            self.lib = {'games': [], 'sources': {}}
        self.lib_scan_at = time.time()
        excl = set(n.lower() for n in self.cfg.get('exclude_processes', []))
        name_kw = [k.lower() for k in self.cfg.get('exclude_name_keywords', [])]
        exes, dirs = set(), []
        for g in self.lib.get('games', []):
            exe = g.get('exe')
            if not exe:
                continue
            base = os.path.basename(exe).lower()
            if base in excl or any(k in base for k in name_kw):
                continue
            exes.add(base)
            for d in (os.path.dirname(exe), g.get('dir')):
                if d:
                    dirs.append(d.lower().rstrip('\\/'))
        self.lib_exes = exes
        self.lib_dirs = sorted(set(dirs), key=len, reverse=True)

    # ---------- 进程 / 游戏判定 ----------
    def check_clock_jump(self, now):
        """检测系统时间被手工改过（回拨或前拨）。

        所有闸门（宽限 / 冷却 / 排队上限）都拿墙钟做基准，因为要和 config 里
        持久化的时间戳比较，没法换成 monotonic。墙钟跳变会让间隔计算失真：
        前拨会让宽限期瞬间"到期"，回拨会让冷却期被无限拉长。这里只检测并告警，
        方便事后从日志判断某次异常行为是不是时间被改导致的。
        """
        prev = self.last_tick_at
        self.last_tick_at = now
        if prev is None:
            return
        gap = now - prev
        if gap < -5 or gap > 300:
            log('检测到系统时间跳变：本轮间隔 %+.0f 秒（正常 0.4~2 秒），'
                'ACE 宽限与冷却基准可能失真；闸门会按夹取后的时间继续判断' % gap)

    def read_procs(self):
        """取进程表：成功返回 dict（并清零失败计数），失败返回 None。

        调用方拿到 None 必须直接放弃本轮判定，保持上一轮的会话 / 宽限 / ACE 排队
        状态不变——把"没读到"当成"没有进程"会直接导致误杀。
        """
        procs = snapshot()
        if procs is None:
            self.snap_fails += 1
            return None
        self.snap_fails = 0
        self.snap_noted = False
        self.last_procs = dict(procs)
        return procs

    def games_present(self, procs=None):
        """关闭流程里复核：游戏是否已回来（白名单 + 路径兜底）。

        procs 可传入本轮已取到的进程表，省一次快照。取不到进程表时返回 True
        （"无法确认游戏已退出"），保证任何快照抖动都不会触发关闭动作。
        """
        if procs is None:
            procs = snapshot()
            if procs is None:
                return True
        names = set(procs.values())
        if names & self.watch:
            return True
        for pid, name in procs.items():
            if name in self.watch:
                continue
            hit = self.path_cache.get(pid)
            if hit and hit[1] and self.path_is_game(name, hit[1]):
                return True
        return False

    def games_running(self, procs, names=None):
        """本轮在跑的游戏名（白名单命中 + 路径兜底），小写有序。"""
        names = set(procs.values()) if names is None else names
        games = sorted(names & self.watch)
        for pid, name in procs.items():
            if name in self.watch:
                continue
            hit = self.path_cache.get(pid)
            if hit and hit[1] and self.path_is_game(name, hit[1]) and name not in games:
                games.append(name)
        return sorted(games)

    # ---------- 会话状态机 ----------
    def retry_close(self, delay=2.0):
        """`ctx.retry_close()` 的内核实现：把"即将 idle"拉回 grace。

        v1 的 close_owned() 在取不到进程表时把 pending_stop 回退 2 秒
        （guard_core.py:867），否则快照抖一下小飞机就再也关不掉了。
        只在 on_session_close 分发期间有效，其它钩子调用一律忽略。
        """
        if not self._close_dispatch:
            return False
        try:
            delay = max(0.2, float(delay))
        except (TypeError, ValueError):
            delay = 2.0
        self.session_state = plugin_api.STATE_GRACE
        # 用本轮的 tick 时刻做基准（同一轮内恒定，离线测试才能断言），没有则退回墙钟
        self.grace_until = (self._tick_now or time.time()) + delay
        self.close_retries += 1
        self._retry_requested = True
        return True

    def advance_session(self, now, procs, names, games):
        """推进会话状态机并分发本轮的会话钩子。

        v1 的 `pending_stop` 三处语义全部映射到这里：
          - 会话开始 → 清空（state 变 active）
          - 宽限到期 → 触发关闭（on_session_close）
          - close_owned 失败 → 回退 2 秒（插件调 ctx.retry_close）
        """
        if games:
            hook = None
            if self.session_state == plugin_api.STATE_IDLE:
                self.session_state = plugin_api.STATE_ACTIVE
                self.session_started_at = now
                self.session_resumed = False
                self.session_seq += 1
                log('检测到游戏: %s' % ', '.join(games))
                events.emit('session', code='SESSION_START',
                            session=self.session_seq, msg=', '.join(games))
                hook = 'on_session_start'
            elif self.session_state == plugin_api.STATE_GRACE:
                # Unity 等游戏启动时会"进程闪断"：只要还在宽限期内就当同一会话继续，
                # 不重开会话、不重置所有权/基线。
                self.session_state = plugin_api.STATE_ACTIVE
                self.grace_until = 0.0
                self.session_resumed = True
                events.emit('session', code='SESSION_RESUME',
                            session=self.session_seq, msg=', '.join(games))
                hook = 'on_session_resume'
            self.last_games = games
            if hook:
                self.host.dispatch(hook, self.make_tick(now, procs, names, games))
            return
        # ---- 没有游戏在跑 ----
        if self.session_state == plugin_api.STATE_ACTIVE:
            grace = int(self.cfg.get('exit_grace_sec', 8))
            self.session_state = plugin_api.STATE_GRACE
            self.grace_until = now + grace
            self.last_games = []
            log('游戏已全部退出，%d 秒后关闭小飞机' % grace)
            events.emit('session', code='SESSION_GRACE',
                        session=self.session_seq, grace_sec=grace)
            self.host.dispatch('on_session_grace',
                               self.make_tick(now, procs, names, games))
        if self.session_state == plugin_api.STATE_GRACE and now >= self.grace_until:
            self._retry_requested = False
            self._close_dispatch = True
            try:
                self.host.dispatch('on_session_close',
                                   self.make_tick(now, procs, names, games))
            finally:
                self._close_dispatch = False
            if self._retry_requested:
                # 插件要求重试（例如取不到进程表）：保持 grace，grace_until 已由
                # retry_close 推到 now+delay，下一轮再发一次 on_session_close。
                log('关闭流程要求 %.0f 秒后重试（累计 %d 次）'
                    % (max(0.2, self.grace_until - now), self.close_retries))
            else:
                self.session_state = plugin_api.STATE_IDLE
                self.grace_until = 0.0
                self.session_resumed = False
                events.emit('session', code='SESSION_CLOSE',
                            session=self.session_seq,
                            retries=self.close_retries)

    # ---------- 主循环 ----------
    def tick(self, now=None):
        """一轮：配置 -> 快照 -> 会话迁移 -> on_tick（顺序见文档 3.4）。"""
        self.reload_config()
        now = time.time() if now is None else float(now)
        self._tick_now = now
        self.check_clock_jump(now)
        gl = self.cfg.get('game_lib') or {}
        flag = os.path.join(BASE, 'rescan_games.flag')
        if os.path.exists(flag):
            try:
                os.remove(flag)
            except OSError:
                pass
            log('收到重扫游戏库请求')
            self.refresh_lib(force=True)
        if gl.get('enabled', True) and \
                now - self.lib_scan_at > float(gl.get('rescan_hours', 6)) * 3600:
            self.refresh_lib(force=True)
        if now - self.last_scan > float(self.cfg.get('rescan_roots_sec', 300)):
            auto = self.scan_roots()
            self.watch = (set(n.lower() for n in self.cfg.get('game_processes', [])) |
                          auto | self.lib_exes)
            self.watch -= set(n.lower() for n in self.cfg.get('exclude_processes', []))
            self.path_cache.clear()

        procs = self.read_procs()
        if procs is None:
            # 1~2 次快照抖动：只跳过本轮，会话 / 宽限 / ACE 排队状态原样保留。
            # 连续 3 次失败才记一行日志并降级（沿用上一轮结果），避免日志刷屏。
            if self.snap_fails < 3:
                return
            if not self.snap_noted:
                log('连续 %d 次取进程表失败，本轮沿用上一轮结果降级处理' % self.snap_fails)
                self.snap_noted = True
            # 降级也不能用空表：空表等于"游戏全退了"。沿用上一轮的成功结果，
            # 会话状态自然保持不变；真要关闭时插件会重新取表并再次放弃。
            procs = dict(self.last_procs)
        names = set(procs.values())
        self._tick_procs = procs
        self.refresh_paths(procs)
        games = self.games_running(procs, names)
        self.tick_seq += 1
        # 会话钩子（本轮最多 1 个，grace 到期那一轮之后再补 close）
        self.advance_session(now, procs, names, games)
        # on_tick 必然最后调用，且拿到本轮最终的会话状态
        self.host.dispatch('on_tick', self.make_tick(now, procs, names, games))

    def reload_plugins(self):
        """面板装/卸插件后的热重载：只换插件宿主，会话状态机保持不动。

        不重启守护进程：重启会丢掉正在跑的会话状态（ACE 限速 / 弹窗监视）。
        这里重建注册表、重新三段合并配置、给新插件发 on_config；会话中途装上的
        插件从下一局开始接管（on_session_start 每个会话只发一次）。
        on_start 不重发：它只负责「守护冷启动」那一轮的残留清理。
        """
        log('[plugin] 收到插件变更，开始热重载')
        try:
            self.host.dispatch('on_exit', 'reload')
        except BaseException as e:
            log('插件退出钩子异常: %r' % (e,))
        new_host = plugin_host.PluginHost(
            BASE, lambda m: log(m), cfg=self.cfg, plugins_dir=PLUGINS_DIR)
        self.host = new_host
        self._install_providers()
        new_host.load_all(None, write_state=True)
        # 配置必须重新合并：新插件的 DEFAULT_CONFIG 要进 cfg，也要给它发 on_config
        self.cfg_mtime = None
        self.reload_config()
        log('[plugin] 插件热重载完成，共 %d 个（会话 %s）'
            % (len(new_host.plugins), self.session_state))

    def cleanup(self):
        """退出清理：先让插件收尾（还原 ACE 优先级等），再删 pid / stop 标记。

        atexit 与 run() 的 finally 都会走到这里，用 _cleaned 保证只做一次
        （插件退出钩子不该被执行两遍）。
        """
        if not self._cleaned:
            self._cleaned = True
            try:
                self.host.dispatch('on_exit', 'guard_stop')
            except BaseException as e:
                log('插件退出钩子异常: %r' % (e,))
        try:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
        except Exception:
            pass
        try:
            if os.path.exists(STOP_PATH):
                os.remove(STOP_PATH)
        except Exception:
            pass
        # v2.5.0：正常退出时清掉 0 字节的 crash 占位文件——活着的日子不留
        # guard_crash.log（真崩过的文件有内容，原样保留）。
        try:
            import crash_log
            crash_log.clear_if_empty()
        except Exception:
            pass

    def poll_delay(self, busy=None):
        """双速节拍：空闲 2s，游戏会话 / 插件登记的监视期 0.4s。

        busy 显式传入时只看它（离线测试断言用）；不传则由
        「会话非 idle」或「有插件登记了快节拍」决定。
        """
        if busy is None:
            # 用本轮 tick 的时刻比（生产里就等于墙钟，测试里注入 now 才可比）
            busy = (self.session_state != plugin_api.STATE_IDLE
                    or self.host.fast_poll_active(self._tick_now or None))
        idle = max(0.5, float(self.cfg.get('poll_interval_sec', 2)))
        fast = max(0.2, min(float(self.cfg.get('poll_fast_sec', 0.4)), idle))
        return fast if busy else idle

    def wait_delay(self):
        """v2.5.0-③：一轮之间的实际等待秒数。

        事件通道健康（self.ev.ok）时轮询降为低频兜底：空闲 2s -> 30s、
        会话中 0.4s -> 2.0s（游戏启动/退出都有进程事件即时兜住）；
        插件登记的快节拍期间绝不放宽（弹窗拦截的功能时延）。
        事件通道不可用时恒等于 poll_delay()，即 v2.4 的纯轮询行为。
        """
        ev = self.ev
        if ev is None or not getattr(ev, 'ok', False):
            return self.poll_delay()
        if self.host.fast_poll_active(self._tick_now or None):
            return self.poll_delay()
        if self.session_state != plugin_api.STATE_IDLE:
            return max(EVENT_FAST_MIN, self.poll_delay(True) * EVENT_FAST_FACTOR)
        return max(EVENT_IDLE_MIN, self.poll_delay(False) * EVENT_IDLE_FACTOR)

    def _sleep_round(self, delay):
        """一轮之间的等待：事件健康时等「唤醒事件或超时」，否则纯 sleep。

        用自动复位事件：一次 WaitForSingleObject 消费掉一批事件唤醒，
        之后立刻跑一轮完整判定（判定逻辑零改动，只改「什么时候跑一轮」）。
        """
        ev = self.ev
        if ev is not None and getattr(ev, 'ok', False):
            try:
                import proc_events
                if proc_events.wait_for_wake(ev.wake, delay * 1000.0):
                    return
                return                  # 超时 = 低频兜底轮询，同样去跑一轮
            except Exception:
                pass
        time.sleep(delay)

    def _watch_wakeups(self):
        """低频兜底期间的 1 秒看门狗（仅事件健康时启动）。

        为什么要它：等待放宽到 30s 后，stop.flag / 插件热重载标记 /
        config.json 变更最慢 30s 才被主循环看见——「停止守护」「改动立即生效」
        不能倒退成"半分钟才生效"。看门狗只做 stat + SetEvent，把主循环叫醒，
        判定仍由主循环那一轮 tick 完成。1 次/秒的 stat 成本可忽略。
        """
        try:
            import proc_events
        except Exception:
            return
        last_mtime = None
        try:
            last_mtime = os.path.getmtime(CONFIG_PATH)
        except OSError:
            pass
        while self.running:
            try:
                if os.path.exists(STOP_PATH) or os.path.exists(RELOAD_FLAG_PATH):
                    proc_events.poke(self.ev)
                mtime = os.path.getmtime(CONFIG_PATH)
                if mtime != last_mtime:
                    last_mtime = mtime
                    proc_events.poke(self.ev)
            except OSError:
                pass
            except Exception:
                pass
            time.sleep(1.0)

    def run(self):
        try:
            if os.path.exists(STOP_PATH):
                os.remove(STOP_PATH)   # 上次的停止标记，防止新实例秒退
        except Exception:
            pass
        try:
            with open(PID_PATH, 'w', encoding='ascii') as f:
                f.write(str(os.getpid()))
        except Exception as e:
            log('写 pid 文件失败: %r' % (e,))
        atexit.register(self.cleanup)
        self.reload_config()
        log('守护已启动 (pid %d)，轮询 空闲 %s 秒 / 游戏会话 %s 秒' %
            (os.getpid(), self.cfg.get('poll_interval_sec', 2),
             self.cfg.get('poll_fast_sec', 0.4)))
        # 插件启动钩子。弹窗的"启动清理"原来就在这里（v1 1266-1272），
        # 现在由插件自己在 on_start 里决定做什么。
        try:
            self.host.dispatch('on_start')
        except BaseException as e:
            log('插件启动钩子异常: %r' % (e,))
        log('SeDebugPrivilege: %s（ACE 限速需要它，否则改优先级会被拒）' %
            ('已启用' if enable_debug_privilege() else '不可用'))
        # v2.5.0-③：低频兜底激活时起 1 秒看门狗（stop.flag / 热重载标记 /
        # config 变更立即叫醒主循环），保住「停止守护 / 改动立即生效」的时延。
        # v2.11.3 修正启动条件：订阅在后台线程建立，这里 ev.ok 一定是 False，
        # 原来的 `ev is not None and ev.ok` 让看门狗永远起不来——等退避重订阅
        # 成功、等待放宽到 30s 之后，"停止守护"就要等半分钟。看门狗只做
        # stat + SetEvent，与订阅状态无关，挂着 ev 就该起。
        if self.ev is not None:
            threading.Thread(target=self._watch_wakeups,
                             name='flag-watchdog', daemon=True).start()
        try:
            while self.running:
                if os.path.exists(STOP_PATH):
                    log('收到停止请求，守护退出')
                    break
                # 面板「＋ / 垃圾桶」写下的插件变更标记。游戏会话中不换插件：
                # 正在跑的 ACE 限速 / 弹窗监视会被打断，等本局结束再重载。
                if os.path.exists(RELOAD_FLAG_PATH):
                    if self.session_state != plugin_api.STATE_IDLE:
                        if not getattr(self, '_reload_noted', False):
                            self._reload_noted = True
                            log('[plugin] 插件变更已登记，等本局结束再热重载')
                    else:
                        self._reload_noted = False
                        try:
                            os.remove(RELOAD_FLAG_PATH)
                        except OSError:
                            pass
                        try:
                            self.reload_plugins()
                        except Exception as e:
                            log('插件热重载失败: %r' % (e,))
                try:
                    self.tick()
                except Exception as e:
                    log('本轮异常: %r' % (e,))
                # v2.5.0-③：等待改走「事件唤醒或低频兜底」。双速节拍的口径
                # 在 wait_delay() 里（事件健康时放宽，见 EVENT_*_FACTOR 注释）；
                # 会话推进 / 闸门判定 / 插件 tick 语义零改动。
                self._sleep_round(self.wait_delay())
        finally:
            self.cleanup()
            log('守护已退出')


def self_test():
    # 自检自己也会 load 配置、写日志；别把「配置已加载」这类行混进运行日志，
    # 否则事后看 guard.log 分不清是守护重启还是自检跑过一次。
    global LOG_PATH
    LOG_PATH = os.path.join(BASE, 'self_test_run.log')
    try:
        os.remove(LOG_PATH)
    except OSError:
        pass
    g = Guard()
    g.reload_config()
    print('config keys   :', len(g.cfg))
    print('plugins dir   :', PLUGINS_DIR)
    print('watch count   :', len(g.watch))
    print('watch names   :')
    for n in sorted(g.watch):
        print('   ', n)
    procs = snapshot()
    if procs is None:
        # 自检同样要区分"读不到"与"没有进程"，否则会打印出一份误导性的空表
        print('processes     : snapshot 失败（取不到进程表，跳过进程相关自检项）')
        procs = {}
    g.refresh_paths(procs)
    bypath = sorted(set(n for pid, n in procs.items()
                        if n not in g.watch and g.path_cache.get(pid)
                        and g.path_is_game(n, g.path_cache[pid][1])))
    print('processes     :', len(procs))
    print('by-path games :', bypath)
    print('games running :', sorted(set(procs.values()) & g.watch | set(bypath)))
    # 各插件自己的诊断行（小飞机/RTSS、ACE 两道、弹窗）
    lines = []
    try:
        g.host.dispatch('on_self_test', lines)
    except BaseException as e:
        lines.append('插件自检异常: %r' % (e,))
    for line in lines:
        print(line)
    print('game lib      :', game_lib.summary(g.lib))
    for ginfo in (g.lib.get('games') or [])[:12]:
        print('   lib game   :', ginfo['name'], '->', ginfo['exe'])
    try:
        print('elevated      :', bool(ctypes.windll.shell32.IsUserAnAdmin()))
    except Exception as e:
        print('elevated      : unknown', e)
    # 插件表：加载状态一眼可见（bad_manifest / too_new / degraded / conflict）
    print('plugin table  : %d 个目录，%d 个已加载'
          % (len(g.host.registry), len(g.host.plugins)))
    print('   %-12s %-8s %-12s %8s %6s' % ('id', 'version', 'status', 'ticks', 'errors'))
    for entry in g.host.registry:
        print('   %-12s %-8s %-12s %8d %6d'
              % (entry.id or '(no-id)', entry.version or '-', entry.status,
                 entry.ticks, entry.error_count))
    for err in g.host.errors():
        print('   ! %-10s %s' % (err['id'] or '(no-id)', err['detail'] or err['last_error']))


def _beat_payload(g):
    """心跳管道的一行状态（协议见 guard_beat 模块头）。任何失败都降级为
    少字段而不是抛异常——provider 抛了，服务端那轮就不发内容。

    v2.11.3 起区分「进程在」与「全功能在」：
      events  = 进程事件通道（mode=event/poll + 失败时的 HRESULT 诊断）
      health  = 降级摘要（面板 / 自检直接照着说，不用自己推断）
    两者都是只增字段，旧面板读到多余字段会忽略。
    """
    plugins = []
    try:
        for entry in g.host.registry:
            plugins.append({
                'id': entry.id, 'status': entry.status, 'ticks': entry.ticks,
                'last_tick': round(getattr(entry, 'last_tick_at', 0.0), 3)})
    except Exception:
        plugins = []

    events = {'ok': False, 'mode': 'poll', 'wakes': 0, 'resubscribes': 0,
              'hint': '进程事件监听未能启动，当前使用轮询'}
    ev = getattr(g, 'ev', None)
    if ev is not None:
        try:
            st = ev.status()
            if isinstance(st, dict):
                events = {
                    'ok': bool(st.get('ok')),
                    'mode': st.get('mode') or 'poll',
                    'wakes': int(st.get('wakes') or 0),
                    'resubscribes': int(st.get('resubscribes') or 0),
                    'hint': str(st.get('hint') or ''),
                }
                for kind in ('start', 'stop'):
                    k = (st.get('kinds') or {}).get(kind) or {}
                    if not k.get('ok'):
                        events[kind] = {'hr': int(k.get('hr') or 0),
                                        'stage': str(k.get('stage') or '')}
        except Exception:
            pass

    reasons = []
    if not events.get('ok'):
        reasons.append(events.get('hint') or '进程事件监听不可用，当前使用轮询')
    for item in plugins:
        if item['status'] not in (plugin_host.STATUS_OK,
                                  plugin_host.STATUS_DEGRADED):
            reasons.append('插件 %s 未加载（%s）'
                           % (item['id'] or '?', item['status']))

    return {
        'pid': os.getpid(),
        'version': core_defaults.VERSION,
        'session': g.session_state,
        'seq': g.tick_seq,
        'started_at': round(getattr(g, 'session_started_at', 0.0), 3),
        'plugins': plugins,
        'events': events,
        'health': {'degraded': bool(reasons), 'reasons': reasons[:4]},
    }


def _recover_plugins():
    """收尾上一次被打断的插件安装（v2.11.3）。

    守护是开机后第一个起来的进程，也是"换入过程中断电"最可能的下一个执行者：
    这里把备份搬回 / 用暂存补完，幂等。失败只记一行，绝不拖住守护启动。
    """
    try:
        import plugin_package
        rec = plugin_package.recover(PLUGINS_DIR, base=BASE, log=log)
        if rec.get('action') == 'kept':
            log('[plugin-package] 上次安装已完成，清掉残留标记')
    except Exception as e:
        try:
            log('[plugin-package] 安装恢复跳过：%r' % (e,))
        except Exception:
            pass


def _start_beat(g):
    """起心跳管道服务（v2.5.0-②）。失败只记一行日志：心跳坏了守护照样工作。"""
    try:
        import guard_beat
        beat = guard_beat.BeatServer(provider=lambda: _beat_payload(g))
        beat.log = log
        beat.start()
        return beat
    except Exception as e:
        try:
            log('[beat] 心跳服务未能启动: %r' % (e,))
        except Exception:
            pass
        return None


def _start_events(g):
    """v2.5.0-③：进程事件唤醒器。任何失败都回落纯轮询并记日志——
    事件机制绝不能成为「不工作」的新理由。"""
    try:
        import proc_events
        ev = proc_events.attach(lambda: g.watch, log=log)
        if ev is not None:
            g.ev = ev
            # 订阅是后台线程建立的：此刻 ok 还是 False，日志不能说"已启用"。
            # 真正的"已建立/不可用（含 HRESULT）"由 watcher 自己往 guard.log 里记。
            log('[proc-events] 事件通道已挂载：订阅建立后游戏启停即时判定，'
                '轮询降为低频兜底（空闲 %d 倍 / 会话 %d 倍）'
                % (EVENT_IDLE_FACTOR, EVENT_FAST_FACTOR))
        else:
            log('[proc-events] 事件机制不可用，维持纯轮询')
        return ev
    except Exception as e:
        try:
            log('[proc-events] 事件机制未能启动，维持纯轮询: %r' % (e,))
        except Exception:
            pass
        return None


def main():
    """统一入口调用。自检输出由 abg_main 重定向到 self_test.txt。"""
    if '--self-test' in sys.argv:
        self_test()
        return 0
    if not acquire_single_instance():
        return 0
    # 插件目录先收尾再建宿主：Guard() 构造时就会扫描 plugins\ ，
    # 恢复晚一步就会出现"这轮看到空目录、下轮才看到插件"。
    _recover_plugins()
    events.configure(BASE)
    events.emit('startup', code='GUARD_START', pid=os.getpid())
    # v2.13.0：上次安装/卸载被打断时在这里收尾（恢复配置 / 报告不完整），
    # 不阻断启动，只把结论写进日志与事件流。
    try:
        import install_txn
        _txn = install_txn.recover(BASE, log=log)
        if _txn.get('action'):
            log('安装事务收尾：%s' % _txn.get('text'))
            events.emit('install', code='TXN_RECOVER',
                        action=_txn.get('action'),
                        msg=_txn.get('text') or '')
    except Exception as _e:
        log('安装事务收尾失败（忽略）：%r' % (_e,))
    g = Guard()
    # v2.5.0：面板状态行改读心跳管道（连上=活着）。服务挂在 main 而不是
    # Guard.run：离线测试直接调 run() 时不起管道，不抢全局管道名。
    beat = _start_beat(g)
    # v2.5.0-③：事件驱动同理挂在 main。不可用时 g.ev 保持 None（纯轮询）。
    ev = _start_events(g)
    try:
        g.run()
    finally:
        if beat is not None:
            beat.stop()
        if ev is not None:
            ev.stop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
