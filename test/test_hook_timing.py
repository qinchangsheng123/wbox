# -*- coding: utf-8 -*-
"""钩子耗时监测 / 卡顿降级 / 异步出口 / 结构化事件 —— 离线验证。

对应审查项 2 与 8：
  - 每个钩子的耗时、连续慢次数、最近一次慢钩子都要能读到（状态文件 + 日志）；
  - 连续 3 次超线 -> 插件降级并点名，恢复后自动回到 ok；
  - ctx.spawn 是插件唯一异步出口，后台异常不外抛、只记日志；
  - events.jsonl 每行是固定字段的 JSON，bundle() 能打诊断包。

跑法：python test\\test_hook_timing.py。不碰真实进程、不建窗口。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import events          # noqa: E402
import plugin_host     # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


TMP = tempfile.mkdtemp(prefix='abg_timing_')
PLUGINS = os.path.join(TMP, 'plugins')
os.makedirs(PLUGINS)

PLUGIN_CODE = '''# -*- coding: utf-8 -*-
import threading
import time


class Plugin(object):
    DEFAULT_CONFIG = {}

    def __init__(self, ctx):
        self.ctx = ctx
        self.slow = True
        self.bg = []

    def on_tick(self, tick):
        if self.slow:
            time.sleep(0.06)

    def on_start(self):
        # 唯一异步出口：后台任务不应该阻塞钩子返回
        t = self.ctx.spawn(self._bg, name='slowpoke-bg')
        self.bg.append(t)

    def _bg(self):
        time.sleep(0.01)

    def on_exit(self, reason):
        self.ctx.spawn(self._boom, name='slowpoke-boom')

    def _boom(self):
        raise RuntimeError('后台任务故意失败')
'''

def write_plugin():
    d = os.path.join(PLUGINS, 'slowpoke')
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump({'id': 'slowpoke', 'name': '慢插件', 'version': '1.0.0',
                   'api': 1, 'entry': 'plugin.py:Plugin'}, f, ensure_ascii=False)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write(PLUGIN_CODE)


write_plugin()

print('[1] 连续慢钩子 -> 记耗时、降级、点名')
host = plugin_host.PluginHost(TMP)
host.hook_warn_sec = 0.02
host.load_all(None, write_state=False)
entry = host.entry_by_id('slowpoke')
check(entry is not None and entry.usable, '慢插件已加载')
check(entry.hook_stats == {}, '初始没有耗时记录')
for _ in range(3):
    host.dispatch('on_tick', None)
st = entry.hook_stats.get('on_tick') or {}
check(st.get('slow', 0) >= 3, 'on_tick 慢事件计数 >= 3（%r）' % (st.get('slow'),))
check(st.get('max', 0) >= 0.05, '记下最慢一次耗时（%.3fs）' % st.get('max', 0))
check(entry.status == plugin_host.STATUS_DEGRADED,
      '连续 3 次超线 -> 状态降级（%s）' % entry.status)
check(entry.slow_hook == 'on_tick' and entry.slow_sec >= 0.05,
      '点名慢钩子与耗时（%s %.3fs）' % (entry.slow_hook, entry.slow_sec))
check('on_tick' in entry.detail, 'detail 写明是哪个钩子（%r）' % entry.detail)
state = entry.to_state_dict()
check(state.get('slow_hook') == 'on_tick' and state.get('slow_count') >= 3,
      '状态记录带 slow_hook / slow_count（%r）'
      % ({k: state.get(k) for k in ('slow_hook', 'slow_count', 'slow_sec')},))

print('[2] 恢复后自动回到 ok')
entry.plugin.slow = False
for _ in range(2):
    host.dispatch('on_tick', None)
check(entry.status == plugin_host.STATUS_OK,
      '不再慢 -> 状态恢复 ok（%s，detail=%r）' % (entry.status, entry.detail))
check(entry.hook_slow_streak == 0, '连续慢计数清零')

print('[3] ctx.spawn：后台执行、异常不外抛')
host.dispatch('on_start')      # load_all 只加载，on_start 由主循环分发
time.sleep(0.15)
check(bool(entry.plugin.bg) and not entry.plugin.bg[0].is_alive(),
      'on_start 里的后台任务已跑完（线程名 %r）'
      % (entry.plugin.bg[0].name if entry.plugin.bg else '-',))
check(entry.plugin.bg[0].name == 'slowpoke-bg', '线程名用插件指定的名字')
host.dispatch('on_exit', 'guard_stop')
time.sleep(0.2)          # 后台异常被宿主捕获，不允许冒到主线程
check(True, 'on_exit 的后台任务抛异常后进程仍存活')

print('[4] 结构化事件流')
base = tempfile.mkdtemp(prefix='abg_events_')
events.configure(base)
ok = events.emit('plugin.hook', code='HOOK_SLOW', plugin='ace', pid=4242,
                 session=7, dur_ms=6120, msg='on_tick 慢')
check(ok is True, 'emit 落盘成功')
path = events.events_path(base)
with open(path, 'r', encoding='utf-8') as f:
    lines = [json.loads(x) for x in f if x.strip()]
check(len(lines) == 1, '一行一个事件')
rec = lines[0]
for key in ('ts', 'ver', 'cat', 'code', 'plugin', 'pid', 'session', 'dur_ms', 'msg'):
    check(key in rec, '字段 %s 存在（%r）' % (key, rec.get(key)))
check(rec['plugin'] == 'ace' and rec['pid'] == 4242 and rec['dur_ms'] == 6120,
      '插件 id / pid / 耗时按原值写入')
events.emit('popup', code='WINDOW_CLOSED', pid=9, msg='直播间弹窗')
with open(path, 'r', encoding='utf-8') as f:
    check(len([x for x in f if x.strip()]) == 2, '追加写不覆盖')

print('[5] 诊断包')
zpath = events.bundle(base)
check(bool(zpath) and os.path.isfile(zpath), '生成诊断包（%s）' % zpath)
with zipfile.ZipFile(zpath) as zf:
    names = zf.namelist()
    meta = json.loads(zf.read('meta.json').decode('utf-8'))
check('meta.json' in names and 'events.jsonl' in names,
      '包含 meta.json 与 events.jsonl（%r）' % (names,))
check(meta.get('app') == 'WBOX' and meta.get('version'),
      'meta 带产品名与版本')
check(isinstance(meta.get('plugins'), list), 'meta 带插件清单')

print('[6] 未 configure 时 emit 安静失败')
events.configure('')
check(events.emit('test', code='X') is False, '没有绑定目录时返回 False（不抛异常）')

if FAILS:
    print('\nFAILED %d 项：%r' % (len(FAILS), FAILS))
    sys.exit(1)
print('\nALL PASS')
