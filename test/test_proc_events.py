# -*- coding: utf-8 -*-
"""v2.5.0-③ 事件驱动进程检测测试：唤醒链路 / 回落轮询 / 低频兜底口径。

三条证据线：
  [1] wait_delay() 口径：事件健康时空闲 2s->30s、会话 0.4s->2.0s、插件登记的
      快节拍不放宽；事件不可用/未挂载恒等于 poll_delay()（v2.4 纯轮询行为）。
  [2] 唤醒链路：真实 WMI（Win32_ProcessStartTrace/StopTrace，本机提权时）
      或注入模式（WMI 不可用时）——进程事件到达 -> wake 事件立刻置位。
  [3] 守护主循环级：g.ev 健康时注入事件 -> 即使 wait_delay=30s，下一轮 tick
      在 1 秒内发生；g.ev 不可用/None -> 回落纯轮询，tick 按 poll_delay 节拍。

不碰生产守护；guard_core 全部路径指到临时目录。真实 WMI 订阅用
本进程里的独立 watcher（测试专用 wake 事件），结束即停。
用法：C:\\Python38\\python.exe test\\test_proc_events.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)

import guard_core  # noqa: E402
import plugin_host  # noqa: E402
import proc_events  # noqa: E402

PASSED = []
FAILED = []
TMP = tempfile.mkdtemp(prefix='abg_proc_ev_')

# 与 beat_cpu_probe 相同的路径重定向手法：不碰仓库/生产。
# 注意 _orig 快照必须在改值之前取（单进程卫生要还原的就是原值）。
_orig = {k: getattr(guard_core, k) for k in
         ('BASE', 'CONFIG_PATH', 'LOG_PATH', 'PID_PATH', 'STOP_PATH',
          'PLUGINS_DIR', 'RELOAD_FLAG_PATH', 'log')}
guard_core.BASE = TMP
guard_core.CONFIG_PATH = os.path.join(TMP, 'config.json')
guard_core.LOG_PATH = os.path.join(TMP, 'guard.log')
guard_core.PID_PATH = os.path.join(TMP, 'guard.pid')
guard_core.STOP_PATH = os.path.join(TMP, 'stop.flag')
guard_core.PLUGINS_DIR = os.path.join(TMP, 'plugins')
guard_core.RELOAD_FLAG_PATH = os.path.join(TMP, 'plugins_reload.flag')

LOGS = []
guard_core.log = lambda m: LOGS.append(str(m))


def restore_guard_globals():
    """单进程卫生：这些是模块级常量，别的测试（如 test_throttle_state）
    之后还要用仓库口径的 PLUGINS_DIR / log，必须还原。"""
    for k, v in _orig.items():
        setattr(guard_core, k, v)


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


class FakeEv(object):
    """最小唤醒器桩：ok 可控；wake 是真内核事件（供 _sleep_round 使用）。"""

    def __init__(self, ok=True):
        self.ok = ok
        self.wake = proc_events.make_wake_event()

    def set(self):
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.SetEvent(wintypes.HANDLE(self.wake))


def temp_guard():
    g = guard_core.Guard()
    g.reload_config()
    return g


def main():
    try:
        print('[1] wait_delay() 口径')
        g = temp_guard()
        # 未挂事件唤醒器：纯轮询
        step(g.ev is None and g.wait_delay() == g.poll_delay(),
             '未挂载：恒等于 poll_delay（%.1fs）' % g.wait_delay())
        ev_bad = FakeEv(ok=False)
        g.ev = ev_bad
        step(g.wait_delay() == g.poll_delay(),
             '事件不可用（ok=False）：回落纯轮询 %.1fs' % g.wait_delay())
        ev_ok = FakeEv(ok=True)
        g.ev = ev_ok
        idle_delay = g.wait_delay()
        step(abs(idle_delay - g.poll_delay(False) * guard_core.EVENT_IDLE_FACTOR) < 1e-9
             and idle_delay >= guard_core.EVENT_IDLE_MIN,
             '事件健康 + 空闲：%.1fs（%.0f 倍，>= %.0f 低限）'
             % (idle_delay, guard_core.EVENT_IDLE_FACTOR, guard_core.EVENT_IDLE_MIN))
        g.session_state = 'active'
        busy_delay = g.wait_delay()
        step(abs(busy_delay - g.poll_delay(True) * guard_core.EVENT_FAST_FACTOR) < 1e-9,
             '事件健康 + 会话中：%.1fs（%.0f 倍，退出判定仍 <= %.1fs 级）'
             % (busy_delay, guard_core.EVENT_FAST_FACTOR, busy_delay))
        g.session_state = 'idle'
        # 插件登记的快节拍（弹窗监视期）不放宽
        until = time.time() + 60
        g.host.fast_poll_until = until
        step(abs(g.wait_delay() - g.poll_delay()) < 1e-9,
             '插件登记快节拍期间：不放宽，仍 %.1fs' % g.wait_delay())
        g.host.fast_poll_until = 0.0
        step(abs(g.wait_delay() - g.poll_delay(False) * guard_core.EVENT_IDLE_FACTOR) < 1e-9,
             '快节拍过期：恢复低频兜底 %.1fs' % g.wait_delay())
        g.ev = None

        print('[2] 唤醒链路（真实 WMI，不可用则注入）')
        names = {'cmd.exe'}
        watcher = proc_events.ProcEventWatcher(lambda: names,
                                               proc_events.make_wake_event())
        watcher.log = lambda m: LOGS.append(str(m))
        watcher.start()
        real_wmi = False
        deadline = time.time() + 6.0
        while time.time() < deadline:
            if watcher.ok:
                real_wmi = True
                break
            if LOGS and any('订阅不可用' in m for m in LOGS[-4:]):
                break
            time.sleep(0.2)
        step(watcher.ok, '订阅状态：ok=%s（real_wmi=%s）' % (watcher.ok, real_wmi))
        if watcher.ok:
            # 真实进程事件：启动一个 cmd.exe
            t0 = time.time()
            p = subprocess.Popen(['cmd.exe', '/c', 'exit'],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            got = proc_events.wait_for_wake(watcher.wake, 5000)
            dt = time.time() - t0
            step(got and dt < 5.0,
                 '真实启动事件 -> wake 立刻置位（%.2fs，wakes=%d，name=%s）'
                 % (dt, watcher.wakes, watcher.last_wake_name))
            step(watcher.last_wake_name == 'cmd.exe',
                 '事件按 watch 名单过滤（只认 cmd.exe）')
            p.wait(timeout=10)
            # 停止事件：进程退出后 StopTrace
            got_stop = proc_events.wait_for_wake(watcher.wake, 8000)
            step(got_stop, '真实退出事件 -> StopTrace 唤醒')
        else:
            # WMI 不可用（未提权等）：注入模式验证唤醒链路本身
            watcher.inject('cmd.exe')
            step(proc_events.wait_for_wake(watcher.wake, 1000),
                 '注入模式：事件到达 -> wake 置位')
            watcher.inject('notepad.exe')
            step(not proc_events.wait_for_wake(watcher.wake, 300),
                 '注入不在名单的进程：不唤醒（过滤正确）')
        watcher.stop()

        print('[3] 主循环级：注入事件 -> 30s 低频兜底下 1 秒内跑一轮')
        g = temp_guard()
        ticks = []

        def fake_tick():
            ticks.append(time.time())
        g.tick = fake_tick
        g.running = True
        evg = FakeEv(ok=True)
        g.ev = evg
        t = threading.Thread(target=g.run, daemon=True)
        t.start()
        time.sleep(0.8)          # 主循环进入低频等待
        base_n = len(ticks)
        t0 = time.time()
        evg.set()
        time.sleep(1.2)
        step(len(ticks) > base_n and (ticks[-1] - t0) < 1.0,
             '事件置位 -> tick 立即发生（延迟 %.2fs，总 tick %d）'
             % (ticks[-1] - t0, len(ticks)))
        g.running = False
        evg.set()                # 再叫一次，别让主循环睡满 30s
        t.join(timeout=5)
        step(not t.is_alive(), '主循环在事件帮助下干净退出')

        print('[4] 回落：事件不可用 -> 纯轮询节拍不受影响')
        g = temp_guard()
        ticks2 = []
        g.tick = lambda: ticks2.append(time.time())
        g.ev = FakeEv(ok=False)
        g.running = True
        t = threading.Thread(target=g.run, daemon=True)
        t.start()
        time.sleep(3.0)
        g.running = False
        t.join(timeout=5)
        step(not t.is_alive(), '回落模式主循环正常退出')
        if len(ticks2) >= 2:
            gaps = [b - a for a, b in zip(ticks2, ticks2[1:])]
            step(all(1.0 <= gap <= 4.5 for gap in gaps),
                 'tick 间隔符合 poll_delay（%.2f~%.2fs，poll=%.1fs）'
                 % (min(gaps), max(gaps), g.poll_delay()))
        else:
            step(len(ticks2) >= 1, '至少跑过 %d 轮' % len(ticks2))

        print('[5] 看门狗：低频兜底（30s）下 stop.flag 也要 ≤4s 生效')
        import guard_core as gc
        g = temp_guard()
        g.tick = lambda: None
        evg = FakeEv(ok=True)
        g.ev = evg
        g.running = True
        t = threading.Thread(target=g.run, daemon=True)
        t.start()
        time.sleep(0.8)               # 主循环进入 30s 等待；看门狗已启动
        t0 = time.time()
        open(gc.STOP_PATH, 'w').close()
        t.join(timeout=6)
        dt = time.time() - t0
        step(not t.is_alive() and dt < 5.0,
             'stop.flag 写入 -> 主循环 %.1fs 内退出（看门狗叫醒）' % dt)

        print('[6] 诊断：HRESULT 人话 / status() 降级说明 / 失败后自动重订阅')
        step(proc_events.describe_hr(0x80041003).startswith('拒绝访问'),
             '0x80041003 -> %s' % proc_events.describe_hr(0x80041003))
        step(proc_events.describe_hr(0x80070422).startswith('WMI 服务'),
             '0x80070422 -> 提示 WMI 服务未启动')
        step(proc_events.describe_hr(0x80070057) == 'Win32 错误码 87',
             'Win32 设施码按十进制给出')
        step(proc_events.describe_hr(0x8004FFFF) == '错误码 0x8004ffff'
             and proc_events.describe_hr(0) == '',
             '未知码照实写；0 不产出噪音')

        # v2.18.2：WMI 的 Next 正常超时是 WBEM_S_TIMEDOUT=0x00040004（非错误）。
        # 旧常量 0x00040001 是 WBEM_S_ALREADY_EXISTS，曾把超时误判为订阅断线。
        step(proc_events.WBEM_S_TIMEDOUT == 0x00040004
             and '超时' in proc_events.describe_hr(0x00040004),
             'WMI 超时常量按微软定义 0x00040004，且提示为非错误')
        w3 = proc_events.ProcEventWatcher(lambda: {'cmd.exe'},
                                          proc_events.make_wake_event())
        w3._mark('start', True, '')
        w3._mark('stop', True, '')
        orig_call, orig_release = proc_events._com_call, proc_events._release
        hits = []

        def fake_next(*a, **k):
            hits.append(1)
            if len(hits) >= 2:
                w3._stop.set()
            return 0x00040004

        proc_events._com_call = fake_next
        proc_events._release = lambda p: None
        try:
            w3._consume('start', object())
        finally:
            proc_events._com_call, proc_events._release = orig_call, orig_release
        step(w3.ok and w3.status()['mode'] == 'event' and len(hits) >= 2,
             'Next 返回 0x00040004 不降级、不重建订阅（%d 轮）' % len(hits))

        wlogs = []
        w = proc_events.ProcEventWatcher(lambda: {'cmd.exe'},
                                         proc_events.make_wake_event(),
                                         log=lambda m: wlogs.append(str(m)))
        w._mark('start', False, 'ConnectServer 失败 (hr=0x80041003)',
                hr=0x80041003, stage='ConnectServer')
        st = w.status()
        step(st['mode'] == 'poll' and not st['ok'],
             '一条订阅失败 -> mode=poll（面板据此说轮询兜底）')
        step('轮询' in st['hint'] and '拒绝访问' in st['hint'],
             '降级说明带原因：%s' % st['hint'])
        step(st['kinds']['start']['hr'] == 0x80041003
             and st['kinds']['start']['stage'] == 'ConnectServer',
             'status() 带 HRESULT 与失败阶段（可直接进诊断日志）')
        step(any('0x80041003' in m for m in wlogs), '日志里带 HRESULT 原文')
        w._mark('start', True, '')
        w._mark('stop', True, '')
        step(w.status()['mode'] == 'event', '两条订阅都健康 -> mode=event')

        # 重订阅：WMI 服务被临时停掉时，守护不该永久退回轮询。
        calls = []
        orig_sub = proc_events.ProcEventWatcher._subscribe
        orig_min, orig_max = proc_events.RETRY_MIN_SEC, proc_events.RETRY_MAX_SEC

        def boom(self, kind):
            calls.append(kind)
            err = OSError('ConnectServer 失败 (hr=0x80070422)')
            err.hr = 0x80070422
            err.stage = 'ConnectServer'
            raise err

        proc_events.ProcEventWatcher._subscribe = boom
        proc_events.RETRY_MIN_SEC = 0.02
        proc_events.RETRY_MAX_SEC = 0.05
        try:
            w2 = proc_events.ProcEventWatcher(lambda: {'cmd.exe'},
                                              proc_events.make_wake_event())
            w2.start()
            time.sleep(0.35)
            w2.stop()
        finally:
            proc_events.ProcEventWatcher._subscribe = orig_sub
            proc_events.RETRY_MIN_SEC = orig_min
            proc_events.RETRY_MAX_SEC = orig_max
        step(len(calls) >= 4, '订阅失败后持续重试（0.35s 内 %d 次）' % len(calls))
        step(w2.resubscribes >= 2 and not w2.ok,
             '重试期间 ok=False（主循环保持纯轮询），resubscribes=%d'
             % w2.resubscribes)
    finally:
        restore_guard_globals()
        shutil.rmtree(TMP, ignore_errors=True)

    print('')
    if FAILED:
        print('FAILED %d 项：%s' % (len(FAILED), FAILED))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
