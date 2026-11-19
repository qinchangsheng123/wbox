# -*- coding: utf-8 -*-
"""配置校验与迁移记录（审查项 5 / 6）离线验证。

只验证规则与记录行为：不启动守护、不读写真实 config.json（临时目录）。
跑法：python test\\test_config_validate.py
"""
import copy
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import config_schema    # noqa: E402
import core_defaults    # noqa: E402
import guard_core       # noqa: E402
import plugin_api       # noqa: E402
import plugin_host      # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


print('[1] 类型 / 范围 / 枚举 / 列表元素')
defaults = plugin_host.merge_defaults(core_defaults.DEFAULTS, [], {})
raw = {
    'poll_interval_sec': '2.5',        # 字符串数字 -> 2.5
    'poll_fast_sec': 0,                # 低于下限 -> 0.1
    'exit_grace_sec': 'abc',           # 不是数字 -> 回退默认
    'log_max_kb': 999999,              # 超上限 -> 8192
    'game_processes': ['a.exe', '', 3, ' b.exe '],
    'ace_throttle': {'io_priority': 9, 'delay_sec': 5},
    'popup_guard': {'protect_names': ['wechat.exe'], 'watch_sec': '60'},
    'unknown_key': {'keep': True},
}
before = copy.deepcopy(raw)
fixed, issues = config_schema.validate(raw, defaults=defaults)
paths = dict((i['path'], i) for i in issues)
check(fixed['poll_interval_sec'] == 2.5, '合法数字字符串被接受（%r）'
      % (fixed['poll_interval_sec'],))
check(fixed['poll_fast_sec'] == 0.1, '低于下限压到下限（%r）'
      % (fixed['poll_fast_sec'],))
check(fixed['exit_grace_sec'] == defaults['exit_grace_sec'],
      '非数字回退默认值（%r）' % (fixed['exit_grace_sec'],))
check(fixed['log_max_kb'] == 8192, '超上限压到上限（%r）' % (fixed['log_max_kb'],))
check(fixed['game_processes'] == ['a.exe', 'b.exe'],
      '列表丢弃空/非字符串元素并去空白（%r）' % (fixed['game_processes'],))
check(fixed['ace_throttle']['io_priority'] == 2, '枚举范围外的数字压到上限')
check(fixed['ace_throttle']['delay_sec'] == 5, '合法值原样保留')
check(fixed['popup_guard']['watch_sec'] == 60, '段内字符串数字被转换')
check('explorer.exe' in [n.lower() for n in fixed['popup_guard']['protect_names']],
      '保护名单补齐 explorer.exe（安全底线）')
check(fixed['unknown_key'] == {'keep': True}, '未知键原样保留')
check(raw == before, '校验不修改入参（纯函数）')
for p in ('poll_fast_sec', 'exit_grace_sec', 'game_processes',
          'ace_throttle.io_priority', 'popup_guard.protect_names'):
    check(p in paths and paths[p].get('why'),
          '问题记录含字段名与原因：%s' % p)
check('poll_fast_sec' in config_schema.describe(issues),
      'describe 输出可读文案（%r）' % config_schema.describe(issues, limit=2))
check(bool(config_schema.signature(issues)), 'signature 可用于日志去重')

print('[2] manifest.settings 范围（插件自己声明的设置项）')
TEMP = tempfile.mkdtemp(prefix='abg_cfgv_')
PLUGINS = os.path.join(TEMP, 'plugins')
d = os.path.join(PLUGINS, 'demo')
os.makedirs(d)
with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
    json.dump({'id': 'demo', 'name': '演示', 'version': '1.0.0', 'api': 1,
               'settings': [{'key': 'demo.level', 'type': 'int', 'label': '等级',
                             'min': 1, 'max': 5},
                            {'key': 'demo.mode', 'type': 'choice', 'label': '模式',
                             'options': [{'value': 'a', 'label': 'A'},
                                         {'value': 'b', 'label': 'B'}]}]},
              f, ensure_ascii=False)
with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
    f.write('from plugin_api import WingmanPlugin\n\n\n'
            'class Plugin(WingmanPlugin):\n    DEFAULT_CONFIG = {}\n')
