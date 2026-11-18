# -*- coding: utf-8 -*-
"""离线验证 gamepp（游戏加加联动）插件：所有权 / 基线 / 关闭重试 / 路径脱敏。

用假 provider 驱动真实插件代码，launch() 与 close_pid() 被替换成记录函数，
绝不真启动 GamePP.exe，也绝不 taskkill 任何进程。
"""
import json
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import core_defaults
import plugin_host
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_src  # noqa: E402  插件源码在独立仓库 wbox-plugins

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


class Tick(object):
    pass


def tick(procs=None, names=None, seq=1, state='active'):
    t = Tick()
    t.procs = {} if procs is None else procs
    t.names = set() if names is None else set(names)
    t.seq, t.now = seq, 1000.0
    t.session = Tick()
    t.session.state = state
    return t


class Harness(object):
    def __init__(self):
        self.base = tempfile.mkdtemp(prefix='abg_gamepp_')
        shutil.copytree(os.path.join(_plugin_src.require('gamepp'), 'gamepp'),
                        os.path.join(self.base, 'plugins', 'gamepp'))
        self.box = {'procs': {}, 'games': False}
        self.retries, self.closed, self.launched, self.logs = [], [], [], []
        self.host = plugin_host.PluginHost(self.base, log_func=self.logs.append)
        self.host.providers.update({
            'procs': lambda: self.box['procs'],
            'snapshot': lambda: self.box['procs'],
            'games_present': lambda procs=None: self.box['games'],
            'retry_close': self._retry,
            'now': lambda: 1000.0,
            'watch_names': lambda: set(),
            'process_path': lambda pid: '',
            'refresh_paths': lambda procs: None,
        })
        self.host.load_all(None, write_state=False)
        self.entry = self.host.entry_by_id('gamepp')
        self.plugin = self.entry.plugin
        self.plugin.on_config(plugin_host.plugin_cfg(
            core_defaults.DEFAULTS, self.entry))
        self.plugin.launch = lambda: self.launched.append(1)
        self.plugin.close_pid = lambda pid, name: self.closed.append((pid, name))
        # v1.1.0 起 close_owned 走批量入口（一批共享 close_wait 期限），
        # 观察点随之从 close_pid 移到 _close_batch；断言内容不变。
        self.plugin._close_batch = lambda targets: self.closed.extend(targets)

    def _retry(self, delay=2.0):
        self.retries.append(delay)
        return True


