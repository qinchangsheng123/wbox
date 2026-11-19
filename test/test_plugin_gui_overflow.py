# -*- coding: utf-8 -*-
"""齿轮菜单溢出回归：插件多到装不下时，菜单被夹在工作区内 + 滚轮可用。

场景：8 个插件（菜单 13+8=21 行）+ 一块刻意很小的「工作区」，
此时 need_h 一定超过可用高度，place_menu 必须：
  1. 把浮层高度夹在 work_area 之内（不越出屏幕、不压到任务栏）；
  2. scrollregion 高度 = 内容真实高度（内容不能被裁掉）；
  3. 滚轮在 Canvas / 内层 Frame / 每个子控件上都绑上了；
  4. yview_scroll() 真的能滚动（不是只绑了不生效）。

离线：probe=True + -alpha 0，不弹真窗口；work_area 用 monkeypatch 造小屏幕，
因为测试机的分辨率到底多高是不可控的（真实 1080p 下 21 行可能装得下）。

v2.9.0 追加：插件设置浮层的长路径胶囊在 240dpi 不越界、换行撑高后
scrollregion 跟随内容、滚到底卸载入口可见。
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

TMP = tempfile.mkdtemp(prefix='abg_plugin_overflow_')
PLUGINS = os.path.join(TMP, 'plugins')
os.makedirs(PLUGINS)
for _name in ('icon-light.ico', 'icon-dark.ico'):
    _src = os.path.join(ROOT, 'app', _name)
    if os.path.isfile(_src):
        shutil.copy(_src, os.path.join(TMP, _name))
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as _f:
    json.dump({'ui_theme': 'light',
               'many_00_path': 'C:\\Program Files (x86)\\MSI Afterburner\\MSIAfterburner.exe'},
              _f, ensure_ascii=False, indent=2)
os.environ['ABG_BASE'] = TMP

# v2.3.0 复核补丁 R2：BASE 是 import 期固化的，同进程跑第二个 GUI 测试前必须摘掉旧模块
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402

sg.enable_dpi_awareness()

PASSED = []


def step(cond, msg):
    if not cond:
        raise AssertionError(msg)
    PASSED.append(msg)
    print('  [ok] ' + msg)


def make_plugin(pid, name, order, settings=None):
    plugin_dir = os.path.join(PLUGINS, pid)
    os.makedirs(plugin_dir)
    manifest = {
        'id': pid, 'name': name, 'version': '1.0.0', 'api': 1,
        'icon': 'dot', 'order': order, 'home': True,
        'toggle': [pid + '_enabled'], 'entry': 'plugin.py:Plugin', 'tip': name,
    }
    if settings:
        manifest['settings'] = settings
    with open(os.path.join(plugin_dir, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with open(os.path.join(plugin_dir, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('# -*- coding: utf-8 -*-\n'
                'import plugin_api\n'
                '\n'
                '\n'
                'class Plugin(plugin_api.WBOXPlugin):\n'
                "    DEFAULT_CONFIG = {'%s_enabled': False}\n" % pid)


for _i in range(8):
    make_plugin('many_%02d' % _i, '插件 %02d' % _i, 10 + _i,
                settings=[{'key': 'many_00_path', 'label': '主程序路径',
                           'type': 'path', 'hint': '留空则自动查找已装好的程序'}]
                if _i == 0 else None)

root = tk.Tk()
root.attributes('-alpha', 0.0)
app = sg.App(root, probe=True)
root.update()

try:
    entries = app.entries_all
    rows_expected = sg.menu_row_count(entries)
    step(len(entries) == 8, '8 个插件全部进入注册表')
    # v2.3.0 复核补丁（QA P2-2）：这里原来断言 menu_row_count(...) ==
    # CORE_MENU_ROWS+1+len+1，正好是 menu_row_count 的实现本身，恒真。
    # 改成写死"此刻应有的行数"：13 内核行 + 插件标题 1 + 8 插件 + 打开目录 1 = 23。
    # v2.7.0：内核行 12 -> 11，「浅色主题 / 深色主题」两行合成「外观与皮肤…」一行。
    # v2.9.0：内核行 11 -> 13，补「启动后收进托盘 / 游戏结束提示」两行。
    # v2.11.3：插件分组多了「浏览插件库」，固定行 1 -> 2，总数 23 -> 24。
    # v2.13.0：内核行 13 -> 14（维护区新增「导出诊断」），总数 24 -> 25。
    # 下面的 step 会再用真实 Tk 控件数（len(rows)）回怼这个 23，
    # 所以"公式被改"和"菜单少画一行"两种情况都不会同时蒙过去。
    EXPECT_ROWS = 14 + 1 + 8 + 2
    step(rows_expected == EXPECT_ROWS,
         'menu_row_count 算出 %d 行（14 内核 + 标题 + 8 插件 + 打开目录）'
         % rows_expected)

    # 造一块小屏幕：菜单必然装不下（真实分辨率不可控，这里显式指定）
    AREA = (0, 0, 1200, 560)
    sg.work_area = lambda: AREA

    for dpi in (96, 144):
        print('[dpi=%d] 菜单溢出与滚动' % dpi)
        app.close_settings()
        app.apply_dpi(dpi)
        root.update()
        app.open_settings()
        root.update()

        win, cv, inner = app.settings_win, app.settings_canvas, app.settings_inner
        need_h = inner.winfo_reqheight()
        mw, mh = win.winfo_width(), win.winfo_height()
        rows = [w for w in sg.walk(inner)
                if isinstance(w, tk.Frame) and hasattr(w, 'hint_label')]
        step(len(rows) == EXPECT_ROWS,
             '真实渲染 %d 行 == 写死的 %d 行' % (len(rows), EXPECT_ROWS))
        step(need_h + 2 > mh,
             '内容高 %d px 超过窗口高 %d px（确实溢出了）' % (need_h, mh))
        step(mh <= AREA[3] - app.px(8),
             '浮层高度被夹进工作区：%d <= %d' % (mh, AREA[3] - app.px(8)))
        step(mh >= app.px(160),
             '夹取后仍有可用高度：%d >= %d' % (mh, app.px(160)))
        mx, my = win.winfo_rootx(), win.winfo_rooty()
        step(mx >= AREA[0] - 2 and mx + mw <= AREA[0] + AREA[2] + 2,
             '横向落在工作区内：x=%d w=%d' % (mx, mw))
        step(my >= AREA[1] - 2 and my + mh <= AREA[1] + AREA[3] + 2,
             '纵向落在工作区内：y=%d h=%d' % (my, mh))

        region = cv.cget('scrollregion').split()
        step(len(region) == 4 and abs(float(region[3]) - need_h) <= 1,
             'scrollregion 高度 = 内容高度（%s ≈ %d）' % (region[3], need_h))

        widgets = list(sg.walk(inner))
        bound = [w for w in [cv, inner] + widgets
                 if isinstance(w, tk.Misc) and w.bind('<MouseWheel>')]
        step(len(bound) >= len(widgets),
             '滚轮绑到了 Canvas + 内层 Frame + 全部子控件（%d 个）' % len(bound))

        before = cv.yview()
        cv.yview_scroll(3, 'units')
        root.update()
        after = cv.yview()
        step(after[0] > before[0], 'yview_scroll 真的能滚动：%.3f -> %.3f'
             % (before[0], after[0]))
        cv.yview_moveto(0)
        root.update()

    # 回到真实工作区：屏幕够高时高度贴合内容，但 560 逻辑像素是硬上限
    # （设计稿 .menu{max-height:560px}：超了让菜单自己滚，不把桌面顶满）
    sg.work_area = lambda: (0, 0, 1920, 4000)
    app.close_settings()
    app.apply_dpi(96)
    root.update()
    app.open_settings()
    root.update()
    win, inner = app.settings_win, app.settings_inner
    need_h = inner.winfo_reqheight()
    want = min(need_h + 2, app.px(560))
    step(abs(win.winfo_height() - want) <= 2,
         '装得下时贴合内容且不超过 560 上限：win=%d want=%d need=%d'
         % (win.winfo_height(), want, need_h + 2))

    # v2.9.0：长路径胶囊在 240dpi 不得把「选择」顶出胶囊（pack 空腔顺序坑），
    # 换行撑高的内容必须进 scrollregion（Configure 晚于 place 的坑），滚到底
    # 卸载入口可见。小工作区保证浮层真的被夹、滚动真的有意义。
    print('[dpi=240] 插件浮层：长路径胶囊不越界 + 滚到底可达')
    sg.work_area = lambda: AREA
    app.close_settings()
    app.apply_dpi(240)
    root.update()
    e = [x for x in app.entries if x.id == 'many_00'][0]
    app.open_plugin_settings(e)
    root.update_idletasks()
    root.update()
    body, pcv = app.plugin_body, app.plugin_canvas
    rx, ry = body.winfo_rootx(), body.winfo_rooty()
    bw, bh = body.winfo_width(), body.winfo_height()
    bad = [c for c in sg.walk(body)
           if c.winfo_width() > 1 and c.winfo_height() > 1
           and (c.winfo_rootx() - rx + c.winfo_width() > bw + 2
                or c.winfo_rooty() - ry + c.winfo_height() > bh + 2)]
    step(not bad, '240dpi 长路径行无子控件越界（%d 个越界）' % len(bad))
    region = [float(v) for v in pcv.cget('scrollregion').split()]
    step(abs(region[3] - body.winfo_reqheight()) <= 2,
         '换行后 scrollregion 仍等于内容全高（%s ≈ %d）'
         % (region[3], body.winfo_reqheight()))
    pcv.yview_moveto(1.0)
    root.update_idletasks()
    root.update()
    vy, vh = pcv.winfo_rooty(), pcv.winfo_height()
    foot = [w for w in sg.walk(body) if w.winfo_class() == 'Label'
            and '卸载' in str(w.cget('text'))]
    step(bool(foot) and all(
        0 <= w.winfo_rooty() - vy
        and w.winfo_rooty() - vy + w.winfo_height() <= vh + 2 for w in foot),
        '滚到底后卸载入口在视口内（%d 枚）' % len(foot))
    app.close_plugin_settings()
finally:
    try:
        app.close_settings()
        for job in root.tk.call('after', 'info'):
            try:
                root.after_cancel(str(job))
            except Exception:
                pass
    finally:
        root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)

print('ALL PASS (%d checks)' % len(PASSED))
