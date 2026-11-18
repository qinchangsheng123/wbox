# -*- coding: utf-8 -*-
"""弹窗宿主清理的进程层测试（真实进程，不是替身）。

做法：把系统 ping.exe 复制成 ClientBrowser.exe，放在两个位置
  A) <tmp>\\clientbrowser2\\ClientBrowser.exe  ← 名字 + 路径都命中，应该被杀
  B) <tmp>\\other\\ClientBrowser.exe           ← 只有名字命中，不该被杀
然后验证四种情况：
  [1] 游戏不在跑 → A 被杀（含子进程），B 活着
  [2] 游戏在跑   → A 也不杀（游戏内嵌页面还要用）
  [3] A 进保护名单 → 不杀
  [4] 只对进程树根动手，不刷一串失败日志
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
import popup_guard as pg

CFG = json.load(open(os.path.join(HERE, '..', 'app', 'config.json'),
                    encoding='utf-8-sig'))['popup_guard']
TMP = os.path.join(tempfile.gettempdir(), 'abg_popup_test')
CREATE_NO_WINDOW = 0x08000000

fails = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        fails.append(msg)


def alive(pid):
    return any(p == pid for p, _e, _pp in pg.enum_procs())


def spawn(exe):
    return subprocess.Popen([exe, '-n', '300', '127.0.0.1'],
                            creationflags=CREATE_NO_WINDOW)


def setup():
    shutil.rmtree(TMP, ignore_errors=True)
    a = os.path.join(TMP, 'clientbrowser2')
    b = os.path.join(TMP, 'other')
    os.makedirs(a)
    os.makedirs(b)
    src = os.path.join(os.environ['SystemRoot'], 'System32', 'ping.exe')
    exe_a = os.path.join(a, 'ClientBrowser.exe')
    exe_b = os.path.join(b, 'ClientBrowser.exe')
    shutil.copy2(src, exe_a)
    shutil.copy2(src, exe_b)
    return exe_a, exe_b


def make_guard(cfg, alive_flag):
    logs = []
    g = pg.PopupGuard(cfg,
                      lambda m: (logs.append(m), print('    log:', m)),
                      game_alive=lambda: alive_flag[0])
    return g, logs


exe_a, exe_b = setup()
print('假宿主 A:', exe_a)
print('假宿主 B:', exe_b)

# 生产守护在跑（含监视期）时，会把本测试的假宿主当成真弹窗宿主清掉，
# 导致「假宿主没起来 / sweep 返回 0」这类假失败。先拦一道，别让人去查错代码。
for _pid, _exe, _ppid in pg.enum_procs():
    _low = (_exe or '').lower()
    if 'wbox.exe' in _low or 'wingman.exe' in _low:
        _name = 'WBOX' if 'wbox.exe' in _low else 'Wingman'
        print('\nSKIP —— 守护进程 %s 正在运行（pid %d）。' % (_name, _pid))
        print('它会把假宿主 ClientBrowser.exe 当成真弹窗宿主清理，本测试必然假失败。')
        print('先停守护再跑：')
        print('  Stop-ScheduledTask -TaskName %s; Stop-Process -Name %s -Force'
              % (_name, _name))
        sys.exit(0)

# ---------- [1] 游戏不在跑：A 该被杀，B 不该 ----------
print('\n[1] 游戏不在跑 → 名字+路径都命中的才杀')
pa = spawn(exe_a)
pb = spawn(exe_b)
time.sleep(1.0)
check(alive(pa.pid) and alive(pb.pid), '两个假宿主都起来了')
flag = [False]
g, logs = make_guard(CFG, flag)
n = g.sweep('测试1')
time.sleep(1.0)
check(n == 1, '结束了 1 个主进程（实际 %d）' % n)
check(not alive(pa.pid), 'A（clientbrowser2 目录）已被结束')
check(alive(pb.pid), 'B（other 目录，只名字命中）没被误杀')
check(not any('失败' in m for m in logs), '没有刷失败日志')

# ---------- [2] 游戏在跑：一个都不杀 ----------
print('\n[2] 游戏在跑 → 一律不动')
pa2 = spawn(exe_a)
time.sleep(1.0)
flag = [True]
g2, logs2 = make_guard(CFG, flag)
n = g2.sweep('测试2')
time.sleep(0.6)
check(n == 0, '游戏在跑时 sweep 返回 0')
check(alive(pa2.pid), 'A 在游戏运行期间没被杀')

# ---------- [3] 保护名单：不杀 ----------
print('\n[3] A 进保护名单 → 不杀')
cfg3 = dict(CFG)
cfg3['protect_names'] = list(CFG['protect_names']) + ['clientbrowser.exe']
flag = [False]
g3, _ = make_guard(cfg3, flag)
n = g3.sweep('测试3')
time.sleep(0.6)
check(n == 0, '保护名单里的进程 sweep 返回 0')
check(alive(pa2.pid), 'A 仍在运行')

# ---------- [4] kill_tree 关闭：不动进程 ----------
print('\n[4] kill_tree=false → 只关窗口，不动进程')
cfg4 = dict(CFG)
cfg4['kill_tree'] = False
g4, _ = make_guard(cfg4, [False])
n = g4.sweep('测试4')
time.sleep(0.5)
check(n == 0, 'kill_tree=false 时 sweep 返回 0')
check(alive(pa2.pid), 'A 仍在运行')

# ---------- 清理 ----------
print('\n清理测试进程...')
for p in (pa, pb, pa2):
    if alive(p.pid):
        pg.kill_process(p.pid)
time.sleep(0.8)
left = [pid for pid, exe, _pp in pg.enum_procs()
        if 'clientbrowser' in exe.lower() and TMP.lower() in (pg.process_path(pid) or '').lower()]
shutil.rmtree(TMP, ignore_errors=True)
check(not left, '临时目录下的假宿主已清空（残留 %r）' % (left,))

print('\n' + ('ALL PASS' if not fails else 'FAILED %d 项: %r' % (len(fails), fails)))
sys.exit(0 if not fails else 1)
