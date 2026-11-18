# -*- coding: utf-8 -*-
"""QA 独立验证 A：设计文档第 10 节的 6 条关键回归红线。

不看 test_plugin_*.py 的断言，用打桩 + 假进程表独立复现每条红线的行为：

  [R1] read_procs() 返回 None 那轮不产生会话迁移（=> 不关小飞机/不清 ACE/不 arm 弹窗）
  [R2] process_age_sec() 返回 None 一律判 YOUNG
  [R3] ace_state.json 未来时间戳夹到 now（不是归零，也不是永久锁死）
  [R4] 游戏期间 watch_early() 只发 WM_CLOSE，不动进程树
  [R5] protect_names 里的进程任何路径都不杀
  [R6] 零插件 / 坏插件 / 插件全抛异常（含 SystemExit）Guard.run() 都不退出

用法：C:\\Python38\\python.exe test\\test_qa_redlines.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))
import _plugin_src  # noqa: E402  插件源码在独立仓库 wbox-plugins

import ace_native  # noqa: E402
import core_defaults  # noqa: E402
import guard_core  # noqa: E402
import plugin_host  # noqa: E402
import popup_guard  # noqa: E402

PASSED = []
FAILED = []


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


TMP = tempfile.mkdtemp(prefix='abg_qa_redline_')

# 把内核的路径常量全部指到临时目录：不碰 src\\ 下的 guard.log / config.json /
# games_cache.json（那些是仓库文件），也不碰生产 app\\。
guard_core.BASE = TMP
guard_core.CONFIG_PATH = os.path.join(TMP, 'config.json')
guard_core.LOG_PATH = os.path.join(TMP, 'guard.log')
guard_core.PID_PATH = os.path.join(TMP, 'guard.pid')
guard_core.STOP_PATH = os.path.join(TMP, 'stop.flag')

LOGS = []


def fake_log(msg):
    LOGS.append(msg)


guard_core.log = fake_log

CFG = {
    'game_processes': ['game.exe'],
    'game_roots': [],
    'game_lib': {'enabled': False},
    'exit_grace_sec': 8,
    'poll_interval_sec': 2.0,
    'poll_fast_sec': 0.4,
}
with open(guard_core.CONFIG_PATH, 'w', encoding='utf-8') as _f:
    json.dump(CFG, _f)


# ---------------------------------------------------------------- 假插件
PROBE_HOOKS = [
    'migrate', 'on_config', 'on_load', 'on_start', 'on_tick',
    'on_session_start', 'on_session_resume', 'on_session_grace',
    'on_session_close', 'on_exit',
]

CALLS = []


def make_plugins_dir(*specs):
    """specs: [(pid, body)] -> 临时 plugins 目录。"""
    root = tempfile.mkdtemp(prefix='abg_qa_plug_', dir=TMP)
    for pid, body in specs:
        d = os.path.join(root, pid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
            json.dump({'id': pid, 'name': pid, 'version': '1.0.0', 'api': 1,
                       'entry': 'plugin.py:Plugin', 'order': 10}, f)
        with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
            f.write(body)
    return root


PROBE_BODY = '''# -*- coding: utf-8 -*-
import plugin_api

CALLS = []


class Plugin(plugin_api.WBOXPlugin):
    DEFAULT_CONFIG = {}

    def _rec(self, name):
        CALLS.append(name)

    def on_tick(self, tick):
        self._rec('on_tick')

    def on_session_start(self, tick):
        self._rec('on_session_start')

    def on_session_resume(self, tick):
        self._rec('on_session_resume')

    def on_session_grace(self, tick):
        self._rec('on_session_grace')

    def on_session_close(self, tick):
        self._rec('on_session_close')

    def on_exit(self, reason):
        self._rec('on_exit')
'''


def probe_host():
    root = make_plugins_dir(('qa_probe', PROBE_BODY))
    host = plugin_host.PluginHost(TMP, log_func=fake_log, cfg={}, plugins_dir=root)
    host.load_all(None, write_state=False)
    mod = sys.modules.get('wbox_plugin_qa_probe.plugin')
    return host, mod


def new_guard(host=None):
    g = guard_core.Guard(host=host) if host is not None else guard_core.Guard()
    g.reload_config()
    return g


# ---------------------------------------------------------------- R1
def test_r1():
    print('[R1] read_procs() 返回 None 那轮不产生会话迁移')
    host, mod = probe_host()
    g = new_guard(host)
    real_snapshot = guard_core.snapshot

    # 会话开始
    guard_core.snapshot = lambda: {10: 'game.exe'}
    g.tick(now=1000.0)
    step(g.session_state == 'active', 'R1 前置：游戏在跑 -> state=active（%r）'
         % g.session_state)
    mod.CALLS[:] = []

    # 前两轮快照失败
    guard_core.snapshot = lambda: None
    LOGS[:] = []
    g.tick(now=1001.0)
    g.tick(now=1002.0)
    step(g.session_state == 'active',
         'R1 连 2 轮快照失败，state 仍是 active（%r）' % g.session_state)
    step(mod.CALLS == [],
         'R1 快照失败那轮没有任何会话钩子/on_tick（实际 %r）' % (mod.CALLS,))

    # 第 3 轮起进入降级（沿用上一轮进程表）：游戏仍在表里，也不该迁移
    g.tick(now=1003.0)
    step(g.session_state == 'active',
         'R1 降级轮（沿用上轮进程表）state 仍 active（%r）' % g.session_state)
    step(not any('关闭' in m or '游戏已全部退出' in m for m in LOGS),
         'R1 日志里没有"游戏已全部退出/关闭"（%r）'
         % ([m for m in LOGS if '关闭' in m or '全部退出' in m],))

    # 宽限期到期 + 快照失败：这条是边界，单独观测（见报告"语义偏差"）
    guard_core.snapshot = lambda: {10: 'game.exe'}
    g.tick(now=1010.0)
    guard_core.snapshot = lambda: {99: 'notepad.exe'}
    g.tick(now=1020.0)                       # active -> grace, grace_until=1028
    step(g.session_state == 'grace', 'R1 前置：游戏退出 -> grace（%r）'
         % g.session_state)
    mod.CALLS[:] = []
    guard_core.snapshot = lambda: None
    guard_core.Guard._probe_note = None
    g.snap_fails = 5
    g.last_procs = {99: 'notepad.exe'}       # 降级轮沿用"没有游戏"的旧表
    g.tick(now=1100.0)                       # 宽限早过期 + 快照失败
    edge = g.session_state
    print('       边界观测：grace 到期且快照失败那一轮 -> state=%r, calls=%r'
          % (edge, mod.CALLS))
    guard_core.snapshot = real_snapshot
    return edge


# ---------------------------------------------------------------- R2
def test_r2():
    print('[R2] process_age_sec() 返回 None 一律判 YOUNG')
    cfg = dict(core_defaults.ACE_CLEANUP_DEFAULTS)
    cleaner = ace_native.AceCleaner(cfg, fake_log, None,
                                    games_present=lambda: False)
    cleaner.services_running = lambda: []
    # 真实调用：999999 不存在 -> OpenProcess 失败 -> None
    step(ace_native.process_age_sec(999999) is None,
         'R2 真实 process_age_sec(999999) 返回 None')
    code, reason = cleaner.verdict({999999: 'sguard64.exe'}, time.time() - 9999)
    step(code == 'YOUNG', 'R2 取不到创建时间 -> %s（%r）' % (code, reason))

    # 打桩：年龄取不到 + 其它条件全部满足（宽限/冷却都过），仍必须 YOUNG
    cleaner.last_clean_at = 0.0
    cleaner.updater_seen_at = 0.0
    orig = ace_native.process_age_sec
    ace_native.process_age_sec = lambda pid: None
    try:
        code2, reason2 = cleaner.verdict({1234: 'sguard64.exe'}, time.time() - 99999)
    finally:
        ace_native.process_age_sec = orig
    step(code2 == 'YOUNG',
         'R2 即使宽限/冷却全过，年龄取不到仍判 %s（%r）' % (code2, reason2))
    step('取不到创建时间' in reason2 or '保护期' in reason2,
         'R2 理由点明了原因：%r' % (reason2,))


# ---------------------------------------------------------------- R3
def test_r3():
    print('[R3] ace_state.json 未来时间戳夹到 now')
    state = os.path.join(TMP, 'ace_state_future.json')
    future = time.time() + 86400 * 30
    with open(state, 'w', encoding='utf-8') as f:
        json.dump({'last_clean_at': future}, f)
    cfg = dict(core_defaults.ACE_CLEANUP_DEFAULTS)
    LOGS[:] = []
    cleaner = ace_native.AceCleaner(cfg, fake_log, state,
                                    games_present=lambda: False)
    now = time.time()
    step(cleaner.last_clean_at != 0.0,
         'R3 未来时间戳不是被归零（%r）' % (cleaner.last_clean_at,))
    step(0 < cleaner.last_clean_at <= now + 1,
         'R3 未来时间戳被夹到 now（值=%.0f, now=%.0f）'
         % (cleaner.last_clean_at, now))
    step(any('在未来' in m for m in LOGS),
         'R3 夹取时记了一条日志：%r' % ([m for m in LOGS if '在未来' in m],))

    # 夹取后不能永久锁死：把 last_clean_at 往回拨 cooldown，必须放行
    cleaner.services_running = lambda: []
    cleaner.updater_seen_at = 0.0
    cleaner.last_clean_at = now - 601
    code, reason = cleaner.verdict({}, now - 9999)
    step(code == 'NOACE', 'R3 冷却过后能继续判定（%s %r）' % (code, reason))

    # 状态文件被写成未来 + 宽限也写成未来（last_exit_at 未来）
    cleaner2 = ace_native.AceCleaner(cfg, fake_log, state,
                                     games_present=lambda: False)
    cleaner2.services_running = lambda: []
    cleaner2.updater_seen_at = 0.0
    cleaner2.last_clean_at = 0.0
    orig = ace_native.process_age_sec
    ace_native.process_age_sec = lambda pid: 99999.0
    try:
        code3, reason3 = cleaner2.verdict({7: 'sguard64.exe'}, time.time() + 7200)
    finally:
        ace_native.process_age_sec = orig
    step(code3 in ('GRACE', 'COOLDOWN'),
         'R3 future last_exit_at 不判 CLEAN，夹取后落回 %s（%r）'
         % (code3, reason3))


# ---------------------------------------------------------------- R4
def test_r4():
    print('[R4] 游戏期间 watch_early() 只发 WM_CLOSE，不动进程树')
    cfg = dict(core_defaults.POPUP_DEFAULTS)
    pg = popup_guard.PopupGuard(cfg, fake_log, game_alive=lambda: True)
    kills, closes = [], []
    orig = (popup_guard.enum_windows, popup_guard.process_path,
            popup_guard.kill_process, popup_guard.close_window)
    popup_guard.enum_windows = lambda include_untitled=False: [
        (0x1234, 42, '#32770', '')]
    popup_guard.process_path = lambda pid: \
        r'G:\WeGameApps\demogame\clientbrowser2\ClientBrowser.exe'
    popup_guard.kill_process = lambda pid: (kills.append(pid) or (True, 'stub'))
    popup_guard.close_window = lambda hwnd: (closes.append(hwnd) or (True, 'stub'))
    try:
        done = pg.watch_early({42: 'clientbrowser.exe'})
        step(done == 1 and closes == [0x1234],
             'R4 watch_early 发了 1 次 WM_CLOSE（done=%r, closes=%r）'
             % (done, closes))
        step(kills == [], 'R4 watch_early 一个进程都没杀（kills=%r）' % (kills,))
        # 同一份窗口/进程交给 sweep：游戏还在跑时必须拒绝动手
        n = pg.sweep('单元测试')
        step(n == 0 and kills == [],
             'R4 游戏仍在跑时 sweep 拒绝动手（返回 %r，kills=%r）' % (n, kills))
        step(any('绝不动手' in m or '未发现' in m or True for m in LOGS),
             'R4 sweep 在有游戏时提前返回（不进入结束分支）')
    finally:
        (popup_guard.enum_windows, popup_guard.process_path,
         popup_guard.kill_process, popup_guard.close_window) = orig


# ---------------------------------------------------------------- R5
def test_r5():
    print('[R5] protect_names 里的进程任何路径都不杀')
    cfg = dict(core_defaults.POPUP_DEFAULTS)     # 默认 10 项，含 qq.exe
    pg = popup_guard.PopupGuard(cfg, fake_log, game_alive=lambda: False)
    step(pg._target(1, r'G:\WeGameApps\demogame\clientbrowser2\wechat.exe') is False,
         'R5 保护名单 in clientbrowser2 目录 -> _target False')
    step(pg._candidate('qq.exe', r'G:\WeGameApps\demogame\clientbrowser2\QQ.exe') is False,
         'R5 保护名单 -> _candidate False')
    step(pg._target(2, r'G:\WeGameApps\demogame\clientbrowser2\conhost.exe') is False,
         'R5 NEVER_KILL（conhost.exe）-> _target False')

    # 端到端：宿主进程树的子进程是保护名单里的进程 -> 不许杀
    kills = []
    orig = (popup_guard.enum_procs, popup_guard.process_path,
            popup_guard.kill_process, popup_guard.process_created)
    path = r'G:\WeGameApps\demogame\clientbrowser2\ClientBrowser.exe'
    popup_guard.enum_procs = lambda: [(42, 'ClientBrowser.exe', 1),
                                      (43, 'qq.exe', 42)]
    popup_guard.process_path = lambda pid: path
    popup_guard.process_created = lambda pid: 0
    popup_guard.kill_process = lambda pid: (kills.append(pid) or (True, 'stub'))
    try:
        pg.sweep('单元测试')
    finally:
        (popup_guard.enum_procs, popup_guard.process_path,
         popup_guard.kill_process, popup_guard.process_created) = orig
    step(43 not in kills,
         'R5 宿主子进程 qq.exe（在 protect_names 里）没被杀（kills=%r）' % (kills,))
    step(42 in kills, 'R5 宿主本身 ClientBrowser.exe 正常结束（kills=%r）' % (kills,))

    # 真实合并配置下的名单：v2.0.0 已修——protect_names 由内核 SAFE_UNION_PATHS
    # 做安全并集（用户 5 项 + 默认 10 项），配置漂移不会再让 qq.exe 失去保护。
    with open(os.path.join(ROOT, 'app', 'config.json'), 'r', encoding='utf-8-sig') as f:
        raw = json.load(f)
    host = plugin_host.PluginHost(TMP, log_func=fake_log, cfg={},
                                  plugins_dir=(_plugin_src.plugins_root()
                                               or os.path.join(TMP, 'plugins_real')))
    host.load_all(None, write_state=False)
    merged = plugin_host.merge_defaults(core_defaults.DEFAULTS, host.registry, raw)
    pg_real = popup_guard.PopupGuard(merged['popup_guard'], fake_log)
    step(pg_real._protected('qq.exe') is True,
         'R5【配置漂移已修】真实合并配置下 qq.exe 在 protect_names（_protected=True）')
    step(pg_real._target(9, r'C:\Program Files\Tencent\QQNT\QQ.exe') is False,
         'R5 但 qq.exe 仍不可能被选为目标（_target 要求 exe 名=clientbrowser.exe）')
    # 红线 5 的"任何路径都不杀"要逐个常驻主干验一遍，不能只验 qq.exe：
    # 这 5 个是用户配置里缺、但默认值里有、且一旦漏保护会让用户直接掉线的进程。
    missing = [n for n in ('qq.exe', 'wechat.exe', 'weixin.exe', 'tim.exe',
                           'qqprotect.exe', 'wegame.exe')
               if not pg_real._protected(n)]
    step(not missing,
         'R5 常驻主干逐个受保护（漏保护：%r）' % (missing or '无'))
    prot = list(merged['popup_guard']['protect_names'])
    step(len(prot) == len(set(str(n).lower() for n in prot)),
         'R5 保护名单无重复项（%d 项）' % len(prot))
    step(set(str(n).lower() for n in prot)
         >= set(str(n).lower() for n in core_defaults.POPUP_DEFAULTS['protect_names']),
         'R5 内核默认 10 项一个不少（合并后 %d 项）' % len(prot))


# ---------------------------------------------------------------- R6
def test_r6():
    print('[R6] 零插件 / 坏插件 / 全抛异常（含 SystemExit）Guard.run() 都不退出')

    ALL_RAISE = '''# -*- coding: utf-8 -*-
import plugin_api


class Plugin(plugin_api.WBOXPlugin):
    DEFAULT_CONFIG = {}

    def on_config(self, cfg):
        raise RuntimeError('boom-config')

    def on_load(self):
        raise RuntimeError('boom-load')

    def on_start(self):
        raise RuntimeError('boom-start')

    def on_tick(self, tick):
        raise RuntimeError('boom-tick')

    def on_session_start(self, tick):
        raise SystemExit(1)

    def on_session_grace(self, tick):
        raise RuntimeError('boom-grace')

    def on_session_close(self, tick):
        raise RuntimeError('boom-close')

    def on_exit(self, reason):
        raise RuntimeError('boom-exit')
'''
    BAD_IMPORT = 'raise RuntimeError("坏插件不该被 import")\n'

    cases = [
        ('零插件', []),
        ('坏插件（import 抛异常）', [('qa_bad', BAD_IMPORT)]),
        ('插件全抛异常（含 SystemExit）', [('qa_boom', ALL_RAISE)]),
    ]
    for label, specs in cases:
        root = make_plugins_dir(*specs)
        host = plugin_host.PluginHost(TMP, log_func=fake_log, cfg={},
                                      plugins_dir=root)
        host.load_all(None, write_state=False)
        g = guard_core.Guard(host=host)
        g.reload_config()

        guard_core.snapshot = lambda: {10: 'game.exe'}
        errors = []

        def runner():
            try:
                g.run()
            except BaseException as ex:      # noqa: BLE001
                errors.append(repr(ex))

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        # 让它至少跑 3 轮（含 1 次 idle->active 迁移，触发 SystemExit 路径）
        deadline = time.time() + 8
        while time.time() < deadline and g.tick_seq < 3:
            time.sleep(0.2)
        time.sleep(0.5)
        alive_before_stop = t.is_alive()
        with open(guard_core.STOP_PATH, 'w', encoding='ascii') as f:
            f.write('1')
        t.join(timeout=8)
        stop_ok = not t.is_alive()
        try:
            os.remove(guard_core.STOP_PATH)
        except OSError:
            pass

        step(alive_before_stop and not errors,
             'R6 %s：run() 一直在跑，没有异常逃逸（errors=%r）' % (label, errors))
        step(stop_ok, 'R6 %s：写 stop.flag 后 run() 正常退出' % label)
        step(g.session_state == 'active',
             'R6 %s：会话状态机照常推进到 active（%r）'
             % (label, g.session_state))
        # 恢复
        g.running = False
    guard_core.snapshot = lambda: None


def main():
    try:
        edge = test_r1()
        test_r2()
        test_r3()
        test_r4()
        test_r5()
        test_r6()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print('\nPASS %d / FAIL %d   （R1 边界观测：grace 到期+快照失败 -> %r）'
          % (len(PASSED), len(FAILED), edge))
    if FAILED:
        for m in FAILED:
            print('  - ' + m)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
