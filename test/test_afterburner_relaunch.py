# -*- coding: utf-8 -*-
"""离线验证小飞机插件 v2.2.1：中途退出后按配置自动重开。

不真启动 MSI Afterburner，也不 taskkill 任何进程：launch() 被替换成记录函数，
_on_tick 用假 tick 驱动，验证 30 秒延时、取消条件、0=旧口径和目录容错。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import plugin_host  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_src  # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


class Tick(object):
    pass


def tick(now, seq=2, names=None, state='active'):
    t = Tick()
    t.procs = {}
    t.names = set(names or ())
    t.seq, t.now = seq, now
    t.session = Tick()
    t.session.state = state
    return t


class Harness(object):
    def __init__(self, cfg=None):
        self.base = tempfile.mkdtemp(prefix='abg_ab_')
        shutil.copytree(os.path.join(_plugin_src.require('afterburner'), 'afterburner'),
                        os.path.join(self.base, 'plugins', 'afterburner'))
        self.logs = []
        self.host = plugin_host.PluginHost(self.base, log_func=self.logs.append)
        self.host.providers['games_present'] = lambda procs=None: False
        self.host.load_all(None, write_state=False)
        self.entry = self.host.entry_by_id('afterburner')
        self.plugin = self.entry.plugin
        merged = dict(self.plugin.DEFAULT_CONFIG)
        if cfg:
            merged.update(cfg)
        self.plugin.on_config(merged)
        self.plugin._core = lambda: type('C', (), {
            'AB_NAME': 'msiafterburner.exe',
            'RTSS_NAMES': ('rtss.exe',),
            'CREATE_NO_WINDOW': 0x08000000,
            'snapshot': staticmethod(lambda: {}),
            'process_path': staticmethod(lambda pid: ''),
            'shell_execute_runas': staticmethod(lambda path: 0),
        })()
        self.launches = []
        self.plugin.launch = lambda now=0.0: self.launches.append(now)
        self.plugin._close_batch = lambda targets: {}


def main():
    h = Harness()
    try:
        p = h.plugin
        print('[1] manifest 与默认配置')
        with open(os.path.join(h.base, 'plugins', 'afterburner', 'plugin.json'),
                  encoding='utf-8') as f:
            raw = json.load(f)
        check(raw['version'] == '2.2.1', 'manifest 升到 2.2.1')
        check('ab_relaunch_sec' in raw['config']['keys'],
              'ab_relaunch_sec 在 manifest 声明的配置键里')
        check(p.relaunch_sec == 30.0, '默认 30 秒后自动重开')

        print('[2] 中途退出：登记待重开，但不当轮启动')
        p.ab_pid = 7880
        p.ab_created = 12345.0
        p.start_seq = 1
        p.on_tick(tick(1000.0, names={'explorer.exe'}))
        check(p.ab_pid is None and p.relaunch_at == 1030.0 and not h.launches,
              '退出后所有权清空，1030s 才重开，当前不启动')
        check(any('30 秒后自动重开' in x for x in h.logs),
              '退出事件写进守护日志')

        print('[3] 到点重开一次；迟到 tick 不重复')
        p.on_tick(tick(1029.0, seq=3, names={'explorer.exe'}))
        check(not h.launches and p.relaunch_at == 1030.0,
              '未到点不启动')
        p.on_tick(tick(1030.0, seq=4, names={'explorer.exe'}))
        check(h.launches == [1030.0] and p.relaunch_at == 0.0,
              '到点启动一次并清掉待办：%r' % (h.launches,))

        print('[4] 自行恢复 / 会话重开 / 关闭联动 都取消待重开')
        h2 = Harness()
        p2 = h2.plugin
        p2.ab_pid = 900
        p2.start_seq = 1
        p2.on_tick(tick(1000.0, names=set()))
        p2.on_tick(tick(1030.0, seq=3, names={'msiafterburner.exe'}))
        check(not h2.launches, '小飞机自己回来了 -> 不重复拉起')
        p2.ab_pid = 900
        p2.on_tick(tick(1100.0, seq=4, names=set()))
        p2.on_tick(tick(1130.0, seq=5, state='idle'))
        p2.on_session_start(tick(1200.0, seq=6, names=set()))
        check(p2.relaunch_at == 0.0, '新会话开始清掉旧待办')
        before = len(h2.launches)
        p2.ab_pid = 900
        p2.on_tick(tick(1300.0, seq=7, names=set()))
        p2.enabled = False
        p2.on_tick(tick(1330.0, seq=8, names=set()))
        check(len(h2.launches) == before, '联动已关 -> 取消重开')

        print('[5] 0 = 旧口径：不再自动拉起')
        h3 = Harness({'ab_relaunch_sec': 0})
        p3 = h3.plugin
        p3.ab_pid = 900
        p3.start_seq = 1
        p3.on_tick(tick(1000.0, names=set()))
        check(p3.relaunch_at == 0.0 and not h3.launches
              and any('本次不再自动拉起' in x for x in h3.logs),
              '配置为 0 时保留旧行为与旧日志')

        print('[6] 关闭小飞机时一并清掉待重开')
        h4 = Harness()
        p4 = h4.plugin
        p4.relaunch_at = 9999.0
        p4.ab_pid = 900
        p4.close_owned({900: 'msiafterburner.exe'})
        check(p4.relaunch_at == 0.0, '会话结束关闭后不留待重开')
    finally:
        shutil.rmtree(h.base, ignore_errors=True)

    if FAILS:
        print('\nFAILED %d: %s' % (len(FAILS), '; '.join(FAILS)))
        sys.exit(1)
    print('\nALL OK')


if __name__ == '__main__':
    main()
