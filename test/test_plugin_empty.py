# -*- coding: utf-8 -*-
"""离线验证零插件：plugins\\ 空目录（甚至不存在）时宿主仍然可用。

这是硬约束（文档第 10 节红线 6）：`plugins\\` 为空、坏、全抛异常三种情况下
守护都不能退出。这里覆盖"为空"这一种。
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


def tick(seq=1):
    return plugin_api.Tick(seq=seq, now=2000.0 + seq, procs={}, names=set(),
                           games=[], session=plugin_api.SessionView())


ALL_HOOKS = ('migrate', 'on_config', 'on_load', 'on_start', 'on_tick',
             'on_session_start', 'on_session_resume', 'on_session_grace',
             'on_session_close', 'on_exit')

print('[1] plugins 目录存在但为空')
base = tempfile.mkdtemp(prefix='abg_empty_')
os.makedirs(os.path.join(base, 'plugins'))
logs = []
host = plugin_host.PluginHost(base, log_func=logs.append)
registry = host.load_all()
check(registry == [], 'load_all() 返回空列表（%r）' % (registry,))
check(host.plugins == [], 'plugins 为空')
check(plugin_host.load_registry(base, log_func=lambda m: None) == [],
      'load_registry() 返回 []')

# 面板进程只读：load_registry 不能写 plugins_state.json（那是守护的产物）
quiet = tempfile.mkdtemp(prefix='abg_panel_')
os.makedirs(os.path.join(quiet, 'plugins'))
plugin_host.load_registry(quiet, log_func=lambda m: None)
check(not os.path.isfile(os.path.join(quiet, 'plugins_state.json')),
      'load_registry 不写 plugins_state.json（面板只读）')

print('[2] 空注册表时 dispatch 全部钩子都不抛')
for hook in ALL_HOOKS:
    args = ()
    if hook == 'migrate':
        args = ({},)
    elif hook == 'on_config':
        args = ({},)
    elif hook == 'on_exit':
        args = ('guard_stop',)
    elif hook in ('on_tick', 'on_session_start', 'on_session_resume',
                  'on_session_grace', 'on_session_close'):
        args = (tick(),)
    try:
        host.dispatch(hook, *args)
        err = None
    except BaseException as ex:
        err = ex
    check(err is None, 'dispatch(%s) 不抛（%r）' % (hook, err))

print('[3] 连跑 5 轮 tick 稳定')
for i in range(5):
    host.dispatch('on_tick', tick(seq=i))
check(host.write_state_file(force=True) is True, '状态文件可写')

print('[4] plugins_state.json：plugins 为空数组')
path = os.path.join(base, 'plugins_state.json')
check(os.path.isfile(path), '文件已生成：%s' % path)
with open(path, 'r', encoding='utf-8') as f:
    state = json.load(f)
check(state['plugins'] == [], 'state["plugins"] == []（%r）' % (state['plugins'],))
check(state['api'] == plugin_api.API_VERSION, 'state["api"] == API_VERSION')
check(state['guard_pid'] == os.getpid(), 'state["guard_pid"] 是本进程')

print('[5] plugins 目录根本不存在（用户删了整个目录）')
base2 = tempfile.mkdtemp(prefix='abg_nodir_')
host2 = plugin_host.PluginHost(base2, log_func=lambda m: None)
check(host2.load_all() == [], '没有 plugins 目录 -> 0 条目')
host2.dispatch('on_tick', tick())
check(host2.write_state_file(force=True) is True, '状态文件照样能写')
check(os.path.isfile(os.path.join(base2, 'plugins_state.json')), '状态文件位置正确')
check(host2.errors() == [], 'errors() 为空')
check(host2.reload_if_changed() is False, 'reload_if_changed 本次未启用，返回 False')
check(host2.plugins_dir == os.path.join(base2, 'plugins'), 'plugins_dir 属性')

print('[6] 只有坏 manifest 的目录：0 个可用插件，但 registry 留下失败行')
base3 = tempfile.mkdtemp(prefix='abg_badonly_')
d = os.path.join(base3, 'plugins', 'broken')
os.makedirs(d)
with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
    f.write('{ nope')
host3 = plugin_host.PluginHost(base3, log_func=lambda m: None)
reg3 = host3.load_all()
check(len(reg3) == 1 and reg3[0].status == 'bad_manifest',
      'registry 有 1 条 bad_manifest（%r）' % (reg3,))
check(host3.plugins == [], '可用插件仍是 0 个')
host3.dispatch('on_tick', tick())
check(len(host3.errors()) == 1, 'errors() 报出 1 条失败')
with open(os.path.join(base3, 'plugins_state.json'), 'r', encoding='utf-8') as f:
    st3 = json.load(f)
check(st3['plugins'][0]['status'] == 'bad_manifest',
      '状态文件里也记了 bad_manifest')

print('[7] 配置合并：零插件时等价于内核默认值 + 用户配置')
merged = plugin_host.merge_defaults({'a': 1, 'seg': {'x': 1, 'y': 2}}, [],
                                   {'seg': {'y': 9}, 'b': 2})
check(merged == {'a': 1, 'b': 2, 'seg': {'x': 1, 'y': 9}},
      'merge_defaults 结果正确（%r）' % (merged,))

print('[8] 零插件时 Guard 内核照常跑：tick 不抛、run() 能起能停')
import guard_core

tmp = tempfile.mkdtemp(prefix='abg_zero_guard_')
old_paths = (guard_core.PID_PATH, guard_core.STOP_PATH,
             guard_core.CONFIG_PATH, guard_core.LOG_PATH)
old_snapshot = guard_core.snapshot
guard_core.PID_PATH = os.path.join(tmp, 'guard.pid')
guard_core.STOP_PATH = os.path.join(tmp, 'stop.flag')
guard_core.CONFIG_PATH = os.path.join(tmp, 'config.json')
guard_core.LOG_PATH = os.path.join(tmp, 'guard.log')
guard_core.snapshot = lambda: {1: 'game.exe'}
try:
    # 一份最小 config.json：让 reload_config 走正常路径（首读失败会回落默认值）
    with open(guard_core.CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump({'game_processes': ['game.exe'],
                   'game_lib': {'enabled': False}}, f)
    gz = guard_core.Guard(host=host)          # 空宿主：零个插件
    gz.last_scan = 1e9
    for i in range(5):
        gz.tick(now=10.0 + i)                 # 红线：零插件也要能 tick
    check(gz.host.plugins == [], '零插件：host.plugins 为空')
    check(gz.session_state == 'active', '状态机照常推进（%s）' % gz.session_state)
    check(gz.cfg.get('poll_interval_sec') == 2.0, '内核默认值照样合并进 cfg')
    gz.running = False
    gz.run()                                  # 起不来就会在这里抛异常
    check(True, 'Guard.run() 在零插件下正常进入并退出')
    check(not os.path.exists(guard_core.PID_PATH), 'run() 退出后 guard.pid 已清理')
finally:
    guard_core.snapshot = old_snapshot
    (guard_core.PID_PATH, guard_core.STOP_PATH,
     guard_core.CONFIG_PATH, guard_core.LOG_PATH) = old_paths

print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