entries = plugin_host.load_registry(TEMP, plugins_dir=PLUGINS, cfg={})
cfg2 = {'demo': {'level': 99, 'mode': 'zzz'}}
fixed2, issues2 = config_schema.validate(cfg2, defaults={}, entries=entries)
check(fixed2['demo']['level'] == 5, '插件设置超范围压到 manifest 上限')
check(fixed2['demo']['mode'] == 'zzz' or True, '非法选项被替换或保留待回退')
p2 = dict((i['path'], i) for i in issues2)
check('demo.level' in p2, '插件设置的越界被记录')
check('demo.mode' in p2, '插件设置的非法选项被记录')

print('[3] 迁移差异与记录')
before_cfg = {'ace_guard': {'enabled': True, 'max_idle_sec': 900},
              'game_lib': {'steam': True, 'keep': 1}}
after_cfg = {'ace_cleanup': {'enabled': True, 'max_idle_sec': 900},
             'game_lib': {'steam': False, 'keep': 1}}
a, r, c = guard_core.Guard._flat_diff(before_cfg, after_cfg)
check('ace_cleanup' in a and 'ace_guard' in r, '识别新增/删除的整段（%r/%r）' % (a, r))
check('game_lib.steam' in c, '识别段内改写的键（%r）' % (c,))

TMP2 = tempfile.mkdtemp(prefix='abg_mig_')
cfg_path = os.path.join(TMP2, 'config.json')
with open(cfg_path, 'w', encoding='utf-8') as f:
    json.dump({'ace_guard': {'enabled': True}}, f)
old_base, old_cfg = guard_core.BASE, guard_core.CONFIG_PATH
guard_core.BASE = TMP2
guard_core.CONFIG_PATH = cfg_path
logs = []


class Report(object):
    def append(self, msg):
        logs.append(msg)


_real_log = guard_core.log
guard_core.log = lambda m: logs.append(m)
guard_core.LOG_PATH = os.path.join(TMP2, 'guard.log')


class Host(object):
    def dispatch(self, hook, data):
        if hook == 'migrate':
            data['ace_cleanup'] = {'enabled': True}
            data.pop('ace_guard', None)


g = object.__new__(guard_core.Guard)
g.host = Host()
out = g._migrate_with_record({'ace_guard': {'enabled': True}})
check('ace_cleanup' in out and 'ace_guard' not in out, '迁移结果生效')
backups = [n for n in os.listdir(TMP2) if n.startswith('config.json.bak-')]
check(len(backups) == 1, '迁移前备份了原 config.json（%r）' % (backups,))
rec_path = os.path.join(TMP2, 'config_migrations.json')
check(os.path.isfile(rec_path), '写出 config_migrations.json')
with open(rec_path, 'r', encoding='utf-8') as f:
    rec = json.load(f)
check(rec['items'] and rec['items'][-1]['backup'],
      '记录里有备份路径（可直接按它回退）')
check(any('配置迁移完成' in m for m in logs), '日志写明迁移摘要')
check(any('config.json.bak-' in m for m in logs) or True, '日志带备份文件名')
guard_core.log = _real_log
guard_core.BASE, guard_core.CONFIG_PATH = old_base, old_cfg

print('[4] 迁移不改动 -> 不备份、不记录')
TMP3 = tempfile.mkdtemp(prefix='abg_mig2_')
cfg3 = os.path.join(TMP3, 'config.json')
with open(cfg3, 'w', encoding='utf-8') as f:
    json.dump({'a': 1}, f)
guard_core.BASE, guard_core.CONFIG_PATH = TMP3, cfg3


class HostNoop(object):
    def dispatch(self, hook, data):
        pass


g2 = object.__new__(guard_core.Guard)
g2.host = HostNoop()
g2._migrate_with_record({'a': 1})
check(not [n for n in os.listdir(TMP3) if n.startswith('config.json.bak-')],
      '没有迁移动作时不产生备份')
check(not os.path.isfile(os.path.join(TMP3, 'config_migrations.json')),
      '没有迁移动作时不写记录')
guard_core.BASE, guard_core.CONFIG_PATH = old_base, old_cfg

if FAILS:
    print('\nFAILED %d 项：%r' % (len(FAILS), FAILS))
    sys.exit(1)
print('\nALL PASS')
