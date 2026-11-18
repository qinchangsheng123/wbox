# -*- coding: utf-8 -*-
"""v2.5.0-② 心跳空闲 CPU 探针（手工测量脚本，不是测试，不进套件）。

把真实 Guard 内核跑在临时目录里（空插件目录、默认配置、无游戏会话），
分三态各测一段空闲窗口的进程 CPU 时间（kernel+user，含全部线程）：

  nobeat   不起心跳服务（≈ v2.4.0 的行为基线）
  beat     起心跳服务，但没有任何客户端来连（服务线程全程阻塞在等待队列）
  beat+cli 起心跳服务 + 模拟面板每 2 秒连一次（真实面板的节拍）

用法：C:\\Python38\\python.exe test\\beat_cpu_probe.py <临时目录> <模式> <秒数>
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

BASE = os.path.abspath(sys.argv[1])
MODE = sys.argv[2]
DUR = float(sys.argv[3]) if len(sys.argv) > 3 else 45.0

os.makedirs(os.path.join(BASE, 'plugins'), exist_ok=True)
open(os.path.join(BASE, 'config.json'), 'w').write('{}')

import guard_core  # noqa: E402

guard_core.BASE = BASE
guard_core.CONFIG_PATH = os.path.join(BASE, 'config.json')
guard_core.LOG_PATH = os.path.join(BASE, 'guard.log')
guard_core.PID_PATH = os.path.join(BASE, 'guard.pid')
guard_core.STOP_PATH = os.path.join(BASE, 'stop.flag')
guard_core.PLUGINS_DIR = os.path.join(BASE, 'plugins')
guard_core.RELOAD_FLAG_PATH = os.path.join(BASE, 'plugins_reload.flag')

LOGS = []
guard_core.log = lambda m: LOGS.append(str(m))


class QuietGuard(guard_core.Guard):
    """把日志塞进内存列表，避免刷屏；行为与生产 Guard 一致。"""


def main():
    g = QuietGuard()
    g.log = lambda m: LOGS.append(str(m))
    beat = None
    ev = None
    cli_stop = threading.Event()
    if 'beat' in MODE:
        beat = guard_core._start_beat(g)
        if beat is None:
            print('ERROR: beat failed to start')
            return 2
    if MODE.startswith('ev'):
        import proc_events
        ev = proc_events.attach(lambda: g.watch, log=LOGS.append)
        if ev is None:
            print('ERROR: proc-events failed to start')
            return 2
        g.ev = ev

    def stopper():
        time.sleep(DUR)
        g.running = False
        cli_stop.set()
        if g.ev is not None and getattr(g.ev, 'ok', False):
            import proc_events
            proc_events.poke(g.ev)

    threading.Thread(target=stopper, daemon=True).start()

    if MODE == 'beat+cli':
        import guard_beat

        def client():
            while not cli_stop.is_set():
                guard_beat.read_status(guard_beat.PIPE_NAME, deadline_ms=400)
                cli_stop.wait(2.0)
        threading.Thread(target=client, daemon=True).start()

    cpu0 = time.process_time()
    w0 = time.time()
    g.run()
    cpu = time.process_time() - cpu0
    wall = time.time() - w0
    if beat is not None:
        beat.stop()
    if ev is not None:
        ev.stop()
    print('%-8s wall=%.1fs cpu=%.3fs cpu_pct=%.3f%% ev_ok=%s wakes=%d'
          % (MODE, wall, cpu, 100.0 * cpu / wall,
             getattr(ev, 'ok', None), getattr(ev, 'wakes', 0)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
