# -*- coding: utf-8 -*-
"""安装器 GUI 四态状态机回归（v2.9.0 任务 10 永久化）：不真装、不提权、不动系统。

monkeypatch tk.Tk.mainloop 为脚本驱动，stub setup_abg.install，
经 root.setup_ctl 驱动 idle -> busy -> done / fail，逐项断言设计稿要求。
两个全局桩（tk.Tk.mainloop / setup_abg.install）try/finally 还原，
单进程顺序跑时不带偏后跑的测试。
"""
import os
import sys
import threading
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'installer'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import setup_abg  # noqa: E402

PASSED, FAILED = [], []


def check(cond, msg):
    (PASSED if cond else FAILED).append(msg)
    print(('  ok   ' if cond else '  FAIL ') + msg)


def labels(w):
    """只收当前可见（mapped）子树的 Label 文案——四态帧共存于控件树。"""
    out = []
    for c in w.winfo_children():
        if not mapped(c):
            continue
        if isinstance(c, tk.Label):
            out.append(str(c.cget('text')))
        out.extend(labels(c))
    return out


def mapped(w):
    try:
        return bool(w.winfo_ismapped())
    except tk.TclError:
        return False


CALLS = []
MODE = {'fail': False}
STEP_TEXTS = []
GATE = threading.Event()      # 卡住假安装，让 busy 态可被探针观察


def fake_install(target, task, auto, launch, log, no_task=False, progress=None):
    CALLS.append(dict(target=target, task=task, auto=auto, launch=launch,
                      no_task=no_task))
    for frac, text, detail in (
            (0.05, '正在检查安装环境…', ''),
            (0.15, '正在复制程序文件…', ''),
            (0.50, '正在配置插件…', '已写入 18 个文件'),
            (0.62, '正在保存设置…', ''),
            (0.72, '正在生成快捷方式…', ''),
            (0.82, '正在设置开机自动运行…', ''),
            (0.92, '正在收尾，请稍候…', '')):
        STEP_TEXTS.append(text)
        progress(frac, text, detail)
    GATE.wait(10)
    log.write('fake detail line: config.json 合并完成')
    if MODE['fail']:
        raise RuntimeError('boom：磁盘写入被拒绝')


TARGET = os.path.join(os.environ.get('TEMP', 'C:\\Temp'), 'abg_probe_target')
DPI = 96
try:
    import ctypes
    # 与 gui() 一致：先声明 DPI 感知再读数，否则拿到的是虚拟化后的 96
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()
    DPI = ctypes.windll.user32.GetDpiForSystem()
except Exception:
    pass
S = max(1.0, DPI / 96.0)


def px(v):
    return max(1, int(round(v * S)))


def pump_until(root, want, timeout=10.0):
    ctl = root.setup_ctl
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if ctl['get_state']() == want:
            return True
        time.sleep(0.02)
    return False


