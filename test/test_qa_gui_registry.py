# -*- coding: utf-8 -*-
"""QA 独立验证 F：面板能否被"加插件"驱动（源码跑，不弹真窗口）。

  [1] 5 个 home 插件（不同 order）-> 主页面 5 行，顺序按 order，菜单行数对得上
  [2] home=false 的插件 -> 主页面不显示，菜单里仍有它
  [3] icon 写不存在值 -> 回落 dot，不抛异常
  [4] toggle=[] 的插件 -> 无开关、不上主页、不报错
  [5] 逐个拨主页 5 个开关 -> 只有该插件的 toggle 路径变，其余键（含 _说明）一字不变

用 probe=True + -alpha 0 建面板：布局/控件是真的，只是不显示。
用法：C:\\Python38\\python.exe test\\test_qa_gui_registry.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_qa_gui_')
PLUGINS = os.path.join(TMP, 'plugins')
os.makedirs(PLUGINS)
CFG_PATH = os.path.join(TMP, 'config.json')

# 配置里放一批"必须原样保留"的键：每拨一次开关都要逐键对比
BASE_CFG = {
    '_说明': '改完保存即生效',
    'ui_theme': 'dark',
    'afterburner_path': r'C:\Program Files (x86)\MSI Afterburner\MSIAfterburner.exe',
    'game_processes': ['game.exe', 'other.exe'],
    'game_roots': ['G:\\game'],
    'exclude_processes': ['steam.exe'],
    'qa_a_enabled': True,
    'qa_b_enabled': True,
    'qa_c': {'enabled': True, 'keep': 5},
    'qa_d_enabled': True,
    'qa_e_enabled': True,
}
with open(CFG_PATH, 'w', encoding='utf-8') as f:
    json.dump(BASE_CFG, f, ensure_ascii=False, indent=2)

os.environ['ABG_BASE'] = TMP
# v2.3.0 复核补丁 R2：BASE 是 import 期固化的，同进程跑第二个 GUI 测试前必须摘掉旧模块
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import plugin_host  # noqa: E402

PASSED = []
FAILED = []


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


def make_plugin(pid, name, order, icon='dot', home=True, toggle=None,
                version='1.0.0', tip=''):
    d = os.path.join(PLUGINS, pid)
    os.makedirs(d, exist_ok=True)
    manifest = {
        'id': pid, 'name': name, 'version': version, 'api': 1,
        'entry': 'plugin.py:Plugin', 'icon': icon, 'order': order,
        'home': home, 'tip': tip,
    }
    if toggle is not None:
        manifest['toggle'] = toggle
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('# -*- coding: utf-8 -*-\n'
                'import plugin_api\n'
                '\n'
                '\n'
                'class Plugin(plugin_api.WBOXPlugin):\n'
                '    DEFAULT_CONFIG = {}\n')
    return d


def new_app():
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True)
    root.update()
    return root, app


def drop(root, app):
    try:
        for job in root.tk.call('after', 'info'):
            try:
                root.after_cancel(str(job))
            except Exception:
                pass
    finally:
        root.destroy()


def row_glyph(app, pid):
    for w in app.switches[pid].master.winfo_children():
        if isinstance(w, sg.Glyph):
            return w.kind
    return None


def row_name(app, pid):
    for w in app.switches[pid].master.winfo_children():
        if isinstance(w, tk.Label):
            return w.cget('text')
    return None


def read_cfg():
    with open(CFG_PATH, 'r', encoding='utf-8-sig') as f:
        return json.load(f)


def main():
    try:
        make_plugin('qa_a', '甲', order=10, icon='link', tip='甲的简介')
        make_plugin('qa_b', '乙', order=20, icon='nope')          # 坏图标
        make_plugin('qa_c', '丙', order=30, icon='shield',
                    toggle=['qa_c.enabled'])                     # 嵌套路径
        make_plugin('qa_d', '丁', order=40, icon='block')
        make_plugin('qa_e', '戊', order=50, icon='play')
        make_plugin('qa_hidden', '隐藏功能', order=15, home=False)  # 只进菜单
        make_plugin('qa_notoggle', '无开关功能', order=25, toggle=[])  # 无开关

        root, app = new_app()

        # ---------------- [1] 5 行、顺序 ----------------
        print('[1] 5 个 home 插件 -> 主页面 5 行，顺序按 order')
        main_ids = [e.id for e in app.entries]
        step(main_ids == ['qa_a', 'qa_b', 'qa_c', 'qa_d', 'qa_e'],
             '主页面行顺序 = (order, id)：%r' % (main_ids,))
        step(sorted(app.switches) == ['qa_a', 'qa_b', 'qa_c', 'qa_d', 'qa_e'],
             '每个主页插件一个开关：%r' % (sorted(app.switches),))
        step([row_name(app, p) for p in main_ids] == ['甲', '乙', '丙', '丁', '戊'],
             '名称全部来自 manifest.name')
        # v2.18.0：菜单行数公式退役。注册表是唯一数据来源，
        # 主页面行数已经验证；这里只核对注册表里 7 个插件一个不少。
        names = [e.name for e in app.entries_all]
        step(all(n in names for n in ('甲', '乙', '丙', '丁', '戊', '隐藏功能',
                                      '无开关功能')),
             '注册表里 7 个插件都有自己一条（含 home=false）：%r' % names)

        # ---------------- [2] home=false 不占主页面 ----------------
        print('[2] home=false -> 主页面不显示，注册表里仍在')
        step('qa_hidden' not in app.switches, 'home=false 的插件没有开关')
        step('隐藏功能' not in [row_name(app, p) for p in app.switches],
             '主页面没有它的行')
        step('隐藏功能' in [e.name for e in app.entries_all],
             '注册表里仍有它（自检能列出，不静默丢）')

        # ---------------- [3] icon 不存在 -> 回落 dot ----------------
        print('[3] icon 写不存在值 -> 回落 dot，不抛异常')
        entry_b = [e for e in app.entries_all if e.id == 'qa_b'][0]
        step(entry_b.icon == 'dot', 'manifest.icon="nope" 被解析成 %r' % entry_b.icon)
        step(row_glyph(app, 'qa_b') == 'dot', '主页那一行画的是 dot 图标')
        step(row_glyph(app, 'qa_a') == 'link' and row_glyph(app, 'qa_c') == 'shield',
             '正常 icon 不受影响（link / shield）')

        # ---------------- [4] toggle=[] -> 无开关 ----------------
        print('[4] toggle=[] -> 主页面不显示开关、不报错')
        step('qa_notoggle' not in app.switches, 'toggle=[] 的插件没有开关')
        entry_n = [e for e in app.entries_all if e.id == 'qa_notoggle'][0]
        step(entry_n.home is False and entry_n.toggle == [],
             'toggle=[] 时 home 被强制为 False（%r / %r）'
             % (entry_n.home, entry_n.toggle))
        step(entry_n.status == plugin_host.STATUS_OK,
             '它仍算加载成功（不被当成坏插件）')

        # ---------------- [5] 逐个拨开关 ----------------
        print('[5] 逐个拨主页 5 个开关：只改自己的 toggle 路径')
        expect_paths = {
            'qa_a': ['qa_a_enabled'],
            'qa_b': ['qa_b_enabled'],
            'qa_c': ['qa_c.enabled'],
            'qa_d': ['qa_d_enabled'],
            'qa_e': ['qa_e_enabled'],
        }
        for pid in ('qa_a', 'qa_b', 'qa_c', 'qa_d', 'qa_e'):
            before = read_cfg()
            app.switches[pid].event_generate('<Button-1>')
            root.update()
            after = read_cfg()
            changed = []

            def flat(d, prefix=''):
                out = {}
                for k, v in d.items():
                    p = prefix + '.' + k if prefix else k
                    if isinstance(v, dict):
                        out.update(flat(v, p))
                    else:
                        out[p] = v
                return out

            fb, fa = flat(before), flat(after)
            for k in set(fb) | set(fa):
                if fb.get(k, '<missing>') != fa.get(k, '<missing>'):
                    changed.append(k)
            step(sorted(changed) == sorted(expect_paths[pid]),
                 '%s 拨一下：改动 = %r（期望 %r）'
                 % (pid, sorted(changed), expect_paths[pid]))
            step(app.vars[pid].get() is False,
                 '%s 拨一下后界面开关状态 = %r（初始 True -> False）'
                 % (pid, app.vars[pid].get()))
            step(after.get('_说明') == BASE_CFG['_说明']
                 and after.get('game_processes') == BASE_CFG['game_processes']
                 and after.get('game_roots') == BASE_CFG['game_roots']
                 and after.get('ui_theme') == BASE_CFG['ui_theme'],
                 '%s 拨一下：_说明 / game_processes / game_roots / ui_theme 未变' % pid)
        step(after.get('qa_c') == {'enabled': False, 'keep': 5},
             'qa_c 的嵌套段只改了 enabled，同段的 keep=5 被保住：%r'
             % (after.get('qa_c'),))

        drop(root, app)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print('\nPASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
    if FAILED:
        for m in FAILED:
            print('  - ' + m)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
