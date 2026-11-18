# -*- coding: utf-8 -*-
"""端到端验证：启动某 WeGame 游戏 → 宿主被拉起 → 结束游戏 → 守护应该清掉宿主。

只读 + 启动/结束游戏进程，不动文件。
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src'))
import popup_guard as pg


def _game_from_cache():
    """从守护生成的游戏缓存里取一个存在的游戏 exe；取不到返回 None。"""
    cache = os.path.join(HERE, '..', 'app', 'games_cache.json')
    try:
        with open(cache, 'r', encoding='utf-8') as f:
            games = (json.load(f) or {}).get('games') or []
    except (OSError, ValueError):
        return None
    for item in games:
        exe = (item or {}).get('exe')
        if exe and os.path.isfile(exe):
            return exe
    return None


# 游戏路径不写死：环境变量优先，其次读本机游戏库缓存
GAME = os.environ.get('WINGMAN_E2E_GAME') or _game_from_cache()
if not GAME:
    print('缺少游戏路径。二选一：')
    print('  1) 设环境变量 WINGMAN_E2E_GAME=<游戏 exe 完整路径>')
    print('  2) 先跑一次守护，让它生成 app/games_cache.json')
    sys.exit(2)

LOG = os.path.join(HERE, '..', 'app', 'guard.log')


def procs(name_key):
    out = []
    for pid, exe, ppid in pg.enum_procs():
        if name_key in exe.lower():
            out.append((pid, exe, ppid, pg.process_path(pid)))
    return out


def log_tail(n=40):
    try:
        data = open(LOG, 'rb').read().decode('utf-8', 'replace')
    except OSError:
        return []
    return data.splitlines()[-n:]


print('启动游戏:', GAME)
t0 = time.time()
try:
    subprocess.Popen([GAME], cwd=os.path.dirname(GAME),
                     creationflags=0x00000008)   # DETACHED_PROCESS
except Exception as e:
    print('启动失败: %r' % (e,))
    sys.exit(1)

host = None
game_pid = None
for i in range(40):
    time.sleep(1.0)
    g = procs('tgame.exe')
    h = [x for x in procs('clientbrowser.exe')
         if 'clientbrowser2' in (x[3] or '').lower()]
    if g and game_pid is None:
        game_pid = g[0][0]
        print('%5.1fs TGame 已起 pid=%d' % (time.time() - t0, game_pid))
    if h and host is None:
        host = h[0][0]
        print('%5.1fs 弹窗宿主已起 pid=%d  路径=%s' % (time.time() - t0, host, h[0][3]))
        break
    if not g and i > 12:
        print('%5.1fs TGame 没起来（可能无登录态直接退了）' % (time.time() - t0))
        break

if host is None:
    print('未观察到弹窗宿主，放弃本次端到端')
    sys.exit(2)

# 让游戏多跑一会，确认守护不会在游戏期间误杀宿主
time.sleep(6)
still = [x for x in procs('clientbrowser.exe')
         if 'clientbrowser2' in (x[3] or '').lower()]
print('游戏运行中：宿主仍在 %s（应为 True，游戏期间不杀）' % bool(still))

print('\n结束游戏进程（模拟退出）...')
for pid, exe, _pp, _p in procs('tgame.exe'):
    ok, note = pg.kill_process(pid)
    print('  结束 TGame pid=%d -> %s %s' % (pid, ok, note))

# 守护轮询 2 秒 + 会话结束判定，给 20 秒
for i in range(20):
    time.sleep(1.0)
    left = [x for x in procs('clientbrowser.exe')
            if 'clientbrowser2' in (x[3] or '').lower()]
    if not left:
        print('%5.1fs 宿主已清空（守护完成清理，用时约 %d 秒）' % (time.time() - t0, i + 1))
        break
else:
    print('20 秒内宿主仍在：%r' % (procs('clientbrowser.exe'),))

print('\n--- guard.log 尾部 ---')
for line in log_tail(24):
    print(line)
