# -*- coding: utf-8 -*-
"""离线验证 plugin.json 校验：必填字段 / id 规则 / api 闸门 / icon 兜底 / toggle 缺省。

不 import 任何插件代码（manifest 非法时宿主本就不该 import），
全部用 tempfile 建的临时目录，不碰 app\\ 下的生产文件。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))
import plugin_api
import plugin_host

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def make_base():
    return tempfile.mkdtemp(prefix='abg_manifest_')


def write_plugin(base, dirname, manifest):
    """写一个插件目录；manifest 可以是 dict，也可以是原样字符串（用于坏 JSON）。"""
    d = os.path.join(base, 'plugins', dirname)
    os.makedirs(d)
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        if isinstance(manifest, str):
            f.write(manifest)
        else:
            json.dump(manifest, f, ensure_ascii=False)
    return d


def discover_one(base):
    entries = plugin_host.discover(base, log_func=lambda m: None)
    assert len(entries) == 1, entries
    return entries[0]


GOOD = {
    'id': 'demo_one',
    'name': '演示一',
    'version': '1.0.0',
    'api': 1,
    'icon': 'link',
    'order': 30,
    'toggle': ['demo_one_enabled'],
}


def with_(**kw):
    """基于 GOOD 造一份 manifest，kw 里的 None 表示删掉该字段。"""
    m = dict(GOOD)
    for k, v in kw.items():
        if v is None:
            m.pop(k, None)
        else:
            m[k] = v
    return m


print('[1] 正常 manifest 全部字段按预期解析')
base = make_base()
write_plugin(base, 'demo_one', GOOD)
e = discover_one(base)
check(e.status == 'ok', 'status=ok（实际 %r / %s）' % (e.status, e.detail))
check(e.id == 'demo_one' and e.name == '演示一' and e.version == '1.0.0', 'id/name/version')
check(e.api == 1 and e.icon == 'link' and e.order == 30, 'api/icon/order')
check(e.toggle == ['demo_one_enabled'], 'toggle 原样保留')
check(e.home is True and e.plugin is None, 'home=True 且 discover 阶段不 import')

print('[2] 必填字段缺失：各被拒绝为 bad_manifest')
for key, label in (('id', 'id'), ('name', 'name'), ('version', 'version'), ('api', 'api')):
    b = make_base()
    write_plugin(b, 'demo_one', with_(**{key: None}))
    e = discover_one(b)
    check(e.status == 'bad_manifest' and e.plugin is None,
          '缺 %s -> %s（%s）' % (label, e.status, e.detail))

print('[3] id 规则：非法字符 / 太短 / 大写 / 数字开头 / 过长')
bad_ids = ['Ace', '1abc', 'a', 'demo-one', 'demo one', 'demo.one', 'a' * 33]
for bad in bad_ids:
    b = make_base()
    write_plugin(b, 'whatever', with_(id=bad))
    e = discover_one(b)
    check(e.status == 'bad_manifest', 'id=%r 被拒（%s）' % (bad, e.detail))

print('[4] api 闸门')
b = make_base()
write_plugin(b, 'demo_one', with_(api=0))
e = discover_one(b)
check(e.status == 'bad_manifest' and 'api' in e.detail,
      'api=0 -> bad_manifest（%s）' % e.detail)

b = make_base()
write_plugin(b, 'demo_one', with_(api=plugin_api.API_VERSION + 1))
e = discover_one(b)
check(e.status == 'too_new', 'api=%d -> too_new（%s）'
      % (plugin_api.API_VERSION + 1, e.detail))

b = make_base()
write_plugin(b, 'demo_one', with_(api='1'))
e = discover_one(b)
check(e.status == 'bad_manifest', 'api 是字符串 -> bad_manifest（%s）' % e.detail)

b = make_base()
write_plugin(b, 'demo_one', with_(api=True))
e = discover_one(b)
check(e.status == 'bad_manifest', 'api=True（bool 冒充 int）-> bad_manifest')

print('[5] icon 未知回落 dot（不拒绝）')
b = make_base()
write_plugin(b, 'demo_one', with_(icon='rocket'))
e = discover_one(b)
check(e.status == 'ok' and e.icon == 'dot',
      'icon=rocket -> dot（status=%s）' % e.status)
check(plugin_api.ICON_WHITELIST == frozenset((
    'link', 'plane', 'pulse', 'shield', 'block', 'dot', 'play', 'stop',
    'boot', 'rescan', 'log', 'cfg', 'sun', 'moon', 'check', 'close',
    'plus', 'trash', 'folder', 'admin')) and len(plugin_api.ICON_WHITELIST) == 20,
    'ICON_WHITELIST 是 20 个（v2.2.0 增 plane/pulse/folder/admin/plus/trash）')

print('[6] toggle 缺省 / 空数组')
b = make_base()
write_plugin(b, 'demo_one', with_(toggle=None))
e = discover_one(b)
check(e.toggle == ['demo_one_enabled'] and e.home is True,
      'toggle 缺省 -> [<id>_enabled]（%r）' % (e.toggle,))

b = make_base()
write_plugin(b, 'demo_one', with_(toggle=[]))
e = discover_one(b)
check(e.toggle == [] and e.home is False,
      'toggle=[] -> 无开关且强制 home=False（home=%r）' % (e.home,))

b = make_base()
write_plugin(b, 'demo_one', with_(toggle=[], home=True))
e = discover_one(b)
check(e.home is False, 'toggle=[] 时即使写 home=true 也强制 False')

b = make_base()
write_plugin(b, 'demo_one', with_(toggle=['  ', 5, 'demo_one_enabled']))
e = discover_one(b)
check(e.toggle == ['demo_one_enabled'], 'toggle 里的空白/非字符串项被洗掉（%r）' % (e.toggle,))

print('[7] 保留键不得被插件声明为开关')
b = make_base()
write_plugin(b, 'demo_one', with_(toggle=['ui_theme', 'poll_fast_sec', 'demo_one_enabled']))
e = discover_one(b)
check(e.toggle == ['demo_one_enabled'], '内核保留键被过滤（%r）' % (e.toggle,))

print('[8] entry 非法 / 坏 JSON / 无 plugin.json')
b = make_base()
write_plugin(b, 'demo_one', with_(entry='..\\evil.py:Plugin'))
e = discover_one(b)
check(e.status == 'bad_manifest', 'entry 带 .. -> bad_manifest（%s）' % e.detail)

b = make_base()
write_plugin(b, 'demo_one', '{ this is not json')
e = discover_one(b)
check(e.status == 'bad_manifest' and '解析' in e.detail, '坏 JSON -> bad_manifest')

b = make_base()
os.makedirs(os.path.join(b, 'plugins', 'empty_dir'))
e = discover_one(b)
check(e.status == 'bad_manifest' and 'plugin.json' in e.detail,
      '缺 plugin.json -> bad_manifest（%s）' % e.detail)

print('[9] 目录名 != id 只告警不拒绝')
b = make_base()
write_plugin(b, '中文目录名', GOOD)
logs = []
e = plugin_host.discover(b, log_func=logs.append)[0]
check(e.status == 'ok' and e.id == 'demo_one', '目录名不同仍按 id 加载')
check(any('不一致' in m for m in logs), '记了一条警告（%s）' % (logs,))

print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
