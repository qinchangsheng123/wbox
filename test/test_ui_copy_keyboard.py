# -*- coding: utf-8 -*-
"""文案红线与键盘可达回归（v2.9.0 任务 9 永久化）：动词按钮 / 安全焦点 /
Esc/Enter/Tab / 停止守护二次确认 / 失败人话 / 卸载标题带对象 / 覆盖与坏包 /
主窗口与菜单 Tab 链 / 置灰必配解释 / 皮肤面板键盘。

全局桩（os.startfile、start_guard、plugin_package.*、filedialog）一律
try/finally 还原——单进程顺序跑时这些模块是共享的，不还原会带偏后跑的测试。
"""
import json
import os
import shutil
import sys
import tempfile
import types
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
TMP = tempfile.mkdtemp(prefix='abg_ui_copy_')
for name in ('icon-light.ico', 'icon-dark.ico'):
    src = os.path.join(ROOT, 'app', name)
    if os.path.isfile(src):
        shutil.copy(src, os.path.join(TMP, name))
with open(os.path.join(TMP, 'config.json'), 'w', encoding='utf-8') as f:
    json.dump({'ui_skin': 'liquid-glass', 'ui_appearance': 'light'}, f)
os.environ['ABG_BASE'] = TMP
sys.modules.pop('settings_gui', None)
import settings_gui as sg  # noqa: E402
import skinfix  # noqa: E402

