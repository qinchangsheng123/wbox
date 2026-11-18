# -*- coding: utf-8 -*-
"""弹窗拦截离线测试：用真实 Tk 窗口验证「命中就关、不命中不动」。

把 path_keywords 设成 'python'，让测试进程自己的路径进入白名单，
这样能真实走到 PostMessage + Tk 处理 WM_CLOSE 的整条链路。
"""
import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import popup_guard as pgmod

FAILED = []
LOGS = []


def log(msg):
    LOGS.append(msg)
    print('    log:', msg)


def exists(w):
    """窗口被 WM_CLOSE 销毁后，Tk 会把解释器一起收掉，winfo_exists 会抛 TclError。"""
    try:
        return bool(w.winfo_exists())
    except tk.TclError:
        return False


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILED.append(msg)


def cfg(**kw):
    base = {
        'enabled': True,
        'delay_sec': 0,
        'watch_sec': 30,
        'title_keywords': ['直播', '赛事', 'live'],
        'path_keywords': ['python'],
        # 候选窗口判定要求"进程名在 process_names 里"或"路径命中 path_hints"，
        # 本测试用 python 自己的路径当游戏目录，所以 path_hints 也指向它。
        'path_hints': ['python'],
        'process_names': [],
        'protect_names': [],
        'log_all_new': True,
    }
    base.update(kw)
    return base


ME = os.getpid()
PROCS = {ME: 'python.exe'}

print('[1] 命中：标题含"直播" + 进程路径命中 → 发 WM_CLOSE 关掉')
pg = pgmod.PopupGuard(cfg(), log)
now = time.time()
pg.arm(now, PROCS)
win = tk.Tk()
win.title('游戏直播 - 赛事推荐')
win.geometry('240x120+60+60')
win.update()
pg.tick(now + 1, PROCS)
win.update()                       # 让 Tk 处理 WM_CLOSE
alive = exists(win)
check(not alive, '窗口已被关闭（winfo_exists=%d）' % alive)
check(any('已关闭弹窗' in m for m in LOGS), '日志里有"已关闭弹窗"')

print('[2] 不命中：标题不含关键词 → 原样保留')
LOGS.clear()
pg2 = pgmod.PopupGuard(cfg(), log)
now2 = time.time()
pg2.arm(now2, PROCS)
win2 = tk.Tk()
win2.title('记事本 - 随便写点')
win2.geometry('240x120+80+80')
win2.update()
pg2.tick(now2 + 1, PROCS)
win2.update()
still = exists(win2)
check(still, '窗口仍在（winfo_exists=%d）' % still)
check(not any('已关闭弹窗' in m for m in LOGS), '没有误关')
check(any('新窗口' in m for m in LOGS), '诊断日志记录了新窗口')

print('[3] 保护名单：标题含"直播"但进程被保护 → 不关')
LOGS.clear()
pg3 = pgmod.PopupGuard(cfg(protect_names=['python.exe']), log)
now3 = time.time()
pg3.arm(now3, PROCS)
win3 = tk.Tk()
win3.title('直播 - 受保护进程')
win3.geometry('240x120+100+100')
win3.update()
pg3.tick(now3 + 1, PROCS)
win3.update()
check(exists(win3), '保护名单里的进程未被关窗')

print('[4] 路径白名单：标题像直播但进程路径不在腾讯系 → 不关')
LOGS.clear()
pg4 = pgmod.PopupGuard(cfg(path_keywords=['wegame', 'txgame']), log)
now4 = time.time()
pg4.arm(now4, PROCS)
win4 = tk.Tk()
win4.title('某个直播间')
win4.geometry('240x120+120+120')
win4.update()
pg4.tick(now4 + 1, PROCS)
win4.update()
check(exists(win4), '路径不在白名单，未被关窗')

print('[5] 基线窗口不碰：arm 之前就存在的窗口永不处理')
LOGS.clear()
win5 = tk.Tk()
win5.title('直播 - arm 之前就在')
win5.geometry('240x120+140+140')
win5.update()
pg5 = pgmod.PopupGuard(cfg(), log)
now5 = time.time()
pg5.arm(now5, PROCS)               # 此时 win5 已存在，进基线
pg5.tick(now5 + 1, PROCS)
win5.update()
check(exists(win5), '基线窗口未被关窗')
check(pg5.seen == 0, '基线窗口没被算成新窗口（seen=%d）' % pg5.seen)