def main():
    tmp = Harness()
    try:
        print('[1] manifest / 配置切分')
        with open(os.path.join(tmp.base, 'plugins', 'gamepp', 'plugin.json'),
                  encoding='utf-8') as f:
            raw = json.load(f)
        check(raw['id'] == 'gamepp' and raw['order'] == 15 and raw['api'] == 1,
              'manifest 合法：id=gamepp / order=15 / api=1')
        check(tmp.entry.status == 'ok' and tmp.entry.home,
              '宿主加载成功且 home=true（主页面会有一行开关）')
        check(tmp.entry.toggle == ['gamepp.enabled'],
              '开关落在 gamepp.enabled，不污染别的配置段')
        cfg = plugin_host.plugin_cfg(core_defaults.DEFAULTS, tmp.entry)
        check(set(cfg) == {'gamepp'},
              'plugin_cfg 只拿到自己的段：%r' % sorted(cfg))
        check(tmp.plugin.enabled is True and tmp.plugin.path == '',
              '默认开启、路径留空（由运行时探测，不写死本机路径）')
        check('gamepp.exe' in tmp.plugin.names and 'gamepplite.exe' in tmp.plugin.names,
              '家族进程名单含主程序与 Lite')

        print('[2] 会话开始：只在本插件没在跑时才拉起')
        p = tmp.plugin
        p.on_session_start(tick())
        check(tmp.launched == [1] and p.pending_close is True,
              '游戏启动且未运行 -> 拉起一次、登记待关闭')
        p.owned_pid = 111
        p.on_session_start(tick(procs={111: 'gamepp.exe'}, names={'gamepp.exe'}))
        check(len(tmp.launched) == 1 and p.pending_close is True,
              '上一局拉起的仍在跑 -> 继续接管，不重复启动')
        p.owned_pid = None
        p.on_session_start(tick(procs={222: 'gamepp.exe'}, names={'gamepp.exe'}))
        check(p.pending_close is False and p.baseline == {222},
              '用户自己常驻的游戏加加 -> 不接管，退出时不关闭')

        print('[3] 会话结束：没接管就不重试；取不到进程表才申请重试')
        tmp.retries, tmp.closed = [], []
        p.on_session_close(tick())
        check(tmp.retries == [] and tmp.closed == [],
              'pending_close=False -> 不关、不申请重试')
        p.pending_close = True
        tmp.box['procs'] = None
        p.on_session_close(tick())
        check(tmp.retries == [2.0] and tmp.closed == [],
              'procs=None -> ctx.retry_close(2)，且不误关')

        print('[4] 关闭所有权：只关本局新起的，基线进程不动')
        h2h = Harness()          # 独立实例，状态干净
        h2 = h2h.plugin
        h2.owned_pid = 900
        h2.baseline = {777}
        h2.launch = lambda: None
        closed = []
        h2.close_pid = lambda pid, name: closed.append((pid, name))
        h2._close_batch = lambda targets: closed.extend(targets)
        procs = {777: 'gamepp.exe',        # 基线：开局前就在跑
                 900: 'gamepp.exe',        # 本插件拉起
                 901: 'gamepplite.exe',    # 本局新出现的子组件
                 555: 'explorer.exe'}      # 无关进程
        h2.close_owned(dict(procs))
        check(sorted(closed) == [(900, 'gamepp.exe'), (901, 'gamepplite.exe')],
              '关掉自己拉起的 + 本局新增：%r' % (closed,))
        check(777 not in [pid for pid, _ in closed],
              '基线里用户常驻的 777 没有被关')

        print('[5] 关闭前复核：游戏回来了就放弃')
        h3 = Harness()
        h3.plugin.owned_pid = 900
        h3.box['games'] = True
        h3.plugin.close_owned({900: 'gamepp.exe'})
        check(h3.closed == [], '游戏回归 -> 不关任何进程')

        print('[6] 中途退出：放弃所有权，不再自动拉起')
        h4 = Harness()
        h4.plugin.owned_pid = 900
        h4.plugin.pending_close = True
        h4.plugin.start_seq = 1
        h4.plugin.on_tick(tick(seq=2, names={'explorer.exe'}))
        check(h4.plugin.owned_pid is None and h4.plugin.owned_by_name is False,
              '主进程中途消失 -> 放弃所有权')

        print('[7] 路径解析与源码脱敏')
        fake = os.path.join(tmp.base, 'GamePP.exe')
        with open(fake, 'wb') as f:
            f.write(b'MZ')
        tmp.plugin.path = fake
        check(tmp.plugin.resolve_path() == fake, 'config 里显式路径优先')
        tmp.plugin.path = ''
        tmp.plugin._detected = ''
        tmp.plugin._from_registry = lambda: ''
        tmp.plugin._core = lambda: type('C', (), {
            'snapshot': staticmethod(lambda: {}),
            'process_path': staticmethod(lambda pid: ''),
        })()
        check(tmp.plugin.resolve_path() == '',
              '探测不到时返回空串并让 launch 记日志，不瞎指路径')
        src = open(os.path.join(_plugin_src.require('gamepp'), 'gamepp', 'plugin.py'),
                   encoding='utf-8').read()
        check(not any(x in src for x in ("'F:", '"F:', "'G:", '"G:'))
              and not re.search(r"nz\.qq\.com[^\s'\"]*\s+\d{10}\b", src),
              '源码不含本机盘符路径 / 账号（脱敏红线）')

        print('[8] 自检输出')
        out = []
        tmp.plugin._core = lambda: type('C', (), {
            'snapshot': staticmethod(lambda: {1: 'gamepp.exe'}),
            'process_path': staticmethod(lambda pid: ''),
        })()
        tmp.plugin.on_self_test(out)
        check(any('GamePP running' in line for line in out) and len(out) >= 3,
              'on_self_test 输出运行态 / 路径 / 监控名单：%r' % out)
    finally:
        for h in (tmp, locals().get('h2h'), locals().get('h3'), locals().get('h4')):
            if h is not None:
                shutil.rmtree(h.base, ignore_errors=True)

    print('')
    if FAILS:
        print('FAILED %d 项：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
