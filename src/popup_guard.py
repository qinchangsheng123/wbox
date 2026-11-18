# -*- coding: utf-8 -*-
"""游戏退出后的弹窗拦截（腾讯系直播 / 赛事 / 推荐弹窗）。

实测取证样本（2026-11-08 DemoGame一局；盘符、目录、账号已脱敏）：
    进程 : <游戏盘>\\WeGameApps\\DemoGame\\Binaries\\Win64\\clientbrowser2\\ClientBrowser.exe
    命令行: clientbrowser2\\ClientBrowser.exe https://<活动页域名>/cp/a2021xxxxlivetv/index.html <QQ号> 1
    窗口  : 类名 #32770、无标题、853x567、无 WS_EX_TOOLWINDOW
    时序  : 游戏启动那一刻（18:04:09）就拉起并加载直播页，窗口先隐藏；
            游戏退出后 ShowWindow 出来 —— 这就是"退出后弹窗"
    子进程: 6 个 CEF 进程（gpu-process / utility / renderer x4），父 pid 全是它

结论：这类弹窗靠「窗口标题关键词」几乎抓不到（无标题、类名是大路货 #32770）。
      可靠特征是**宿主进程的 exe 名 + 路径片段**，所以主手段放在进程层：
      游戏全部退出时，把命中「process_names ∩ path_hints」的进程连同它的整个
      CEF 子进程树一起结束。窗口层只作兜底和诊断记录。

安全边界（任何一条不满足都不动手）：
  - 只在游戏全部退出后动手（arm 之后），游戏运行期间一律不碰：ClientBrowser
    在游戏里还承担活动页/领奖页，运行中杀掉会影响游戏内嵌页面
  - exe 名和路径片段必须**同时**命中，光有名字（同名 exe 遍地都是）不杀
  - protect_names 里的进程绝不碰；只杀命中规则的那些
  - 只结束进程，不删文件、不改注册表、不动游戏目录
"""
import ctypes
import time
from ctypes import wintypes

u32 = ctypes.WinDLL('user32', use_last_error=True)
k32 = ctypes.WinDLL('kernel32', use_last_error=True)

WM_CLOSE = 0x0010
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

u32.IsWindowVisible.argtypes = [wintypes.HWND]
u32.IsWindowVisible.restype = wintypes.BOOL
u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u32.GetWindowTextLengthW.restype = ctypes.c_int
u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetWindowTextW.restype = ctypes.c_int
u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetClassNameW.restype = ctypes.c_int
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowThreadProcessId.restype = wintypes.DWORD
u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM]
u32.PostMessageW.restype = wintypes.BOOL
u32.IsWindow.argtypes = [wintypes.HWND]
u32.IsWindow.restype = wintypes.BOOL

k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                           wintypes.LPWSTR,
                                           ctypes.POINTER(wintypes.DWORD)]
k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.TerminateProcess.restype = wintypes.BOOL
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.GetProcessTimes.argtypes = ([wintypes.HANDLE] +
                                [ctypes.POINTER(wintypes.FILETIME)] * 4)
k32.GetProcessTimes.restype = wintypes.BOOL

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
u32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
u32.EnumWindows.restype = wintypes.BOOL

# 进程树里可能混进来的系统进程，一律不碰（宁可漏杀，不可误杀）。
# 实测 ping.exe 会带一个 conhost.exe 子进程，就是这个原因加的白名单。
NEVER_KILL = frozenset([
    'system', 'smss.exe', 'csrss.exe', 'wininit.exe', 'services.exe',
    'lsass.exe', 'winlogon.exe', 'fontdrvhost.exe', 'dwm.exe',
    'explorer.exe', 'conhost.exe', 'ctfmon.exe', 'sihost.exe',
])

