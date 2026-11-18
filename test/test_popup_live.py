# -*- coding: utf-8 -*-
"""现场验证：用真实配置 + 真实进程表，跑一次弹窗宿主清理。

游戏已退出（TGame 不在），此时应当把 clientbrowser2\ClientBrowser.exe 连同
它的 CEF 子进程一起结束。只读 + 结束进程，不碰文件。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.join(HERE, '..', 'app')
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
import popup_guard as pg

cfg = json.load(open(os.path.join(BASE, 'config.json'), encoding='utf-8-sig'))
pc = cfg.get('popup_guard', {}) or {}
print('配置: enabled=%s kill_tree=%s' % (pc.get('enabled'), pc.get('kill_tree')))
print('      process_names=%s' % pc.get('process_names'))
print('      path_hints=%s' % pc.get('path_hints'))
print('      protect_names=%s' % pc.get('protect_names'))


def dump(tag):
    rows = pg.enum_procs()
    out = []
    for pid, exe, ppid in rows:
        if 'clientbrowser' in exe.lower():
            path = pg.process_path(pid)
            out.append((pid, exe, ppid, path))
    print('\n[%s] ClientBrowser 相关进程 %d 个:' % (tag, len(out)))
    for pid, exe, ppid, path in sorted(out):
        print('   pid=%-6d ppid=%-6d %s' % (pid, ppid, path or exe))
    return set(p for p, _e, _pp, _p in out)


before = dump('清理前')

logs = []
guard = pg.PopupGuard(pc, lambda m: (logs.append(m), print('   LOG', m)), game_alive=lambda: False)
t0 = time.time()
n = guard.sweep('现场验证')
print('\n结束主进程数 =', n, '耗时 %.2fs' % (time.time() - t0))

time.sleep(1.2)
after = dump('清理后')
print('\n剩余 pid:', sorted(after) or '无')
if before and not after:
    print('RESULT: PASS —— 全部清理干净')
elif not before:
    print('RESULT: SKIP —— 本来就没有宿主进程在跑')
else:
    print('RESULT: FAIL —— 还剩 %d 个' % len(after))