print('[6] 到期自动收摊')
LOGS.clear()
pg6 = pgmod.PopupGuard(cfg(watch_sec=5), log)
now6 = time.time()
pg6.arm(now6, PROCS)
pg6.tick(now6 + 6, PROCS)
check(pg6.armed is False, 'armed 已置 False')
check(any('弹窗监视结束' in m for m in LOGS), '日志里有结束汇总')

print('[7] 开关关闭时 arm 不生效')
pg7 = pgmod.PopupGuard(cfg(enabled=False), log)
check(pg7.arm(time.time(), PROCS) is False, 'enabled=false 时 arm 返回 False')

print('[8] 系统外壳的一次性窗口不算新弹窗')
pg8 = pgmod.PopupGuard(cfg(), log)
# 实测：任务栏悬停预览会同时冒出 tooltips_class32 和 TaskListOverlayWnd 两个空标题窗口
check(pg8._shell_noise('explorer.exe', 'tooltips_class32'), '任务栏提示窗被判为噪声')
check(pg8._shell_noise('explorer.exe', 'TaskListOverlayWnd'), '任务栏悬停预览被判为噪声')
check(pg8._shell_noise('', 'Shell_TrayWnd'), '任务栏本体被判为噪声')
check(pg8._shell_noise('explorer.exe', '#32770'), 'explorer 的普通窗口也算噪声（进程名命中）')
check(not pg8._shell_noise('ClientBrowser.exe', '#32770'), '真弹窗宿主不被误判成噪声')
check(not pg8._shell_noise('wegame.exe', 'CefTopWindow'), 'WeGame 主界面不被误判成噪声')

for w in (win2, win3, win4, win5):
    try:
        w.destroy()
    except Exception:
        pass

print('[9] 提前拦截：游戏还在跑时，宿主弹窗窗口一出现就发 WM_CLOSE')
LOGS.clear()
orig_enum = pgmod.enum_windows
orig_path = pgmod.process_path
orig_close = pgmod.close_window
closed_hwnds = []
HOST = r'G:\WeGameApps\DemoGame\Binaries\Win64\clientbrowser2\ClientBrowser.exe'
HOST_WINS = [(9101, 555, '#32770', ''),
             (9102, 777, 'Chrome_WidgetWin_1', '用户自己的浏览器'),
             (9103, 555, '#32770', '')]
pgmod.enum_windows = lambda include_untitled=False: list(HOST_WINS)
pgmod.process_path = lambda pid: {
    555: HOST,
    777: r'C:\Program Files\Google\Chrome\Application\chrome.exe',
}.get(pid, '')
pgmod.close_window = lambda hwnd: (closed_hwnds.append(hwnd) or
                                   (True, '已发送 WM_CLOSE'))
