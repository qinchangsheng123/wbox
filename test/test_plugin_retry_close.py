# -*- coding: utf-8 -*-
"""离线验证 afterburner 插件的"关闭重试"语义（v1 guard_core.py:863-888）。

断言：
  1. `close_owned(None)`（取不到进程表）→ 调 ctx.retry_close(2)，且只记一条日志；
  2. `close_owned({})`（表是空的、确实没游戏）→ 不重试；有 ab_pid 时直接关；
  3. 关闭前复核发现游戏回来了 → 放弃本轮关闭、不重试；
  4. `games_present` 在取不到进程表时返回 True（红线：抖动不许触发关闭）；
  5. `retry_close` 只在 on_session_close 分发期间被内核接受。

用仓库里真实的 plugins\\afterburner（拷到临时目录），只用假 provider 驱动，
不碰真实进程、不写 app\\。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import core_defaults
import guard_core
import plugin_host
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_src  # noqa: E402  插件源码在独立仓库 wbox-plugins

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def make_base():
    base = tempfile.mkdtemp(prefix='abg_retry_')
    shutil.copytree(_plugin_src.require('retry_close'),
                    os.path.join(base, 'plugins'))
    return base


class Harness(object):
    """把 afterburner 插件装进假宿主：provider 全部由测试控制。"""

    def __init__(self):
        self.base = make_base()
        self.box = {'procs': {}}          # 当前进程表（None = 取不到）
        self.games_back = False
        self.retries = []
        self.logs = []
        self.closed = []
        self.host = plugin_host.PluginHost(self.base, log_func=self.logs.append)
        self.host.providers.update({
            'procs': lambda: self.box['procs'],
            'snapshot': lambda: self.box['procs'],
            'refresh_paths': lambda procs: None,
            'games_present': lambda procs=None: self.games_back,
            'watch_names': lambda: set(),
            'now': lambda: 1000.0,
            'retry_close': self._retry,
        })
        self.host.load_all(None, write_state=False)
        self.entry = self.host.entry_by_id('afterburner')
        self.plugin = self.entry.plugin
        self.plugin.on_config(plugin_host.plugin_cfg(
            core_defaults.DEFAULTS, self.entry))
        self.plugin.close_pid = lambda pid, name: self.closed.append((pid, name))

        def batch(targets):
            # v2.0.0：close_owned 改走批量入口（与 gamepp 一致），测试桩随之上移，
            # 否则会真的调用 taskkill 去关假 pid。
            self.closed.extend(targets)
            return dict((pid, 'exited') for pid, _n in targets)
        self.plugin._close_batch = batch

    def _retry(self, delay=2.0):
        self.retries.append(delay)
        return True


print('[1] 取不到进程表 -> retry_close(2)，日志只记一条')
h = Harness()
h.box['procs'] = None
h.plugin.ab_pid = 4321
h.plugin.close_owned(None)
check(h.retries == [2.0], '调了 ctx.retry_close(2)（%r）' % (h.retries,))
check(h.closed == [], '没有误关任何进程')
check(any('取进程表失败' in m for m in h.logs), '记了"取进程表失败"日志（%s）'
      % ([m for m in h.logs if '进程表' in m],))
n_logs = len([m for m in h.logs if '取进程表失败' in m])
check(n_logs == 1, '第一轮记了一条"取进程表失败"日志')
h.plugin.close_owned(None)        # 模拟内核下一轮再发一次 on_session_close
check(h.retries == [2.0, 2.0], '每轮都会重试（%r）' % (h.retries,))
check(len([m for m in h.logs if '取进程表失败' in m]) == 1,
      '连续失败只记一条日志（close_noted 语义，v1 785-788）')

print('[2] 进程表能取到（{}）-> 不重试，直接按所有权关闭')
h = Harness()
h.box['procs'] = {}
h.plugin.ab_pid = 4321
h.plugin.close_owned({})
check(h.retries == [], '没有调 retry_close（%r）' % (h.retries,))
check(h.plugin.ab_pid is None, '关闭后放弃所有权（ab_pid=None）')

h = Harness()
h.box['procs'] = {4321: 'msiafterburner.exe'}
h.plugin.ab_pid = 4321
h.plugin.close_owned({4321: 'msiafterburner.exe'})
check(h.closed == [(4321, 'msiafterburner.exe')],
      '按 pid 关闭自己拉起的小飞机（%r）' % (h.closed,))
check(h.retries == [], '正常路径不重试')

print('[3] 关闭前复核发现游戏回来了 -> 放弃本轮关闭、不重试')
h = Harness()
h.box['procs'] = {4321: 'msiafterburner.exe'}
h.games_back = True
h.plugin.ab_pid = 4321
h.plugin.close_owned({4321: 'msiafterburner.exe'})
check(h.closed == [], '游戏还在跑就不是"退出后清理"的时机')
check(h.retries == [], '不重试')
check(any('放弃本轮关闭' in m for m in h.logs), '日志说明了原因')

print('[4] games_present 在取不到进程表时返回 True（红线）')
base = make_base()
host = plugin_host.PluginHost(base, log_func=lambda m: None)
host.load_all(None, write_state=False)
g = guard_core.Guard(host=host)
g.watch = {'game.exe'}
orig = guard_core.snapshot
guard_core.snapshot = lambda: None
try:
    check(g.games_present() is True, 'snapshot 失败 -> games_present=True（不许关）')
    check(g.games_present({}) is False, '传空表 -> False（确实没游戏）')
    check(g.games_present({1: 'game.exe'}) is True, '白名单命中 -> True')
finally:
    guard_core.snapshot = orig

print('[5] retry_close 只在 on_session_close 分发期间被接受')
base = make_base()
host = plugin_host.PluginHost(base, log_func=lambda m: None)
host.load_all(None, write_state=False)
g = guard_core.Guard(host=host)
check(g.retry_close(2) is False, '非 on_session_close 期间返回 False（%r）'
      % (g.retry_close(2),))
g._close_dispatch = True
g._tick_now = 500.0
check(g.retry_close(2) is True, '分发期间接受重试')
check(g.session_state == 'grace' and abs(g.grace_until - 502.0) < 1e-6,
      '状态回 grace，grace_until = now + 2（%s）' % g.grace_until)
check(g.close_retries == 1, 'close_retries 计数 +1')

print('[6] 通过宿主分发 on_session_close：真实插件也会要求重试')
h = Harness()
h.box['procs'] = None
h.plugin.ab_pid = 999
h.host.dispatch('on_session_close', None)
check(h.retries == [2.0], '宿主分发路径同样触发 retry_close（%r）' % (h.retries,))

print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
