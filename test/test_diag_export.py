# -*- coding: utf-8 -*-
"""导出诊断回归（v2.13.0 审查项 8）：齿轮菜单有入口、on_diag 真打出 zip、
包里有日志/事件流/配置/插件状态、成功留一行日志、失败走错误弹框不崩。

临时 ABG_BASE + probe=True，不碰生产配置；os.startfile 打桩防真开资源管理器。
"""
import json
import os
import sys
import tempfile
import tkinter as tk
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

TMP = tempfile.mkdtemp(prefix='abg_diag_test_')
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as f:
    json.dump({'ui_theme': 'light'}, f)
with open(os.path.join(TMP, 'guard.log'), 'w', encoding='utf-8') as f:
    f.write('2026-11-19 20:00:00 测试日志行\n')
with open(os.path.join(TMP, 'events.jsonl'), 'w', encoding='utf-8') as f:
    f.write('{"cat":"plugin.hook","code":"HOOK_SLOW","plugin":"demo"}\n')
with open(os.path.join(TMP, 'plugins_state.json'), 'w', encoding='utf-8') as f:
    json.dump({'plugins': {}}, f)
os.environ['ABG_BASE'] = TMP
for _m in ('settings_gui', 'panel_env', 'win_env', 'panel_state',
           'ui_gfx', 'ui_widgets'):
    sys.modules.pop(_m, None)
import settings_gui as sg  # noqa: E402
import events  # noqa: E402

FAILED = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILED.append(msg)


sg.enable_dpi_awareness()
root = tk.Tk()
root.attributes('-alpha', 0.0)
app = sg.App(root, probe=True)
root.update()

opened = []
_orig_startfile = os.startfile
os.startfile = lambda p: opened.append(p)
try:
    print('[1] 齿轮菜单里有「导出诊断」一行')
    app.open_settings()
    root.update()
    labels = [w.cget('text') for w in sg.walk(app.settings_inner)
              if isinstance(w, tk.Label)]
    check('导出诊断' in labels, '菜单含「导出诊断」')
    app.close_settings()

    print('[2] on_diag 在安装目录打出 zip，内容齐全，成功后打开所在文件夹')
    app.on_diag()
    root.update()
    zips = [n for n in os.listdir(TMP) if n.startswith('wbox-diag-')
            and n.endswith('.zip')]
    check(len(zips) == 1, '生成了一个诊断包：%r' % zips)
    if zips:
        with zipfile.ZipFile(os.path.join(TMP, zips[0])) as z:
            names = z.namelist()
        for want in ('meta.json', 'guard.log', 'events.jsonl',
                     'plugins_state.json', 'config.json'):
            check(want in names, '包里有 %s' % want)
        check(opened == [TMP], '成功后打开的是诊断包所在文件夹：%r' % opened)
    with open(os.path.join(TMP, 'guard.log'), encoding='utf-8') as f:
        check('诊断包已生成' in f.read(), '成功留了一行日志')

    print('[3] bundle 失败（目录不可写）→ 走错误提示，不抛异常')
    _orig_bundle = events.bundle
    events.bundle = lambda *a, **kw: ''
    errors = []
    _orig_error = app._error
    app._error = lambda title, body, can_retry=False: errors.append(title)
    try:
        app.on_diag()
        check(errors == ['导出诊断'], '失败时给出「导出诊断」错误提示')
    finally:
        events.bundle = _orig_bundle
        app._error = _orig_error
finally:
    os.startfile = _orig_startfile
    for job in root.tk.call('after', 'info'):
        try:
            root.after_cancel(str(job))
        except Exception:
            pass
    root.destroy()

import shutil  # noqa: E402
shutil.rmtree(TMP, ignore_errors=True)
print('')
print('ALL PASS' if not FAILED else 'FAILED: %d -> %s' % (len(FAILED), FAILED))
sys.exit(1 if FAILED else 0)