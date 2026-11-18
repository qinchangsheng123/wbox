# -*- coding: utf-8 -*-
"""ACE 清理（原生实现，不再依赖 PowerShell 与 SYSTEM 计划任务）。

判定沿用 v2 的四道闸门，任何一条不通过就一律不动手：
  闸门 1 GAME    ：游戏目录/白名单里还有进程 → 游戏会话中
  闸门 2 BLOCKER ：出现 ACE 启动/更新组件（SGuardUpdate64、ACE-Setup64、TASLogin…）
  闸门 3 YOUNG   ：ACE 主进程运行时长不足 min_ace_age（刚被拉起，可能正是本局）
  闸门 4 QUIET   ：最近 quiet_after_update_sec 秒内见过 ACE 启动/更新组件（正在升级/自修复）
                   —— 口径是"观察到的更新动作"，不再看文件 mtime：开局更新组件写驱动
                   会把 mtime 留在整局里，按 mtime 判会把正常局面误拦成 QUIET。
再加两道时间条件：游戏退出满 grace_sec；两次清理间隔满 cooldown_sec。

清理动作：停 ACE 服务（让驱动按序卸载）→ 结束残留进程（3 轮）→ 复核 → 报告驱动。
只做服务控制与进程结束，不删文件、不改注册表、不碰内核驱动。
"""
import ctypes
import os
import time
from ctypes import wintypes

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
advapi = ctypes.WinDLL('advapi32', use_last_error=True)

# ---------- 服务控制 ----------
SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SERVICE_STOP = 0x0020
SERVICE_CONTROL_STOP = 0x00000001
SC_STATUS_PROCESS_INFO = 0
SERVICE_STOPPED = 1
SERVICE_STOP_PENDING = 3
SERVICE_RUNNING = 4
STATE_NAMES = {1: 'stopped', 2: 'start-pending', 3: 'stop-pending',
               4: 'running', 5: 'continue-pending', 6: 'pause-pending', 7: 'paused'}
ERROR_SERVICE_NOT_ACTIVE = 1062
ERROR_SERVICE_ALREADY_RUNNING = 1056


class SERVICE_STATUS_PROCESS(ctypes.Structure):
    _fields_ = [
        ('dwServiceType', wintypes.DWORD),
        ('dwCurrentState', wintypes.DWORD),
        ('dwControlsAccepted', wintypes.DWORD),
        ('dwWin32ExitCode', wintypes.DWORD),
        ('dwServiceSpecificExitCode', wintypes.DWORD),
        ('dwCheckPoint', wintypes.DWORD),
        ('dwWaitHint', wintypes.DWORD),
        ('dwProcessId', wintypes.DWORD),
        ('dwServiceFlags', wintypes.DWORD),
    ]


advapi.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
advapi.OpenSCManagerW.restype = wintypes.HANDLE
advapi.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
advapi.OpenServiceW.restype = wintypes.HANDLE
advapi.CloseServiceHandle.argtypes = [wintypes.HANDLE]
advapi.CloseServiceHandle.restype = wintypes.BOOL
advapi.QueryServiceStatusEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                        ctypes.c_void_p, wintypes.DWORD,
                                        ctypes.POINTER(wintypes.DWORD)]
advapi.QueryServiceStatusEx.restype = wintypes.BOOL
advapi.ControlService.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                  ctypes.c_void_p]
advapi.ControlService.restype = wintypes.BOOL

# ---------- 进程 ----------
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
k32.TerminateProcess.restype = wintypes.BOOL
k32.GetProcessTimes.argtypes = [wintypes.HANDLE,
                                ctypes.POINTER(wintypes.FILETIME),
                                ctypes.POINTER(wintypes.FILETIME),
                                ctypes.POINTER(wintypes.FILETIME),
                                ctypes.POINTER(wintypes.FILETIME)]
k32.GetProcessTimes.restype = wintypes.BOOL


