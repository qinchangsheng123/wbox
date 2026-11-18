# -*- coding: utf-8 -*-
"""离线验证内核会话状态机 + 钩子分发顺序（用假插件，不碰真进程）。

断言（对应 design\\plugin-arch.md §3.4 与任务书 §一.7）：
  1. 一轮 tick 的分发顺序：idle→active 发 on_session_start 再发 on_tick；
     active→grace 发 on_session_grace 再发 on_tick；grace 到期发 on_session_close
     （close 时仍是 grace，on_tick 已经能看到 idle）；grace→active 发 on_session_resume。
  2. `procs is None` 的那一轮：零钩子调用、零会话迁移。
  3. `on_session_close` 里调 retry_close → 状态保持 grace、下一轮再发一次 close；
     不调 → 转 idle。
  4. `ctx.request_fast_poll()` 让 `poll_delay()` 在空闲时也走快节拍。
  5. 钩子遍历顺序 = (order, id)。
"""
import io
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import plugin_api
import plugin_host
import guard_core

FAILS = []
REC_PATH = os.path.join(tempfile.mkdtemp(prefix='abg_hooks_rec_'), 'rec.txt')


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


# 假插件：把每次钩子调用追加到 ABG_HOOKS_REC 指向的文件（跨插件保序）
RECORDER = '''# -*- coding: utf-8 -*-
import io
import os

import plugin_api

REC_PATH = os.environ['ABG_HOOKS_REC']
RETRY = {}


def _w(pid, hook, a='', b=''):
    with io.open(REC_PATH, 'a', encoding='utf-8') as f:
        f.write(u'%s|%s|%s|%s\\n' % (pid, hook, a, b))


class Plugin(plugin_api.WBOXPlugin):
    DEFAULT_CONFIG = {}

    def on_config(self, cfg):
        pass

    def on_load(self):
        _w(self.ctx.id, 'load')

    def on_start(self):
        _w(self.ctx.id, 'start')

    def on_tick(self, tick):
        _w(self.ctx.id, 'tick', tick.seq, tick.session.state)

    def on_session_start(self, tick):
        _w(self.ctx.id, 'session_start', tick.seq, tick.session.state)

    def on_session_resume(self, tick):
        _w(self.ctx.id, 'session_resume', tick.seq, tick.session.state)

    def on_session_grace(self, tick):
        _w(self.ctx.id, 'session_grace', tick.seq, tick.session.state)
        # 弹窗插件就是这么登记快节拍的
        self.ctx.request_fast_poll(tick.now + 90)

    def on_session_close(self, tick):
        _w(self.ctx.id, 'session_close', tick.seq, tick.session.state)
        left = RETRY.get(self.ctx.id, 0)
        if left > 0:
            RETRY[self.ctx.id] = left - 1
            self.ctx.retry_close(2)

    def on_exit(self, reason):
        _w(self.ctx.id, 'exit', 0, reason)
'''


def reset_rec():
    with io.open(REC_PATH, 'w', encoding='utf-8') as f:
        f.write(u'')
    os.environ['ABG_HOOKS_REC'] = REC_PATH


def read_rec():
    with io.open(REC_PATH, 'r', encoding='utf-8') as f:
        rows = [ln.rstrip('\n').split('|') for ln in f if ln.strip()]
    return [(r[0], r[1], r[3]) for r in rows]


def make_base(orders=(10, 20)):
    base = tempfile.mkdtemp(prefix='abg_hooks_')
    root = os.path.join(base, 'plugins')
    os.makedirs(root)
    for order in orders:
        pid = 'fake%d' % order
        d = os.path.join(root, pid)
        os.makedirs(d)
        with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
            json.dump({'id': pid, 'name': pid, 'version': '1.0.0', 'api': 1,
                       'icon': 'dot', 'order': order, 'toggle': [pid + '_enabled']},
                      f, ensure_ascii=False)
        with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
            f.write(RECORDER)
    return base


def make_guard(base, procs_box, grace=8, orders=(10, 20)):
    """真 Guard + 假插件宿主 + 可控进程表（patch 模块级 snapshot）。"""
    host = plugin_host.PluginHost(base, log_func=lambda m: None)
    host.load_all(None, write_state=False)
    g = guard_core.Guard(host=host)
    g.reload_config = lambda: None            # 不读真 config.json
    g.cfg = dict(guard_core.DEFAULTS)
    g.cfg['exit_grace_sec'] = grace
    g.cfg['poll_interval_sec'] = 2.0
    g.cfg['poll_fast_sec'] = 0.4
    g.watch = {'game.exe'}
    g.last_scan = 1e9                          # 不触发目录重扫
    g.refresh_paths = lambda procs: None
    g.path_is_game = lambda name, path: False
    g.check_clock_jump = lambda now: None
    return g


