# -*- coding: utf-8 -*-
"""离线验证面板动作：行尾箭头（每插件设置弹框）、＋、垃圾桶、装卸重建。

probe=True + -alpha 0 建真实控件但不显示；插件全在临时目录，不碰 app\\。
v2.4.0：齿轮语义改为「头部 1 个 + 插件行（仅声明 settings 的插件）」，并补
每插件设置弹框的全链路测试（点开 / 四类控件 / 落盘 / 重建销毁）。
"""
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_actions_')
PLUGINS = os.path.join(TMP, 'plugins')
os.makedirs(PLUGINS)
for name in ('icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as f:
    json.dump({'ui_theme': 'light', 'ui_skin': 'liquid-glass'}, f)

os.environ['ABG_BASE'] = TMP
# v2.3.0 复核补丁 R2：BASE 是 import 期固化的，同进程跑第二个 GUI 测试前必须摘掉旧模块
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import plugin_package      # noqa: E402
import skinfix             # noqa: E402

# v2.17.0：骨架只剩默认一套——行尾是「›」箭头（点整行行内展开），不再有齿轮
# 滑块；插件设置浮层（on_plugin_settings）仍在，由本测试与 --layout-check 覆盖。
# fixture 提供的老包（声明 standard）用来验证降级渲染不报错。
skinfix.write_glass(TMP)

sg.enable_dpi_awareness()
FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


# demo_a 声明全部四类设置控件（bool / int / choice / path）；
# demo_b 不声明 settings —— 它的浮层里只有「卸载此插件」。
SETTINGS_A = [
    {'key': 'demo_a.enabled', 'type': 'bool', 'label': '启用示例',
     'hint': '开关型：拨动立即写盘'},
    {'key': 'demo_a.count', 'type': 'int', 'label': '等待时间',
     'min': 0, 'max': 100, 'step': 5, 'unit': '秒', 'hint': '数值型：步进 5'},
    {'key': 'demo_a.mode', 'type': 'choice', 'label': '显示位置',
     'options': [{'value': 'tr', 'label': '右上'},
                 {'value': 'bl', 'label': '左下'}]},
    {'key': 'demo_a.exe_path', 'type': 'path', 'label': '程序路径'},
]


def make_plugin(pid, name, order, settings=None):
    d = os.path.join(PLUGINS, pid)
    os.makedirs(d, exist_ok=True)
    manifest = {'id': pid, 'name': name, 'version': '1.0.0', 'api': 1,
                'icon': 'dot', 'order': order, 'home': True,
                'toggle': [pid + '_enabled'], 'entry': 'plugin.py:Plugin',
                'tip': name + ' 简介'}
    if settings:
        manifest['settings'] = settings
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('from plugin_api import WBOXPlugin\nclass Plugin(WBOXPlugin):\n    pass\n')


def glyphs(app, kind):
    return [w for w in sg.walk(app.main)
            if isinstance(w, sg.Glyph) and w.kind == kind]


def entry_by_id(app, pid):
    for e in app.entries_all:
        if e.id == pid:
            return e
    return None


def labels_of(body):
    return [w for w in sg.walk(body) if isinstance(w, tk.Label)]


def read_disk_cfg():
    with open(os.path.join(TMP, 'config.json'), 'r', encoding='utf-8') as f:
        return json.load(f)


def disk_get(key, default=None):
    """点路径读盘：设置项的 key 是点路径，落盘就是嵌套结构。"""
    return sg.cfg_get_path(read_disk_cfg(), key, default)


def click(widget, root):
    """按真实绑定触发一次左键（控件未映射也能送达）。"""
    widget.event_generate('<Button-1>')
    root.update()


def main():
    make_plugin('demo_a', '甲功能', 10, settings=SETTINGS_A)
    make_plugin('demo_b', '乙功能', 20)
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True)
    root.update()
    try:
        print('[1] 头部「＋」与行尾箭头')
        check(len(glyphs(app, 'plus')) == 1 and app.add_btn.kind == 'plus',
              '标题栏有一个 ＋ 按钮')
        # v2.17.0：行尾是「›」箭头（行内展开），齿轮滑块随玻璃骨架退役。
        check(len(glyphs(app, 'cfg')) == 0,
              '行尾不再画齿轮滑块：%d 枚' % len(glyphs(app, 'cfg')))
        check(len(glyphs(app, 'chev_r')) == len(app.entries),
              '每行一枚行尾箭头 = %d 行 / %d 枚'
              % (len(app.entries), len(glyphs(app, 'chev_r'))))
        check(sorted(app.plugin_gears) == ['demo_a', 'demo_b'],
              '每一行都有设置入口（含无可调项的）：%r' % sorted(app.plugin_gears))
        check(app.brand_wrap is not None and app.brand_wrap.winfo_exists()
              and isinstance(app.brand_btn, tk.Label)
              and app.brand_btn.cget('text') == 'WBOX',
              '字标 WBOX 仍在（身份标识，不绑点击）')
        check(app.add_wrap.winfo_manager() == 'pack', '＋ 用 pack 排布在同个 top 容器')
        check(len(glyphs(app, 'plus')[0].find_all()) > 0,
              '＋ 图标真的画出了图元（不是空白 Canvas）')

        print('[2] 行尾不再挂垃圾桶')
        check(len(glyphs(app, 'trash')) == 0,
              '2 个插件 -> 0 个垃圾桶（卸载沉到设置浮层底部）：%d'
              % len(glyphs(app, 'trash')))
        check(sorted(app.switches) == ['demo_a', 'demo_b'],
              '功能开关仍按插件 id 一个一个建，没被新按钮顶掉')
        check(app.plugins_ro is False, '临时目录可写 -> 装卸按钮可用')

        print('[3] 设置页固定内容：守护状态 + 关于组三件工具')
        # v2.18.0：齿轮菜单整块退役，原来这段断言 menu_row_count 等于
        # 「14 内核 + 标题 + 插件数 + 2」——那个公式只描述浮层行数，
        # 现在没有浮层了。改成数设置页真实控件：工具行少了任何一条都会红。
        app.toggle_pane()
        root.update()
        texts = [w.cget('text') for w in sg.walk(app.set_inner)
                 if isinstance(w, tk.Label)]
        check(all(n in texts for n in ('守护状态', '打开日志目录',
                                       '编辑配置文件', '导出诊断')),
              '设置页含守护状态与三个工具入口：%r'
              % [t for t in texts if t])
        app.toggle_pane()
        root.update()

        print('[4] reload_registry：装/卸插件后主页面跟着变')
        make_plugin('demo_c', '丙功能', 30)
        app.reload_registry()
        root.update()
        check([e.id for e in app.entries] == ['demo_a', 'demo_b', 'demo_c'],
              '新插件出现在注册表：%r' % [e.id for e in app.entries])
        check(len(glyphs(app, 'chev_r')) == 3 and sorted(app.switches) ==
              ['demo_a', 'demo_b', 'demo_c'], '3 行、3 箭头、3 开关')
        check(sorted(app.plugin_gears) == ['demo_a', 'demo_b', 'demo_c'],
              '装卸重建后每行仍有设置入口：%r' % sorted(app.plugin_gears))
        shutil.rmtree(os.path.join(PLUGINS, 'demo_b'))
        app.reload_registry()
        root.update()
        check([e.id for e in app.entries] == ['demo_a', 'demo_c'] and
              len(glyphs(app, 'chev_r')) == 2,
              '删掉目录后重建：2 行、2 箭头，不留幽灵开关')
        check(sorted(app.plugin_gears) == ['demo_a', 'demo_c'],
              '删插件后不留幽灵设置入口：%r' % sorted(app.plugin_gears))

        # ---- 以下要真点弹框，临时摘掉 probe 保护；messagebox 一律捕获防阻塞 ----
        print('[5] 插件设置浮层：点开 / 无可调项提示 / 四类控件落盘')
        app.probe = False
        shown = []
        orig_info = sg.messagebox.showinfo
        orig_fd = sg.filedialog.askopenfilename
        sg.messagebox.showinfo = lambda *a, **k: shown.append(a)
        try:
            # v2.7.0：无 settings 的插件也开浮层，里面只有「卸载此插件」
            app.on_plugin_settings(entry_by_id(app, 'demo_c'))
            check(app.plugin_win is not None and len(shown) == 0,
                  '无可调项的插件开浮层（卸载在里面），不弹提示')
            if app.plugin_win is not None:
                try:
                    app.plugin_win.attributes('-alpha', 0.0)
                except Exception:
                    pass
                root.update()
                c_texts = [w.cget('text') for w in labels_of(app.plugin_body)]
                check('卸载此插件' in c_texts,
                      '无语义设置的浮层里仍有「卸载此插件」：%r' % c_texts)
                app.close_plugin_settings()
                root.update()
            app.on_plugin_settings(entry_by_id(app, 'demo_a'))
            win = app.plugin_win
            check(win is not None and win.winfo_exists()
                  and app.plugin_entry.id == 'demo_a',
                  '点有可调项插件的行尾箭头：弹出对应设置浮层')
            if win is not None:
                try:
                    win.attributes('-alpha', 0.0)
                except Exception:
                    pass
            root.update()
            body_texts = [w.cget('text') for w in labels_of(app.plugin_body)]
            check(all(t in body_texts for t in
                      ('启用示例', '等待时间', '显示位置'))
                  and '程序路径' not in body_texts
                  and '全部 4 项设置…' in body_texts,
                  '默认前 3 项 + 「全部 4 项设置…」（rp-more）：%r' % body_texts)
            more_w = [w for w in labels_of(app.plugin_body)
                      if w.cget('text') == '全部 4 项设置…'][0]
            click(more_w, root)
            root.update()
            body_texts = [w.cget('text') for w in labels_of(app.plugin_body)]
            check(all(t in body_texts for t in
                      ('启用示例', '等待时间', '显示位置', '程序路径')),
                  '点 rp-more 后四类设置的标题都渲染出来了：%r' % body_texts)

            # bool：开关拨动 → 落盘
            sw = app._setting_switches.get('demo_a.enabled')
            check(sw is not None and sw.value is False,
                  'bool 控件按三段合并后的值初始化（缺省关）')
            sw.toggle()
            root.update()
            check(disk_get('demo_a.enabled') is True,
                  '开关拨动立即落盘 config.json（demo_a.enabled=true）')

            # int：＋/− 步进、上下限夹紧 → 落盘
            plus_w = [w for w in labels_of(app.plugin_body)
                      if w.cget('text') == '+'][0]
            minus_w = [w for w in labels_of(app.plugin_body)
                       if w.cget('text') == '−'][0]
            click(plus_w, root)
            check(disk_get('demo_a.count') == 5,
                  '数值 ＋ 一步 = min+step 落盘（0→5）')
            click(minus_w, root)
            click(minus_w, root)
            check(disk_get('demo_a.count') == 0,
                  '数值 − 两次夹在下限：第二次不越界也不重复写坏')

            # choice：点选项 → 落盘
            bl = [w for w in labels_of(app.plugin_body)
                  if w.cget('text') == '左下'][0]
            click(bl, root)
            check(disk_get('demo_a.mode') == 'bl',
                  '点「左下」选项落盘（demo_a.mode=bl）')

            # path：文件对话框打桩 → 落盘
            sg.filedialog.askopenfilename = lambda **kw: 'D:/fake/tool.exe'
            pick = [w for w in labels_of(app.plugin_body)
                    if w.cget('text') == '选择'][0]
            click(pick, root)
            check(disk_get('demo_a.exe_path') == 'D:\\fake\\tool.exe',
                  '路径选择落盘且统一成反斜杠')

            # 单项写盘口径：其它叶子键没被这轮操作抹掉
            disk = read_disk_cfg()
            check(disk.get('ui_theme') == 'light' and
                  disk_get('demo_a.enabled') is True and
                  disk_get('demo_a.count') == 0 and
                  disk_get('demo_a.mode') == 'bl',
                  '四轮操作只动各自叶子键，config.json 其余键原样保留')

            print('[6] 弹框随面板重建 / DPI 切换正确销毁，不留残骸')
            app.rebuild()
            root.update()
            check(app.plugin_win is None and not win.winfo_exists(),
                  '切主题/重建时设置弹框被销毁（winfo_exists=False）')
            toplevels = [w for w in root.winfo_children()
                         if isinstance(w, tk.Toplevel)]
            check(not toplevels, '重建后无残留 Toplevel：%d' % len(toplevels))
            app.on_plugin_settings(entry_by_id(app, 'demo_a'))
            win2 = app.plugin_win
            check(win2 is not None and win2.winfo_exists(),
                  '重建后行尾箭头仍能再次打开弹框')
            if win2 is not None:
                try:
                    win2.attributes('-alpha', 0.0)
                except Exception:
                    pass
            old_dpi = app.dpi
            app.apply_dpi(old_dpi + 48)
            root.update()
            check(app.plugin_win is None and not win2.winfo_exists(),
                  'DPI 切换触发重建：弹框销毁、不叠两层')
            app.apply_dpi(old_dpi)
            root.update()
        finally:
            sg.messagebox.showinfo = orig_info
            sg.filedialog.askopenfilename = orig_fd
            app.probe = True
            app.close_plugin_settings()

        print('[7] probe 模式下点按钮不会弹框 / 不会写盘')
        app.on_add_plugin()
        if app.entries:
            app.on_delete_plugin(app.entries[0])
        check(True, '＋ / 垃圾桶回调在 probe 下安全返回')

        print('[8] 自检输出带插件包格式与按钮状态')
        sg.self_check()
        with open(os.path.join(TMP, 'settings_selfcheck.txt'), encoding='utf-8') as f:
            text = f.read()
        check('plugin_package=' + plugin_package.FORMAT_ID in text,
              'self_check 记录了插件包格式')
        check('plugin_actions=add:plus,row:chev_r,inline_open' in text,
              'self_check 记录了 ＋ / 行尾箭头 / 行内展开')
        check('menu_entry=label:设置' in text and 'skin=' in text,
              'self_check 记录了设置入口与当前皮肤')
    finally:
        app.close_plugin_settings()
        root.destroy()

    shutil.rmtree(TMP, ignore_errors=True)
    print('')
    if FAILS:
        print('FAILED %d 项：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
