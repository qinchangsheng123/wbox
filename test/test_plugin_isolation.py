# -*- coding: utf-8 -*-
"""离线验证故障隔离（核心用例）：一个插件每个钩子都炸，不能影响别的插件。

断言：
  1. 坏插件在 on_load / on_config / on_start / on_tick / on_session_* / on_exit 全炸，
     好插件的调用次数完全不受影响；
  2. dispatch 永不抛异常；
  3. plugins_state.json 里坏插件 status=degraded、error_count>0；
  4. on_tick 连续失败 10 次后降频，成功一次立即恢复每轮。
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import plugin_api
import plugin_host

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


GOOD_SRC = '''# -*- coding: utf-8 -*-
import plugin_api

CALLS = {'on_load': 0, 'on_config': 0, 'on_start': 0, 'on_tick': 0,
         'on_session_start': 0, 'on_session_resume': 0, 'on_session_grace': 0,
         'on_session_close': 0, 'on_exit': 0}


class Plugin(plugin_api.WBOXPlugin):
    DEFAULT_CONFIG = {'good_enabled': True}

    def _bump(self, name):
        CALLS[name] += 1

    def on_load(self):
        self._bump('on_load')

    def on_config(self, cfg):
        self._bump('on_config')

    def on_start(self):
        self._bump('on_start')

    def on_tick(self, tick):
        self._bump('on_tick')

    def on_session_start(self, tick):
        self._bump('on_session_start')

    def on_session_resume(self, tick):
        self._bump('on_session_resume')

    def on_session_grace(self, tick):
        self._bump('on_session_grace')

    def on_session_close(self, tick):
        self._bump('on_session_close')

    def on_exit(self, reason):
        self._bump('on_exit')
'''

# FAIL 是模块级开关：测试可以拨回 False 验证"成功一次即恢复"
BAD_SRC = '''# -*- coding: utf-8 -*-
import plugin_api

FAIL = True
CALLS = {'on_load': 0, 'on_config': 0, 'on_start': 0, 'on_tick': 0,
         'on_session_start': 0, 'on_session_resume': 0, 'on_session_grace': 0,
         'on_session_close': 0, 'on_exit': 0}


class Plugin(plugin_api.WBOXPlugin):
    DEFAULT_CONFIG = {'bad_enabled': True}

    def _go(self, name):
        CALLS[name] += 1
        if FAIL:
            raise RuntimeError('boom-' + name)

    def on_load(self):
        self._go('on_load')

    def on_config(self, cfg):
        self._go('on_config')

    def on_start(self):
        self._go('on_start')

    def on_tick(self, tick):
        self._go('on_tick')

    def on_session_start(self, tick):
        self._go('on_session_start')

    def on_session_resume(self, tick):
        self._go('on_session_resume')

    def on_session_grace(self, tick):
        self._go('on_session_grace')

    def on_session_close(self, tick):
        self._go('on_session_close')

    def on_exit(self, reason):
        self._go('on_exit')
'''

EXIT_SRC = '''# -*- coding: utf-8 -*-
import plugin_api


class Plugin(plugin_api.WBOXPlugin):
    DEFAULT_CONFIG = {}

    def on_tick(self, tick):
        raise SystemExit(9)
'''


def make_base():
    base = tempfile.mkdtemp(prefix='abg_isolation_')
    os.makedirs(os.path.join(base, 'plugins'))
    return base


def write_plugin(base, dirname, pid, order, source):
    d = os.path.join(base, 'plugins', dirname)
    os.makedirs(d)
    manifest = {
        'id': pid,
        'name': pid,
        'version': '1.0.0',
        'api': 1,
        'icon': 'dot',
        'order': order,
        'toggle': [pid + '_enabled'],
    }
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write(source)
    return d


def tick(seq=1, state=plugin_api.STATE_IDLE):
    return plugin_api.Tick(seq=seq, now=1000.0 + seq, procs={}, names=set(),
                           games=[], session=plugin_api.SessionView(state=state),
                           events=('tick',))


print('[1] 坏插件在每个钩子里抛异常，好插件不受影响')
base = make_base()
write_plugin(base, 'a_bad', 'bad', 10, BAD_SRC)
write_plugin(base, 'b_good', 'good', 20, GOOD_SRC)
host = plugin_host.PluginHost(base, log_func=lambda m: None)
host.load_all({'bad_enabled': True})
good = sys.modules['wbox_plugin_good.plugin'].CALLS
bad = sys.modules['wbox_plugin_bad.plugin'].CALLS

check(good['on_load'] == 1 and bad['on_load'] == 1, 'on_load 两边都调了一次')
e_bad = [x for x in host.registry if x.id == 'bad'][0]
e_good = [x for x in host.registry if x.id == 'good'][0]
check(e_bad.status == 'degraded', 'load 阶段就炸 -> status=degraded（%s）' % e_bad.status)
check(e_good.status == 'ok', '好插件 status=ok')

hooks = ['on_config', 'on_start', 'on_session_start', 'on_session_resume',
         'on_session_grace', 'on_session_close', 'on_tick', 'on_exit']
for hook in hooks:
    args = ()
    if hook == 'on_config':
        args = ({'bad_enabled': True},)
    elif hook == 'on_exit':
        args = ('guard_stop',)
    elif hook != 'on_start':
        args = (tick(),)
    try:
        host.dispatch(hook, *args)
        raised = None
    except BaseException as ex:      # dispatch 自己不许抛，这里兜一层是为了给出可读断言
        raised = ex
    check(raised is None, 'dispatch(%s) 不抛异常（%r）' % (hook, raised))
    check(good[hook] == 1, '好插件 %s 被调用了 1 次（%d）' % (hook, good[hook]))
    check(bad[hook] == 1, '坏插件 %s 也被调用了（隔离 != 停用）' % hook)

check(e_bad.status == 'degraded' and e_bad.error_count >= 8,
      '坏插件 error_count 累计（%d）' % e_bad.error_count)
check('RuntimeError' in e_bad.last_error, 'last_error 记录了异常类型（%r）' % e_bad.last_error)
check(e_good.error_count == 0, '好插件 error_count=0')

print('[2] SystemExit 也必须被拦住（否则插件能带走整个守护）')
write_plugin(base, 'c_exit', 'suicide', 30, EXIT_SRC)
host2 = plugin_host.PluginHost(base, log_func=lambda m: None)
host2.load_all()
try:
    host2.dispatch('on_tick', tick())
    survived = True
except BaseException as ex:
    survived = False
    print('     抛出：%r' % (ex,))
check(survived, 'on_tick 里 raise SystemExit 没有中断 dispatch')
e_exit = [x for x in host2.registry if x.id == 'suicide'][0]
check(e_exit.status == 'degraded' and 'SystemExit' in e_exit.last_error,
      'SystemExit 被采集（%s / %s）' % (e_exit.status, e_exit.last_error))

print('[3] plugins_state.json：状态变化才落盘，且记下 degraded / error_count')
state_path = os.path.join(base, 'plugins_state.json')
check(os.path.isfile(state_path), '状态文件已生成')
with open(state_path, 'r', encoding='utf-8') as f:
    state = json.load(f)
check(state['api'] == 1 and state['guard_pid'] == os.getpid(), 'api / guard_pid 字段')
rec = [p for p in state['plugins'] if p['id'] == 'bad'][0]
check(rec['status'] == 'degraded', 'state 里 status=degraded')
check(rec['error_count'] > 0, 'state 里 error_count=%d > 0' % rec['error_count'])
check(bool(rec['last_error']) and rec['last_error_at'] > 0, 'state 里有 last_error / last_error_at')
check(rec['ticks'] >= 1, 'state 里有 ticks=%d' % rec['ticks'])
good_rec = [p for p in state['plugins'] if p['id'] == 'good'][0]
check(good_rec['status'] == 'ok' and good_rec['error_count'] == 0, '好插件状态正常')

mtime1 = os.path.getmtime(state_path)
wrote = host2.write_state_file()
check(wrote is False, '状态没变且未到心跳 -> 不落盘')
check(os.path.getmtime(state_path) == mtime1, '文件 mtime 未变化')

print('[4] 同类异常 60 秒内只记一条日志')
logs = []
base3 = make_base()
write_plugin(base3, 'a_bad', 'bad', 10, BAD_SRC)
host3 = plugin_host.PluginHost(base3, log_func=logs.append)
host3.load_all()
logs[:] = []
for i in range(6):
    host3.dispatch('on_tick', tick(seq=i))
tick_errs = [m for m in logs if 'on_tick' in m and '抛异常' in m]
check(len(tick_errs) == 1, '6 次同类 on_tick 异常只记 1 条日志（实际 %d）' % len(tick_errs))

print('[5] on_tick 连续失败 >=10 次后降频，成功一次立即恢复')
base4 = make_base()
write_plugin(base4, 'a_bad', 'bad', 10, BAD_SRC)
host4 = plugin_host.PluginHost(base4, log_func=lambda m: None)
host4.load_all()
bad4 = sys.modules['wbox_plugin_bad.plugin']
e4 = [x for x in host4.registry if x.id == 'bad'][0]
for i in range(plugin_host.TICK_FAIL_LIMIT):
    host4.dispatch('on_tick', tick(seq=i))
check(bad4.CALLS['on_tick'] == 10 and e4.tick_fails == 10,
      '10 次全调、失败计数=10（%d / %d）' % (bad4.CALLS['on_tick'], e4.tick_fails))
for i in range(5):
    host4.dispatch('on_tick', tick(seq=100 + i))
check(bad4.CALLS['on_tick'] == 10, '接着 5 轮被跳过，调用次数仍是 %d' % bad4.CALLS['on_tick'])

bad4.FAIL = False          # 拨回正常（模块级开关，模拟"暂时性故障已消失"）
recovered_at = None
for i in range(plugin_host.TICK_SKIP_ROUNDS + 1):
    before = bad4.CALLS['on_tick']
    host4.dispatch('on_tick', tick(seq=200 + i))
    if bad4.CALLS['on_tick'] > before:
        recovered_at = i
        break
check(recovered_at is not None and recovered_at <= plugin_host.TICK_SKIP_ROUNDS,
      '降频后只在第 N 轮试跑（N=%r）' % (recovered_at,))
check(e4.tick_fails == 0 and e4.status == 'ok',
      '成功一次即清零并回到 ok（fails=%d, status=%s）' % (e4.tick_fails, e4.status))
before = bad4.CALLS['on_tick']
host4.dispatch('on_tick', tick(seq=999))
check(bad4.CALLS['on_tick'] == before + 1, '恢复后重新每轮调用')

print('[6] on_exit 抛异常不阻止其它插件收尾')
host2.dispatch('on_exit', 'guard_stop')
check(True, 'on_exit 全量分发完成（坏插件异常已吞掉）')

print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