def service_state(name):
    """返回 (state_int, pid, 错误说明)。服务不存在返回 (None, 0, '不存在')。"""
    scm = advapi.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not scm:
        return None, 0, '打不开服务管理器(错误 %d)' % ctypes.get_last_error()
    h = None
    try:
        h = advapi.OpenServiceW(scm, name, SERVICE_QUERY_STATUS | SERVICE_STOP)
        if not h:
            err = ctypes.get_last_error()
            return None, 0, '不存在' if err == 1060 else '打不开(错误 %d)' % err
        buf = SERVICE_STATUS_PROCESS()
        need = wintypes.DWORD(0)
        if not advapi.QueryServiceStatusEx(h, SC_STATUS_PROCESS_INFO,
                                           ctypes.byref(buf), ctypes.sizeof(buf),
                                           ctypes.byref(need)):
            return None, 0, '查询失败(错误 %d)' % ctypes.get_last_error()
        return int(buf.dwCurrentState), int(buf.dwProcessId), 'ok'
    finally:
        if h:
            advapi.CloseServiceHandle(h)
        advapi.CloseServiceHandle(scm)


def stop_service(name, wait_sec=10.0):
    """停服务并等待进入 stopped。返回 (ok, 说明)。"""
    scm = advapi.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not scm:
        return False, '打不开服务管理器(错误 %d)' % ctypes.get_last_error()
    h = None
    try:
        # 先取状态：已停的直接返回，避免把"未运行"当成失败
        q = advapi.OpenServiceW(scm, name, SERVICE_QUERY_STATUS)
        if not q:
            err = ctypes.get_last_error()
            return False, '服务不存在' if err == 1060 else '打开失败(错误 %d)' % err
        st = SERVICE_STATUS_PROCESS()
        need = wintypes.DWORD(0)
        advapi.QueryServiceStatusEx(q, SC_STATUS_PROCESS_INFO, ctypes.byref(st),
                                    ctypes.sizeof(st), ctypes.byref(need))
        advapi.CloseServiceHandle(q)
        if st.dwCurrentState == SERVICE_STOPPED:
            return True, '本来就是 stopped'

        h = advapi.OpenServiceW(scm, name, SERVICE_STOP | SERVICE_QUERY_STATUS)
        if not h:
            return False, '无停止权限(错误 %d)' % ctypes.get_last_error()
        status = SERVICE_STATUS_PROCESS()
        if not advapi.ControlService(h, SERVICE_CONTROL_STOP, ctypes.byref(status)):
            err = ctypes.get_last_error()
            if err == ERROR_SERVICE_NOT_ACTIVE:
                return True, '未在运行'
            return False, 'ControlService 失败(错误 %d)' % err
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            time.sleep(0.4)
            st = SERVICE_STATUS_PROCESS()
            if not advapi.QueryServiceStatusEx(h, SC_STATUS_PROCESS_INFO,
                                               ctypes.byref(st), ctypes.sizeof(st),
                                               ctypes.byref(need)):
                break
            if st.dwCurrentState == SERVICE_STOPPED:
                return True, '已停止'
        return False, '停止超时（仍在 %s）' % STATE_NAMES.get(st.dwCurrentState, st.dwCurrentState)
    finally:
        if h:
            advapi.CloseServiceHandle(h)
        advapi.CloseServiceHandle(scm)


def process_age_sec(pid):
    """进程已运行秒数；取不到（含时钟回拨算出负数）返回 None。"""
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        c = wintypes.FILETIME()
        e = wintypes.FILETIME()
        k = wintypes.FILETIME()
        u = wintypes.FILETIME()
        if not k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                   ctypes.byref(k), ctypes.byref(u)):
            return None
        created = (c.dwHighDateTime << 32) | c.dwLowDateTime   # 1601 起的 100ns
        now = int(time.time() * 10000000) + 116444736000000000
        delta = (now - created) / 10000000.0
        # 负数只可能来自时钟回拨 / FILETIME 异常。之前返回 0.0 会被当成
        # "刚启动"，这里统一按"取不到"处理，交给闸门 3 按保护期拦截。
        return delta if delta >= 0 else None
    finally:
        k32.CloseHandle(h)


