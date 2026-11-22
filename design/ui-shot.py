# -*- coding: utf-8 -*-
"""启动设置面板并截图（验收用）。

用法：C:/Python38/python.exe design/ui-shot.py out.bmp [light|dark] [skin] [panel] [dot]
  panel: set / inline / plugin（v2.18.0 起 menu 只是 set 的兼容别名）
  dot:   row / badge / off（v2.15.0 插件状态点挂法，不写就用配置里的值）
不写配置文件——明暗、皮肤与状态点挂法只在内存里改，避免污染生产 app\\config.json。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
sys.path.insert(0, os.path.join(ROOT, 'design'))

import tkinter as tk          # noqa: E402
import settings_gui as g      # noqa: E402
import shot                   # noqa: E402
import skins                  # noqa: E402


def main(argv):
    out = argv[1] if len(argv) > 1 else 'D:/06-Temp/ui.bmp'
    appearance = argv[2] if len(argv) > 2 else 'light'
    skin_id = argv[3] if len(argv) > 3 else None
    panel = argv[4] if len(argv) > 4 else ''      # set / inline / plugin

    g.enable_dpi_awareness()
    root = tk.Tk()
    app = g.App(root, probe=True)
    if skin_id and skin_id != app.skin.id:
        app.skin = skins.get(skin_id, base=g.BASE) or app.skin
    app.theme = appearance
    if len(argv) > 5 and argv[5]:
        app.plugin_dot = argv[5]
    app.tok = app.skin.tokens(appearance)
    app.colors = g.legacy_colors(app.tok, appearance)
    app.resize_fonts()
    app.rebuild()

    # 截图必须把窗口压到最前：桌面上还开着别的程序，不置顶会截到别人的窗口
    try:
        root.attributes('-topmost', True)
        root.lift()
        root.focus_force()
    except Exception:
        pass
    for _ in range(8):
        root.update()
        root.update_idletasks()
        time.sleep(0.14)
    # v2.18.0：齿轮浮层菜单没了，`menu` 保留为 `set` 的兼容别名，
    # 老截图命令不会报错（同窗换页，不建 Toplevel）。
    if panel in ('menu', 'set'):
        app.toggle_pane()
        for _ in range(4):
            root.update()
            time.sleep(0.12)
    elif panel == 'inline' and app.entries:
        app.toggle_inline(app.entries[0])
        for _ in range(4):
            root.update()
            time.sleep(0.12)
    elif panel == 'plugin' and app.entries:
        app.open_plugin_settings(app.entries[0])
        try:
            app.plugin_win.attributes('-alpha', 1.0)
        except Exception:
            pass
        for _ in range(4):
            root.update()
            time.sleep(0.12)

    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    print('skin=%s appearance=%s win=%dx%d at %d,%d' % (
        app.skin.id, app.theme, w, h, x, y))
    # 插件设置浮层可能超出窗口（贴行尾箭头展开），截「窗口 ∪ 浮层」的并集。
    # 设置页是同窗换页，不再有第二个 Toplevel。
    box = [x, y, x + w, y + h]
    pop = app.plugin_win if panel == 'plugin' else None
    if pop is not None and pop.winfo_exists():
        box[0] = min(box[0], pop.winfo_rootx())
        box[1] = min(box[1], pop.winfo_rooty())
        box[2] = max(box[2], pop.winfo_rootx() + pop.winfo_width())
        box[3] = max(box[3], pop.winfo_rooty() + pop.winfo_height())
    pad = 6
    shot.grab(out, box[0] - pad, box[1] - pad,
              box[2] - box[0] + pad * 2, box[3] - box[1] + pad * 2)
    print('saved', out)
    root.destroy()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