# 系统外壳自己的窗口，不算"新出现的弹窗"（不进 seen、不记日志、不参与判断）。
# 实测任务栏悬停预览会同时冒出 tooltips_class32 和 TaskListOverlayWnd 两个空标题窗口。
SHELL_NOISE_NAMES = frozenset([
    'explorer.exe', 'dwm.exe', 'sihost.exe', 'ctfmon.exe',
    'searchhost.exe', 'startmenuexperiencehost.exe', 'shellexperiencehost.exe',
    'textinputhost.exe', 'applicationframehost.exe',
    # 用户自己开的浏览器窗口不是游戏弹窗宿主（宿主是 clientbrowser.exe），
    # 计进"新窗口"只会把真实弹窗淹掉。
    'msedge.exe', 'msedgewebview2.exe', 'chrome.exe', 'firefox.exe',
])
SHELL_NOISE_CLASSES = frozenset([
    'progman', 'workerw', 'shell_traywnd', 'shell_secondarytraywnd',
    'tooltips_class32', 'tasklistoverlaywnd', 'windows.ui.core.corewindow',
    'multitaskingviewframe', 'foregroundstaging', 'default ime',
])

# path_hints 是"只处理这类宿主"的硬约束。配置里写成 [] / None 时必须回落到这里，
# 绝不能退化成"只看进程名"——同名 exe 遍地都是（QQ / WeChat / 各种浏览器都有
# 带直播字样的窗口），只按名字动手会直接误杀用户正在用的聊天软件。
# 单一来源仍是 guard_core.POPUP_DEFAULTS['path_hints']；这里另存一份是因为
# guard_core 已经 import popup_guard，反向 import 会形成循环依赖。
DEFAULT_PATH_HINTS = ('\\clientbrowser2\\',)


def process_created(pid):
    """进程创建时间（FILETIME，1601 起的 100ns）；取不到返回 None。"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        c = wintypes.FILETIME()
        e = wintypes.FILETIME()
        kt = wintypes.FILETIME()
        u = wintypes.FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                   ctypes.byref(kt), ctypes.byref(u)):
            return None
        return (c.dwHighDateTime << 32) | c.dwLowDateTime
    finally:
        k32.CloseHandle(h)


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_wchar * 260)]


def enum_windows(include_untitled=False):
    """可见的顶层窗口：[(hwnd, pid, 类名, 标题)]。

    include_untitled=True 时把无标题窗口也算进来 —— CEF 弹窗常常无标题。
    """
    out = []

    def cb(hwnd, _lparam):
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if n <= 0 and not include_untitled:
            return True
        title = ''
        if n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            u32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
        cls = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((int(hwnd), int(pid.value), cls.value, title))
        return True

    u32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def process_path(pid):
    """进程完整路径；取不到返回空串。"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ''
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return ''
        return buf.value
    finally:
        k32.CloseHandle(h)