ORIG_SNAPSHOT = guard_core.snapshot

try:
    print('[1] 分发顺序：start → grace → close → tick，on_tick 永远最后')
    reset_rec()
    base = make_base()
    procs_box = {'v': {100: 'game.exe'}}
    guard_core.snapshot = lambda: procs_box.get('v')
    g = make_guard(base, procs_box, grace=8)
    g.host.dispatch('on_start')
    mark = len(read_rec())

    g.tick(now=100.0)      # idle -> active
    check(read_rec()[mark:] == [
        ('fake10', 'session_start', 'active'), ('fake20', 'session_start', 'active'),
        ('fake10', 'tick', 'active'), ('fake20', 'tick', 'active')],
        '新会话：两个插件都先 session_start 再 tick（%s）' % (read_rec()[mark:],))
    check(g.session_state == 'active' and g.session is True, 'state=active')

    mark = len(read_rec())
    g.tick(now=101.0)
    check(read_rec()[mark:] == [('fake10', 'tick', 'active'), ('fake20', 'tick', 'active')],
          '会话中只发 on_tick（%s）' % (read_rec()[mark:],))

    mark = len(read_rec())
    procs_box['v'] = {}
    g.tick(now=102.0)      # active -> grace
    check(read_rec()[mark:] == [
        ('fake10', 'session_grace', 'grace'), ('fake20', 'session_grace', 'grace'),
        ('fake10', 'tick', 'grace'), ('fake20', 'tick', 'grace')],
        '游戏退出：grace 钩子先于 on_tick（%s）' % (read_rec()[mark:],))
    check(abs(g.grace_until - 110.0) < 1e-6, 'grace_until = now + exit_grace_sec（%s）'
          % g.grace_until)

    mark = len(read_rec())
    g.tick(now=109.0)
    check(read_rec()[mark:] == [('fake10', 'tick', 'grace'), ('fake20', 'tick', 'grace')],
          '宽限期内只发 on_tick（%s）' % (read_rec()[mark:],))

    mark = len(read_rec())
    g.tick(now=110.0)      # grace 到期 -> close
    check(read_rec()[mark:] == [
        ('fake10', 'session_close', 'grace'), ('fake20', 'session_close', 'grace'),
        ('fake10', 'tick', 'idle'), ('fake20', 'tick', 'idle')],
        '到期关闭：close 时是 grace，on_tick 已是 idle（%s）' % (read_rec()[mark:],))
    check(g.session_state == 'idle' and g.session is False, 'state=idle')

    print('[2] 闪断恢复：grace -> active 发 on_session_resume，不重开会话')
    procs_box['v'] = {100: 'game.exe'}
    g.tick(now=111.0)
    check(g.session_state == 'active' and g.session_resumed is False, '重新 active')
    procs_box['v'] = {}
    g.tick(now=112.0)      # -> grace，grace_until = 120
    mark = len(read_rec())
    procs_box['v'] = {100: 'game.exe'}
    g.tick(now=113.0)
    check(read_rec()[mark:] == [
        ('fake10', 'session_resume', 'active'), ('fake20', 'session_resume', 'active'),
        ('fake10', 'tick', 'active'), ('fake20', 'tick', 'active')],
        '宽限期内恢复 -> on_session_resume（%s）' % (read_rec()[mark:],))
    check(g.session_resumed is True and g.grace_until == 0.0,
          'resumed=True 且宽限被清空')

    print('[3] procs is None 的那一轮：零钩子调用、零迁移')
    mark = len(read_rec())
    before = g.session_state
    procs_box['v'] = None
    g.snap_fails = 0
    for i in range(2):
        g.tick(now=200.0 + i)
    check(len(read_rec()) == mark, '快照失败 1~2 次不产生任何钩子调用（+%d 条）'
          % (len(read_rec()) - mark))
    check(g.session_state == before, '会话状态不变（%s）' % g.session_state)
    check(g.snap_fails == 2, 'snap_fails 计数=2（%d）' % g.snap_fails)

    print('[4] 连续失败 3 次后降级：沿用上一轮进程表，会话不被判成"游戏退了"')
    mark = len(read_rec())
    g.tick(now=202.0)      # 第 3 次失败 -> 降级
    check(g.snap_fails == 3, 'snap_fails 计数=3（%d）' % g.snap_fails)
    check(read_rec()[mark:] == [('fake10', 'tick', 'active'), ('fake20', 'tick', 'active')],
          '降级轮照常分发 on_tick（%s）' % (read_rec()[mark:],))
    check(g.session_state == 'active', '降级不迁移会话（%s）' % g.session_state)

    print('[5] on_session_close 里 retry_close：保持 grace，下一轮再发一次 close')
    reset_rec()
    procs_box2 = {'v': {100: 'game.exe'}}
    guard_core.snapshot = lambda: procs_box2.get('v')
    base2 = make_base((10,))
    g2 = make_guard(base2, procs_box2, grace=1, orders=(10,))
    sys.modules['wbox_plugin_fake10.plugin'].RETRY['fake10'] = 1   # 只重试一次
    g2.tick(now=10.0)      # -> active
    procs_box2['v'] = {}
    g2.tick(now=11.0)      # -> grace，grace_until = 12
    mark = len(read_rec())
    g2.tick(now=12.0)      # close -> 插件要求 2 秒后重试
    check(read_rec()[mark:] == [('fake10', 'session_close', 'grace'),
                                ('fake10', 'tick', 'grace')],
          '重试那一轮：on_tick 看到 grace 而不是 idle（%s）' % (read_rec()[mark:],))
    check(g2.session_state == 'grace', 'retry_close 后状态保持 grace')
    check(abs(g2.grace_until - 14.0) < 1e-6, 'grace_until = now + 2（%s）' % g2.grace_until)
    check(g2.close_retries == 1, 'close_retries 计数=1')
    mark = len(read_rec())
    g2.tick(now=13.0)
    check(read_rec()[mark:] == [('fake10', 'tick', 'grace')],
          '重试间隔内不再发 close（%s）' % (read_rec()[mark:],))
    mark = len(read_rec())
    g2.tick(now=14.0)      # 第二次 close，这次不重试
    check(read_rec()[mark:] == [('fake10', 'session_close', 'grace'),
                                ('fake10', 'tick', 'idle')],
          '重试到位后再发一次 close 并转 idle（%s）' % (read_rec()[mark:],))
    check(g2.session_state == 'idle', 'state=idle')

    print('[6] request_fast_poll 影响 poll_delay')
    g2.host.fast_poll_until = 0.0
    check(g2.poll_delay() == 2.0, '空闲且无登记 -> 慢节拍 %s' % g2.poll_delay())
    g2.host.request_fast_poll(time.time() + 30)
    check(g2.poll_delay() == 0.4, '空闲但有插件登记快节拍 -> %s' % g2.poll_delay())
    g2.host.fast_poll_until = 0.0
    g2.session_state = 'grace'
    check(g2.poll_delay() == 0.4, '会话中（grace）-> 快节拍')
    g2.session_state = 'idle'
    check(g2.poll_delay(True) == 0.4 and g2.poll_delay(False) == 2.0,
          '显式 busy 参数优先（test_popup_guard 依赖这条）')

    print('[7] 钩子遍历顺序按 (order, id)，与目录枚举顺序无关')
    reset_rec()
    base3 = make_base((30, 20, 10))     # 目录名一样，只是 order 不同
    host3 = plugin_host.PluginHost(base3, log_func=lambda m: None)
    host3.load_all(None, write_state=False)
    g3 = guard_core.Guard(host=host3)
    g3.host.dispatch('on_start')
    starts = [r[0] for r in read_rec() if r[1] == 'start']
    check(starts == ['fake10', 'fake20', 'fake30'], 'order 决定遍历顺序（%s）' % (starts,))

    print('[8] on_exit 分发一次（cleanup 幂等）')
    mark = len(read_rec())
    g.cleanup()
    g.cleanup()
    exits = [r for r in read_rec()[mark:] if r[1] == 'exit']
    check(len(exits) == 2 and all(r[2] == 'guard_stop' for r in exits),
          '两个插件各收到一次 on_exit(guard_stop)（%s）' % (exits,))

    print('[9] 零插件也要能 tick / cleanup')
    empty = tempfile.mkdtemp(prefix='abg_hooks_empty_')
    os.makedirs(os.path.join(empty, 'plugins'))
    host0 = plugin_host.PluginHost(empty, log_func=lambda m: None)
    host0.load_all(None, write_state=False)
    guard_core.snapshot = lambda: {1: 'game.exe'}
    g0 = guard_core.Guard(host=host0)
    g0.reload_config = lambda: None
    g0.cfg = dict(guard_core.DEFAULTS)
    g0.watch = {'game.exe'}
    g0.last_scan = 1e9
    g0.refresh_paths = lambda procs: None
    g0.check_clock_jump = lambda now: None
    g0.tick(now=1.0)
    g0.tick(now=2.0)
    g0.cleanup()
    check(g0.host.plugins == [], '零插件：plugins 为空')
    check(g0.session_state == 'active', '零插件时状态机照常推进（%s）' % g0.session_state)
finally:
    guard_core.snapshot = ORIG_SNAPSHOT

print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