def scripted_mainloop(self):
    ctl = self.setup_ctl
    # 单进程顺序跑时新窗口不一定拿到 OS 焦点（前一个测试的窗口刚销毁），
    # focus_force 模拟用户点窗，focus_get 才能反映 apply_state 设的内部焦点。
    self.focus_force()
    self.update()

    # ---- idle ----
    check(ctl['get_state']() == 'idle', '初始状态 idle')
    check(self.winfo_width() == px(480) and self.winfo_height() == px(460),
          '窗口 480×460 逻辑尺寸（实际 %dx%d, dpi=%d）'
          % (self.winfo_width(), self.winfo_height(), DPI))
    txt = labels(self)
    for need in ('WBOX', '安装程序', '安装 WBOX', '安装位置', '开机自动运行',
                 '装完立即启动', '取消', '安装',
                 '按需启用功能，随时可以卸载。装完不需要重启。'):
        check(need in txt, '首屏含文案：%s' % need)
    check('v%s' % setup_abg.VERSION in txt, '标题栏带版本号 v%s' % setup_abg.VERSION)
    check(ctl['auto'].get() is True, '开机自动运行默认开')
    check(ctl['launch'].get() is False, '装完立即启动默认关（设计稿 aria-checked=false）')
    check(ctl['dir'].get() == TARGET, '安装位置预填默认目录')
    # 极简档：36×18 超采样开关（与面板 plain 同几何），不再是矢量椭圆
    for key in ('sw_auto', 'sw_launch'):
        sw = ctl[key]
        check(sw.winfo_width() == px(36) and sw.winfo_height() == px(18),
              '%s 尺寸 36×18（实际 %dx%d）'
              % (key, sw.winfo_width(), sw.winfo_height()))
        check(any(sw.type(i) == 'image' for i in sw.find_all()),
              '%s 用超采样 sprite 绘制（抗锯齿）' % key)
    check(isinstance(self.focus_get(), tk.Entry), '初始焦点在路径输入框')
    self.event_generate('<Tab>')
    self.update()
    check(not isinstance(self.focus_get(), tk.Entry), 'Tab 离开输入框（焦点链生效）')

    # ---- busy：关闭必须被挡住 ----
    MODE['fail'] = False
    GATE.clear()
    ctl['install']()
    self.update()
    check(ctl['get_state']() == 'busy', '点安装进入 busy')
    txt = labels(self)
    check('正在安装' in txt, 'busy 标题「正在安装」')
    check(any('92%' in t for t in txt), 'busy 步骤行带百分比（%r）'
          % ([t for t in txt if '%' in t],))
    check(any(TARGET in t for t in txt), 'busy 底注含安装路径')
    check('安装' not in txt and '取消' not in txt, 'busy 态没有可点的按钮')
    ctl['close']()
    self.update()
    alive = True
    try:
        self.winfo_exists()
    except tk.TclError:
        alive = False
    check(alive and ctl['get_state']() == 'busy', 'busy 中 close 被忽略，窗口仍在')
    GATE.set()
    check(pump_until(self, 'done'), '工作线程 + 80ms 轮询推进到 done')
    check(bool(CALLS) and CALLS[-1]['auto'] is True
          and CALLS[-1]['launch'] is False and CALLS[-1]['no_task'] is True,
          'install() 收到开关值与 no_task：%r' % (CALLS[-1:],))

    # ---- done ----
    txt = labels(self)
    check('安装完成' in txt, 'done 标题「安装完成」')
    check(any('已装到 %s' % TARGET in t for t in txt), 'done 正文含实际安装路径')
    check('打开设置' in txt and '完成' in txt, 'done 按钮：打开设置 / 完成')
    check('安装' not in txt and '取消' not in txt, 'done 态不再显示首屏按钮')

    # ---- fail + 详情 ----
    MODE['fail'] = True
    GATE.clear()
    ctl['install']()
    GATE.set()
    check(pump_until(self, 'fail'), '失败进入 fail 态')
    txt = labels(self)
    check('安装未完成' in txt, 'fail 标题「安装未完成」')
    check(any('安装没能完成，文件没有被改动' in t for t in txt),
          'fail 正文用规范 §2.4 文案')
    check('重试' in txt and '查看详情' in txt and '退出' in txt,
          'fail 按钮：重试 / 查看详情 / 退出')
    ctl['detail']()
    self.update()
    check('收起详情' in labels(self), '详情展开后按钮变「收起详情」')
    boxes = []

    def find_text(w):
        for c in w.winfo_children():
            if isinstance(c, tk.Text):
                boxes.append(c)
            find_text(c)
    find_text(self)
    body = boxes[0].get('1.0', 'end') if boxes else ''
    check('boom' in body and 'fake detail line' in body,
          '详情里能看到原始异常与技术日志（诊断逃生口）')
    ctl['detail']()
    self.update()
    check('查看详情' in labels(self) and not (boxes and mapped(boxes[0])),
          '再点一次收起详情')

    # ---- 重试成功 ----
    MODE['fail'] = False
    GATE.clear()
    ctl['install']()
    GATE.set()
    check(pump_until(self, 'done'), 'fail 态重试可回到 done')

    # ---- 步骤文案红线 ----
    banned = ('config.json', '计划任务', '注册表', 'schtasks', 'payload')
    check(not any(b in t for t in STEP_TEXTS for b in banned),
          '进度步骤文案不含技术禁词（%d 条）' % len(STEP_TEXTS))

    ctl['close']()      # 非 busy 态关闭：destroy 后不能再 update


ORIG_INSTALL = setup_abg.install
ORIG_MAINLOOP = tk.Tk.mainloop
try:
    setup_abg.install = fake_install
    tk.Tk.mainloop = scripted_mainloop
    rc = setup_abg.gui(TARGET, 'WBOX', no_task=True)
    check(rc == 0, 'gui() 正常返回 0')
finally:
    setup_abg.install = ORIG_INSTALL
    tk.Tk.mainloop = ORIG_MAINLOOP

print('\nPASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
for m in FAILED:
    print('  - ' + m)
sys.exit(1 if FAILED else 0)