def enum_procs():
    """[(pid, exe 文件名, 父 pid)]，按系统进程表顺序。"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    rows = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if k32.Process32FirstW(snap, ctypes.byref(e)):
            while True:
                rows.append((int(e.th32ProcessID), e.szExeFile,
                             int(e.th32ParentProcessID)))
                if not k32.Process32NextW(snap, ctypes.byref(e)):
                    break
    finally:
        k32.CloseHandle(snap)
    return rows


def kill_process(pid):
    """结束单个进程。返回 (ok, 说明)。"""
    h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False, '打不开进程(错误 %d)' % ctypes.get_last_error()
    try:
        if k32.TerminateProcess(h, 1):
            return True, '已结束'
        return False, 'TerminateProcess 失败(错误 %d)' % ctypes.get_last_error()
    finally:
        k32.CloseHandle(h)


def close_window(hwnd):
    """给窗口发 WM_CLOSE，返回 (ok, 说明)。"""
    if not u32.IsWindow(hwnd):
        return False, '窗口已不存在'
    if u32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
        return True, '已发送 WM_CLOSE'
    return False, 'PostMessage 失败(错误 %d)' % ctypes.get_last_error()


class PopupGuard(object):
    """游戏退出后的弹窗监视。arm() 之后才工作，窗口到期自动收摊。"""

    def __init__(self, cfg, log, game_alive=None):
        self.cfg = cfg or {}
        self.log = log
        self.game_alive = game_alive or (lambda: False)
        self.armed = False
        self.armed_at = 0.0
        self.until = 0.0
        self.baseline = set()      # 退出瞬间就在的窗口，一律不碰
        self.handled = set()       # 已处理过的 hwnd，避免重复发 WM_CLOSE
        self.logged = set()        # 已记录过诊断行的 hwnd
        self.closed = 0
        self.seen = 0
        self.last_scan = 0.0
        self.killed = []           # 本次会话结束的进程 (pid, 名字)
        self.procs = {}
        # 提前拦截（游戏会话期间）：hwnd -> 已发 WM_CLOSE 次数
        self.early_attempts = {}
        self.early_logged = set()
        self.early_failed = set()
        # 本局累计提前拦截数。必须在**会话开始时**清零，不能在 arm() 里清：
        # arm() 是"游戏刚全部退出"的时点，紧接着就要打印这个数，先清再打印
        # 会让「本局提前拦截 N 次」永远显示 0（2026-11-12 真机日志证实）。
        self.session_early = 0
        self._path_cache = {}      # 单轮扫描内的 pid -> 路径（每轮清空，防 pid 复用）

    # ---------- 开关 ----------
    def enabled(self):
        return bool(self.cfg.get('enabled', False))

    def begin_session(self):
        """新一局游戏开始：清零本局提前拦截计数。

        只在真正的新会话调用；Unity 类游戏进程"闪断后恢复"走 resuming 分支，
        不调这里，本局计数要跨闪断累计（否则一局分成两段会少算）。
        """
        self.session_early = 0
        self.early_attempts.clear()
        self.early_logged.clear()
        self.early_failed.clear()

    def arm(self, now=None, procs=None):
        """游戏全部退出时调用：立刻清一轮进程，再拍窗口基线开始监视。"""
        if not self.enabled():
            return False
        now = time.time() if now is None else now
        self.procs = dict(procs or {})
        self.armed = True
        self.armed_at = now
        self.until = now + float(self.cfg.get('watch_sec', 90))
        self.handled.clear()
        self.logged.clear()
        self.closed = 0
        self.seen = 0
        self.last_scan = 0.0
        self.killed = []
        self.early_attempts.clear()
        self.early_logged.clear()
        self.early_failed.clear()
        self._path_cache = {}

        # 关键一步：弹窗宿主在游戏启动时就已经拉起来了，退出那一刻直接清掉，
        # 用户根本看不到窗口被显示出来。窗口基线要在清理**之后**再拍，
        # 否则被清掉的那批窗口进了基线，后面复活的同名窗口会被当成"退出前就在"而放过。
        self.sweep('游戏退出')

        self.baseline = set(w[0] for w in enum_windows(include_untitled=True))
        self.log('弹窗监视开始：基线 %d 个窗口，往后 %d 秒内出现的新窗口才处理'
                 '（本局提前拦截 %d 次）' %
                 (len(self.baseline), int(self.cfg.get('watch_sec', 90)),
                  self.session_early))
        return True

    def _stop(self, why):
        self.armed = False
        self.log('弹窗监视结束（%s）：新窗口 %d 个，关闭 %d 个，结束进程 %d 个，'
                 '提前拦截 %d 次' %
                (why, self.seen, self.closed, len(self.killed),
                 self.session_early))

    # ---------- 判定 ----------
    def _path_ok(self, path):
        if not path:
            return False
        p = path.lower()
        return any(k.lower() in p for k in (self.cfg.get('path_keywords') or []))

    def _title_ok(self, title):
        t = (title or '').lower()
        if not t:
            return False
        return any(k.lower() in t for k in (self.cfg.get('title_keywords') or []))

    def _name_hit(self, name):
        want = set(n.lower() for n in (self.cfg.get('process_names') or []))
        return bool(name) and name.lower() in want

    def _hint_hit(self, path):
        """路径片段是否命中 path_hints；缺省 / 清空 / 纯空白时回落到内置值。

        注意不能只判 `cfg.get('path_hints') or DEFAULT`：`['']` 是真值列表，
        `'' in path` 恒为 True，等于退化成"任意路径都命中"，会打穿
        "同名 exe 不在游戏目录就不动手"这条安全边界。这里先剔掉空白项，
        全被剔空再回落；顺手解决 `['\\clientbrowser2\\ ']` 这类尾空格导致
        永远不命中的静默失效。
        """
        raw = [str(h).strip() for h in (self.cfg.get('path_hints') or [])
               if str(h).strip()]
        hints = raw or DEFAULT_PATH_HINTS
        # 进程路径可能返回正斜杠，统一成反斜杠再比，否则会静默漏杀
        p = (path or '').lower().replace('/', '\\')
        if not p:
            return False
        return any(str(h).lower().replace('/', '\\') in p for h in hints)

    def _protected(self, name):
        prot = set(n.lower() for n in (self.cfg.get('protect_names') or []))
        return bool(name) and name.lower() in prot

    def _candidate(self, name, path):
        """窗口候选判定：必须再满足其一，否则一律跳过。

        1) 进程名在 process_names 里（默认 clientbrowser.exe，这类宿主只为弹窗存在）；
        2) 路径命中 path_hints 且进程名不在 protect_names（会话期临时拉起的变体）。
        只有"标题像直播 + 路径在腾讯系目录"是不够的 —— QQ / 微信的窗口同时满足
        这两条，按旧口径会被当成弹窗关掉。
        """
        if not name:
            return False
        low = name.lower()
        if low in NEVER_KILL or self._protected(low):
            return False
        if self._name_hit(low):
            return True
        return self._hint_hit(path)

    def _cached_path(self, pid):
        """单轮扫描内复用进程路径，避免同一个 pid 反复 OpenProcess。"""
        if pid in self._path_cache:
            return self._path_cache[pid]
        p = process_path(pid)
        self._path_cache[pid] = p
        return p

    def _shell_noise(self, name, cls):
        """系统外壳自己的一次性窗口：任务栏悬停预览、开始菜单、菜单弹出等。

        这些窗口每秒都可能新建/销毁，计入 seen 和日志只会把真实弹窗淹掉。
        """
        n = (name or '').lower()
        c = (cls or '').lower()
        if n in SHELL_NOISE_NAMES:
            return True
        if c in SHELL_NOISE_CLASSES:
            return True
        # 任务栏的 TaskListOverlayWnd 之类带 Overlay/Tooltip 的
        return c.startswith('#32768') or 'overlaywnd' in c or c.startswith('tooltips_')

    def _target(self, pid, path):
        """该进程是否属于"弹窗宿主"。exe 名与路径片段必须同时命中。"""
        name = (path or '').replace('/', '\\').rsplit('\\', 1)[-1]
        if not name or self._protected(name):
            return False
        if name.lower() in NEVER_KILL:
            return False
        return self._name_hit(name) and self._hint_hit(path)

    # ---------- 进程清理 ----------
    def sweep(self, why, quiet=False):
        """找出所有弹窗宿主进程，连同子进程树一起结束。返回结束的主进程数。

        quiet=True 时"没发现宿主"不打日志 —— 监视期每秒扫一次，否则日志全是噪声。
        """
        if not self.enabled() or not self.cfg.get('kill_tree', True):
            return 0
        if self.game_alive():
            return 0             # 游戏还在跑，绝不动手（ClientBrowser 还承载游戏内页面）

        rows = enum_procs()
        if not rows:
            return 0
        parent = {}
        kids = {}
        exe_of = {}
        for pid, exe, ppid in rows:
            parent[pid] = ppid
            exe_of[pid] = exe
            kids.setdefault(ppid, []).append(pid)

        hits = []
        for pid, exe, _pp in rows:
            if pid <= 4:
                continue
            path = process_path(pid)
            if not path:
                continue
            # 快路径：exe 名先粗筛，省掉大量 OpenProcess
            if not self._name_hit(exe) or not self._hint_hit(path):
                continue
            if self._target(pid, path):
                hits.append((pid, exe, path))

        # 只处理进程树的根：CEF 的子进程 exe 名和路径一样，但它们是根的后代，
        # 跟着根一起结束就够了；否则根先死后子进程变孤儿，还会刷一串"失败"。
        hit_pids = set(p for p, _e, _p in hits)
        roots = [h for h in hits if parent.get(h[0]) not in hit_pids]

        done = 0
        for pid, exe, path in roots:
            # 后序收集：先叶子后根，避免父进程先死导致子进程变孤儿
            order = []

            def walk(p, depth=0):
                if depth > 8:
                    return
                for c in kids.get(p, []):
                    walk(c, depth + 1)
                order.append(p)

            walk(pid)
            # order 是后序：除最后一个（根）以外都是它的后代
            born = process_created(pid)
            sub_ok = 0
            for p in order[:-1]:
                sub_name = (exe_of.get(p) or '').lower()
                if sub_name in NEVER_KILL or self._protected(sub_name):
                    continue
                # ppid 可能被复用：比宿主还老的进程不是它拉起来的，别误杀。
                # 取不到创建时间时保持原行为，避免影响已验证的清理效果。
                if born is not None:
                    child_born = process_created(p)
                    if child_born is not None and child_born < born - 10000000:
                        continue
                ok, _note = kill_process(p)
                if ok:
                    sub_ok += 1
            root_ok = False
            if order and (exe_of.get(order[-1]) or '').lower() not in NEVER_KILL:
                root_ok, _note = kill_process(order[-1])
            if root_ok or sub_ok:
                done += 1
                self.killed.append((pid, exe))
                self.log('%s：结束弹窗宿主 %s (pid %d) 及 %d 个子进程 —— %s' %
                         (why, exe, pid, sub_ok, path))
            else:
                self.log('%s：结束弹窗宿主失败 %s (pid %d) —— %s' %
                         (why, exe, pid, path))
        if not hits and not quiet:
            self.log('%s：未发现弹窗宿主进程' % why)
        return done

    # ---------- 提前拦截（游戏还没退干净时） ----------
    def watch_early(self, procs):
        """游戏会话进行中：宿主弹窗窗口一显示就发 WM_CLOSE，不等游戏进程全退。

        取证（2026-11-08）：该类弹窗宿主在游戏退出流程早期就 ShowWindow，
        比"游戏全部退出"早约 7 秒。旧逻辑必须等 arm() 才动手，用户会盯着
        弹窗挂好几秒。这里**只关窗口、绝不结束进程**：游戏运行期间
        ClientBrowser 还承担内嵌页面，原安全边界不变；进程树仍由
        arm()/sweep() 在退出后清理。
        """
        if not self.enabled() or not self.cfg.get('early_close_enabled', True):
            return 0
        wins = enum_windows(include_untitled=True)
        visible = set(w[0] for w in wins)
        # 窗口消失后允许同一个 hwnd（系统可能复用）重新计数
        for hwnd in list(self.early_attempts):
            if hwnd not in visible:
                self.early_attempts.pop(hwnd, None)
                self.early_logged.discard(hwnd)
                self.early_failed.discard(hwnd)
        if not wins:
            return 0

        # 先按进程名粗筛：绝大多数窗口不必 OpenProcess 取路径。
        # 名字没命中时，只有"路径命中 path_hints 且不在 protect_names"才算候选，
        # 这种情形才值得多花一次 process_path（QQ / 微信等常驻进程一律跳过）。
        self._path_cache = {}
        by_pid = {}
        for hwnd, pid, cls, title in wins:
            name = (procs or {}).get(pid) or ''
            if not name:
                continue
            if self._candidate(name, ''):
                by_pid.setdefault(pid, []).append((hwnd, cls, title))
                continue
            if self._protected(name) or name.lower() in NEVER_KILL:
                continue
            path = self._cached_path(pid)
            if path and self._candidate(name, path):
                by_pid.setdefault(pid, []).append((hwnd, cls, title))

        max_tries = max(1, int(self.cfg.get('early_max_tries', 8)))
        done = 0
        for pid, items in by_pid.items():
            path = self._cached_path(pid)
            # 候选只是必要条件；真正动手仍要求"exe 名 + 路径片段"双命中
            if not self._target(pid, path):
                continue
            exe = (path or '').replace('/', '\\').rsplit('\\', 1)[-1] or \
                ((procs or {}).get(pid) or '')
            for hwnd, cls, title in items:
                tried = self.early_attempts.get(hwnd, 0)
                if tried >= max_tries:
                    if hwnd not in self.early_failed:
                        self.early_failed.add(hwnd)
                        self.log('提前拦截：WM_CLOSE 连发 %d 次窗口仍在 (pid %d)，'
                                 '等游戏退干净后由进程树清理兜底' % (tried, pid))
                    continue
                self.early_attempts[hwnd] = tried + 1
                ok, note = close_window(hwnd)
                if ok:
                    done += 1
                    self.session_early += 1
                    if hwnd not in self.early_logged:
                        self.early_logged.add(hwnd)
                        self.log('提前拦截弹窗：%s (pid %d, %s) —— %s' %
                                 (exe, pid, cls or '?', note))
        return done

    # ---------- 主循环 ----------
    def tick(self, now, procs=None):
        if not self.armed or not self.enabled():
            return
        if procs:
            self.procs = procs
        if now > self.until:
            self._stop('到时间')
            return
        # arm() 已经立刻清过一轮进程，窗口层不必再等 delay_sec；
        # 扫描节拍跟随守护主循环（会话期 0.4s），别再叠一层 1 秒节流。
        delay = float(self.cfg.get('delay_sec', 0))
        if delay > 0 and now < self.armed_at + delay:
            return
        gap = float(self.cfg.get('scan_interval_sec', 0))
        if gap > 0 and now - self.last_scan < gap:
            return
        self.last_scan = now

        # 窗口层兜底：宿主被重新拉起时再清一次
        self.sweep('监视期', quiet=True)

        self._path_cache = {}
        for hwnd, pid, cls, title in enum_windows(include_untitled=True):
            if hwnd in self.baseline or hwnd in self.handled:
                continue
            path = self._cached_path(pid)
            name = (path or '').replace('/', '\\').rsplit('\\', 1)[-1] if path \
                else (self.procs.get(pid) or '')

            # 系统外壳自身的一次性窗口（任务栏悬停预览、开始菜单、菜单弹出）不是弹窗，
            # 计入 seen 与日志只会制造噪声
            if self._shell_noise(name, cls):
                self.handled.add(hwnd)
                continue

            # 候选窗口判定：只有"进程名在 process_names 里"或"路径命中 path_hints
            # 且不在 protect_names"才算弹窗宿主候选。QQ / 微信的窗口标题同样会
            # 出现"直播 / 赛事"，路径也在腾讯系目录下，不加这道闸门就会被误关。
            cand = self._candidate(name, path)

            if hwnd not in self.logged:
                self.logged.add(hwnd)
                self.seen += 1
                if self.cfg.get('log_all_new', True):
                    if cand:
                        self.log('弹窗监视：新窗口 %s (pid %d, %s) 标题=%r' %
                                 (name or '?', pid, cls, (title or '')[:40]))
                    else:
                        self.log('弹窗监视：跳过 %s (pid %d, %s) 标题=%r —— '
                                 '不属于弹窗宿主候选' %
                                 (name or '?', pid, cls, (title or '')[:40]))

            if self._protected(name):
                continue
            if not cand:
                continue

            # 进程名硬命中（不看标题）：这类宿主进程只为弹窗存在
            hit_proc = self._target(pid, path)
            # 关键词路径：标题像直播/赛事，且进程来自腾讯系目录（仍要求是候选）
            hit_kw = self._title_ok(title) and self._path_ok(path)

            if not (hit_proc or hit_kw):
                continue

            ok, note = close_window(hwnd)
            self.handled.add(hwnd)
            if ok:
                self.closed += 1
                self.log('已关闭弹窗：%s (pid %d) 标题=%r —— %s' %
                         (name or '?', pid, (title or '')[:40], note))
            else:
                self.log('关闭弹窗失败：%s (pid %d) 标题=%r —— %s' %
                         (name or '?', pid, (title or '')[:40], note))
