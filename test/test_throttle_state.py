# -*- coding: utf-8 -*-
"""离线验证限速状态机：会话结束后必须还原、失败要能重试、无 ACE 时不排清理。

S3-S5 之后限速状态机整体搬进了 `plugins\\ace\\plugin.py`，所以本文件改为
直接驱动那个插件（用假 ctx + 假 provider），**断言与迁移前逐条一致**：
  - 游戏期间限速 / 宽限期内保持 / 会话彻底结束后还原
  - 失败隔 60 秒重试、没出现 ACE 时不排清理
  - 还原日志不自相矛盾（2026-11-08 17:42 真机日志踩到）
  - config 缺段时守护与面板口径一致

不发真实 API 调用：throttle_process / restore_process / priority_class /
io_priority 全部用替身拦截。
"""
import json
import os
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import _plugin_src  # noqa: E402  插件源码在独立仓库 wbox-plugins
import core_defaults
import guard_core as g
import plugin_api
import plugin_host

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


# ---- 拦截内核里的优先级调用（插件通过 ctx.import_core('guard_core') 拿到同一个模块对象，
#      所以这里替换模块属性对插件同样生效）----
calls = {'restored': [], 'throttled': []}
g.restore_process = lambda pid: calls['restored'].append(pid)
g.throttle_process = lambda pid, hint=0, bind=False: (
    calls['throttled'].append(pid) or (True, 'CPU=空闲，磁盘IO=very-low'))
g.priority_class = lambda pid: (0, 'idle')
g.io_priority = lambda pid: 0


class AceHarness(object):
    """装一个真实的 ace 插件，provider 与日志都由测试控制。"""

    def __init__(self, session_state='active'):
        self.base = tempfile.mkdtemp(prefix='abg_throttle_')
        shutil.copytree(_plugin_src.require('ace 节流测试'),
                        os.path.join(self.base, 'plugins'))
        self.logs = []
        self.view = plugin_api.SessionView(state=session_state)
        self.box = {'procs': {}}
        self.host = plugin_host.PluginHost(self.base, log_func=self.logs.append)
        self.host.providers.update({
            'procs': lambda: self.box['procs'],
            'snapshot': lambda: self.box['procs'],
            'refresh_paths': lambda procs: None,
            'games_present': lambda procs=None: False,
            'watch_names': lambda: set(),
            'session': lambda: self.view,
            'now': lambda: 0.0,
        })
        self.host.load_all(None, write_state=False)
        self.entry = self.host.entry_by_id('ace')
        self.plugin = self.entry.plugin

    def configure(self, ace_throttle=None, ace_cleanup=None):
        """按 config.json 的写法注入配置，再走真实的 on_config 路径。"""
        raw = {}
        if ace_throttle is not None:
            raw['ace_throttle'] = ace_throttle
        if ace_cleanup is not None:
            raw['ace_cleanup'] = ace_cleanup
        merged = plugin_host.merge_defaults(core_defaults.DEFAULTS,
                                            self.host.registry, raw)
        self.host.cfg = merged
        self.plugin.on_config(plugin_host.plugin_cfg(merged, self.entry))
        return self.plugin

    def set_state(self, state):
        self.view = plugin_api.SessionView(state=state)


TH = {
    'enabled': True, 'delay_sec': 20, 'io_priority': 0, 'affinity_last_core': False,
    'processes': ['SGuard64.exe', 'SGuardSvc64.exe'],
    'update_processes': ['SGuardUpdate64.exe'],
}

print('[1] 游戏期间把 ACE 压到最低优先级')
h = AceHarness('active')
p = h.configure(ace_throttle=dict(TH))
p.ace_seen = {100: 1000.0}          # pid 100 早已出现
procs = {100: 'sguard64.exe', 200: 'game.exe'}
p.tick_throttle(1100.0, procs)
print('游戏期间限速:', 100 in p.throttled, '| 日志应有一条 ACE 限速')
check(100 in p.throttled, 'pid 100 被限速')
check(any('ACE 限速' in m for m in h.logs), '打了一条 ACE 限速日志')

print('[2] 游戏退出进宽限期：还不该还原')
h.set_state('grace')
p.tick_throttle(1101.0, procs)
check(100 in p.throttled and not calls['restored'], '宽限期内保持限速')

print('[3] 会话彻底结束（idle）：还原')
h.set_state('idle')
p.tick_throttle(1110.0, procs)
print('会话结束后已还原:', calls['restored'], '| throttled:', p.throttled)
check(calls['restored'] == [100] and not p.throttled, 'idle 后立即还原')

print('[4] 限速失败 60 秒后重试')
h = AceHarness('active')
p = h.configure(ace_throttle=dict(TH))
p.ace_seen = {300: 2000.0}
procs2 = {300: 'sguard64.exe'}
state = {'n': 0}


def flaky(pid, hint=0, bind=False):
    state['n'] += 1
    return (state['n'] > 1, '错误码 5' if state['n'] == 1 else 'CPU=空闲')


g.throttle_process = flaky
p.tick_throttle(2100.0, procs2)
print('首次尝试失败:', state['n'], '| 已排重试:', 300 in p.throttle_retry)
check(state['n'] == 1 and 300 in p.throttle_retry, '首次失败后排了重试')
p.tick_throttle(2130.0, procs2)
print('60 秒内不重试，尝试次数:', state['n'])
check(state['n'] == 1, '60 秒内不重试')
p.tick_throttle(2170.0, procs2)
print('超过 60 秒后重试成功:', 300 in p.throttled, '| 尝试次数:', state['n'])
check(300 in p.throttled and state['n'] == 2, '超过 60 秒后重试成功')
g.throttle_process = lambda pid, hint=0, bind=False: (
    calls['throttled'].append(pid) or (True, 'CPU=空闲，磁盘IO=very-low'))