# v2.10.0：液态玻璃不再内置，本测试按 standard 档语义跑（fixture 用户皮肤）。
skinfix.write_glass(TMP)

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def make_plugin(pid, order):
    d = os.path.join(TMP, 'plugins', pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump({'id': pid, 'name': pid, 'version': '1.0.0', 'api': 1,
                   'icon': 'dot', 'order': order, 'home': True,
                   'toggle': [pid + '_enabled'], 'entry': 'plugin.py:Plugin'},
                  f)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('from plugin_api import WBOXPlugin\n'
                'class Plugin(WBOXPlugin):\n    pass\n')


make_plugin('demo_a', 10)
make_plugin('demo_b', 20)

sg.enable_dpi_awareness()
root = tk.Tk()
root.attributes('-alpha', 0.0)
app = sg.App(root, probe=True)
root.update()
# 单进程顺序跑时 OS 焦点不会自动落到新窗口（前一个测试的窗口刚销毁），
# Tk display 焦点为 NULL 会丢弃一切合成键盘事件；focus_force 模拟用户点窗。
root.focus_force()


def labels(win):
    return [w.cget('text') for w in sg.walk(win) if isinstance(w, tk.Label)]


def dlg_buttons():
    out = []
    for cv in (app._dialog_picks or []):
        if cv.winfo_exists():
            out.append((cv.winfo_children()[0].cget('text'), cv))
    return out


def schedule_click(text, delay=0.08):
    def go():
        for t, cv in dlg_buttons():
            if t == text:
                cv.event_generate('<ButtonRelease-1>')
                root.update()
                return
    root.after(int(delay * 1000), go)


def schedule_grab(bucket, delay=0.06):
    def go():
        if app._dialog_win is not None and app._dialog_win.winfo_exists():
            bucket.extend(labels(app._dialog_win))
    root.after(int(delay * 1000), go)


ORIG = dict(start_guard=sg.start_guard, startfile=os.startfile,
            remove_plugin=sg.plugin_package.remove_plugin,
            notify_reload=sg.plugin_package.notify_reload,
            inspect_zip=sg.plugin_package.inspect_zip,
            install_zip=sg.plugin_package.install_zip,
            askopenfilename=sg.filedialog.askopenfilename)

try:
    # ---------------- [1] 自绘弹框：动词按钮 / 安全焦点 / Esc / Enter / Tab ----
    print('[1] 弹框：动词按钮、焦点默认安全键、Esc=取消、Enter 不默认触发危险')
    seen = []

    def grab_focus():
        win = app._dialog_win
        seen.append(('buttons', [t for t, _ in dlg_buttons()]))
        fw = win.focus_get()
        try:
            seen.append(('focus_label', fw.winfo_children()[0].cget('text')))
        except Exception:
            seen.append(('focus_label', None))
    root.after(60, grab_focus)
    root.after(120, lambda: app._dialog_win.event_generate('<Escape>'))
    r = app.confirm('停止守护？', '正文', '停止')
    check(r is False, 'Esc 返回安全值（False）')
    d = dict(seen)
    check(d.get('buttons') == ['停止', '取消'],
          '按钮是具体动词「停止 / 取消」，不是「确定 / 是」：%r' % (seen,))
    check(d.get('focus_label') == '取消',
          '默认焦点落在安全按钮（取消）：%r' % (d.get('focus_label'),))

    root.after(60, lambda: app._dialog_win.focus_get().event_generate('<Return>'))
    r = app.confirm('卸载「甲」？', '正文', '卸载')
    check(r is False, 'Enter 落在默认焦点（取消）上，不触发危险动作')

    def tab_then_enter():
        win = app._dialog_win
        win.event_generate('<Tab>')
        root.update()
        win.focus_get().event_generate('<Return>')
    root.after(80, tab_then_enter)
    r = app.confirm('卸载「甲」？', '正文', '卸载')
    check(r is True, 'Tab 移到「卸载」再 Enter 才触发危险动作')

    # ---------------- [2] 停止守护：L2 确认 + 动词按钮 ----------------
    print('[2] 停止守护：先确认，取消不写 stop.flag')
    stop_path = os.path.join(TMP, 'stop.flag')
    schedule_click('取消')
    app.on_stop()
    check(not os.path.exists(stop_path), '点「取消」不写 stop.flag')
    schedule_click('停止')
    app.on_stop()
    check(os.path.exists(stop_path), '点「停止」才写 stop.flag')
    if os.path.exists(stop_path):
        os.remove(stop_path)

    # ---------------- [3] 启动守护：成功不弹框，失败人话 ----------------
    print('[3] 启动守护：成功不打断，失败给「未能启动」+ 下一步')
    sg.start_guard = lambda: (0, '')
    app.on_start()
    check(app._dialog_win is None, '启动成功不弹框（hint 变运行中）')
    sg.start_guard = lambda: (1, 'boom')
    cap = []
    schedule_grab(cap)
    schedule_click('关闭', 0.12)
    app.on_start()
    check(any(t == '未能启动' for t in cap), '失败标题「未能启动」：%r' % cap)
    check(any('可以重试一次' in t for t in cap), '失败正文给下一步')
    check(not any('boom' in t for t in cap), '原始输出不进弹框正文')

    # ---------------- [4] 打开失败：repr(e) 退出界面 ----------------
    print('[4] 打开日志失败：人话 + 下一步，异常只进日志')

    def deny(p):
        raise OSError('拒绝访问。')
    os.startfile = deny
    cap = []
    schedule_grab(cap)
    schedule_click('关闭', 0.12)
    app.on_log()
    check(any('日志没能打开' in t for t in cap), '正文是人话：%r' % cap)
    check(not any('OSError' in t or '拒绝访问' in t for t in cap),
          'repr(e) / 原始异常不进界面')
    with open(os.path.join(TMP, 'guard.log'), encoding='utf-8') as f:
        logtext = f.read()
    check('打开日志失败' in logtext and 'OSError' in logtext,
          '原始异常记进日志（详情可查看日志）')

    # ---------------- [5] 卸载插件：标题带对象 / 无比内置提示 / 动词 -----------
    print('[5] 卸载确认：标题带对象、不再有"内置"提示、按钮「卸载 / 取消」')
    app.probe = False
    calls = []
    sg.plugin_package.remove_plugin = \
        lambda pid, pdir, base=None: calls.append(pid) or '/backup'
    sg.plugin_package.notify_reload = lambda base: None
    ent = types.SimpleNamespace(id='demo_x', name='示例插件')
    schedule_click('取消')
    app.on_delete_plugin(ent)
    check(calls == [], '取消不卸载')
    ent2 = types.SimpleNamespace(id='ace', name='ACE 防护')
    cap = []
    schedule_grab(cap)
    schedule_click('取消', 0.15)
    app.on_delete_plugin(ent2)
    check(any(t == '卸载「ACE 防护」？' for t in cap),
          '标题带具体对象防误删：%r' % cap)
    # v2.11.3：本体不再附带插件，连官方 id 卸载时也不该说"内置插件、要重装程序"
    # ——插件库随时能重装，那句话现在是假的。
    check(not any('内置插件' in t for t in cap),
          '卸载官方 id 不再谎称"内置插件"：%r' % cap)
    check(any('先备份' in t for t in cap), '正文讲可逆（先备份）')
    schedule_click('卸载', 0.08)
    schedule_click('好', 0.35)
    app.on_delete_plugin(ent)
    check(calls == ['demo_x'], '点「卸载」才真卸载')

    # ---------------- [6] 覆盖安装 / 包不合规 ----------------
    print('[6] 来源确认 -> 覆盖确认「覆盖安装 / 取消」；包不合规「重新选择 / 取消」')
    installed = []
    # v2.11.3：装包前多了一屏「来源确认」（发布者 / 权限 / 完整性），
    # 所以这里要把**两屏**都点掉。桩按 inspect_zip 的真实返回结构给全字段。
    info_stub = {'id': 'demo_x', 'name': '示例插件', 'version': '1.0.0',
                 'members': [('demo_x/plugin.py', 10)], 'total_bytes': 1024,
                 'publisher': '示例作者', 'homepage': '',
                 'known_permissions': ['process.kill'],
                 'unknown_permissions': [], 'permissions_invalid': False,
                 'sha256': 'a' * 64, 'integrity': 'sidecar_missing',
                 'trust': 'third_party'}
    sg.plugin_package.inspect_zip = lambda p: dict(info_stub)
    sg.plugin_package.install_zip = \
        lambda p, pdir, base=None, overwrite=False: (
            installed.append(overwrite) or {'replaced': overwrite})
    sg.filedialog.askopenfilename = lambda **kw: 'D:/fake/p.zip'
    os.makedirs(os.path.join(TMP, 'plugins', 'demo_x'), exist_ok=True)

    seen = []                     # [(按钮元组, 弹框正文), ...] 每屏一条
    recording = [True]

    def record():
        if app._dialog_win is not None and app._dialog_win.winfo_exists():
            b = tuple(t for t, _ in dlg_buttons())
            if not seen or seen[-1][0] != b:
                seen.append((b, labels(app._dialog_win)))
        if recording[0]:
            root.after(40, record)
    record()

    def click_chain(steps, tries=24):
        """按顺序点掉一串链式弹框；按钮还没出现就 50ms 后重试（模态靠 grab）。"""
        def step(i):
            if i >= len(steps):
                return
            text = steps[i]

            def go(left):
                for t, cv in dlg_buttons():
                    if t == text:
                        cv.event_generate('<ButtonRelease-1>')
                        root.update()
                        step(i + 1)
                        return
                if left <= 0:
                    seen.append((('【没找到 %s】' % text,), []))
                    return
                root.after(50, lambda: go(left - 1))
            root.after(80, lambda: go(tries))
        step(0)

    # 卡死保护：链式弹框点不到时立刻失败，别让整套测试挂在这里等超时
    watchdog = root.after(20000, lambda: (
        print('TIMEOUT: 弹框链路未走完 %r'
              % ([t for t, _ in dlg_buttons()],)), os._exit(2)))
    click_chain(['安装', '取消'])
    app.on_add_plugin()
    recording[0] = False
    check(installed == [], '来源确认 / 覆盖确认任一步点取消都不安装')
    check(seen and seen[0][0] == ('安装', '取消'),
          '第一屏是来源确认，按钮「安装 / 取消」：%r'
          % ((seen[0][0] if seen else None),))
    check(any('示例作者' in t for t in (seen[0][1] if seen else [])),
          '来源确认展示发布者（第三方措辞）')
    check(any('结束进程' in t for t in (seen[0][1] if seen else [])),
          '来源确认把权限键翻成人话')
    check(any('完全信任' in t for t in (seen[0][1] if seen else [])),
          '来源确认点明"安装即完全信任"')
    check(len(seen) > 1 and seen[1][0] == ('覆盖安装', '取消'),
          '第二屏是覆盖确认「覆盖安装 / 取消」：%r'
          % ((seen[1][0] if len(seen) > 1 else None),))
    check(any('备份旧版本' in t for t in (seen[1][1] if len(seen) > 1 else [])),
          '覆盖正文说明备份价值')

    click_chain(['安装', '覆盖安装', '好'])
    app.on_add_plugin()
    root.after_cancel(watchdog)
    check(installed == [True], '点「覆盖安装」才覆盖（overwrite=True）')

    n = [0]

    def fd(**kw):
        n[0] += 1
        return 'D:/fake/bad.zip' if n[0] == 1 else ''
    sg.filedialog.askopenfilename = fd

    def bad(p):
        raise ValueError('bad manifest')
    sg.plugin_package.inspect_zip = bad
    cap = []
    schedule_grab(cap)
    schedule_click('重新选择', 0.12)
    app.on_add_plugin()
    check(n[0] == 2, '「重新选择」回到文件框（第二次选取消结束）')
    check(not any('manifest' in t or 'ValueError' in t for t in cap),
          '包不合规不说 manifest / 异常名：%r' % cap)
    app.probe = True

    # ---------------- [7] 键盘：主窗口 Tab 链 + 焦点环 ----------------
    print('[7] 键盘：Tab 链 ＋→字标→开关→行尾，焦点环可见且不被 hover 盖掉')
    chain = getattr(root, '_focus_chain', [])
    sws = list(app.switches.values())
    gears = list(app.plugin_gears.values())
    check(chain[:2] == [app.add_wrap, app.brand_wrap],
          'Tab 链前两枚 = ＋ / 字标')
    check(chain[2:2 + len(sws)] == sws and chain[2 + len(sws):] == gears,
          'Tab 链随后 = 各行开关 → 各行行尾')
    chain[0].focus_set()
    root.update()
    root.event_generate('<Tab>')
    root.update()
    check(root.focus_get() is chain[1], 'Tab 从＋走到字标')
    root.event_generate('<Shift-Tab>')
    root.update()
    check(root.focus_get() is chain[0], 'Shift+Tab 走回＋')
    sw = sws[0]
    sw.focus_set()
    root.update()
    check(bool(sw.find_withtag('focusring')), '开关焦点环画出来了')
    sw.event_generate('<Enter>')
    root.update()
    check(bool(sw.find_withtag('focusring')), 'hover 重绘后焦点环仍在')
    sw.event_generate('<Leave>')
    root.update()
    sw.event_generate('<FocusOut>')
    root.update()
    check(not sw.find_withtag('focusring'), '失焦后焦点环收起')
    before = sw.value
    sw.focus_set()
    sw.event_generate('<Return>')
    root.update()
    check(sw.value != before, 'Enter 拨动焦点所在开关')
    sw.event_generate('<space>')
    root.update()
    check(sw.value == before, 'Space 再拨回来')

    # ---------------- [8] 菜单：首项焦点 / Esc 回字标 / 链 ----------------
    print('[8] 菜单：打开焦点入首项，Esc 收起且焦点回字标')
    app.open_settings()
    root.update()
    mchain = getattr(app.settings_win, '_focus_chain', [])
    check(len(mchain) >= 10, '菜单 Tab 链覆盖全部可操作行（%d）' % len(mchain))
    check(app.settings_win.focus_get() is mchain[0], '菜单打开焦点在首项')
    app.settings_win.event_generate('<Escape>')
    root.update()
    check(app.settings_win is None, 'Esc 收起菜单')
    check(root.focus_get() is app.brand_wrap, 'Esc 后焦点还给字标')

    # ---------------- [9] 置灰必配解释 ----------------
    print('[9] 只读态：状态行两行说明，置灰开关不进 Tab 链')
    app.readonly = True
    app.open_settings()
    root.update()
    texts = labels(app.settings_inner)
    check(any('需要管理员权限' in t for t in texts), '状态行写明为什么灰')
    check(any('以管理员身份重新打开后即可修改' in t for t in texts),
          '第二行给怎么办')
    boot = app.switches.get('boot')
    check(boot is not None and str(boot.cget('takefocus')) in ('0', 'False'),
          '置灰开关不可 Tab 聚焦')
    app.close_settings()
    app.readonly = False
    root.update()

    # ---------------- [10] 皮肤面板键盘 ----------------
    print('[10] 皮肤面板：卡片 / 分段 / 安装入口都可键盘聚焦')
    app.open_skin_settings()
    root.update()
    schain = getattr(app.skin_win, '_focus_chain', [])
    check(len(schain) >= 6, '皮肤面板 Tab 链 = 卡片 + 三明暗 + 安装（%d）'
          % len(schain))
    schain[0].focus_set()
    root.update()
    check(app.skin_win.focus_get() is schain[0], '卡片可聚焦')
    app.skin_win.event_generate('<Escape>')
    root.update()
    check(app.skin_win is None and root.focus_get() is app.brand_wrap,
          'Esc 收皮肤面板且焦点回字标')
finally:
    sg.start_guard = ORIG['start_guard']
    os.startfile = ORIG['startfile']
    sg.plugin_package.remove_plugin = ORIG['remove_plugin']
    sg.plugin_package.notify_reload = ORIG['notify_reload']
    sg.plugin_package.inspect_zip = ORIG['inspect_zip']
    sg.plugin_package.install_zip = ORIG['install_zip']
    sg.filedialog.askopenfilename = ORIG['askopenfilename']
    app.probe = True
    root.destroy()
    shutil.rmtree(TMP, ignore_errors=True)

print('\n%s (%d FAIL)' % ('ALL PASS' if not FAILS else 'FAILED', len(FAILS)))
sys.exit(1 if FAILS else 0)