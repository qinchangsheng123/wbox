# -*- coding: utf-8 -*-
"""面板运行环境常量：运行目录、配置文件/状态文件路径、版本号、窗口尺寸档位。

拆出来的原因（v2.13.0 审查项 ⑨）：settings_gui.py 长期 5000 行，把"路径解析 +
Windows 探测 + 配置读写 + 自绘控件 + 窗口逻辑"堆在一个文件里，改任何一处都要
先在 5000 行里找位置。这个模块只有常量、没有逻辑，谁都能安全 import。

路径解析口径与拆分前一字不差（ABG_BASE 环境变量 -> frozen 可执行文件目录 ->
源码目录依次向上找 config.json）。面板与 --self-check / --layout-check 共用
同一份结果，不各写一套。
"""
import os
import sys

import core_defaults
import plugin_host

# ---- 运行目录与文件路径 ----
if os.environ.get('ABG_BASE'):
    BASE = os.path.abspath(os.environ['ABG_BASE'])
elif getattr(sys, 'frozen', False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    # 源码调试时 src/ 里没有配置，按 src -> 项目根 -> app/ 顺序找真正的运行目录，
    # 这样 --self-check / --layout-check 直接跑源码也能读到完整 config 与图标。
    _up = os.path.dirname(BASE)
    for _cand in (BASE, _up, os.path.join(_up, 'app')):
        if os.path.isfile(os.path.join(_cand, 'config.json')):
            BASE = _cand
            break

CFG = os.path.join(BASE, 'config.json')
LOG = os.path.join(BASE, 'guard.log')
PID_FILE = os.path.join(BASE, 'guard.pid')
STOP_FILE = os.path.join(BASE, 'stop.flag')
SELFCHECK = os.path.join(BASE, 'settings_selfcheck.txt')
LAYOUTCHECK = os.path.join(BASE, 'layout_check.txt')
ICON_LIGHT = os.path.join(BASE, 'icon-light.ico')
ICON_DARK = os.path.join(BASE, 'icon-dark.ico')
TASK = 'WBOX'
VERSION = core_defaults.VERSION   # 单一来源在 core_defaults，此处 re-export 供面板与测试
# 插件目录与 plugins_state.json 的位置必须与守护**完全一致**（两边读同一份注册表、
# 同一份运行期状态），所以解析逻辑抽到 plugin_host.resolve_plugins_dir() 里，
# 这里只调用它，绝不各写一份（两份规则迟早会分叉，症状是"面板能看到插件、守护看不到"）。
PLUGINS_DIR = plugin_host.resolve_plugins_dir(BASE)
STATE_FILE = os.path.join(BASE, 'plugins_state.json')
# 系统命令一律用 SystemRoot\System32 下的绝对路径：面板可能以管理员身份运行，
# 裸名 tasklist / powershell 会按 PATH（含当前目录）解析，存在同目录劫持风险。
SYS32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
PS = os.path.join(SYS32, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
TASKLIST = os.path.join(SYS32, 'tasklist.exe')

# ---- 窗口尺寸档位 ----
# v2.17.0：骨架只留默认一套，基准宽度就是 480（v2.10.0 起极简档的尺寸）。
# 曾经的 BASE_W=640 是玻璃档宽度，随骨架合并一起删掉了。
PLAIN_W = 480                # 基准窗口宽度（逻辑像素）
# 窗口大小三档（v2.12.0）。倍率乘在 DPI 缩放**之上**，px() 一处生效，
# 窗口宽高、字号、行高、图标、浮层全部等比走。小 = 1.00 即历史尺寸，
# 所以老配置不写 ui_size 时观感与升级前完全一致，默认就是「小」。
UI_SIZES = (('small', '小', 1.00), ('medium', '中', 1.15),
            ('large', '大', 1.30))
UI_SIZE_ZOOM = {key: zoom for key, _label, zoom in UI_SIZES}
DEFAULT_UI_SIZE = 'small'