print('[5] 无 ACE 的会话不该排清理任务')
h = AceHarness('grace')
p = h.configure(ace_cleanup={'enabled': True})
p.ace_touched = False
p.queue_ace(3000.0)
print('无 ACE 时排队:', p.ace_pending, '(应为 False)')
check(p.ace_pending is False, '没出现 ACE 就不排队')
check(any('跳过清理调度' in m for m in h.logs), '日志说明了跳过原因')

p.ace_touched = True
p.queue_ace(3000.0)
print('有 ACE 时排队:', p.ace_pending, '(应为 True)')
check(p.ace_pending is True and p.ace_exit_at == 3000.0, '出现 ACE 才排队')

print('[6] 还原日志不能自相矛盾（2026-11-08 17:42 真实日志踩到）')
h = AceHarness('idle')
p = h.configure(ace_throttle=dict(TH), ace_cleanup={'enabled': True})
p.ace_touched = True
p.throttled = {500}
p.tick_throttle(2400.0, {500: 'sguard64.exe'})
print('还原当轮日志:', [m for m in h.logs if '还原' in m])
check(any('已还原 1 个' in m for m in h.logs), '打了一条"已还原 1 个"')
p.tick_throttle(2402.0, {500: 'sguard64.exe'})
print('下一轮日志:', [m for m in h.logs if '还原' in m])
check(not any('无需还原' in m for m in h.logs), '还原成功后不该再补"无需还原"')

h = AceHarness('idle')
p = h.configure(ace_throttle=dict(TH))
p.throttled = set()
p.ace_touched = True
p.restore_noted = False
p.tick_throttle(2410.0, {})
print('ACE 自行退出日志:', [m for m in h.logs if '无需还原' in m])
check(any('无需还原' in m for m in h.logs), '从没限速过、ACE 自己退了要补一条')
p.tick_throttle(2412.0, {})
n = sum('无需还原' in m for m in h.logs)
print('补日志次数:', n, '(应为 1)')
check(n == 1, '每局只补一次')

print('[7] config 缺 ace_throttle 段时，守护 / 面板 / 默认值口径必须一致')
import settings_gui as s

bare = dict(g.DEFAULTS)
bare.pop('ace_throttle', None)
tmpdir = tempfile.mkdtemp(prefix='abg_defaults_')
cfgp = os.path.join(tmpdir, 'config.json')
with open(cfgp, 'w', encoding='utf-8') as f:
    json.dump(bare, f, ensure_ascii=False)
old = (g.BASE, g.CONFIG_PATH, g.LOG_PATH)
g.BASE = tmpdir
g.CONFIG_PATH = cfgp
g.LOG_PATH = os.path.join(tmpdir, 'guard.log')
# v2.11.3：插件源码在独立仓库；guard_core 的 PLUGINS_DIR 在 import 时算好，
# 仓库里不再有 plugins\ 就指向空目录，这里显式指回插件仓库。
g.PLUGINS_DIR = _plugin_src.require('ace 默认值口径')
try:
    g4 = g.Guard()
    g4.reload_config()
    ace4 = g4.host.entry_by_id('ace').plugin
    guard_on = bool(ace4.throttle_cfg().get('enabled'))
    delay = ace4.throttle_cfg().get('delay_sec')
finally:
    g.BASE, g.CONFIG_PATH, g.LOG_PATH = old
panel_on = bool(s.effective_cfg(bare)['ace_throttle'].get('enabled'))
default_on = bool(g.ACE_THROTTLE_DEFAULTS['enabled'])
print('缺段时 enabled: 守护=%s 面板=%s 默认值=%s | delay=%s' %
      (guard_on, panel_on, default_on, delay))
check(guard_on is True and panel_on is True and default_on is True, '三方口径一致')
check(delay == 20, 'delay 取默认 20')

print('[8] 面板保存只改 enabled，不许把其它限速参数抹掉')
part = {'ace_throttle': {'enabled': False}}
seg = s.with_defaults(part, 'ace_throttle', g.ACE_THROTTLE_DEFAULTS)
seg['enabled'] = False
print('保存后限速段键数:', len(seg))
check(set(seg) == set(g.ACE_THROTTLE_DEFAULTS) and seg['delay_sec'] == 20,
      '段内其它键一个都没丢')

print('[9] 重扫请求必须真的强制扫描，不能继续吃 6 小时缓存')
import game_lib

cachep = os.path.join(tmpdir, 'games_cache.json')
with open(cachep, 'w', encoding='utf-8') as f:
    json.dump({'games': [{'exe': r'D:\Games\X.exe', 'name': 'X', 'dir': r'D:\Games'}],
               'sources': {}, 'scanned_at': time.time()}, f)
calls2 = [0]
orig_scan = game_lib.scan
game_lib.scan = lambda cfg, log=None: (calls2.__setitem__(0, calls2[0] + 1) or
    {'games': [], 'sources': {}, 'scanned_at': time.time()})
old = (g.BASE, g.CONFIG_PATH, g.LOG_PATH)
g.BASE, g.CONFIG_PATH, g.LOG_PATH = tmpdir, cfgp, os.path.join(tmpdir, 'guard.log')
try:
    g5 = g.Guard()
    g5.cfg = dict(bare)
    g5.cfg['game_lib'] = {'enabled': True, 'rescan_hours': 6}
    g5.refresh_lib()
    no_force = calls2[0]
    g5.refresh_lib(force=True)
    with_force = calls2[0]
finally:
    g.BASE, g.CONFIG_PATH, g.LOG_PATH = old
    game_lib.scan = orig_scan
print('重扫 scan 次数：普通=%d force=%d' % (no_force, with_force - no_force))
check(no_force == 0 and with_force == 1, 'force=True 才真扫')

print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
