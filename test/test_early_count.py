# -*- coding: utf-8 -*-
"""提前拦截计数语义回归：arm() 不能把本局计数清零。

2026-11-12 真机日志暴露的缺陷：`arm()` 里先 `early_closed = 0` 再打印，
导致「本局提前拦截 N 次」永远是 0（实际 17:41:33 刚拦了 1 次）。
计数改为 `session_early`，只在 `begin_session()`（真正新会话）清零，
arm() 与 _stop() 都只读不清。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import popup_guard as pgmod

FAILED = []
LOGS = []
CHECKS = [0]


def log(msg):
    LOGS.append(msg)


def check(name, ok, detail=''):
    CHECKS[0] += 1
    if ok:
        print('  ok   %s' % name)
    else:
        FAILED.append(name)
        print('  FAIL %s  %s' % (name, detail))


def new_guard():
    cfg = {'enabled': True, 'watch_sec': 90, 'process_names': [],
           'path_hints': [], 'protect_names': [], 'early_close_enabled': True}
    return pgmod.PopupGuard(cfg, log, game_alive=lambda: False)


def main():
    g = new_guard()
    check('初始计数为 0', g.session_early == 0)

    # 模拟会话中拦了 3 次
    g.session_early = 3
    g.arm(time.time())
    check('arm() 后计数保持不变（这是原缺陷点）', g.session_early == 3,
          'arm 之后变成 %d' % g.session_early)

    start_logs = [l for l in LOGS if '弹窗监视开始' in l]
    check('arm() 日志打印真实计数 3', bool(start_logs) and '本局提前拦截 3 次' in start_logs[-1],
          start_logs[-1] if start_logs else '没有该日志')

    # 抖动恢复不重开会话，计数要保留
    g.arm(time.time())
    check('再次 arm()（会话抖动）计数仍为 3', g.session_early == 3,
          '变成 %d' % g.session_early)

    # 真正的新会话才清零
    g.begin_session()
    check('begin_session() 清零', g.session_early == 0)

    g.session_early = 2
    g.session_early = 0 if False else g.session_early
    g._stop('到时间')
    stop_logs = [l for l in LOGS if '弹窗监视结束' in l]
    check('_stop() 打印真实计数 2', bool(stop_logs) and '提前拦截 2 次' in stop_logs[-1],
          stop_logs[-1] if stop_logs else '没有该日志')

    print()
    if FAILED:
        print('FAILED %d / %d' % (len(FAILED), CHECKS[0]))
        return 1
    print('ALL PASS (%d checks)' % CHECKS[0])
    return 0


if __name__ == '__main__':
    sys.exit(main())
