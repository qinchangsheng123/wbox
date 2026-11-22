# -*- coding: utf-8 -*-
"""离线验证插件发现与加载：排序、id 冲突、坏 JSON 隔离、entry 指向不存在的类。

用 tempfile 临时目录 + test\\fixtures\\plugins 的拷贝，不碰 app\\ 下生产文件，
也不依赖任何真实进程。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import plugin_api
import plugin_host

FIXTURES = os.path.join(ROOT, 'test', 'fixtures', 'plugins')
FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def make_base():
    """临时安装根：把 fixtures 里的示例插件拷进去（保持仓库里那份只读）。"""
    base = tempfile.mkdtemp(prefix='abg_discovery_')
    shutil.copytree(FIXTURES, os.path.join(base, 'plugins'))
    return base


def write_plugin(base, dirname, manifest, source):
    d = os.path.join(base, 'plugins', dirname)
    if not os.path.isdir(d):
        os.makedirs(d)
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        if isinstance(manifest, str):
            f.write(manifest)
        else:
            json.dump(manifest, f, ensure_ascii=False)
    if source is not None:
        with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
            f.write(source)
    return d


PLUGIN_TMPL = '''# -*- coding: utf-8 -*-
import plugin_api

TAG = %r


class Plugin(plugin_api.WBOXPlugin):
    DEFAULT_CONFIG = {'%s_enabled': True}
    seen = []

    def on_load(self):
        Plugin.seen.append('load')

    def on_tick(self, tick):
        Plugin.seen.append('tick')
'''


def manifest_for(pid, order, **over):
    m = {
        'id': pid,
        'name': pid.upper(),
        'version': '1.0.0',
        'api': 1,
        'icon': 'dot',
        'order': order,
        'toggle': [pid + '_enabled'],
    }
    m.update(over)
    return m


print('[1] 三个插件按 (order, id) 排序，不依赖目录枚举顺序')
base = make_base()
# 目录名字典序 = c/z/a，加载顺序必须由 order 决定
write_plugin(base, 'z_last', manifest_for('zulu', 30), PLUGIN_TMPL % ('zulu', 'zulu'))
write_plugin(base, 'c_mid', manifest_for('charlie', 20), PLUGIN_TMPL % ('charlie', 'charlie'))
write_plugin(base, 'a_first', manifest_for('alpha', 10), PLUGIN_TMPL % ('alpha', 'alpha'))
# 同 order 的两个：必须按 id 字典序，而不是目录名字典序
write_plugin(base, 'b_same2', manifest_for('tango', 40), PLUGIN_TMPL % ('tango', 'tango'))
write_plugin(base, 'b_same1', manifest_for('sierra', 40), PLUGIN_TMPL % ('sierra', 'sierra'))

host = plugin_host.PluginHost(base, log_func=lambda m: None)
host.load_all()
ids = [e.id for e in host.registry]
check(ids == ['alpha', 'charlie', 'zulu', 'sierra', 'tango', 'demo_sample'],
      '顺序 = (order, id)（实际 %s）' % (ids,))
loaded = [e.id for e in host.registry if e.usable]
check(loaded == ids, '全部 6 个都加载成功（实际 %s）' % (loaded,))
check([p.ctx.id for p in host.plugins] == ids, 'plugins 属性同样排序')
check(plugin_api.deep_merge({'a': 1}, {}) != {}, 'deep_merge 可用')

mod = sys.modules['wbox_plugin_tango.plugin']
check(mod.TAG == 'tango', '插件模块按合成包名注册：wbox_plugin_tango.plugin')
check(os.path.dirname(mod.__file__) == os.path.join(base, 'plugins', 'b_same2'),
      '插件模块 __file__ 指向插件目录内的真实文件')
check(os.path.join(base, 'plugins', 'b_same2') not in sys.path,
      '插件目录没有被塞进 sys.path（导入劫持面）')

print('[2] 相对导入：from . import helper 可用（不走 sys.path）')
write_plugin(base, 'd_helper', manifest_for('helper_user', 50),
             'from plugin_api import WBOXPlugin\n'
             'from . import helper\n\n\n'
             'class Plugin(WBOXPlugin):\n'
             '    DEFAULT_CONFIG = {}\n\n'
             '    def on_load(self):\n'
             '        self.ctx.log(helper.VALUE)\n')
with open(os.path.join(base, 'plugins', 'd_helper', 'helper.py'), 'w',
          encoding='utf-8') as f:
    f.write('VALUE = "from-helper"\n')
host2 = plugin_host.PluginHost(base, log_func=lambda m: None)
host2.load_all()
e = [x for x in host2.registry if x.id == 'helper_user'][0]
check(e.status == 'ok', '相对导入的插件加载成功（%s）' % e.detail)
check(sys.modules['wbox_plugin_helper_user'].__path__ ==
      [os.path.join(base, 'plugins', 'd_helper')], '合成父包 __path__ 指向插件目录')

print('[3] 重复 id：后者 conflict 且**未执行**模块顶层代码')
sentinel = os.path.join(base, 'conflict_sentinel.txt')


def dup_source(tag):
    return ('# -*- coding: utf-8 -*-\n'
            'import io, plugin_api\n'
            'TAG = %r\n'
            'with io.open(%r, "a", encoding="utf-8") as _f:\n'
            '    _f.write(TAG + "\\n")\n\n\n'
            'class Plugin(plugin_api.WBOXPlugin):\n'
            '    DEFAULT_CONFIG = {}\n' % (tag, sentinel))


write_plugin(base, 'e_dup_a', manifest_for('dup', 60), dup_source('first'))
write_plugin(base, 'e_dup_b', manifest_for('dup', 60), dup_source('second'))
host3 = plugin_host.PluginHost(base, log_func=lambda m: None)
host3.load_all()
dups = [x for x in host3.registry if x.id == 'dup']
check(len(dups) == 2, '两个同 id 目录都留在注册表里（面板要能显示冲突行）')
check(dups[0].status == 'ok' and dups[1].status == 'conflict',
      '先加载者 ok、后者 conflict（%s / %s）' % (dups[0].status, dups[1].status))
check('e_dup_a' in dups[1].detail, 'conflict detail 指明冲突目录（%s）' % dups[1].detail)
check(dups[1].plugin is None, 'conflict 条目没有插件实例')
with open(sentinel, 'r', encoding='utf-8') as f:
    ran = [x.strip() for x in f if x.strip()]
check(ran == ['first'], '只有第一个插件执行了模块顶层代码（sentinel=%r）' % (ran,))

print('[4] 坏 JSON 只影响自己')
write_plugin(base, 'f_badjson', '{ broken json ...', None)
host4 = plugin_host.PluginHost(base, log_func=lambda m: None)
host4.load_all()
bad = [x for x in host4.registry if x.dir.endswith('f_badjson')][0]
check(bad.status == 'bad_manifest' and bad.plugin is None, '坏 JSON 条目 bad_manifest')
good_ids = [x.id for x in host4.registry if x.usable]
check('alpha' in good_ids and 'charlie' in good_ids, '其余插件照常加载（%d 个）' % len(good_ids))

print('[5] 导入期抛异常 / 类不存在 / 构造抛异常 -> load_failed')
write_plugin(base, 'g_raise', manifest_for('raiser', 70),
             'raise RuntimeError("顶层就炸")\n')
write_plugin(base, 'h_noclass', manifest_for('noclass', 71),
             'class NotTheOne(object):\n    pass\n')
write_plugin(base, 'i_badctor', manifest_for('badctor', 72),
             'import plugin_api\n\n\n'
             'class Plugin(plugin_api.WBOXPlugin):\n'
             '    def __init__(self, ctx):\n'
             '        raise ValueError("构造就炸")\n')
write_plugin(base, 'j_exit', manifest_for('suicide', 73),
             'raise SystemExit(3)\n')
host5 = plugin_host.PluginHost(base, log_func=lambda m: None)
host5.load_all()
by_id = dict((x.id, x) for x in host5.registry if x.id)
check(by_id['raiser'].status == 'load_failed' and 'RuntimeError' in by_id['raiser'].detail,
      '导入期抛异常 -> load_failed（%s）' % by_id['raiser'].detail)
check(by_id['noclass'].status == 'load_failed' and 'NotTheOne' not in by_id['noclass'].detail,
      '类不存在 -> load_failed（%s）' % by_id['noclass'].detail)
check(by_id['badctor'].status == 'load_failed' and 'ValueError' in by_id['badctor'].detail,
      '构造抛异常 -> load_failed（%s）' % by_id['badctor'].detail)
check(by_id['suicide'].status == 'load_failed' and 'SystemExit' in by_id['suicide'].detail,
      '插件顶层调 sys.exit（SystemExit）被拦住（%s）' % by_id['suicide'].detail)
check('wbox_plugin_raiser.plugin' not in sys.modules,
      '加载失败后半成品模块已从 sys.modules 摘掉')

print('[6] 校验通过的 manifest 才能被 import（坏 id 的插件代码不执行）')
sentinel2 = os.path.join(base, 'badid_sentinel.txt')
write_plugin(base, 'k_badid', manifest_for('Bad-Id', 80),
             'import io\n'
             'with io.open(%r, "w", encoding="utf-8") as f:\n'
             '    f.write("ran")\n' % sentinel2)
host6 = plugin_host.PluginHost(base, log_func=lambda m: None)
host6.load_all()
check(not os.path.isfile(sentinel2), 'manifest 非法的目录，插件代码一行都没执行')

print('[7] load_registry：只发现+加载，不调 on_start / on_tick')
base2 = make_base()
write_plugin(base2, 'z_last', manifest_for('zulu', 30), PLUGIN_TMPL % ('zulu', 'zulu'))
entries = plugin_host.load_registry(base2, log_func=lambda m: None)
check([e.id for e in entries] == ['zulu', 'demo_sample'], 'load_registry 返回 entries（%s）'
      % ([e.id for e in entries],))
zmod = sys.modules['wbox_plugin_zulu.plugin']
check(zmod.Plugin.seen == ['load'], '只走了 on_load，没被 tick（%r）' % (zmod.Plugin.seen,))
demo = sys.modules['wbox_plugin_demo_sample.plugin']
check(all(c[0] != 'on_start' for c in demo.CALLS),
      '面板路径不触发 on_start（%r）' % ([c[0] for c in demo.CALLS],))

print('[8] ctx 契约与基类默认实现（gui_state / gui_toggle / 越权写 / 状态文件）')
demo_entry = [e for e in entries if e.id == 'demo_sample'][0]
demo_plugin = demo_entry.plugin
ctx = demo_plugin.ctx

check(ctx.id == 'demo_sample' and ctx.api == plugin_api.API_VERSION, 'ctx.id / ctx.api')
check(ctx.plugin_dir == os.path.join(base2, 'plugins', 'demo_sample'), 'ctx.plugin_dir')
check(os.path.isdir(ctx.data_dir), 'ctx.data_dir 已由宿主 makedirs（%s）' % ctx.data_dir)

# 默认 gui_state：manifest.toggle 的路径全部为真才算开
check(demo_plugin.gui_state({}) is False, 'toggle 缺失 -> gui_state=False（默认关）')
check(demo_plugin.gui_state({'demo_sample_enabled': True}) is True, '全真 -> True')
check(demo_plugin.gui_state({'demo_sample_enabled': False}) is False, '有假 -> False')
cfg_mem = {}
check(demo_plugin.gui_toggle(cfg_mem, True) is True, 'gui_toggle 写入成功')
check(cfg_mem == {'demo_sample_enabled': True},
      'gui_toggle 只写自己声明的键（%r）' % (cfg_mem,))
# v2.18.0：gui_menu_rows 契约随齿轮菜单一起删除——插件自定义界面只走
# manifest.settings（行内展开的浮层），不再有「往菜单塞一行」这条口子。
check(not hasattr(plugin_api.WBOXPlugin, 'gui_menu_rows'),
      '基类不再提供 gui_menu_rows（齿轮菜单退役）')
check(plugin_api.WingmanPlugin is plugin_api.WBOXPlugin,
      '旧类名 WingmanPlugin 保留为别名（第三方插件不改源码也能加载）')

# ctx.cfg / set_cfg 权限
check(ctx.cfg('demo_sample.greeting') == 'hello', 'ctx.cfg 默认值来自 DEFAULT_CONFIG')
try:
    ctx.set_cfg('ui_theme', 'dark')
    denied = False
except PermissionError:
    denied = True
check(denied, 'ctx.set_cfg 写内核键抛 PermissionError')
try:
    ctx.set_cfg('not_declared_key', 1)
    denied2 = False
except PermissionError:
    denied2 = True
check(denied2, 'ctx.set_cfg 写未声明键抛 PermissionError')
ctx.set_cfg('demo_sample.greeting', 'hi')
check(ctx.cfg('demo_sample.greeting') == 'hi', 'ctx.set_cfg 写自己声明的键成功')

# 状态文件与 import_core
ctx.write_state('cache', {'n': 1})
check(ctx.read_state('cache') == {'n': 1}, 'ctx.write_state / read_state 往返')
check(ctx.read_state('not_exist', 'dflt') == 'dflt', 'read_state 缺文件回落 default')
try:
    ctx.import_core('os')
    key_err = False
except KeyError:
    key_err = True
check(key_err, 'ctx.import_core 未知名字抛 KeyError')
check(ctx.import_core('plugin_api') is plugin_api, 'ctx.import_core 命中白名单')
check(ctx.procs() is None and ctx.games_present() is True and ctx.watch_names() == set(),
      '没有内核 provider 时 ctx 返回安全值（None / True / 空集合）')
ctx.set_status('示例：就绪')
check(demo_entry.state_text == '示例：就绪', 'ctx.set_status 写到注册表摘要')

print('[9] 配置合并：插件 DEFAULT_CONFIG 被 deep-merge 进内核默认值')
cfg_in = {'demo_sample': {'times': 9}, '_说明': '用户手写的说明键'}
merged = plugin_host.merge_defaults({'poll_interval_sec': 2.0, 'demo_sample': {'times': 3}},
                                    entries, cfg_in)
check(merged['poll_interval_sec'] == 2.0, '内核键保留')
check(merged['demo_sample']['greeting'] == 'hello', '插件默认值补进缺的键')
check(merged['demo_sample']['times'] == 9, '用户值优先')
check(merged['_说明'] == '用户手写的说明键', '未知键原样保留')
check(merged['demo_sample_enabled'] is True, '开关键来自插件默认值')
check(cfg_in == {'demo_sample': {'times': 9}, '_说明': '用户手写的说明键'},
      'merge_defaults 不修改入参')
one = plugin_host.plugin_cfg({'demo_sample': {'times': 5}, 'ui_theme': 'dark'}, demo_entry)
check(one['demo_sample']['times'] == 5 and 'ui_theme' not in one,
      'plugin_cfg 只带自己声明的键（%r）' % (one,))

print('[10] dispatch(on_config) 每个插件只收到自己声明的键')
base5 = make_base()
write_plugin(base5, 'z_last', manifest_for('zulu', 30), PLUGIN_TMPL % ('zulu', 'zulu'))
host5 = plugin_host.PluginHost(base5, log_func=lambda m: None)
host5.load_all()
host5.dispatch('on_config', {'demo_sample_enabled': False,
                             'demo_sample': {'times': 7},
                             'ui_theme': 'dark',
                             'poll_fast_sec': 1.0})
demo_mod = sys.modules['wbox_plugin_demo_sample.plugin']
got = [c[1] for c in demo_mod.CALLS if c[0] == 'on_config'][-1]
check(got.get('demo_sample_enabled') is False, '用户值覆盖插件默认值（%r）' % (got,))
check('ui_theme' not in got and 'poll_fast_sec' not in got, '内核键没有泄漏给插件')
check(got['demo_sample'] == {'greeting': 'hello', 'times': 7}, '自己的段深合并正确（%r）'
      % (got['demo_sample'],))

print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