try:
    fake = {555: 'clientbrowser.exe', 777: 'chrome.exe'}
    fast_cfg = cfg(process_names=['clientbrowser.exe'],
                   path_hints=['\\clientbrowser2\\'],
                   early_close_enabled=True, early_max_tries=3)
    pg9 = pgmod.PopupGuard(fast_cfg, log)
    n1 = pg9.watch_early(fake)
    check(n1 == 2, '两个宿主窗口都发了 WM_CLOSE，chrome 窗口不动: %d' % n1)
    check(sorted(closed_hwnds) == [9101, 9103], '只关命中 exe+路径的窗口')
    check(any('提前拦截弹窗' in m for m in LOGS), '有提前拦截日志')
    n2 = pg9.watch_early(fake)
    n3 = pg9.watch_early(fake)
    n4 = pg9.watch_early(fake)
    check(n2 == 2 and n3 == 2 and n4 == 0,
          'WM_CLOSE 无效时最多发 3 轮就停（%s）' % str((n2, n3, n4)))
    check(any('WM_CLOSE 连发' in m for m in LOGS), '无效时留可诊断日志')
    # 窗口消失后允许同一个 hwnd 重新计数（系统会复用 hwnd）
    pgmod.enum_windows = lambda include_untitled=False: []
    pg9.watch_early(fake)
    pgmod.enum_windows = lambda include_untitled=False: list(HOST_WINS)
    closed_hwnds[:] = []
    check(pg9.watch_early(fake) == 2, '窗口重新出现后能再拦一次')
    # 开关关掉就完全不动手
    closed_hwnds[:] = []
    off_cfg = cfg(process_names=['clientbrowser.exe'],
                  path_hints=['\\clientbrowser2\\'],
                  early_close_enabled=False)
    pg9b = pgmod.PopupGuard(off_cfg, log)
    check(pg9b.watch_early(fake) == 0 and not closed_hwnds,
          'early_close_enabled=false 时不碰任何窗口')
    # 名字命中但路径不在游戏目录，不能动手
    pgmod.enum_windows = lambda include_untitled=False: [
        (9104, 888, '#32770', '')]
    pgmod.process_path = lambda pid: r'C:\Windows\System32\ClientBrowser.exe'
    closed_hwnds[:] = []
    pg9c = pgmod.PopupGuard(fast_cfg, log)
    check(pg9c.watch_early({888: 'clientbrowser.exe'}) == 0 and not closed_hwnds,
          '同名 exe 但路径不在游戏目录时不误关')
finally:
    pgmod.enum_windows = orig_enum
    pgmod.process_path = orig_path
    pgmod.close_window = orig_close

print('[10] 双速轮询：会话 0.4s / 空闲 2s')
import guard_core as gcore
g = gcore.Guard.__new__(gcore.Guard)
g.cfg = {'poll_interval_sec': 2.0, 'poll_fast_sec': 0.4}
check(g.poll_delay(True) == 0.4, '游戏会话用快节拍')
check(g.poll_delay(False) == 2.0, '空闲用慢节拍')
g.cfg = {'poll_interval_sec': 1.0, 'poll_fast_sec': 5.0}
check(g.poll_delay(True) == 1.0, 'fast 不会比 idle 还慢')
g.cfg = {}
check(g.poll_delay(True) == 0.4 and g.poll_delay(False) == 2.0,
      '配置缺段时用内置默认值')
g.cfg = {'poll_interval_sec': 2.0, 'poll_fast_sec': 0.0}
check(g.poll_delay(True) == 0.2, '非法 fast 夹到 0.2s 下限')

print('[11] path_hints 的空白项 / 假值必须回落，不能退化成"任意路径命中"')
WIN_CB = r'C:\Windows\System32\ClientBrowser.exe'
GAME_CB = r'G:\WeGameApps\DemoGame\Binaries\Win64\clientbrowser2\ClientBrowser.exe'
base = cfg(process_names=['clientbrowser.exe'])


def hint(v, path):
    return pgmod.PopupGuard(dict(base, path_hints=v), log)._hint_hit(path)


check(hint([''], WIN_CB) is False, "path_hints=[''] 不再命中任意路径（QA 复现例）")
check(hint([''], GAME_CB) is True, "path_hints=[''] 回落到内置值，游戏目录仍命中")
check(hint(['   '], WIN_CB) is False, '全空白项同样回落')
check(hint([], GAME_CB) is True, '空列表回落内置 clientbrowser2（功能不静默失效）')
check(hint(None, GAME_CB) is True, 'None 回落内置 clientbrowser2')
check(hint(['\\clientbrowser2\\ '], GAME_CB) is True, '尾空格被 strip，不再静默失效')
check(hint(['\\clientbrowser2\\'], WIN_CB) is False, '不在游戏目录 → 不命中')
check(hint([''], '') is False, '空路径一律不命中')
pg_guard = pgmod.PopupGuard(dict(base, path_hints=['']), log)
check(pg_guard._target(1, WIN_CB) is False,
      "path_hints=[''] 时 _target 对同名 exe 仍为 False（QA 复现例）")
check(pg_guard._target(2, GAME_CB) is True, 'path_hints=[\'\'] 时游戏目录内的同名 exe 仍命中')

print('')
print('ALL PASS' if not FAILED else 'FAILED: %d -> %s' % (len(FAILED), FAILED))
sys.exit(1 if FAILED else 0)