def kill_process(pid):
    """结束进程；受保护进程会失败。返回 (ok, 说明)。"""
    h = k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        err = ctypes.get_last_error()
        return False, '打不开进程(错误 %d)' % err
    try:
        if k32.TerminateProcess(h, 1):
            return True, '已结束'
        return False, 'TerminateProcess 失败(错误 %d)' % ctypes.get_last_error()
    finally:
        k32.CloseHandle(h)


class AceCleaner(object):
    """四道闸门 + 清理执行。所有判断只读系统状态，动作只有停服务与结束进程。"""

    def __init__(self, cfg, log, state_path=None, games_present=None):
        self.cfg = cfg
        self.log = log
        self.state_path = state_path
        self.games_present = games_present or (lambda: False)
        self.last_clean_at = 0.0
        # 闸门 4 的时间基准：最后一次"看到"ACE 启动/更新组件的时刻。
        # 起点取构造时刻（保守）——守护刚起来时什么都没观察到，先静默一个窗口，
        # 避免"重启后立刻判 CLEAN"撞上正在进行的更新。
        self.updater_seen_at = time.time()
        self._load_state()

    # ---------- 状态持久化（冷却时间跨重启有效） ----------
    def _clamp_clock(self, value, label):
        """把未来时间戳夹到 now：时钟回拨 / 状态文件被写入未来时间会让
        冷却（cooldown_sec）与宽限（grace_sec）永远无法满足，等于把清理永久锁死。

        设计选择（保守夹取，2026-11 定版，勿改成 return 0）：
          - 未来值统一夹到 now，而不是"当成 0 直接忽略"。理由是 last_clean_at 是
            冷却闸门的输入，当成 0 等于"状态文件被写坏 / 被篡改时静默绕过冷却"，
            而 ACE 清理本身是破坏性动作，宁可多等一个 cooldown（默认 600s）。
          - 夹取后冷却有界、可自愈：记一行日志说明原因，cooldown_sec 一到即放行，
            既不会永久锁死，也不会在异常状态下立刻动手。
          - 顺带覆盖时钟回拨：进程年龄算成负数时由 process_age_sec 返回 None
            （按"取不到"处理），这里只负责时间戳本身。
        """
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        now = time.time()
        if v > now:
            self.log('[ACE] 状态里的 %s 在未来（%.0f 秒后），按当前时间处理'
                     % (label, v - now))
            return now
        return v

    def _load_state(self):
        if not self.state_path:
            return
        try:
            import json
            with open(self.state_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.last_clean_at = self._clamp_clock(data.get('last_clean_at', 0),
                                                   'last_clean_at') or 0.0
        except Exception:
            self.last_clean_at = 0.0

    def _save_state(self):
        if not self.state_path:
            return
        try:
            import json
            tmp = self.state_path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'last_clean_at': self.last_clean_at,
                           'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S')}, f)
            os.replace(tmp, self.state_path)
        except Exception:
            pass

    def enabled(self):
        return bool(self.cfg.get('enabled', False))

    def _names(self, key):
        return set(n.lower() for n in (self.cfg.get(key) or []) if n)

    def ace_processes(self, procs):
        """按名字找出 ACE 进程：返回 [(pid, name)]。"""
        want = self._names('ace_processes')
        return sorted([(pid, n) for pid, n in procs.items() if n in want])

    def observe(self, procs, now=None):
        """每轮轮询调用一次：只要看到启动/更新组件，就把闸门 4 的时间基准推到现在。

        这是"更新的最后痕迹"——更新组件退出后再宽限 quiet_after_update_sec 秒才放行。
        轮询粒度 2 秒，极短命（<2s）的更新组件可能漏看，但这种情况下 ACE 主进程
        必然还很年轻，闸门 3（min_ace_age）会兜住。
        """
        now = time.time() if now is None else now
        want = self._names('blockers')
        if not want:
            return False
        hit = [n for n in procs.values() if n in want]
        if hit:
            self.updater_seen_at = now
            return True
        return False

    def services_running(self):
        out = []
        for name in (self.cfg.get('ace_services') or []):
            state, pid, note = service_state(name)
            if state is not None and state != SERVICE_STOPPED:
                out.append((name, STATE_NAMES.get(state, state)))
            elif state is None and note not in ('不存在',):
                out.append((name, note))
        return out

    def _recent_writes(self, minutes):
        """ACE 目录近 N 分钟被写过的文件（最多返回 3 个名字）。"""
        cut = time.time() - minutes * 60
        hits = []
        for d in (self.cfg.get('ace_dirs') or []):
            if not d or not os.path.isdir(d):
                continue
            want_ace_prefix = os.path.basename(d).lower() == 'drivers'
            for root, dirs, files in os.walk(d):
                for fn in files:
                    if want_ace_prefix and not fn.lower().startswith('ace'):
                        continue
                    p = os.path.join(root, fn)
                    try:
                        if os.path.getmtime(p) > cut:
                            hits.append(fn)
                            if len(hits) >= 3:
                                return hits
                    except OSError:
                        continue
        return hits

    # ---------- 判定 ----------
    def verdict(self, procs, last_exit_at):
        """返回 (code, reason)。code 见下方注释。"""
        if self.games_present():
            return 'GAME', '游戏会话中，不清理'

        blockers = set(n.lower() for n in (self.cfg.get('blockers') or []))
        hit = sorted(set(n for _, n in procs.items() if n in blockers))
        if hit:
            return 'BLOCKER', 'ACE 启动/更新阶段（%s）' % ', '.join(hit)

        ace = self.ace_processes(procs)
        svc = self.services_running()
        if not ace and not svc:
            return 'NOACE', 'ACE 未运行，无需清理'

        min_age = float(self.cfg.get('min_ace_age_sec', 300))
        for pid, name in ace:
            age = process_age_sec(pid)
            if age is None:
                # 取不到创建时间（权限不足 / 进程刚退出 / 受保护进程）时不能当成
                # "已经跑够久"。一律按保护期处理：宁可漏清，不可误清正在更新的 ACE。
                return 'YOUNG', 'ACE 进程 %s (pid %d) 取不到创建时间，按保护期处理' % (
                    name, pid)
            if age < min_age:
                return 'YOUNG', 'ACE 进程 %s 仅运行 %.0fs，保护期内（阈值 %.0fs）' % (
                    name, age, min_age)

        quiet = float(self.cfg.get('quiet_after_update_sec', 30))
        if self.updater_seen_at:
            gap = time.time() - self.updater_seen_at
            if gap < quiet:
                return 'QUIET', '距上次见到 ACE 更新组件仅 %.0fs（静默阈值 %.0fs），可能仍在更新' % (
                    gap, quiet)

        if last_exit_at is None:
            return 'WAIT', '尚未观察到游戏退出'
        last_exit_at = self._clamp_clock(last_exit_at, 'last_exit_at')
        if last_exit_at is None:
            return 'WAIT', '游戏退出时间不可用'
        idle = time.time() - last_exit_at
        grace = float(self.cfg.get('grace_sec', 180))
        if idle < grace:
            return 'GRACE', '游戏退出仅 %.0fs，未满 %.0fs' % (idle, grace)

        cooldown = float(self.cfg.get('cooldown_sec', 600))
        # 冷却基准同样可能被写成未来时间，先夹到 now，否则会永远判 COOLDOWN
        self.last_clean_at = self._clamp_clock(self.last_clean_at, 'last_clean_at') or 0.0
        since = time.time() - self.last_clean_at if self.last_clean_at else 1e9
        if since < cooldown:
            return 'COOLDOWN', '冷却期内（距上次清理 %.0fs）' % since

        return 'CLEAN', '游戏已退出 %.0fs，ACE 进程均已运行超 %.0fs' % (idle, min_age)

    # ---------- 执行 ----------
    def _game_back(self, why):
        """每轮动手前复核游戏是否已回来；回来就立刻放弃，绝不继续杀进程。"""
        if self.games_present():
            self.log('[ACE] 复核发现游戏进程已回来，立即中止清理（%s）' % why)
            return True
        return False

    def _wait_with_recheck(self, seconds, why):
        """分片等待（0.5 秒一步），每步都复核 games_present()。

        总时长与原来的固定 sleep 一致（3s + 3×2s），只是把"闭眼等"换成
        "边等边复核"：游戏在等待期间被重新拉起时，不会再多杀一轮。
        返回 False 表示游戏已回来、应立即中止。
        """
        deadline = time.time() + float(seconds)
        while True:
            if self._game_back(why):
                return False
            remain = deadline - time.time()
            if remain <= 0:
                return True
            time.sleep(min(0.5, remain))

    def run_cleanup(self, procs):
        """停服务 + 结束进程 + 复核。返回成功与否。"""
        dry = bool(self.cfg.get('dry_run', False))
        # 动手前最后一道复核：verdict 到 run_cleanup 之间游戏可能已经重启
        if self._game_back('清理开始前'):
            return False
        ace = self.ace_processes(procs)
        svc = self.services_running()
        head = '服务 %s；进程 %s' % (
            ', '.join('%s=%s' % s for s in svc) or '无',
            ', '.join('%s#%d' % (n, p) for p, n in ace) or '无')
        # 文件 mtime 已经退出判定，只留作事后核对的参考信息
        recent = self._recent_writes(30)
        if recent:
            self.log('[ACE] 参考：ACE 目录近 30 分钟有写入 %s（仅诊断，不参与判定）'
                     % ', '.join(recent))
        if dry:
            self.log('[ACE] DryRun 命中清理条件，不执行。' + head)
            return False
        self.log('[ACE] 开始清理。' + head)

        for name, _state in svc:
            ok, note = stop_service(name)
            self.log('[ACE] 停服务 %s：%s' % (name, note))

        if not self._wait_with_recheck(3, '停服务后等待'):
            return False

        total = 0
        for round_i in range(3):
            snap = self._snapshot()
            if snap is None:
                self.log('[ACE] 取进程表失败，放弃本轮清理')
                return False
            left = [(p, n) for p, n in self.ace_processes(snap)]
            if not left:
                break
            if self._game_back('第 %d 轮结束进程前' % (round_i + 1)):
                return False
            for pid, name in left:
                ok, note = kill_process(pid)
                if ok:
                    total += 1
                    self.log('[ACE] 已结束 %s (pid %d)' % (name, pid))
                else:
                    self.log('[ACE] 结束失败 %s (pid %d)：%s' % (name, pid, note))
            if not self._wait_with_recheck(2, '第 %d 轮后等待' % (round_i + 1)):
                return False

        left_snap = self._snapshot()
        if left_snap is None:
            self.log('[ACE] 取进程表失败，无法复核残留（已结束 %d 个进程）' % total)
            return False
        left_proc = self.ace_processes(left_snap)
        left_svc = self.services_running()
        if not left_proc and not left_svc:
            self.log('[ACE] 已清空（服务已停、进程已退），共结束 %d 个进程' % total)
            self.last_clean_at = time.time()
            self._save_state()
            return True
        self.log('[ACE] 仍有残留：服务 %s；进程 %s' % (
            ', '.join('%s=%s' % s for s in left_svc) or '无',
            ', '.join('%s#%d' % (n, p) for p, n in left_proc) or '无'))
        self.last_clean_at = time.time()
        self._save_state()
        return False

    # 供内部复用；调用方也可注入自己的快照函数
    _snapshot_impl = None

    def _snapshot(self):
        if self._snapshot_impl:
            return self._snapshot_impl()
        from guard_core import snapshot
        return snapshot()
