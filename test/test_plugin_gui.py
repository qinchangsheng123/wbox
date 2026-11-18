# -*- coding: utf-8 -*-
"""面板「注册表驱动」回归（离线、临时 BASE、不弹真窗口）。

盯着一条硬要求：**删掉一个插件目录，那一行就消失，不报错、不留空行**。
主页面行数、名称、图标、简介、开关全部来自 plugins\\*\\plugin.json。

  [1] 3 个插件      -> 主页面 3 行，名称/图标/简介与 manifest 一致
  [2] 删掉 1 个     -> 2 行，被删的 id 不在 self.switches 里
  [3] 空目录        -> 0 行 + 灰字提示，不抛异常
  [4] 坏插件        -> 主页面不出现该行，齿轮菜单显示「未能加载」
  [5] 拨开关        -> config.json 里对应 toggle 路径被写，其它键原样保留
  [6] 运行期异常    -> 菜单 hint 变「运行异常 N 次」；面板绝不写状态文件

用 probe=True + -alpha 0 建面板：布局、行数、控件层级都是真的，只是不显示。
"""
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_plugin_gui_')
PLUGINS = os.path.join(TMP, 'plugins')
CFG_PATH = os.path.join(TMP, 'config.json')
STATE_PATH = os.path.join(TMP, 'plugins_state.json')

# plugins 目录必须在 import settings_gui 之前建好：PLUGINS_DIR 是模块级常量，
# import 时就按 BASE 解析完了（生产环境同理，BASE 由 ABG_BASE / exe 位置决定）。
os.makedirs(PLUGINS)
for _name in ('icon-light.ico', 'icon-dark.ico'):
    _src = os.path.join(ROOT, 'app', _name)
    if os.path.isfile(_src):
        shutil.copy(_src, os.path.join(TMP, _name))

# 配置里放两条"必须原样保留"的键：任何一次保存都不能把 config.json 抹成只剩开关
BASE_CFG = {
    'ui_theme': 'light',
    # v2.10.0：字标菜单提示等断言是 standard 档语义；液态玻璃不再内置，
    # 显式选 skinfix 写的 fixture 用户皮肤。
    'ui_skin': 'liquid-glass',
    'afterburner_path': r'C:\MSI Afterburner\MSIAfterburner.exe',
    'game_processes': ['game.exe'],
}


