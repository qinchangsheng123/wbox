# -*- coding: utf-8 -*-
"""皮肤面板回归（v2.9.0 任务 7 永久化）：卡片/缩略图/pill/描述、明暗三段落盘、
api 过新需要更新卡、坏包拒绝留痕、超屏滚动、极简设置页入口、高 DPI 不越界。

v2.10.0：液态玻璃不再内置，第二张卡由 skinfix 的用户皮肤 fixture 提供；
内置副标题版本断言随 plain v1.1.0。

临时 ABG_BASE + 临时 skins/ 目录，不碰生产配置；只 patch 实例级 app.note。
"""
import json
import os
import shutil
import sys
import tempfile
import tkinter as tk
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
TMP = tempfile.mkdtemp(prefix='abg_skin_panel_')
for name in ('icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as f:
    json.dump({'ui_skin': 'liquid-glass', 'ui_appearance': 'light'}, f)
os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import skins  # noqa: E402
import skin_package  # noqa: E402
import skinfix  # noqa: E402

# v2.10.0：液态玻璃不再内置；面板卡片语义靠用户皮肤 fixture 补足第二张卡。
skinfix.write_glass(TMP)

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def labels(body):
    return [w for w in sg.walk(body) if isinstance(w, tk.Label)]


def texts(body):
    return [w.cget('text') for w in labels(body)]


def disk():
    with open(os.path.join(TMP, 'config.json'), encoding='utf-8') as f:
        return json.load(f)


def write_skin(sid, api=1, base=None):
    d = os.path.join(base or TMP, 'skins', sid)
    os.makedirs(d, exist_ok=True)
    data = dict(skins.BUILTIN[0])
    data['id'] = sid
    data['api'] = api
    data['name'] = sid[:12]
    with open(os.path.join(d, 'skin.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return d


def main():
    sg.enable_dpi_awareness()
    root = tk.Tk()
    root.attributes('-alpha', 0.0)
    app = sg.App(root, probe=True)
    root.update()
    logs = []
    orig_note = app.note
    app.note = lambda m: (logs.append(m), orig_note(m))[1]
    try:
        print('[1] 玻璃档皮肤面板：卡片 + 缩略图 + pill + 描述')
        app.probe = False
        app.open_skin_settings()
        root.update()
        app.skin_win.attributes('-alpha', 0.0)
        root.update()
        t = texts(app.skin_body)
        check('液态玻璃' in t and '极简' in t, '两张内置皮肤卡都在：%r' % t[:6])
        check('标准' in t and '最省' in t, '性能 pill 标准/最省 都在')
        check(any(x.startswith('内置 · v1.1.0 · ') for x in t),
              '副标题含 来源·版本·描述')
        thumbs = [w for w in sg.walk(app.skin_body)
                  if isinstance(w, tk.Canvas) and w.winfo_reqwidth() == app.px(112)]
        check(len(thumbs) == 2 and all(len(th.find_all()) > 5 for th in thumbs),
              '每卡一枚 112x63 缩略图且真画了图元：%r'
              % [len(th.find_all()) for th in thumbs])
        checks = [w for w in sg.walk(app.skin_body)
                  if isinstance(w, sg.Glyph) and w.kind == 'check']
        check(len(checks) == 1, '当前皮肤一枚勾：%d' % len(checks))
        seg = [x for x in ('跟随系统', '浅色', '深色') if x in t]
        check(seg == ['跟随系统', '浅色', '深色'], '明暗三段都在：%r' % seg)
        check('安装皮肤（.wbskin）…' in t, '安装入口文案对齐设计稿')
        w_geo = app.skin_win.winfo_width()
        check(w_geo == min(app.px(600), sg.work_area()[2] - app.px(20)),
              '面板宽 600 逻辑像素（夹进工作区）：%d' % w_geo)

        print('[2] 明暗分段：跟随系统落盘 system，解析仍 light/dark')
        dark_lbl = [w for w in labels(app.skin_body) if w.cget('text') == '深色'][0]
        dark_lbl.event_generate('<ButtonRelease-1>')
        root.update()
        check(app.theme == 'dark' and app.appearance_pref == 'dark'
              and disk().get('ui_appearance') == 'dark', '点深色：解析+偏好+落盘一致')
        sys_lbl = [w for w in labels(app.skin_body) if w.cget('text') == '跟随系统'][0]
        sys_lbl.event_generate('<ButtonRelease-1>')
        root.update()
        check(app.appearance_pref == 'system'
              and app.theme == skins.detect_appearance()
              and disk().get('ui_appearance') == 'system'
              and disk().get('ui_theme') == app.theme,
              '跟随系统：落盘 system、ui_theme 留解析快照')
        light_lbl = [w for w in labels(app.skin_body) if w.cget('text') == '浅色'][0]
        light_lbl.event_generate('<ButtonRelease-1>')
        root.update()
        check(app.appearance_pref == 'light' and app.theme == 'light', '切回浅色')

        print('[3] api 过新：需要更新卡，点了不生效')
        write_skin('future-glass', api=2)
        app.refresh_skin_list()
        root.update()
        t = texts(app.skin_body)
        check('需要更新' in t and 'future-glass' in t,
              'api=2 的皮肤露出为需要更新卡')
        check(any('api 2' in x and 'api 1' in x for x in t),
              '卡上写明 api 要求与当前支持')
        before = app.skin.id
        fut = [w for w in labels(app.skin_body) if w.cget('text') == 'future-glass'][0]
        fut.event_generate('<Button-1>')
        root.update()
        check(app.skin.id == before, '点需要更新卡不切换皮肤')
        check(any('未启用' in m for m in logs), '拒绝选择留了日志')

        print('[4] 坏包拒绝：skin.reject reason=executable 落日志')
        bad = os.path.join(TMP, 'bad.wbskin')
        with zipfile.ZipFile(bad, 'w') as z:
            z.writestr('skin.json', json.dumps(skins.BUILTIN[0]))
            z.writestr('evil.py', 'print(1)')
        rej = []
        try:
            skin_package.install_zip(bad, TMP, log=lambda m: rej.append(m))
            check(False, '带 .py 的皮肤包必须被拒')
        except skin_package.SkinPackageError as e:
            check(e.reason == 'executable', 'reason=executable：%s' % e.reason)
        check(any(m.startswith('skin.reject reason=executable') for m in rej),
              'skin.reject 日志行：%r' % rej)
        bad2 = os.path.join(TMP, 'bad2.wbskin')
        data = dict(skins.BUILTIN[0])
        data['id'] = 'zipfuture'
        data['api'] = 9
        with zipfile.ZipFile(bad2, 'w') as z:
            z.writestr('skin.json', json.dumps(data))
        rej2 = []
        try:
            skin_package.install_zip(bad2, TMP, log=lambda m: rej2.append(m))
            check(False, 'api 过新的包必须被拒')
        except skin_package.SkinPackageError as e:
            check(e.reason == 'api', 'reason=api：%s' % e.reason)
        check(any('skin.reject reason=api' in m for m in rej2), 'api 拒绝也留痕')

        print('[5] 卡片多到超屏：滚轮可达，不截断')
        for i in range(8):
            write_skin('user-s%d' % i)
        app.refresh_skin_list()
        root.update()
        cv = app.skin_canvas
        region = cv.cget('scrollregion')
        full_h = app.skin_body.winfo_reqheight()
        win_h = app.skin_win.winfo_height()
        check(float(region.split()[3]) >= full_h - 2 and win_h <= app.px(560),
              'scrollregion=%s 内容高=%d 窗高=%d' % (region, full_h, win_h))
        cv.yview_scroll(3, 'units')
        root.update()
        check(cv.yview()[0] > 0, '滚轮真的滚得动：%r' % (cv.yview(),))
        last = [w for w in labels(app.skin_body) if w.cget('text') == 'user-s7'][0]
        check(last is not None, '最后一张卡在控件树里（没被截掉）')

        print('[6] 极简档：设置页有装皮肤入口 + 需要更新行')
        app.close_skin_settings()
        app.set_skin('plain')
        root.update()
        check(app.glass is False, '已切极简')
        app.toggle_pane()
        root.update()
        t = texts(app.set_inner)
        check('从文件安装皮肤…' in t, 'plain 设置页有装第三方皮肤入口')
        check('需要更新' in t and '○ future-glass' in t,
              'plain 设置页也露出需要更新行')
        check('● 极简' in t, '当前皮肤_radio 勾在极简上')
        app.toggle_pane()
        root.update()

        print('[7] 高 DPI 下面板不越界')
        app.set_skin('liquid-glass')
        root.update()
        app.probe = False
        app.open_skin_settings()
        root.update()
        for dpi in (96, 144, 192, 240):
            app.apply_dpi(dpi)
            root.update()
            if app.skin_win is None:
                app.open_skin_settings()
                root.update()
            lx, ly, sw, sh = sg.work_area()
            x, y = app.skin_win.winfo_rootx(), app.skin_win.winfo_rooty()
            w, h = app.skin_win.winfo_width(), app.skin_win.winfo_height()
            check(x >= lx and x + w <= lx + sw and y + h <= ly + sh,
                  'dpi=%d 面板在工作区内 (%d,%d %dx%d)' % (dpi, x, y, w, h))
        app.apply_dpi(96)
        root.update()
    finally:
        app.note = orig_note
        app.close_skin_settings()
        app.close_settings()
        root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)
    print('')
    if FAILS:
        print('FAILED %d：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()