def write_cfg(cfg):
    with open(CFG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


write_cfg(BASE_CFG)
os.environ['ABG_BASE'] = TMP

# v2.3.0 复核补丁 R2（QA）：settings_gui 在 import 期就把 BASE / PLUGINS_DIR 固化成
# 当时的 ABG_BASE。同一个进程里再跑第二个 GUI 测试时，若不清掉旧模块，它会继续用
# 上一个测试（已经 rmtree 掉的）临时目录，于是注册表读不到插件、断言假失败。
# 单进程 runner / 任何顺序组合都要求这里先摘干净。
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import skinfix  # noqa: E402

skinfix.write_glass(TMP)

sg.enable_dpi_awareness()

PASSED = []


def step(cond, msg):
    if not cond:
        raise AssertionError(msg)
    PASSED.append(msg)
    print('  [ok] ' + msg)


# ---------------- 造插件 ----------------
def _nested_defaults(paths):
    """把 ['a.b', 'c'] 变成 {'a': {'b': False}, 'c': False}（插件的 DEFAULT_CONFIG）。"""
    out = {}
    for path in paths:
        cur = out
        parts = path.split('.')
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = False
    return out


def make_plugin(pid, name, icon='dot', order=100, tip='', toggle=None,
                raises=False):
    """写一个最小可加载的插件目录，返回目录路径。"""
    plugin_dir = os.path.join(PLUGINS, pid)
    if not os.path.isdir(plugin_dir):
        os.makedirs(plugin_dir)
    toggle = [pid + '_enabled'] if toggle is None else list(toggle)
    manifest = {
        'id': pid, 'name': name, 'version': '1.0.0', 'api': 1,
        'icon': icon, 'order': order, 'home': True,
        'toggle': toggle, 'entry': 'plugin.py:Plugin', 'tip': tip,
    }
    with open(os.path.join(plugin_dir, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    if raises:
        body = 'raise RuntimeError(%r)\n' % ('坏插件 %s' % pid,)
    else:
        body = ('# -*- coding: utf-8 -*-\n'
                'import plugin_api\n'
                '\n'
                '\n'
                'class Plugin(plugin_api.WBOXPlugin):\n'
                '    DEFAULT_CONFIG = %r\n' % (_nested_defaults(toggle),))
    with open(os.path.join(plugin_dir, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write(body)
    return plugin_dir


def clear_plugins():
    for name in sorted(os.listdir(PLUGINS)):
        path = os.path.join(PLUGINS, name)
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


# ---------------- 建 / 拆面板 ----------------
def new_app():
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True)
    root.update()
    return root, app


def drop(root, app):
    """关菜单 + 收掉全部挂起的 after 定时器，最后销毁窗口。

    必须先取消定时器：菜单的 _menu_watch 是按 Toplevel 注册的 120ms 轮询，
    窗口销毁后定时器仍会触发，Tcl 会往 stderr 打一串
    `invalid command name "..._menu_watch"`（不影响结果，但会淹没测试输出）。
    """
    try:
        app.close_settings()
        for job in root.tk.call('after', 'info'):
            try:
                root.after_cancel(str(job))
            except Exception:
                pass
    finally:
        root.destroy()


def row_of(app, pid):
    """某个插件在主页面上的那一行（开关的父 Frame）。"""
    return app.switches[pid].master


def row_text(app, pid):
    for w in row_of(app, pid).winfo_children():
        if isinstance(w, tk.Label):
            return w.cget('text')
    return None


def row_icon(app, pid):
    for w in row_of(app, pid).winfo_children():
        if isinstance(w, sg.Glyph):
            return w.kind
    return None


def menu_rows(app):
    return [w for w in sg.walk(app.settings_inner)
            if isinstance(w, tk.Frame) and hasattr(w, 'hint_label')]


def menu_hint(app, text):
    """按行文字找菜单行，返回右侧提示文本。"""
    for row in menu_rows(app):
        for w in row.winfo_children():
            if isinstance(w, tk.Label) and w.cget('text') == text:
                return row.hint_label.cget('text') if row.hint_label else ''
    raise AssertionError('菜单里没有这一行：' + text)


def main_texts(app):
    labels = []
    for w in sg.walk(app.main):
        if isinstance(w, tk.Label):
            labels.append(w.cget('text'))
    return labels


try:
    # ---------------- [1] 三个插件 ----------------
    print('[1] 三个插件：主页面三行，内容全部来自 manifest')
    make_plugin('demo_alpha', '甲功能', icon='link', order=10, tip='甲的简介')
    make_plugin('demo_beta', '乙功能', icon='shield', order=20, tip='乙的简介')
    make_plugin('demo_gamma', '丙功能', icon='block', order=30, tip='丙的简介')
    root, app = new_app()
    step([e.id for e in app.entries] == ['demo_alpha', 'demo_beta', 'demo_gamma'],
         '注册表按 (order, id) 排出 3 行')
    step(sorted(app.switches) == ['demo_alpha', 'demo_beta', 'demo_gamma'],
         '每个插件一个开关，键就是插件 id')
    step([row_text(app, p) for p in ('demo_alpha', 'demo_beta', 'demo_gamma')]
         == ['甲功能', '乙功能', '丙功能'], '名称取自 manifest.name')
    step([row_icon(app, p) for p in ('demo_alpha', 'demo_beta', 'demo_gamma')]
         == ['link', 'shield', 'block'], '图标取自 manifest.icon')
    # ＋ 的提示挂在 Frame 上（v2.1.0），功能行的 tip 才是 Label。
    # v2.7.0：字标 WBOX 本身也是一个 Label（点开菜单），它排在所有功能行之前。
    feature_tips = [t.text for t in app.tooltips
                    if isinstance(t.widget, tk.Label)]
    step(feature_tips[1:] == ['甲的简介', '乙的简介', '丙的简介'],
         '功能行的悬停简介取自 manifest.tip')
    step(bool(feature_tips) and '菜单' in feature_tips[0],
         '字标自带一条「点开菜单」的提示')
    step(app.vars['demo_alpha'].get() is False and
         app.vars['demo_beta'].get() is False,
         '开关初始值由插件的 gui_state 算出（配置缺键 -> 关）')
    app.open_settings()
    root.update()
    step(len(menu_rows(app)) == sg.menu_row_count(app.entries_all),
         '菜单行数 = menu_row_count(注册表) = %d' % sg.menu_row_count(app.entries_all))
    step(menu_hint(app, '甲功能') == '正常 · 已关闭',
         '菜单行显示「正常 · 已关闭」（v2.9.0 起版本号不进界面）：%r'
         % menu_hint(app, '甲功能'))
    drop(root, app)

    # ---------------- [2] 删掉一个插件 ----------------
    print('[2] 删掉一个插件目录：那一行消失')
    shutil.rmtree(os.path.join(PLUGINS, 'demo_beta'))
    root, app = new_app()
    step([e.id for e in app.entries] == ['demo_alpha', 'demo_gamma'],
         '主页面剩 2 行')
    step('demo_beta' not in app.switches, '被删的 id 不在 switches 里')
    step('乙功能' not in main_texts(app), '被删插件的名字不再出现在主页面')
    app.open_settings()
    root.update()
    step(len(menu_rows(app)) == sg.menu_row_count(app.entries_all),
         '菜单行数跟着注册表缩短')
    drop(root, app)

    # ---------------- [3] 空目录 ----------------
    print('[3] 空 plugins 目录：0 行 + 灰字提示，不抛异常')
    clear_plugins()
    root, app = new_app()
    step(app.entries == [] and app.switches == {}, '0 个插件时没有任何功能行')
    step(any('还没有安装任何插件' in t for t in main_texts(app)),
         '主页面出现「还没有安装任何插件」提示')
    app.open_settings()
    root.update()
    step(all(any(isinstance(w, tk.Label) for w in r.winfo_children())
             for r in menu_rows(app)), '菜单每行都有文字（无空行）')
    step(menu_hint(app, '已安装插件') == '还没有安装插件', '菜单插件分组提示「还没有安装任何插件」')
    drop(root, app)

    # ---------------- [4] 坏插件 ----------------
    print('[4] 坏插件（plugin.py 顶部 raise）：主页面没有，菜单显示「未能加载」')
    clear_plugins()
    make_plugin('demo_bad', '坏功能', icon='dot', order=10, raises=True)
    root, app = new_app()
    step(app.entries == [], '未能加载的插件不占主页面')
    step('demo_bad' not in app.switches, '坏插件没有开关')
    step(len(app.entries_all) == 1 and app.entries_all[0].status == 'load_failed',
         '注册表里仍保留该条（状态 load_failed），不静默丢掉')
    app.open_settings()
    root.update()
    step('未能加载' in menu_hint(app, '坏功能'),
         '菜单里显示「未能加载」：%r' % menu_hint(app, '坏功能'))
    drop(root, app)

    # ---------------- [5] 拨开关 ----------------
    print('[5] 拨开关：写回 config.json 且不抹其它键')
    clear_plugins()
    make_plugin('demo_delta', '丁功能', icon='dot', order=10,
                toggle=['demo_delta.enabled'])
    cfg = dict(BASE_CFG)
    cfg['demo_delta'] = {'enabled': False}
    write_cfg(cfg)
    root, app = new_app()
    step(app.vars['demo_delta'].get() is False, '初始开关为关')
    app.switches['demo_delta'].event_generate('<Button-1>')
    root.update()
    step(app.vars['demo_delta'].get() is True, '点击后开关变开')
    saved = sg.load_cfg()
    step(saved.get('demo_delta') == {'enabled': True},
         'config.json 里 demo_delta.enabled 写成 true')
    step(saved.get('afterburner_path') == BASE_CFG['afterburner_path'],
         '未声明的键（afterburner_path）原样保留')
    step(saved.get('game_processes') == BASE_CFG['game_processes'],
         '未声明的键（game_processes）原样保留')
    drop(root, app)

    # ---------------- [6] 运行期异常 ----------------
    print('[6] plugins_state.json 里的 degraded：菜单显示「运行异常 N 次」')
    make_plugin('demo_alpha', '甲功能', icon='link', order=10)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump({'generated_at': 0, 'guard_pid': 1, 'api': 1,
                   'plugins': [
                       {'id': 'demo_alpha', 'version': '1.0.0',
                        'status': 'degraded', 'error_count': 3},
                       {'id': 'ghost_gone', 'version': '1.0.0',
                        'status': 'degraded', 'error_count': 9},
                   ]}, f, ensure_ascii=False)
    before = open(STATE_PATH, 'rb').read()
    root, app = new_app()
    app.open_settings()
    root.update()
    step(menu_hint(app, '甲功能') == '运行异常 3 次 · 已关闭',
         'hint 变成「运行异常 3 次」：%r' % menu_hint(app, '甲功能'))
    step('ghost_gone' not in [e.id for e in app.entries_all] and
         all('ghost_gone' not in r.hint_label.cget('text')
             for r in menu_rows(app) if r.hint_label),
         '状态文件里有、注册表里没有的 id 不显示（插件已被删）')
    drop(root, app)
    step(open(STATE_PATH, 'rb').read() == before,
         '面板绝不写 plugins_state.json（内容逐字节未变）')

    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        f.write('{ this is not json')
    root, app = new_app()
    app.open_settings()
    root.update()
    step(menu_hint(app, '甲功能') == '正常 · 已关闭',
         '状态文件坏了就忽略，不报错、不弹窗')
    drop(root, app)

    # ---------------- [7] 启用态 / 需管理员 ----------------
    print('[7] 菜单 hint 的启用态与「需管理员」')
    clear_plugins()
    admin_dir = make_plugin('demo_admin', '戊功能', icon='shield', order=10,
                            toggle=['demo_admin.enabled'])
    # 补一个 requires_admin：make_plugin 不写这个字段（老插件的写法）
    mf_path = os.path.join(admin_dir, 'plugin.json')
    with open(mf_path, 'r', encoding='utf-8') as f:
        mf = json.load(f)
    mf['requires_admin'] = True
    with open(mf_path, 'w', encoding='utf-8') as f:
        json.dump(mf, f, ensure_ascii=False, indent=2)
    write_cfg(dict(BASE_CFG, demo_admin={'enabled': True}))
    entries = sg.plugin_entries()
    step(len(entries) == 1 and entries[0].requires_admin is True,
         'manifest.requires_admin 解析进 RegistryEntry')
    step(sg.plugin_hint(entries[0], None, None) == '正常',
         'switch=None 时不追加启用态（调用方没要求就不猜）')
    real_is_admin = sg.is_admin
    try:
        sg.is_admin = lambda: False
        step(sg.plugin_hint(entries[0], None, True)
             == '正常 · 已启用 · 需管理员',
             '未提权：追加「已启用」+「需管理员」')
        sg.is_admin = lambda: True
        step(sg.plugin_hint(entries[0], None, False) == '正常 · 已关闭',
             '已提权：不追加「需管理员」')
    finally:
        sg.is_admin = real_is_admin
    # 面板上的实时值优先：拨开关后菜单立刻跟着变（不必先保存）
    root, app = new_app()
    step(app.vars['demo_admin'].get() is True, '开关初始为开（配置里 enabled=true）')
    app.switches['demo_admin'].event_generate('<Button-1>')
    root.update()
    app.open_settings()
    root.update()
    hint = menu_hint(app, '戊功能')
    step(hint.startswith('正常 · 已关闭'),
         '拨到关之后菜单立刻显示「已关闭」（读面板实时值）：%r' % hint)
    drop(root, app)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('ALL PASS (%d checks)' % len(PASSED))
