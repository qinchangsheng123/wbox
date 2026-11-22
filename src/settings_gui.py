# -*- coding: utf-8 -*-
"""WBOX 设置面板（默认骨架 + 第三方皮肤，高 DPI 自适应）。

界面约定（2026-11-09 定版，2026-11-12 起改为插件驱动，v2.18.0 收成一套骨架）：
  1. 列表页**只**保留「名称 + 开关」的功能行，行数由插件注册表决定：
     有几个 home=true 的插件就有几行，删掉插件目录那一行就消失。
     无编号、无详情小字、无状态行。
  2. 鼠标悬停在名称上显示该功能简介（ToolTip，文案来自 manifest.tip）。
  3. 右上角「设置」二字是**唯一**的二级入口，点它同窗换页（不弹浮层）。
     守护启停、开机自启、收托盘、游戏结束提示、重扫、日志、诊断、配置、
     外观与皮肤全在那一页。行尾「›」箭头是第三级：行内展开该插件的设置。
     （v2.18.0 删掉了「点字标弹浏览器式菜单」那一套，约 900 行，生产不可达。）
  4. 三个开关切换即时写回 config.json，守护 2 秒内热加载，不用点保存。
  5. 外观走皮肤层（src/skins.py）：`ui_skin` 选皮肤、`ui_appearance` 选明暗；
     老配置的 `ui_theme` 仍能读，按它推默认皮肤与明暗，升级不变脸。
     皮肤只能换颜色与尺寸，骨架只有这一套。

关键：
  - 创建任何 Tk 窗口前启用 Per-Monitor DPI Awareness V2，否则 Windows 会把
    整个窗口当位图拉伸（150% 缩放下文字发虚）。
  - 字体用「像素尺寸 + tk scaling = DPI/72」，DPI 变化时显式重设。
  - 控件全部用 tk 原生控件 + Canvas 自绘（开关、图标），不依赖 ttk 主题，
    这样纯黑主题下不会出现系统绘制的白底方块。
"""
import ctypes
import json
import math
import os
import queue
import shutil
import subprocess
import sys
import time
import tkinter as tk
import core_defaults
import guard_beat
import plugin_api
import plugin_host
import plugin_package
import skin_package
import skins
import tray_icon
from ctypes import wintypes
from tkinter import filedialog
from tkinter import font as tkfont
# 面板自带自绘确认框，messagebox 仅作为界面测试的打桩口保留。
from tkinter import messagebox  # noqa: F401
from tkinter import messagebox

# ---------------- 拆分后的模块边界（v2.13.0 审查项 ⑨） ----------------
# 面板原来是一个 5000 行文件：配色/绘图/控件/Windows 探测/配置读写/窗口逻辑
# 全在里面。现在按"谁会被别人复用"切开，本文件只留窗口与交互：
#   panel_env    运行目录与文件路径常量
#   win_env      DPI / 工作区 / 权限 / 窗口图标 / powershell
#   panel_state  配置读写 + 插件注册表 + 守护状态（视图模型）
#   ui_gfx       配色映射 + Canvas 绘图原语（圆角、混色、缓动）
#   ui_widgets   Switch / Glyph / ToolTip 三个自绘控件
# 名字在这里原样再导一遍：App 的调用点一个不改，测试里的 settings_gui.xxx 照旧。
from panel_env import (BASE, CFG, DEFAULT_UI_SIZE, ICON_DARK, ICON_LIGHT,
                       LAYOUTCHECK, LOG, PLAIN_W, PLUGINS_DIR, SELFCHECK,
                       STATE_FILE, STOP_FILE, SYS32, TASK, TASKLIST, UI_SIZES,
                       UI_SIZE_ZOOM, VERSION)
from ui_gfx import (LEGACY_KEYS, THEMES, _cb, _compat_themes,
                    ease, legacy_colors, mix_hex, round_rect)
from ui_widgets import Dot, Glyph, Switch, ToolTip
from win_env import (POINT, RECT, can_write_config, dir_writable,
                     elevate_settings, enable_dpi_awareness as _dpi_awareness,
                     file_writable, get_window_icons, is_admin,
                     panel_single_instance, ps_run,
                     set_window_icons, system_dpi, window_dpi, work_area,
                     win_hwnd)
from panel_state import (SESSION_TEXT, PLUGIN_DOT_MODES, autostart_state,
                         cfg_get_path,
                         cfg_set_path, effective_cfg,
                         guard_diagnostics,  # noqa: F401  测试按老名字取用
                         guard_running, guard_snapshot,
                         guard_state, load_cfg, load_config_raw, pick_family,
                         plugin_dot_from_cfg, plugin_entries,
                         plugin_switch_state,
                         plugin_switch_write, save_cfg, set_autostart,
                         # v2.18.0：面板不再自己显示这行状态文字（齿轮菜单退役），
                         # 保留导入是因为 test_guard_beat 按 settings_gui 的老名字取用。
                         short_guard_state, start_guard, ui_size_from_cfg,
                         walk, with_defaults)

# DPI 感知模式是**进程级**状态，真正设置在 win_env；这里保留一份镜像，
# 因为界面按它决定 1px 线怎么取整。enable_dpi_awareness() 负责同步两边。
DPI_MODE = 'unknown'
READONLY = False             # 配置目录不可写且无法提权时为 True：功能项全部置灰


def enable_dpi_awareness():
    """必须在创建 Tk 窗口之前调用。返回实际生效的模式。"""
    global DPI_MODE
    DPI_MODE = _dpi_awareness()
    return DPI_MODE


# 主页面与设置页的**唯一**数据来源是插件注册表（plugins\*\plugin.json）。
# v1 的 FEATURES 常量（写死三个功能的名字/图标/简介）已删除：删掉一个插件目录，
# 面板就该少一行、不报错、不留空行，而不是继续显示一个点不动的幽灵功能。
def plugin_dot_state(entry, state=None, switch=None):
    """插件行状态点（v2.15.0）：返回 'ok' / 'off' / 'bad'，None = 不画。

    这是面板上唯一表达「这个插件能不能用」的地方（v2.18.0 删掉齿轮菜单后，
    原来那行「正常 · 已启用」的文字提示随之退役）。
    点的意义不是把开关再画一遍——开关是意愿，点是事实：
      - 加载期不是 ok：None。这类插件根本不进主页面（entries 只收 STATUS_OK），
        真被别的入口叫到也不该冒一个它自己都代表不了的色。
      - 运行期 degraded / error_count>0：'bad'。开关明明开着但守护在报错，
        这是两处唯一会不一致的时刻，也是这枚点真正值钱的地方。
      - 开关闭：'off'。放在 bad 之后判——一个关着但上次跑崩过的插件，
        打开后大概率还崩，用户先要知道这件事。
    """
    if entry.status != plugin_host.STATUS_OK:
        return None
    if isinstance(state, dict):
        errs = state.get('error_count')
        if isinstance(errs, bool) or not isinstance(errs, int):
            errs = 0
        if state.get('status') == plugin_host.STATUS_DEGRADED or errs > 0:
            return 'bad'
    if switch is False:
        return 'off'
    return 'ok'


def plugin_dot_color(colors, state):
    """状态点取色：只用已有色，不新增强调色。

    ok 用 inv（legacy 键名，对应皮肤 token `on`，和开关开态同色，说的是同一
    件事：在跑）；off 用 t4（够看见但不抢）；bad 用 danger——极简的铁律是
    danger 只给破坏性操作，这里破例给「运行异常」，因为它是数据状态而不是
    装饰，且版本号会同时提到 fg，不只靠颜色传达。
    """
    return {'ok': colors['inv'], 'off': colors['t4'],
            'bad': colors['danger']}.get(state)


def read_plugin_states():
    """读守护写的 plugins_state.json，返回 {id: 记录}。

    面板只读不写（这份文件是守护的产物，面板跟着写会把运行期异常状态覆盖掉）。
    文件不存在 / JSON 坏 / 结构不认识一律返回 {}：插件状态面板打不开是本末倒置。
    """
    try:
        with open(STATE_FILE, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    records = data.get('plugins') if isinstance(data, dict) else None
    if not isinstance(records, list):
        return {}
    out = {}
    for rec in records:
        if isinstance(rec, dict) and isinstance(rec.get('id'), str) and rec['id']:
            out[rec['id']] = rec
    return out


# ---------------- 界面 ----------------

class App(object):
    def __init__(self, root, probe=False, dpi_override=None):
        self.root = root
        self.probe = probe
        # 只读模式：config.json 写不进去（典型：装在 Program Files 且拒绝了 UAC）
        # 时三个功能开关、停止守护、重扫游戏库、开机自启全部置灰。
        self.readonly = bool(READONLY) and not probe
        # 插件管理（＋ / 垃圾桶）要写 plugins\\ 与安装根目录（热重载标记），
        # 权限口径单独判：config.json 可写但 plugins\\ 被收紧时按钮也要置灰。
        self.plugins_ro = self.readonly or not (
            dir_writable(BASE) and plugin_package.plugins_writable(PLUGINS_DIR))
        self.dpi = int(dpi_override) if dpi_override else window_dpi(root.winfo_id())
        self._job = None
        self._dpi_lock = False
        # 浮层只剩两类：皮肤面板、每插件设置弹框。v2.18.0 起没有「点字标弹菜单」
        # 那一套（浏览器式气泡菜单整块删除），设置改由右上角「设置」同窗换页。
        self.plugin_win = None
        self.plugin_body = None
        self.plugin_canvas = None
        self._plugin_window_id = None
        self._plugin_expanded = False
        self.plugin_entry = None
        self.plugin_anchor = None
        self.plugin_gears = {}
        self._plugin_was_pressed = False
        self._setting_switches = {}
        self.tooltips = []
        self.switches = {}

        # 显示名与系统标识统一 WBOX（v2.11.0 全量改名：exe / 安装目录 /
        # 计划任务 / 注册表键 / 管道 / mutex / 备份目录）；旧 Wingman 安装
        # 由安装器 migrate_legacy 迁移，面板只认新标识。
        root.title('WBOX')
        # 窗口大小必须在 apply_scaling 之前定：px() 的倍率由它带进来，
        # 晚一步，第一版控件就按旧尺寸画出来，要等下一次 rebuild 才对齐。
        self.ui_size = ui_size_from_cfg()
        self.ui_zoom = UI_SIZE_ZOOM[self.ui_size]
        # v2.15.0 插件状态点的挂法（row / badge / off），与窗口大小同一层：
        # 都属于「纯展示偏好」，读不到就按默认「行内」画。
        self.plugin_dot = plugin_dot_from_cfg()
        self.apply_scaling()

        self.family = pick_family(root)
        # 负号 = 像素尺寸。Tk 会缓存点数字体的像素度量，只改 tk scaling 时字号不变，
        # 所以这里由 resize_fonts() 按 DPI 显式重设，保证 150%/200% 下字也等比放大。
        self.f_title = tkfont.Font(root, family=self.family, weight='bold')
        self.f_head = tkfont.Font(root, family=self.family, weight='bold')
        # 行名用中等字重：设计稿 .nm 是 500，Tk 只有 400/700 两档，
        # 700 在小字号上比设计稿重一档，整屏看着发黑，所以行名走 400。
        self.f_row = tkfont.Font(root, family=self.family)
        self.f_body = tkfont.Font(root, family=self.family)
        self.f_small = tkfont.Font(root, family=self.family)
        self.f_wordmark = tkfont.Font(root, family=self.family, weight='bold')
        # 注册表必须在读配置之前建好：三段合并要用各插件的 DEFAULT_CONFIG，
        # 主页面行数也全靠它（顺序反了就会拿不到插件默认值）。
        self.entries_all = plugin_entries()
        self.runtime_state = read_plugin_states()
        # 主页面只显示「加载成功 + 作者声明 home」的插件；失败的插件不占主页面
        #（半成品功能开关比没有更糟）。它们仍然留在 entries_all 里，自检和
        # --self-check 能列出「加载失败」，只是界面上没有位置了。
        self.entries = [e for e in self.entries_all
                        if e.status == plugin_host.STATUS_OK and e.home]
        self.entries.sort(key=lambda e: (e.order, e.id))

        cfg = effective_cfg(entries=self.entries_all)
        # 皮肤与外观：resolve() 内部处理老配置（只有 ui_theme）的迁移，
        # 找不到 skin.json 时回退 plain，绝不让面板变成白板。
        self.skin, self.theme = skins.resolve(cfg, base=BASE, log=self.note)
        # 明暗偏好与解析结果分开存：选「跟随系统」时落盘的是 system，
        # 而 self.theme 永远是解析后的 light/dark（控件配色只认这两个）。
        pref = str(cfg.get('ui_appearance') or '').lower()
        self.appearance_pref = pref if pref in ('system', 'light', 'dark') \
            else self.theme
        # v2.10.0 迁移：老配置显式写过 liquid-glass 的，内置已移除会回退
        # plain；把解析结果写回一次，免得每次启动都走「不存在→回退」日志。
        # v2.17.0 起这条也顺手把「声明 standard 的第三方包」落到实际 id 上。
        if not self.readonly and not probe \
                and cfg.get('ui_skin') not in (None, self.skin.id):
            self.save_appearance()
        self.tok = self.skin.tokens(self.theme)
        self.colors = legacy_colors(self.tok, self.theme)
        # v2.17.0：只剩一套骨架——方角直排 + 行内展开 + 同窗换页。
        # performance / engine 不再参与渲染，皮肤只能改颜色与尺寸（26 token
        # + metrics），这正是「以后做皮肤都以默认为底」的落地约束。
        self.pane = 'list'                # list / set 同窗两页
        self.inline_open = None           # 哪一行展开了内联设置
        self.inline_full = None           # 哪一行摊开了全部设置项
        self.board_full_h = 0             # 列表内容全高，溢出滚动的基准
        self.set_canvas = None            # 设置页的滚动容器（仅溢出时滚）
        self.set_inner = None
        # 设置页的守护状态行（状态值 / 进程事件 / 插件健康 + 启停按钮）。
        # 三个值每 2 秒刷新一次，所以控件引用要留住；None = 当前不在设置页。
        self.set_guard = None
        self._guard_want = None           # 'up' / 'down'：刚点完启停，等它生效
        self._guard_since = 0.0
        self.skin_canvas = None           # 皮肤面板的滚动容器（仅溢出时滚）
        self.skin_window_id = None
        self.win_w_pref = PLAIN_W         # 最大化按钮会改它
        self.custom_chrome = False
        self._drag = None
        self._maximized = False
        self._normal_geo = None
        # 窗口顶边（屏幕坐标）。极简档展开一行会变高，窗口必须**向下长**：
        # 之前每次 place() 都按新高度重新居中，展开时整个窗口向上跳 280 px，
        # 点中的那一行跟着跑掉，看起来就像"点了没反应"。
        self._win_top = None
        self._tweens = {}
        self._dialog_win = None
        self._dialog_picks = []
        self.resize_fonts()

        # 开关状态一个插件一个：键就是插件 id，值是插件自己算出来的初始状态。
        self.vars = {}
        for entry in self.entries:
            self.vars[entry.id] = tk.BooleanVar(
                root, value=plugin_switch_state(entry, cfg))
        self.boot_var = tk.BooleanVar(root, value=(True if probe else autostart_state()))
        self.boot_initial = bool(self.boot_var.get())
        # 托盘两项是面板级偏好（不是插件键），默认关：收托盘/弹气泡都改变
        # 用户可见行为，opt-in 比 opt-out 安全。图标存在性不在此列（v2.11.2
        # 起常驻，见 apply_tray_pref）。设计稿 wbox-skins.html 设置页。
        self.tray_var = tk.BooleanVar(root, value=bool(cfg.get('start_in_tray')))
        self.notify_var = tk.BooleanVar(
            root, value=bool(cfg.get('notify_game_end')))
        self.tray = None
        self._close_without_tray = False
        self._last_session = ''

        self.main = tk.Frame(root, bg=self.colors['bg'],
                             padx=self.px(self.pad_x()), pady=self.px(self.pad_y()))
        self.main.pack(fill='both', expand=True)
        self.apply_chrome()
        self.apply_icon(root)
        self.build_main()

        root.minsize(self.px(430), self.px(200))
        root.resizable(False, False)
        root.bind('<Configure>', self.on_configure)

        def _esc(e):
            # 收最上面那层浮层；都收起后焦点回字标（close_* 里做）
            if getattr(self, 'skin_win', None) and self.skin_win.winfo_exists():
                self.close_skin_settings()
            elif getattr(self, 'plugin_win', None) and self.plugin_win.winfo_exists():
                self.close_plugin_settings()
        root.bind('<Escape>', _esc, add='+')
        self.place()

        if not probe:
            self.apply_tray_pref()
            self.refresh()
        # <Destroy> 会沿父链冒泡：rebuild() 销毁任意子控件都会触发这里。
        # 只在根窗口自己销毁（面板退出）时才摘托盘，否则每次换肤/改 DPI/
        # 缩放都误杀托盘线程；stop() 若卡在右键菜单里超时，旧图标残留，
        # 重建再挂一枚就是「托盘多个 W」的另一成因。
        root.bind('<Destroy>',
                  lambda e: self._tray_shutdown() if e.widget is root else None,
                  add='+')

    # ---- 尺寸 ----
    def pad_x(self):
        # v2.8.0 起主容器不再留边：标题栏与列表各自带内边距（设计稿的
        # 28/8 与 8/6 是两层，交给一处算会把两档皮肤的间距搅在一起）。
        return 0

    def pad_y(self):
        return 0

    def px(self, value):
        return max(1, int(round(value * self.scale)))

    def apply_scaling(self):
        # 两个倍率相乘：系统 DPI × 用户选的窗口大小。tk scaling 只跟 DPI 走，
        # 不乘窗口大小——所有字号都是像素负值（-px(n)），已经由 px() 放大过；
        # 再乘一次 tk scaling 会让 Tk 内部按点计的默认度量二次放大。
        self.scale = self.dpi / 96.0 * getattr(self, 'ui_zoom', 1.0)
        self.root.tk.call('tk', 'scaling', self.dpi / 72.0)

    def resize_fonts(self):
        # 字号基准来自皮肤 metrics.font（默认 13），标题与辅助字按它派生，
        # 不让每套皮肤各写一套固定像素。
        skin = getattr(self, 'skin', None)
        base = skin.metrics.get('font', 13) if skin is not None else 13
        # 字标单独一档 15：它要压得住整屏，跟着正文字号等比放大会头重脚轻。
        word = 15
        # 辅助字只比正文小 1/2 px：v2.9.0 的 -2/-3 派生把「设置」等入口
        # 压到 10px，用户实测偏小。
        for f, size_px in ((self.f_title, int(round(base * 1.5))),
                           (self.f_head, base),
                           (self.f_row, base),
                           (self.f_body, max(10, base - 1)),
                           (self.f_small, max(9, base - 2)),
                           (self.f_wordmark, word)):
            try:
                f.configure(size=-self.px(size_px))     # 负数 = 像素
            except Exception:
                pass

    def relayout(self):
        """新版没有需要按窗口宽度换行的静态文本，保留接口给 apply_dpi。"""
        return

    def popup_colors(self, key='pop_base'):
        """浮层用的配色：底色换成 `pop_base`。

        窗口底（token `glass`）与浮层底必须分开：两者同色时浮层像"窗口的
        一部分"，用户分不清哪块是可以点掉的浮层。

        `menu_base` 从 v2.18.0 起是**历史占位名**：它是齿轮气泡菜单的底，
        那套菜单整块删了，面板不再读它。token 名不改——26 个 token 是所有已分发
        皮肤包的一部分，删名或改名会让别人的包直接失效。
        """
        c = dict(self.colors)
        c['bg'] = self.tok.get(key, c['bg'])
        return c

    def note(self, msg):
        """皮肤回退这类"用户可能想知道、但不该被弹框打断"的事件，只写日志。"""
        self.log_line('skin', msg)

    def log_line(self, tag, msg):
        try:
            with open(os.path.join(BASE, 'guard.log'), 'a', encoding='utf-8') as f:
                f.write('%s [%s] %s\n' % (
                    __import__('time').strftime('%Y-%m-%d %H:%M:%S'), tag, msg))
        except Exception:
            pass

    def dbg(self, msg):
        """开发期定位用：设了 ABG_DBG 才写日志，正常使用零开销。"""
        if not os.environ.get('ABG_DBG'):
            return
        try:
            with open(os.path.join(BASE, 'gui_debug.log'), 'a', encoding='utf-8') as f:
                f.write('%s dpi=%s scale=%.3f win=%dx%d %s\n' % (
                    __import__('time').strftime('%H:%M:%S'), self.dpi, self.scale,
                    self.root.winfo_width(), self.root.winfo_height(), msg))
        except Exception:
            pass

    def place(self):
        lx, ly, sw, sh = work_area()
        self.dbg('place start area=%d,%d,%d,%d mode=%s sdpi=%d wdpi=%d' % (
            lx, ly, sw, sh, DPI_MODE, system_dpi(), window_dpi(self.root.winfo_id())))
        w = self.content_width()
        cur_y = self._win_top if self._win_top is not None else ly
        self.root.geometry('%dx100+%d+%d' % (w, lx + max(0, (sw - w) // 2),
                                            cur_y))
        self.root.update_idletasks()
        self.relayout()
        self.root.update_idletasks()
        # 高度完全由内容决定：标题栏 + 列表（+ 极简档展开的设置块）。
        # 量之前先把容器恢复成全高，否则上一次溢出裁剪后的高度会骗过 reqheight。
        bd = getattr(self, 'board', None)
        if bd is not None and bd.winfo_exists() and self.board_full_h:
            bd.configure(height=self.board_full_h)
        if (self.set_canvas is not None and self.set_canvas.winfo_exists()
                and self.set_inner is not None):
            self.set_canvas.configure(height=self.set_inner.winfo_reqheight())
        self.root.update_idletasks()
        need = self.root.winfo_reqheight()
        if getattr(self, '_maximized', False):
            h, x, y = sh, lx, ly
            self._win_top = None
        else:
            h = min(need, sh - self.px(40))
            x = lx + max(0, (sw - w) // 2)
            if self._win_top is None:
                y = ly + max(0, (sh - h) // 2)
            else:
                # 顶边钉住：展开/收起只改高度，不重新居中。
                # 下面装不下时才上移，仍不越过工作区顶。
                y = max(ly, min(self._win_top,
                                ly + max(0, sh - h - self.px(8))))
            self._win_top = y
        self.root.geometry('%dx%d+%d+%d' % (w, h, x, y))
        self.root.update_idletasks()
        self.apply_window_region()
        self._fit_overflow(need, h)
        self.dbg('place end w=%d h=%d need=%d' % (w, h, need))

    def _fit_overflow(self, need, h):
        """内容比窗口高时滚轮滚，不硬截（屏幕高度是硬上限，设计稿的
        「高度随内容」只在不超屏时成立）。列表与极简设置页各走自己的容器。"""
        over = max(0, need - h)
        bd = getattr(self, 'board', None)
        if bd is not None and bd.winfo_exists() and self.board_full_h:
            bd.configure(height=max(self.px(60), self.board_full_h - over))
            bd.configure(scrollregion=(0, 0, bd.winfo_reqwidth(),
                                       self.board_full_h))
            self._bind_wheel(bd, bd)
        cv, inner = self.set_canvas, self.set_inner
        if cv is not None and cv.winfo_exists() and inner is not None:
            cw = max(1, cv.winfo_width())
            cv.itemconfigure(self._set_window_id, width=cw)
            cv.configure(scrollregion=(0, 0, cw, inner.winfo_reqheight()))
            self._bind_wheel(cv, inner)

    def apply_dpi(self, dpi):
        self.dpi = int(dpi)
        self.apply_scaling()
        self.resize_fonts()
        self.root.minsize(self.px(430), self.px(200))
        self.rebuild()

    def on_configure(self, ev):
        if ev.widget is not self.root:
            return
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
        self._job = self.root.after(180, self._after_resize)

    def _after_resize(self):
        self._job = None
        self.relayout()
        if self.probe or self._dpi_lock:
            return
        try:
            new = window_dpi(self.root.winfo_id())
        except Exception:
            return
        self.dbg('after_resize wdpi=%d' % new)
        if new != self.dpi:
            self._dpi_lock = True
            try:
                self.apply_dpi(new)
            finally:
                self._dpi_lock = False
            self.dbg('apply_dpi -> %d' % new)

    # ---- 主题 / 图标 ----
    def apply_icon(self, win):
        p = ICON_DARK if self.theme == 'dark' else ICON_LIGHT
        if os.path.isfile(p):
            try:
                win.iconbitmap(p)
            except Exception:
                pass
        # wm_frame() 要等窗口被 map 之后才有值；补两次延迟，确保系统标题栏
        # 与任务管理器用的大小图标都落在顶层 frame 上，而不是客户区子窗口。
        def refresh(w=win):
            self.apply_titlebar(w)
            set_window_icons(w, ICON_DARK if self.theme == 'dark' else ICON_LIGHT)

        try:
            win.after(60, refresh)
            win.after(260, refresh)
        except Exception:
            pass

    def apply_titlebar(self, win):
        """Windows 系统标题栏跟随主题：纯黑主题启用 DWM 沉浸式深色模式。

        系统标题栏不能直接用 Tk 改色，用 DWMWA_USE_IMMERSIVE_DARK_MODE（新系统
        20 / 旧系统 19）切到深色；失败就保留系统默认，不影响功能。
        """
        try:
            frame = win.wm_frame()
            hwnd = int(frame, 16) if frame else win.winfo_id()
            f = ctypes.windll.dwmapi.DwmSetWindowAttribute
            f.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                          ctypes.POINTER(ctypes.c_int), ctypes.c_uint32]
            f.restype = ctypes.c_long
            val = ctypes.c_int(1 if self.theme == 'dark' else 0)
            for attr in (20, 19):
                f(ctypes.c_void_p(hwnd), attr, ctypes.byref(val), 4)
            # 切换后强制重画非客户区，否则部分系统上标题栏要等一次 resize 才刷新
            u = ctypes.windll.user32
            u.RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_void_p, ctypes.c_uint32]
            u.RedrawWindow(ctypes.c_void_p(hwnd), None, None,
                           0x0001 | 0x0400 | 0x0100)   # INVALIDATE|FRAME|UPDATENOW
        except Exception:
            pass

    def save_appearance(self):
        """外观单独落盘。

        不放进 save_features：用户拨一个功能开关时，配置里不该多出 ui_skin /
        ui_appearance 两条改动——那是切换外观的副作用，会让「只改了这一项」
        的断言（以及用户对比配置 diff 时的判断）失真。
        """
        if self.readonly:
            return
        try:
            cfg = load_cfg()
            cfg['ui_skin'] = self.skin.id
            cfg['ui_appearance'] = self.appearance_pref
            cfg['ui_size'] = self.ui_size    # v2.12.0：小 / 中 / 大
            cfg['ui_plugin_dot'] = self.plugin_dot   # v2.15.0：row / badge / off
            cfg['ui_theme'] = self.theme      # 老字段留一份快照，降级不突变
            save_cfg(cfg)
        except Exception:
            self.note('外观未能保存到 config.json')

    def apply_skin(self, skin, appearance=None, persist=True):
        """换皮肤 / 换明暗的唯一入口。

        每次都重建控件：Tk 控件一旦创建就记住自己的 bg，改配色只改
        Frame 那一层会留下旧色的方块（历史坑）。重建一次的成本是几十毫秒，
        用户点一下皮肤能接受。persist=False 供 layout-check 这类
        「只换骨架不落盘」的自检路径用。
        """
        self.skin = skin
        if appearance in ('light', 'dark', 'system'):
            self.appearance_pref = appearance
            self.theme = skins.detect_appearance() if appearance == 'system' \
                else appearance
        self.tok = skin.tokens(self.theme)
        self.colors = legacy_colors(self.tok, self.theme)
        # 换皮肤只换颜色与尺寸，不换骨架：v2.17.0 起只有默认一套。
        # 留一条不变式——窗口宽度基准始终是 PLAIN_W，皮肤改不了。
        self.win_w_pref = PLAIN_W
        # 换明暗/换皮肤时留在原页：用户在「设置」里点「深色」，
        # 结果被弹回列表页，等于这一下白点了。
        self.resize_fonts()
        # 只读模式下外观只在本进程内生效，不弹"需要管理员权限"打断切换
        if persist:
            self.save_appearance()
        self.apply_icon(self.root)
        self.apply_tray_pref()
        self.rebuild()

    def set_theme(self, name):
        """兼容旧调用：只换明暗，皮肤不变。system = 跟随系统应用主题。"""
        if name not in ('light', 'dark', 'system') or name == self.appearance_pref:
            return
        self.apply_skin(self.skin, name)

    def set_skin(self, skin_id):
        if skin_id == self.skin.id:
            return
        skin = skins.get(skin_id, base=BASE, log=self.note)
        if skin is None:
            self.note('皮肤 %s 不存在，忽略切换' % skin_id)
            return
        self.apply_skin(skin, self.appearance_pref)

    def set_ui_size(self, key, persist=True):
        """窗口大小三档。px() 的结果已经烘进每个控件的像素尺寸里，换倍率
        只能整屏重建（和换皮肤同一套路径），不能指望 configure 追得上。

        persist=False 只给 layout-check 用：自检要量三档，不能顺手把
        用户选的大小改掉、更不能往生产 config.json 里写。
        """
        if key not in UI_SIZE_ZOOM or key == self.ui_size:
            return
        self.ui_size = key
        self.ui_zoom = UI_SIZE_ZOOM[key]
        self.apply_scaling()
        self.resize_fonts()
        try:
            self.root.minsize(self.px(430), self.px(200))
        except Exception:
            pass
        # 只读模式下只在本进程内生效（save_appearance 内部已判 readonly）
        if persist:
            self.save_appearance()
        self.rebuild()

    def set_plugin_dot(self, key, persist=True):
        """插件状态点三档：行内 / 角标 / 不显示。

        只换绘制位置、不动布局骨架，但控件是整块重建出来的（和换皮肤同一条
        路径），所以仍走 rebuild()。persist=False 只给自检用：自检要量三档，
        不能顺手改掉用户的选择。
        """
        if key not in PLUGIN_DOT_MODES or key == self.plugin_dot:
            return
        self.plugin_dot = key
        if persist:
            self.save_appearance()
        self.rebuild()

    def rebuild(self):
        """切主题 / 改 DPI 时按新令牌重建所有控件，避免残留旧配色的方块。"""
        self.close_plugin_settings()
        self.hide_tips()
        try:
            self.root.configure(bg=self.colors['bg'])
            self.main.configure(bg=self.colors['bg'],
                                padx=self.px(self.pad_x()),
                                pady=self.px(self.pad_y()))
        except Exception:
            pass
        for w in list(self.main.winfo_children()):
            w.destroy()
        self.tooltips = []
        self.switches = {}
        self.build_main()
        if getattr(self, 'skin_win', None) and self.skin_win.winfo_exists():
            self.refresh_skin_list()
        self.place()
        self.relayout()

    def hide_tips(self):
        for t in self.tooltips:
            t.hide()
        self.tooltips = []

    # ---- 动效：单 after 链插值（设计稿时长 + 唯一曲线） ----
    def anim_ms(self, ms):
        """过渡时长。默认骨架零动画：一律返回 0，所有 tween 同步跳终态。

        v2.17.0 之前这里还有一道 `glass` 闸（只有玻璃档才动），骨架合并后
        没有"哪档该动"的区分了；默认就是最省的零动画。调用点全部保留
        （tween / _pop_in / 开关），将来要加回动效只改这一处返回值。
        """
        return 0

    def cancel_tween(self, key):
        job = self._tweens.pop(key, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    def tween(self, key, ms, step, done=None):
        """step(p) 收 eased 进度。同 key 重开先 cancel 旧链：hover 进出交替、
        开关连点都不会留两条链抢同一个控件。"""
        self.cancel_tween(key)
        if ms <= 0:
            step(1.0)
            if done:
                done()
            return
        t0 = time.monotonic()

        def tick():
            p = (time.monotonic() - t0) * 1000.0 / ms
            if p >= 1.0:
                self._tweens.pop(key, None)
                step(1.0)
                if done:
                    done()
                return
            step(ease(p))
            self._tweens[key] = self.root.after(16, tick)
        tick()

    def _pop_in(self, win, slide=0):
        """浮层入场 200ms 淡入；按 slide 再叠一个 translateY 落位。

        只做入场：退场立即 destroy——淡出链会让 Toplevel 残骸多活一百多毫秒，
        撞重建的「无残留浮层」断言，也撞点外部收起的全局判定。
        """
        ms = self.anim_ms(200)
        if ms <= 0 or not win.winfo_exists():
            return
        win.update_idletasks()
        x, y = win.winfo_x(), win.winfo_y()
        try:
            win.attributes('-alpha', 0.0)
        except Exception:
            return

        def step(p):
            try:
                win.attributes('-alpha', p)
                if slide:
                    win.geometry('+%d+%d' % (x, y - int(round(slide * (1 - p)))))
            except Exception:
                pass

        def done():
            try:
                win.geometry('+%d+%d' % (x, y))
                win.attributes('-alpha', 1.0)
            except Exception:
                pass
        self.tween(('pop', id(win)), ms, step, done)

    # ---- 通用控件 ----
    # v1.2.0 起界面只剩「自绘按钮 + 行内控件」，旧版 tk.Button 工厂已删除：
    # 三类交互全部由 Glyph / Switch / 行控件承接，避免两套控件风格并存。
    def sep(self, master, pady=0):
        tk.Frame(master, height=1, bg=self.colors['line']).pack(
            fill='x', pady=pady)

    def hover(self, widgets, on, base=None, press=False):
        base = base or self.colors['bg']
        target = self.colors['press'] if press else (
            self.colors['hover'] if on else base)
        ms = self.anim_ms(120 if press else 160)
        key = ('hover', id(widgets[0]))
        if ms <= 0:
            self.cancel_tween(key)
            self._paint_bg(widgets, target)
            return
        cur = self._bg_of(widgets[0]) or base
        self.tween(key, ms,
                   lambda p: self._paint_bg(widgets, mix_hex(cur, target, p)))

    def _bg_of(self, w):
        try:
            return w.cget('bg')
        except Exception:
            return None

    def _paint_bg(self, widgets, color):
        for w in widgets:
            if isinstance(w, (Switch, Glyph)):
                w.set_bg(color)
            else:
                try:
                    w.configure(bg=color)
                except Exception:
                    pass

    def bind_hover(self, row, widgets, base=None):
        allw = (row,) + tuple(widgets)

        def enter(ev, ws=widgets):
            self.hover(ws, True, base)

        def leave(ev, ws=widgets):
            self.hover(ws, False, base)

        def press(ev, ws=widgets):
            self.hover(ws, True, base, press=True)

        def release(ev, ws=widgets):
            self.hover(ws, True, base)

        for w in allw:
            # add='+'：名称上已经绑了 ToolTip 的 Enter/Leave，不能被覆盖
            w.bind('<Enter>', enter, add='+')
            w.bind('<Leave>', leave, add='+')
            w.bind('<ButtonPress>', press, add='+')
            w.bind('<ButtonRelease>', release, add='+')

    # ---- 窗口外壳：自绘标题栏（设计稿 v3.4.0 方案 4） ----
    def content_width(self):
        """客户区宽度（像素）：基准 640 逻辑像素，屏幕窄了才收。"""
        _, _, sw, _ = work_area()
        if getattr(self, '_maximized', False):
            return sw
        want = int(round(self.win_w_pref * self.scale))
        return min(want, max(self.px(320), sw - self.px(30)))

    def apply_chrome(self):
        """把系统标题栏换掉，整行交给自绘。

        方案 4 要求「字标 + 加号 + 窗口控制」同处一行 48px，系统标题栏做不到。
        overrideredirect 之后系统不再给标题栏、圆角、阴影，代价自己补：
          * 圆角 = SetWindowRgn(CreateRoundRectRgn)，半径取皮肤 radius.window
            （极简档半径 0，一次 API 都不调，保持"方角零开销"）
          * 任务栏 = WS_EX_APPWINDOW，否则 overrideredirect 窗口最小化等于消失；
            wrapper 诞生于 map，风格位要在 <Map> 钩子里重补（apply_appwindow）
          * 拖动 = 标题栏上跟鼠标，窗口尺寸由内容定，没有可缩放的余地
        阴影不做：DWM 只给带 frame 的窗口投影，硬要就得拦 WndProc，
        收益一个像素级观感、代价是整条窗口消息链，不划算。
        """
        if self.skin.chrome != 'custom':
            return
        try:
            self.root.overrideredirect(True)
        except Exception:
            return
        self.custom_chrome = True
        self.apply_appwindow()
        self.root.bind('<Map>', lambda e: self.apply_appwindow(refresh=True),
                       add='+')

    def apply_appwindow(self, refresh=False):
        """任务栏按钮：overrideredirect 的 wrapper 在 map 时才被 Tk 创建并带上
        WS_EX_TOOLWINDOW（init 时 wm_frame() 落回客户区子窗口，风格位加错对象），
        所以 <Map> 之后必须对 wrapper 重补：清 TOOLWINDOW、加 APPWINDOW。
        可见路径实测（probe_tb_deploy）：shell 在 wrapper 首次 show 时就按
        TOOLWINDOW 缓存了排除决定，Map 里改位 + FRAMECHANGED 不够，必须
        hide/show 刷新一次才出按钮；alpha=0 探针路径改位即够（hook_bits 截图），
        两条路径都覆盖才稳。withdraw/deiconify 会清键盘焦点（焦点 None 时 Tk
        丢弃键事件），刷新前记住焦点、刷新后 focus_force 回填——focus_set 在
        非前景进程不生效，force 才落得住（bisect 实测）。
        iconic 时不刷新，等下次 show 自然重判，避免把最小化按钮刷没。"""
        try:
            u = ctypes.windll.user32
            hwnd = win_hwnd(self.root)
            u.GetWindowLongW.restype = ctypes.c_long
            ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), -20)      # GWL_EXSTYLE
            want = (ex | 0x00040000) & ~0x00000080   # +APPWINDOW -TOOLWINDOW
            if want == ex:
                return
            u.SetWindowLongW(ctypes.c_void_p(hwnd), -20, ctypes.c_long(want))
            u.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                           0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)
            if refresh and self.root.state() == 'normal':
                try:
                    focus = self.root.focus_get()
                except Exception:
                    focus = None
                self.root.withdraw()
                self.root.deiconify()
                try:
                    (focus if focus is not None else self.root).focus_force()
                except Exception:
                    pass
        except Exception:
            pass

    def apply_window_region(self):
        """圆角窗口：Tk 只能画方形窗口，圆角靠 Windows 区域裁。"""
        try:
            u = ctypes.windll.user32
            g = ctypes.windll.gdi32
            hwnd = win_hwnd(self.root)
            r = int(round(float(self.skin.radius.get('window') or 0) * self.scale))
            if r <= 0:
                u.SetWindowRgn(ctypes.c_void_p(hwnd), None, True)
                return
            w, h = self.root.winfo_width(), self.root.winfo_height()
            if w < 2 or h < 2:
                return
            rgn = g.CreateRoundRectRgn(0, 0, w + 1, h + 1, r * 2, r * 2)
            u.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True)
        except Exception:
            pass

    def _drag_start(self, ev):
        self._drag = (ev.x_root - self.root.winfo_x(),
                      ev.y_root - self.root.winfo_y())

    def _drag_move(self, ev):
        d = getattr(self, '_drag', None)
        if not d:
            return
        self.root.geometry('+%d+%d' % (ev.x_root - d[0], ev.y_root - d[1]))

    def on_close(self):
        if bool(self.tray_var.get()):
            if self.tray is None or not self.tray.active:
                # 勾了「收进托盘」但图标不在：先补一次。补不上就不隐藏——
                # 窗口一收，用户既没有窗口也没有托盘图标，只能当程序丢了。
                self.apply_tray_pref()
            if self.tray is not None and self.tray.active:
                self.root.withdraw()
                return
            if not self._close_without_tray:
                self._close_without_tray = True
                self.log_line('tray', '托盘不可用，已保持面板可见；'
                                      '再点一次关闭将退出面板')
                return
        self.root.destroy()

    def on_min(self):
        """最小化：overrideredirect 窗口 iconify() 无效，先装回系统外壳再最小化，
        重新映射后把自绘外壳装回去。"""
        if not self.custom_chrome:
            self.root.iconify()
            return
        self.root.overrideredirect(False)
        self.root.iconify()
        self.root.after(100, self._restore_chrome)

    def on_max(self):
        """最大化 = 铺满工作区（留任务栏，不是全屏）；再点还原最大化前的尺寸位置。"""
        lx, ly, sw, sh = work_area()
        if getattr(self, '_maximized', False):
            self._maximized = False
            self.rebuild()
            geo = getattr(self, '_normal_geo', None)
            if geo:
                try:
                    self.root.geometry(geo)
                    self.root.update_idletasks()
                    self.apply_window_region()
                    hh = int(geo.split('+')[0].split('x')[1])
                    self._fit_overflow(self.root.winfo_reqheight(), hh)
                    # 还原后顶边要跟着几何走，否则下一次展开会跳到居中位置
                    self._win_top = int(geo.rsplit('+', 1)[1])
                except Exception:
                    pass
            kind = 'max'
        else:
            self._normal_geo = self.root.geometry()
            self._maximized = True
            self.rebuild()
            kind = 'restore'
        g = getattr(self, 'max_wrap', None)
        if g is not None and getattr(g, 'glyph', None) is not None:
            g.glyph.kind = kind
            g.glyph.draw()

    def _restore_chrome(self):
        if not self.custom_chrome:
            return
        try:
            if self.root.state() == 'iconic':
                self.root.after(100, self._restore_chrome)
                return
        except Exception:
            pass
        try:
            self.root.overrideredirect(True)
            self.apply_appwindow()
            self.apply_window_region()
        except Exception:
            pass

    # ---- 标题栏控件 ----
    def chrome_button(self, master, kind, command, tip):
        """窗口控制：34×26 命中区，悬停铺一层方角底。"""
        c = self.colors
        w, h = self.px(34), self.px(26)
        cv = tk.Canvas(master, width=w, height=h, bg=c['bg'],
                       highlightthickness=0, bd=0, cursor='hand2')
        g = Glyph(cv, self, kind, size=self.px(12), ink='t3')
        g.set_bg(c['bg'])
        cv.create_window(w // 2, h // 2, window=g)
        base = c['bg']
        hot = c['hover']

        def paint(on, col=None):
            col = col or (hot if on else base)
            try:
                cv.delete('hl')
                g.set_bg(col)
                if col != base:
                    cv.create_rectangle(1, 1, w - 1, h - 1, fill=col,
                                        outline='', tags='hl')
                    cv.tag_lower('hl')
            except Exception:
                pass

        def fade(to, ms):
            frm = g.bg
            self.tween(('chr', id(cv)), ms,
                       lambda p: paint(True, mix_hex(frm, to, p)))

        for wid in (cv, g):
            wid.bind('<Enter>', lambda e: fade(hot, self.anim_ms(160)), add='+')
            wid.bind('<Leave>', lambda e: fade(base, self.anim_ms(160)), add='+')
            wid.bind('<ButtonPress>',
                     lambda e: fade(self.colors['press'], self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease>', lambda e: fade(hot, self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease-1>', lambda e: command(), add='+')
        self._keyboard(cv, command)
        cv.glyph = g
        self.tooltips.append(ToolTip(self, cv, tip))
        return cv

    def round_button(self, master, size, glyph_kind, glyph_size, command, tip):
        """圆形按钮（加号）：浅灰圆角方底 + 字形，悬停加深。"""
        c = self.colors
        cv = tk.Canvas(master, width=size, height=size, bg=c['bg'],
                       highlightthickness=0, bd=0)
        g = Glyph(cv, self, glyph_kind, size=glyph_size, ink='fg')
        # 也要读得出「这是个按钮」：浅灰圆角方底 + 悬停加深，
        # 不是一枚裸字形漂在标题栏上（v2.10.0 打磨）。
        base = self.tok['fill_2']
        hot = self.tok['fill']
        g.set_bg(base)
        cv.create_window(size // 2, size // 2, window=g)

        def paint(on, col=None):
            col = col or (hot if on else base)
            try:
                cv.delete('bg')
                round_rect(cv, 0, 0, size, size, self.px(6), fill=col,
                           outline='', tags='bg')
                cv.tag_lower('bg')
                g.set_bg(col)
            except Exception:
                pass

        paint(False)

        def fade(to, ms):
            frm = g.bg
            self.tween(('rnd', id(cv)), ms,
                       lambda p: paint(True, mix_hex(frm, to, p)))

        for wid in (cv, g):
            wid.bind('<Enter>', lambda e: fade(hot, self.anim_ms(160)), add='+')
            wid.bind('<Leave>', lambda e: fade(base, self.anim_ms(160)), add='+')
            wid.bind('<ButtonPress>',
                     lambda e: fade(self.colors['press'], self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease>', lambda e: fade(hot, self.anim_ms(120)),
                     add='+')
            wid.bind('<ButtonRelease-1>', lambda e: command(), add='+')
        self._keyboard(cv, command)
        cv.glyph = g        # 调用方按 kind 找图标（测试与自检都靠它）
        self.tooltips.append(ToolTip(self, cv, tip))
        return cv

    # ---- 主页面 ----
    def build_main(self):
        """标题栏 + 内容区（只有一套骨架）。

        * 列表页：方角直排，每行行内展开设置（不新建窗口）；
        * 设置页：同窗换页，标题栏左上角变成「←」面包屑。

        行数据来源只有一个——插件注册表。
        """
        self.plugin_gears = {}
        self.build_titlebar(self.main)
        if self.pane == 'set':
            self.build_set_view(self.main)
        else:
            self.build_list_view(self.main)
        # Tab 顺序照文案规范 §4：＋ → 「设置」/「←」→ 各行开关 → 各行行尾。
        chain = []
        if self.pane != 'set' and getattr(self, 'add_wrap', None) is not None:
            chain.append(self.add_wrap)
        if getattr(self, 'pane_btn', None) is not None:
            chain.append(self.pane_btn)
        if self.pane == 'set':
            chain.extend(getattr(self, '_set_focusables', []))
        else:
            chain.extend(self.switches.values())
            chain.extend(self.plugin_gears.values())
        self._set_chain(self.root, chain)

    def build_titlebar(self, master):
        """一行装下字标、加号、窗口控制、设置入口。

        间距照 design/wbox-preview.html 的极简档：左右 16/6、字标 15、
        加号 26 圆角方底、窗口控制 34×26 且用发丝线隔开。
        """
        c = self.colors
        tb_h = self.px(self.skin.metrics.get('titlebar', 36))
        bar = tk.Frame(master, bg=c['bg'], height=tb_h)
        bar.pack(fill='x')
        bar.pack_propagate(False)
        self.titlebar = bar

        # 右起：关闭、发丝线、最大化、最小化（设计稿 34×26 三枚）
        ctrls = tk.Frame(bar, bg=c['bg'])
        ctrls.pack(side='right', padx=(0, self.px(4)))
        self.chrome_button(ctrls, 'close', self.on_close,
                           '关闭面板（守护在后台继续运行）').pack(side='right')
        tk.Frame(ctrls, bg=c['line'], width=self.px(1)).pack(
            side='right', fill='y', pady=self.px(9), padx=self.px(4))
        self.max_wrap = self.chrome_button(ctrls, 'max', self.on_max,
                                           '最大化 / 还原窗口')
        self.max_wrap.pack(side='right')
        self.chrome_button(ctrls, 'min', self.on_min,
                           '最小化').pack(side='right')

        # 加号：唯一动作按钮（装插件）。设置页里不出现——设计稿在 set 页把
        # .p-add 一起 display:none，装插件跟"改设置"不是一件事。
        size = self.px(26)
        self.add_wrap = self.round_button(
            bar, size, 'plus', self.px(15),
            self.on_add_plugin,
            '安装插件：选一个插件文件，装好后立即生效。')
        if self.pane != 'set':
            self.add_wrap.pack(side='right', padx=(0, self.px(6)))
        self.add_btn = getattr(self.add_wrap, 'glyph', self.add_wrap)
        self.add_wrap.configure(
            cursor='hand2' if not self.plugins_ro else 'arrow')

        # 设置入口：标题栏一枚纯文字「设置」，点它同窗换页；
        # 进设置页后换成「← 设置」面包屑（设计稿 p-back / p-crumb）。
        if self.pane == 'set':
            back = tk.Label(bar, text='←', bg=c['bg'], fg=c['t2'],
                            font=self.f_body, padx=self.px(8),
                            cursor='hand2')
            back.pack(side='left', padx=(self.px(6), 0))
            back.bind('<ButtonRelease-1>', lambda e: self.toggle_pane())
            back.bind('<Enter>', lambda e: back.configure(bg=c['hover']),
                      add='+')
            back.bind('<Leave>', lambda e: back.configure(bg=c['bg']),
                      add='+')
            self._keyboard(back, self.toggle_pane)
            self.pane_btn = back
        else:
            txt = tk.Label(bar, text='设置', bg=c['bg'], fg=c['t2'],
                           font=self.f_body, padx=self.px(9), cursor='hand2')
            txt.pack(side='right', padx=(0, self.px(4)))
            txt.bind('<ButtonRelease-1>', lambda e: self.toggle_pane())
            txt.bind('<Enter>', lambda e: txt.configure(bg=c['hover']),
                     add='+')
            txt.bind('<Leave>', lambda e: txt.configure(bg=c['bg']), add='+')
            self._keyboard(txt, self.toggle_pane)
            self.pane_btn = txt

        # 左：字标。右上角已经有「设置」，字标只是身份标识——同一件事挂两个
        # 入口既重复又容易误触，所以字标不绑点击（v2.12.2），只留拖动。
        self.brand_wrap = tk.Frame(bar, bg=c['bg'], padx=self.px(8),
                                   pady=self.px(4), cursor='arrow')
        self.brand_wrap.pack(side='left', padx=(self.px(8), 0))
        # v2.15.0 B 案：字标吃强调色（inv = token on）。默认皮肤里 on 是近黑，
        # 与原来的 fg 几乎同色，等于零视觉回归；第三方配色主题才会显出颜色。
        brand = tk.Label(self.brand_wrap, text='WBOX', bg=c['bg'], fg=c['inv'],
                         font=self.f_wordmark, cursor='arrow')
        brand.pack(side='left')
        self.brand_btn = brand        # 测试与键盘焦点靠它定位字标
        for w in (self.brand_wrap, brand):
            # 拖动是窗口手势，标题栏和字标都能拖。
            w.bind('<Button-1>', self._drag_start, add='+')
            w.bind('<B1-Motion>', self._drag_move, add='+')
        bar.bind('<Button-1>', self._drag_start, add='+')
        bar.bind('<B1-Motion>', self._drag_move, add='+')
        if self.pane == 'set':
            tk.Label(bar, text='设置', bg=c['bg'], fg=c['t3'],
                     font=self.f_body).pack(side='left', padx=(self.px(6), 0))

    def build_list_view(self, master):
        """插件列表：方角直排，行间 1px 发丝线。"""
        c = self.colors
        self.row_h = self.px(self.skin.metrics.get('row', 40))
        body_px = 0
        card_pad = 0
        board_w = self.content_width()
        row_w = board_w
        self.row_base = c['bg']

        board = tk.Canvas(master, bg=c['bg'], highlightthickness=0, bd=0,
                          width=board_w, height=self.row_h)
        board.pack(fill='x', padx=body_px, pady=0)
        self.board = board

        y = card_pad
        for i, entry in enumerate(self.entries):
            row, row_h = self.make_row(board, entry, i, card_pad, row_w, y)
            y += self.row_h
            if self.inline_open == entry.id:
                y += self.make_inline(board, entry, card_pad, row_w, y)

        board_h = y + card_pad
        board.configure(height=board_h)
        self.board_full_h = board_h

        if not self.entries:
            box = tk.Frame(master, bg=c['bg'])
            box.pack(fill='x', pady=self.px(14))
            tk.Label(box, text='还没有安装任何插件\n'
                               '主程序不附带插件，从插件库下载后用右上角 ＋ 安装',
                     bg=c['bg'], fg=c['t3'], font=self.f_body,
                     anchor='w', justify='left').pack(fill='x')
            btns = tk.Frame(box, bg=c['bg'])
            btns.pack(anchor='w', pady=(self.px(10), 0))
            self.capsule_button(btns, '安装插件', self.on_add_plugin).pack(
                side='left')
            self.capsule_button(btns, '插件库', self.on_plugin_repo,
                                primary=False).pack(side='left',
                                                    padx=(self.px(8), 0))
        self._bind_wheel(board, board)

    def capsule_button(self, master, text, command, primary=True):
        """整宽胶囊之外的单枚胶囊按钮（空态引导 / 皮肤安装入口 / 弹框共用）。"""
        c = self.colors
        h = self.px(30)
        fill = c['fg'] if primary else c['fill']
        fg = c['bg'] if primary else c['fg']
        cv = tk.Canvas(master, bg=c['bg'], highlightthickness=0, bd=0,
                       height=h, cursor='hand2')
        lbl = tk.Label(cv, text=text, bg=fill, fg=fg, font=self.f_body)
        cv.update_idletasks()
        w = lbl.winfo_reqwidth() + self.px(36)
        cv.configure(width=w)
        round_rect(cv, 0.5, 0.5, w - 0.5, h - 0.5, 0, fill=fill,
                   outline='', tags='cap')
        cv.create_window(w // 2, h // 2, window=lbl)
        for wdg in (cv, lbl):
            wdg.bind('<ButtonRelease-1>', lambda e: command())
        # 焦点环画在实心底上时用反色，否则 fg 环叠 fg 底看不见
        self._keyboard(cv, command, ring_color=(c['bg'] if primary else c['fg']))
        return cv

    # ---- 键盘可达与焦点环（文案规范 §4） ----
    def _ring(self, cv, on, color=None):
        """Canvas 焦点环：1px、距边 2px。Tk 画不出控件外偏移，偏移向内实现；
        hover 只删自己的底色 tag，不碰 focusring，所以环不会被悬停盖掉。"""
        try:
            cv.delete('focusring')
            if on:
                w, h = int(cv.cget('width')), int(cv.cget('height'))
                ins = self.px(2)
                cv.create_rectangle(
                    ins + 0.5, ins + 0.5, w - ins - 0.5, h - ins - 0.5,
                    outline=color or self.colors['fg'], width=1,
                    tags='focusring')
        except Exception:
            pass

    def _keyboard(self, widget, command, ring_color=None):
        """Enter / Space 激活 + 焦点可见。Canvas 画环，Frame/Label 用 1px 外框。"""
        try:
            widget.configure(takefocus=1)
        except Exception:
            pass
        widget.bind('<Return>', lambda e: command(), add='+')
        widget.bind('<space>', lambda e: command(), add='+')
        if isinstance(widget, tk.Canvas):
            widget.bind('<FocusIn>',
                        lambda e: self._ring(widget, True, ring_color), add='+')
            widget.bind('<FocusOut>', lambda e: self._ring(widget, False), add='+')
        else:
            def fin(e):
                try:
                    widget.configure(highlightthickness=1,
                                     highlightbackground=self.colors['fg'],
                                     highlightcolor=self.colors['fg'])
                except Exception:
                    pass

            def fout(e):
                try:
                    widget.configure(highlightthickness=0)
                except Exception:
                    pass
            widget.bind('<FocusIn>', fin, add='+')
            widget.bind('<FocusOut>', fout, add='+')

    def _set_chain(self, win, widgets):
        """显式 Tab 链：Tk 默认遍历序是创建序，排不出设计稿要求的
        「＋ → 字标 → 各行开关 → 各行行尾」，所以自己接管 Tab / Shift+Tab。"""
        win._focus_chain = [w for w in widgets if w is not None]
        win.bind('<Tab>', lambda e: self._tab_step(win, 1), add='+')
        # Windows Tk 没有 ISO_Left_Tab 这个 keysym，Shift+Tab 走 Shift-Tab
        try:
            win.bind('<ISO_Left_Tab>', lambda e: self._tab_step(win, -1), add='+')
        except tk.TclError:
            win.bind('<Shift-Tab>', lambda e: self._tab_step(win, -1), add='+')

    def _tab_step(self, win, step):
        chain = [w for w in getattr(win, '_focus_chain', [])
                 if w.winfo_exists()]
        if not chain:
            return 'break'
        cur = win.focus_get()
        i = chain.index(cur) if cur in chain else (-1 if step > 0 else 0)
        chain[(i + step) % len(chain)].focus_set()
        return 'break'

    def _focus_brand(self):
        """浮层收起后焦点还给入口：右上角的「设置 / ←」。"""
        w = getattr(self, 'pane_btn', None)
        if w is not None and w.winfo_exists():
            try:
                w.focus_set()
            except Exception:
                pass

    def dialog(self, title, body, buttons, extra=None):
        """自绘模态弹框：动词按钮、焦点默认落在最后一个（安全）按钮、Esc = 安全按钮。

        buttons = [(文字, 返回值), ...]：第一枚是主动作（最右、实心），
        其余是安全动作。Enter 只触发当前焦点按钮，绝不默认触发危险动作。
        messagebox 的「确定 / 是」改不成动词，所以确认框一律走这里。
        """
        c = self.colors
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg=c['line'])
        try:
            win.attributes('-topmost', True)
        except Exception:
            pass
        if self.probe:
            win.attributes('-alpha', 0.0)
        inner = tk.Frame(win, bg=c['bg'], padx=self.px(20), pady=self.px(16))
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        tk.Label(inner, text=title, bg=c['bg'], fg=c['fg'], font=self.f_row,
                 anchor='w').pack(fill='x')
        tk.Label(inner, text=body, bg=c['bg'], fg=c['t2'], font=self.f_body,
                 anchor='w', justify='left',
                 wraplength=self.px(340)).pack(fill='x', pady=(self.px(8), 0))
        if extra:
            tk.Label(inner, text=extra, bg=c['bg'], fg=c['t3'],
                     font=self.f_small, anchor='w', justify='left',
                     wraplength=self.px(340)).pack(fill='x', pady=(self.px(8), 0))
        foot = tk.Frame(inner, bg=c['bg'])
        foot.pack(fill='x', pady=(self.px(16), 0))
        result = [buttons[-1][1]]

        def pick(val):
            result[0] = val
            try:
                win.grab_release()
            except Exception:
                pass
            win.destroy()

        picks = []
        for i, (text, val) in enumerate(buttons):
            b = self.capsule_button(foot, text, lambda v=val: pick(v),
                                    primary=(i == 0))
            b.pack(side='right', padx=(self.px(8), 0))
            picks.append(b)
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        par = self.parent()
        x = par.winfo_rootx() + max(0, (par.winfo_width() - w) // 2)
        y = par.winfo_rooty() + max(0, (par.winfo_height() - h) // 2)
        win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        win.bind('<Escape>', lambda e: pick(buttons[-1][1]))
        self._set_chain(win, picks)
        self._dialog_win = win
        self._dialog_picks = picks
        win.grab_set()
        picks[-1].focus_set()
        win.wait_window()
        self._dialog_win = None
        self._dialog_picks = []
        return result[0]

    def confirm(self, title, body, ok_text, extra=None):
        """L2 危险确认：标题带对象、正文讲后果与可逆、按钮用具体动词。"""
        return bool(self.dialog(title, body, [(ok_text, True), ('取消', False)],
                                extra=extra))

    def _error(self, title, body, can_retry=False):
        """错误弹框：人话 + 下一步，不给裸「确定」。返回用户是否选了「重试」。"""
        buttons = ([('重试', True), ('关闭', False)] if can_retry
                   else [('关闭', False)])
        return bool(self.dialog(title, body, buttons)) and can_retry

    def make_row(self, board, entry, i, card_pad, row_w, y):
        """一行：图标 / 名称 / 版本号 / 状态点 / 开关 / 行尾箭头。
        行自己是一块 Canvas，悬停底板画在它上面，子控件用 create_window 摆位。"""
        c = self.colors
        rh = self.row_h
        row = tk.Canvas(board, bg=self.row_base, highlightthickness=0, bd=0,
                        width=row_w, height=rh)
        board.create_window(card_pad, y, window=row, anchor='nw')
        row.w = row_w

        if i:
            row.create_line(0, 0.5, row_w, 0.5, fill=c['line'])

        icon_px = self.px(self.skin.metrics.get('icon', 16))
        pad_l = self.px(14)
        gap = self.px(10)
        # v2.15.0 状态点：开关说「你要不要」，点说「现在到底跑没跑」。
        dot_mode = self.plugin_dot
        dot = plugin_dot_state(entry, self.runtime_state.get(entry.id),
                               self.vars[entry.id].get()) \
            if dot_mode != 'off' else None
        dot_col = plugin_dot_color(c, dot) if dot else None
        # 角标档的关闭态不挂：图标本身就是灰的（t2×0.84），灰点贴上去只差
        # 一档亮度，7px 也分不开；开关已经把「这行是关的」说清楚了。
        icon_badge = dot_col if (dot_mode == 'badge' and dot != 'off') else None
        icon = Glyph(row, self, entry.icon, size=icon_px, badge=icon_badge)
        icon.set_bg(self.row_base)
        # 角标把 Canvas 撑到 icon_px + px(6) 且图标下移 px(6)，视觉中心跟着
        # 掉了 px(3)——y 减回来，六行图标才在同一条横线上。
        row.create_window(pad_l, rh // 2 - (self.px(3) if icon_badge else 0),
                          window=icon, anchor='w')

        sw_w = self.px(36)
        tail = self.px(22)
        tail_gap = self.px(4)
        name_x = pad_l + icon_px + gap
        # v2.12.1：版本号必须有地方露出来——行尾留一枚「v2.1.1」小字
        # （设计稿 .p-row .hint 的位置），展开后来源行再写全。
        ver_lbl = None
        dotv = None
        ver_w = self.px(44) if getattr(entry, 'version', '') else 0
        # 行内档的点槽 16px（5+6+5）从行尾 px(24) 的冗余里借 10px 出来：
        # 名称列 310→300，最长的「关机清理回收站」约 91px，仍然宽裕。
        # 另外两档不占宽，name_w 保持原样。
        dot_slot = self.px(10) if (dot_mode == 'row' and ver_w) else 0
        name_w = max(self.px(40), row_w - name_x - tail - tail_gap
                     - sw_w - self.px(24) - ver_w - dot_slot)
        name = tk.Label(row, text=entry.name, bg=self.row_base, fg=c['fg'],
                        font=self.f_row, anchor='w')
        row.create_window(name_x, rh // 2, window=name, anchor='w',
                          width=name_w, height=rh)
        if entry.tip:
            self.tooltips.append(ToolTip(self, name, entry.tip))
        if ver_w:
            # 版本号跟着点一起说话：异常提到 fg（不只靠红色传达），
            # 关闭退到 t4（整行安静下来）。点本身仍是主信号。
            ver_fg = c['t3']
            if dot == 'bad':
                ver_fg = c['fg']
            elif dot == 'off':
                ver_fg = c['t4']
            ver_lbl = tk.Label(row, text='v%s' % entry.version,
                               bg=self.row_base, fg=ver_fg,
                               font=self.f_small, anchor='e')
            ver_x = (row_w - self.px(10) - tail - tail_gap - sw_w
                     - self.px(6))
            row.create_window(ver_x, rh // 2, window=ver_lbl, anchor='e')
            if dot_mode == 'row' and dot:
                dotv = Dot(row, self, dot_col, size=self.px(6))
                dotv.set_bg(self.row_base)
                row.create_window(ver_x - ver_w - self.px(5), rh // 2,
                                  window=dotv, anchor='e')

        sw = Switch(row, self, value=self.vars[entry.id].get(),
                    command=self.on_toggle)
        sw.set_bg(self.row_base)
        row.create_window(row_w - self.px(10) - tail - tail_gap,
                          rh // 2, window=sw, anchor='e')
        if self.readonly:
            sw.set_enabled(False)
        self.switches[entry.id] = sw

        more = Glyph(row, self, 'chev_r', size=self.px(14))
        more.set_bg(self.row_base)
        row.create_window(row_w - self.px(12), rh // 2, window=more, anchor='e')
        self.plugin_gears[entry.id] = row
        row.configure(cursor='hand2')
        row.bind('<ButtonRelease-1>',
                 lambda e, ent=entry: self.toggle_inline(ent))
        self._keyboard(row, lambda ent=entry: self.toggle_inline(ent))
        # v2.10.0 修「点不开」：行面大部分被子控件盖住（Tk 事件不穿透
        # create_window），只绑 row 时点名称/图标/右箭头全无响应。
        # 开关除外——它有自己的 toggle，不能连带展开。
        # 状态点也是 create_window 的子控件，不绑就点不动（同一类坑）。
        clickable = [icon, name, more]
        if dotv is not None:
            clickable.append(dotv)
        for w in clickable:
            w.configure(cursor='hand2')
            w.bind('<ButtonRelease-1>',
                   lambda e, ent=entry: self.toggle_inline(ent))
        if ver_lbl is not None:
            ver_lbl.configure(cursor='hand2')
            ver_lbl.bind('<ButtonRelease-1>',
                         lambda e, ent=entry: self.toggle_inline(ent))

        kids = [icon, name, sw]
        if ver_lbl is not None:
            kids.append(ver_lbl)
        if dot_mode == 'row' and dot:
            kids.append(dotv)
        self.bind_row_hover(row, kids, None)
        return row, rh

    def bind_row_hover(self, row, kids, tail):
        """整行悬停：铺一层方角底板，子控件底色同步跟上（Tk 没有真透明）。"""
        c = self.colors
        rh = row.winfo_reqheight() or self.row_h
        w = getattr(row, 'w', 0) or row.winfo_reqwidth()

        def paint(on):
            row.delete('hl')
            col = c['hover'] if on else self.row_base
            if on:
                row.create_rectangle(1, 1, w - 1, rh - 1, fill=col,
                                     outline='', tags='hl')
                row.tag_lower('hl')
            for k in kids:
                if k is not None:
                    try:
                        k.set_bg(col)
                    except AttributeError:
                        k.configure(bg=col)
            if tail is not None:
                tail.configure(bg=col)
                try:
                    for sub in tail.winfo_children():
                        sub.set_bg(col)
                except Exception:
                    pass

        targets = [row] + [k for k in kids if k is not None]
        for t in targets:
            t.bind('<Enter>', lambda e: paint(True), add='+')
            t.bind('<Leave>', lambda e: paint(False), add='+')

    # ---- 极简档：行内展开 + 同窗换页 ----
    INLINE_MAX = 3          # 行内先摊前 3 项，「全部 N 项设置…」再摊全量

    def toggle_inline(self, entry):
        opening = self.inline_open != entry.id
        self.inline_open = entry.id if opening else None
        if not opening or self.inline_full != entry.id:
            self.inline_full = None
        self.close_plugin_settings()
        self.rebuild()

    def toggle_inline_full(self, entry):
        self.inline_full = None if self.inline_full == entry.id else entry.id
        self.rebuild()

    def make_inline(self, board, entry, card_pad, row_w, y):
        """行内展开：设置项直接摊在这行底下，不新建 Toplevel。

        极简档的性能账在这里：少一个窗口句柄、少一套 z 序、展开收合零动画，
        代价是列表会跳动（设计稿明确接受）。
        """
        # 设计稿 .p-expand 底色 #F7F7F7 / 深色 #181818，比窗口底深一档，
        # 但比"卡片"轻：极简档没有卡片阴影，全靠这一层色差说明归属。
        bg = self.tok['fill_2']
        c = self.colors
        box = tk.Frame(board, bg=bg)
        # 展开块缩进到名称列（图标 14 + 图标 22 + 间距 14 ≈ 40），
        # 与设计稿 .p-expand padding-left:40px 对齐，视觉上归属上面那一行。
        indent = tk.Frame(box, bg=bg)
        indent.pack(fill='both', expand=True, padx=(self.px(26), 0))
        cfg = effective_cfg(entries=self.entries_all)
        specs = list(getattr(entry, 'settings', None) or [])
        # 展开块的底色不是窗口底：让所有子控件工厂读到的 bg 暂时跟着换，
        # 否则 int/choice/path 这几类控件会画出白色方块。
        old_bg = self.colors['bg']
        self.colors['bg'] = bg
        self._setting_switches = {}
        try:
            full = self.inline_full == entry.id
            show = specs if full else specs[:self.INLINE_MAX]
            # 设计稿 .p-expand padding-top:8px；顶部留一点，展开块才不会贴住
            # 上一行的分隔线。
            tk.Frame(indent, bg=bg, height=self.px(4)).pack(fill='x')
            for spec in show:
                self.build_inline_item(indent, entry, spec, cfg, bg)
        finally:
            self.colors['bg'] = old_bg
        if len(specs) > self.INLINE_MAX:
            txt = ('收起额外设置' if full else '全部 %d 项设置…' % len(specs))
            ml = tk.Label(indent, text=txt, bg=bg, fg=c['t2'],
                          font=self.f_small, anchor='w', cursor='hand2',
                          padx=self.px(2), pady=self.px(2))
            ml.pack(fill='x', padx=self.px(14), pady=(self.px(2), self.px(2)))
            ml.bind('<Button-1>',
                    lambda e, ent=entry: self.toggle_inline_full(ent))
        if not specs:
            tk.Label(indent, text='这个插件没有可调项', bg=bg, fg=c['t3'],
                     font=self.f_small, anchor='w').pack(
                fill='x', padx=self.px(14), pady=self.px(6))
        # 来源一行：装完之后也能看见"这插件是哪一版、谁给的、要碰什么"。
        # v2.12.1：版本号放最前——极简档没有版本胶囊，这里是唯一能核对的地方。
        pub = (getattr(entry, 'publisher', '') or '').strip()
        perms = list(getattr(entry, 'permissions', None) or ())
        src_parts = []
        ver = (getattr(entry, 'version', '') or '').strip()
        src_parts.append('版本 v' + ver if ver else '版本未知')
        src_parts.append('发布者：' + (pub or '未声明（来源不明）'))
        if perms:
            labels = [plugin_package.PERMISSION_LABELS.get(p, p + '（未登记）')
                      for p in perms[:4]]
            src_parts.append('权限：' + '、'.join(labels))
        src = tk.Label(indent, text=' · '.join(src_parts), bg=bg, fg=c['t3'],
                       font=self.f_small, anchor='w', justify='left',
                       wraplength=max(self.px(220), row_w - self.px(80)))
        src.pack(fill='x', padx=self.px(14), pady=(self.px(2), self.px(2)))
        # 卸载：设计稿 .p-del 是带 1px 描边的方角按钮，不是一行红字。
        # 方角 + 描边把「破坏性操作」和普通设置项区分开，也离手指远一点。
        del_row = tk.Frame(indent, bg=bg)
        del_row.pack(fill='x', padx=self.px(14), pady=(self.px(6), self.px(8)))
        del_btn = tk.Frame(del_row, bg=bg, highlightthickness=1,
                           highlightbackground=c['line'],
                           highlightcolor=c['line'], cursor='hand2')
        del_btn.pack(anchor='w')
        rm = tk.Label(del_btn, text='卸载此插件', bg=bg, fg=c['danger'],
                      font=self.f_small, anchor='w', cursor='hand2',
                      padx=self.px(10), pady=self.px(4))
        rm.pack()
        hover_bg = mix_hex(bg, c['danger'], 0.08)

        def paint_del(on):
            col = hover_bg if on else bg
            del_btn.configure(bg=col)
            rm.configure(bg=col)

        for w in (del_btn, rm):
            w.bind('<ButtonRelease-1>',
                   lambda e, ent=entry: self.on_delete_plugin(ent))
            w.bind('<Enter>', lambda e: paint_del(True), add='+')
            w.bind('<Leave>', lambda e: paint_del(False), add='+')
        self._keyboard(rm, lambda ent=entry: self.on_delete_plugin(ent))
        box.update_idletasks()
        hh = max(self.px(30), box.winfo_reqheight())
        board.create_window(card_pad, y, window=box, anchor='nw',
                            width=row_w, height=hh)
        return hh

    def build_inline_item(self, master, entry, spec, cfg, bg):
        """极简档行内展开的一行（设计稿 .p-item：30 高、标签左、值右）。

        和浮层版 `build_setting_row` 的差别只有排版：不写第二行说明
        （hint 收进 Tooltip），开关用 30×16 的小号。这样 4 项设置只占
        120 px，展开时窗口不会一下子顶到屏幕上沿——旧版每项 60+ px，
        展开后窗口从 409 长到 969，直接把点中的那一行顶走。
        """
        c = dict(self.colors)
        c['bg'] = bg
        ro = self.readonly
        row = tk.Frame(master, bg=bg, height=self.px(30))
        row.pack(fill='x', padx=(self.px(14), self.px(32)))
        row.pack_propagate(False)
        lb = tk.Label(row, text=spec['label'], bg=bg,
                      fg=(c['t3'] if ro else c['fg']), font=self.f_body,
                      anchor='w')
        lb.pack(side='left', fill='y')
        if spec.get('hint'):
            self.tooltips.append(ToolTip(self, lb, spec['hint']))

        typ = spec['type']
        if typ == 'bool':
            cur = bool(cfg_get_path(cfg, spec['key'], False))
            sw = Switch(row, self, value=cur, small=True,
                        command=lambda sp=spec: self.on_setting_bool(
                            entry, sp, self.switch_of(sp['key'])))
            sw.set_bg(bg)
            sw.pack(side='right', padx=(self.px(8), 0))
            if ro:
                sw.set_enabled(False)
            self._setting_switches[spec['key']] = sw
            return row

        ink = c['t3'] if ro else c['fg']
        if typ == 'int':
            try:
                cur = int(cfg_get_path(cfg, spec['key'], spec['min']))
            except (TypeError, ValueError):
                cur = spec['min']
            cur = max(spec['min'], min(spec['max'], cur))
            unit = spec.get('unit') or ''
            state = [cur]
            var = tk.StringVar(row, value=('%d %s' % (cur, unit)).strip())
            plus = tk.Label(row, text='+', bg=bg, fg=ink, font=self.f_small,
                            padx=self.px(6), cursor=('arrow' if ro else 'hand2'))
            plus.pack(side='right')
            tk.Label(row, textvariable=var, bg=bg, fg=c['t3'],
                     font=self.f_small, anchor='e', width=6).pack(side='right')
            minus = tk.Label(row, text='−', bg=bg, fg=ink, font=self.f_small,
                             padx=self.px(6), cursor=('arrow' if ro else 'hand2'))
            minus.pack(side='right')

            def step(delta):
                new = max(spec['min'], min(spec['max'], state[0] + delta))
                if new == state[0]:
                    return
                if self.write_setting(entry, spec, new):
                    state[0] = new
                    var.set(('%d %s' % (new, unit)).strip())

            if not ro:
                minus.bind('<Button-1>', lambda e: step(-spec.get('step', 1)))
                plus.bind('<Button-1>', lambda e: step(spec.get('step', 1)))
            return row

        if typ == 'choice':
            try:
                cur = str(cfg_get_path(cfg, spec['key'], spec['options'][0][0]))
            except (KeyError, IndexError):
                cur = ''
            buttons = {}

            def paint(btn, on):
                btn.configure(bg=(c['fg'] if on else bg),
                              fg=(c['bg'] if on else c['t2']))

            def pick(value):
                if ro:
                    return
                if self.write_setting(entry, spec, value):
                    for v, b in buttons.items():
                        paint(b, v == value)

            for value, label in reversed(list(spec['options'])):
                b = tk.Label(row, text=label, font=self.f_small,
                             padx=self.px(6), pady=self.px(1),
                             cursor=('arrow' if ro else 'hand2'))
                b.pack(side='right', padx=(self.px(4), 0))
                buttons[value] = b
                paint(b, value == cur)
                if not ro:
                    b.bind('<Button-1>', lambda e, v=value: pick(v))
            return row

        # path：标签左、值右；值旁一枚「选择」，点它开文件对话框。
        cur = str(cfg_get_path(cfg, spec['key'], '') or '')
        shown = os.path.basename(cur) if cur else '自动查找'
        # 先 pack 按钮再 pack 值：pack 是"先占先得"，反了会把「选择」挤到
        # 文件名左边（值右对齐、按钮在最右，设计稿 p-item 的顺序）。
        btn = tk.Label(row, text='选择', bg=bg,
                       fg=(c['t3'] if ro else c['t2']), font=self.f_small,
                       cursor=('arrow' if ro else 'hand2'), padx=self.px(6))
        btn.pack(side='right')
        val = tk.Label(row, text=shown, bg=bg, fg=c['t3'],
                       font=self.f_small, anchor='e')
        val.pack(side='right')

        def choose():
            if ro:
                return
            try:
                path = filedialog.askopenfilename(
                    parent=self.root, title='选择「%s」' % spec['label'],
                    filetypes=[('程序', '*.exe'), ('所有文件', '*.*')])
            except Exception:
                return
            if not path:
                return
            if self.write_setting(entry, spec, path.replace('/', '\\')):
                val.configure(text=os.path.basename(path) or path)

        if not ro:
            btn.bind('<Button-1>', lambda e: choose())
            val.bind('<Button-1>', lambda e: choose())
        return row

    def toggle_pane(self):
        self.pane = 'set' if self.pane == 'list' else 'list'
        self.inline_open = None
        self.rebuild()

    def build_set_view(self, master):
        """极简档的设置页：同一个窗口换内容，不叠浮层。

        尺寸照 design/wbox-skins.html 的 .p-set：分组头 24 高、行固定 36 高、
        行内边距 0 12 0 14、值用等宽小字。行高必须写死而不是靠 pady 撑——
        靠 pady 撑出来的行只有 27 左右，整页会显得比列表页挤。
        v2.9.0：套 Canvas 视口，矮屏溢出时滚轮可达（设计稿「不建滚动容器」
        指不叠独立滚动窗，视口在不超屏时与整块 Frame 无异）。
        """
        c = self.colors
        bg = c['bg']
        outer = tk.Frame(master, bg=bg)
        outer.pack(fill='both', expand=True)
        # 设置页也套一层 Canvas 视口：不超屏时和整块 Frame 无异（窗口按内容长），
        # 超屏时滚轮可达，避免「窗口高度按内容长」在矮屏上把关于组截掉。
        cv = tk.Canvas(outer, bg=bg, highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        wrap = tk.Frame(cv, bg=bg)
        self.set_canvas = cv
        self.set_inner = wrap
        self._set_window_id = cv.create_window(0, 0, window=wrap, anchor='nw')
        pad_l = self.px(14)
        pad_r = self.px(12)
        self._set_focusables = []
        cfg = effective_cfg(entries=self.entries_all)
        try:
            mono = tkfont.Font(root=self.root, family='Consolas',
                               size=-self.px(11))
        except Exception:
            mono = self.f_small

        def group(title):
            box = tk.Frame(wrap, bg=bg, height=self.px(24))
            box.pack(fill='x')
            box.pack_propagate(False)
            tk.Label(box, text=title, bg=bg, fg=c['t3'], font=self.f_small,
                     anchor='sw', padx=pad_l, pady=self.px(3)).pack(
                fill='both', expand=True)
            # 分组头下面**不画**分隔线：设计稿里 .p-grp 没有 border，
            # 每一行自带 border-bottom，多画这一条会变成"头-线-行-线"的杂音。

        def row_box():
            row = tk.Frame(wrap, bg=bg, height=self.px(36))
            row.pack(fill='x')
            row.pack_propagate(False)
            return row

        def line(text, value='', command=None, accent=False):
            row = row_box()
            lb = tk.Label(row, text=text, bg=bg, fg=c['fg'], font=self.f_body,
                          anchor='w', padx=pad_l)
            lb.pack(side='left', fill='y')
            # accent=True 只给「守护状态」：那是这页里唯一一条「现在活着」的
            # 事实，用强调色与开关同源；其余行的值保持 t3，不抢注意力。
            val = tk.Label(row, text=value, bg=bg,
                           fg=(c['inv'] if accent else c['t3']),
                           font=mono, anchor='e', padx=pad_r)
            val.pack(side='right', fill='y')
            if command:
                for w in (row, lb, val):
                    w.configure(cursor='hand2')
                    w.bind('<ButtonRelease-1>', lambda e: command())
                    w.bind('<Enter>', lambda e: [x.configure(bg=c['hover'])
                                                 for x in (row, lb, val)], add='+')
                    w.bind('<Leave>', lambda e: [x.configure(bg=bg)
                                                 for x in (row, lb, val)], add='+')
                self._keyboard(row, command)
                self._set_focusables.append(row)
            tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
            # 值标签挂到行上：设置页有一批「会变的事实」（守护状态/进程事件/
            # 插件健康），刷新只该改这几个 label 的文字，不整页重建——重建会
            # 丢焦点、跳滚动位置，用户正看着的那一行会跑掉。
            row.value_label = val
            return row

        def radio_line(text, value, on, command, disabled=False):
            """设计稿 .p-radio：13 px 圆圈 + 居中 7 px 实心点。

            皮肤与明暗都走这一种行：圆圈比「● / ○」字符轻，也不会因
            字体不同而错位（字符方案在 125% 缩放下大小不一）。
            """
            row = row_box()
            rad = tk.Canvas(row, width=self.px(13), height=self.px(13),
                            bg=bg, highlightthickness=0, bd=0)
            rad.pack(side='left', padx=(pad_l, self.px(10)))
            # v2.15.0 B 案：选中圈（环 + 点）吃强调色。
            ring = c['t4'] if not on else c['inv']
            rad.create_oval(0.5, 0.5, self.px(13) - 0.5, self.px(13) - 0.5,
                            outline=ring, width=1)
            if on:
                ins = self.px(3)
                rad.create_oval(ins, ins, self.px(13) - ins,
                                self.px(13) - ins, fill=c['inv'], outline='')
            lb = tk.Label(row, text=text, bg=bg,
                          fg=(c['t3'] if disabled else c['fg']),
                          font=self.f_body, anchor='w')
            lb.pack(side='left', fill='y')
            # v2.15.0 B 案：「当前」吃强调色。非选中行的性能标签（最省 /
            # 均衡）仍是 t3——它只是说明，不该抢。
            val_fg = c['danger'] if disabled else (c['inv'] if on else c['t3'])
            val = tk.Label(row, text=value, bg=bg, fg=val_fg,
                           font=mono, anchor='e', padx=pad_r)
            val.pack(side='right', fill='y')
            if command is not None and not disabled:
                for w in (row, rad, lb, val):
                    w.configure(cursor='hand2')
                    w.bind('<ButtonRelease-1>', lambda e: command())
                    w.bind('<Enter>', lambda e: [x.configure(bg=c['hover'])
                                                 for x in (row, lb, val)],
                           add='+')
                    w.bind('<Leave>', lambda e: [x.configure(bg=bg)
                                                 for x in (row, lb, val)],
                           add='+')
                self._keyboard(row, command)
                self._set_focusables.append(row)
            tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
            return row

        def seg_row(label, options, cur_key, command):
            """`标签 … [ 选项 | 选项 ]` 一行：明暗与窗口大小共用。

            选中的那一格用强调色底 / 反色字（v2.15.0 前是 fg 底 / bg 字）。
            默认皮肤的强调色就是近黑，所以观感仍是黑白翻转；配色主题才会显色。
            """
            row = row_box()
            tk.Label(row, text=label, bg=bg, fg=c['fg'], font=self.f_body,
                     anchor='w', padx=pad_l).pack(side='left', fill='y')
            seg = tk.Frame(row, bg=c['fill'])
            seg.pack(side='right', padx=pad_r)
            for key, lab in options:
                on = key == cur_key
                # v2.15.0 B 案：选中格改成强调色底 + invfg 字。默认皮肤里
                # inv/invfg 就是 fg/bg 那一对，所以老观感不变。
                b = tk.Label(seg, text=lab, font=self.f_small,
                             padx=self.px(8), pady=self.px(2),
                             bg=(c['inv'] if on else c['fill']),
                             fg=(c['invfg'] if on else c['t2']), cursor='hand2')
                b.pack(side='left')
                b.bind('<ButtonRelease-1>', lambda e, k=key: command(k))
                self._keyboard(b, lambda k=key: command(k))
                self._set_focusables.append(b)
            tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
            return row

        group('外观')
        for item in skins.catalog(BASE):
            if item['reason'] == 'api':
                radio_line(item['name'], '需要更新', False, None, disabled=True)
                continue
            sk_ = item['skin']
            cur = sk_.id == self.skin.id
            radio_line(sk_.name, '当前' if cur else sk_.perf_label, cur,
                       lambda i=sk_.id: self.set_skin(i))
        line('从文件安装皮肤…', '.wbskin / .zip', self.on_install_skin)

        # v2.12.1：明暗（黑/白）必须在设置页里就能选。之前只在「外观与皮肤…」
        # 浮层里有，用户进了极简设置页只能看到白色，等于黑色入口消失了。
        seg_row('明暗', (('system', '跟随系统'), ('light', '浅色'),
                         ('dark', '深色')),
                self.appearance_pref, self.set_theme)
        # v2.12.0：窗口大小三档。和明暗同一排法，选完按新倍率重建当前页。
        seg_row('窗口大小', tuple((k, lab) for k, lab, _z in UI_SIZES),
                self.ui_size, self.set_ui_size)
        # v2.15.0：状态点挂法。默认「行内」，多数人不用进这一页；
        # 「不显示」留着——有人就是不要任何额外的点。
        seg_row('插件状态点', (('row', '行内'), ('badge', '角标'),
                             ('off', '不显示')),
                self.plugin_dot, self.set_plugin_dot)

        group('启动与守护')
        boot_line = row_box()
        tk.Label(boot_line, text='开机自动启动', bg=bg, fg=c['fg'],
                 font=self.f_body, anchor='w', padx=pad_l).pack(
            side='left', fill='y')
        bsw = Switch(boot_line, self, value=self.boot_var.get(),
                     command=self.on_boot_toggle)
        bsw.set_bg(bg)
        bsw.pack(side='right', padx=pad_r)
        self._set_focusables.append(bsw)
        tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
        for key, text, var, cmd in (
                ('tray', '启动后收进托盘', self.tray_var, self.on_tray_toggle),
                ('notify', '游戏结束提示', self.notify_var,
                 self.on_notify_toggle)):
            trow = row_box()
            tk.Label(trow, text=text, bg=bg, fg=c['fg'], font=self.f_body,
                     anchor='w', padx=pad_l).pack(side='left', fill='y')
            tsw = Switch(trow, self, value=bool(var.get()), command=cmd)
            tsw.set_bg(bg)
            tsw.pack(side='right', padx=pad_r)
            self.switches[key] = tsw
            self._set_focusables.append(tsw)
            tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
        # v2.16.0：「守护状态」右侧带一枚启停按钮。
        # 以前这一行只会说「守护没在运行」——用户看得到问题，却没有解决它的
        # 入口（启动藏在二级菜单里，默认还收进托盘）。状态和「能怎么办」
        # 放同一行：看到问题的地方就是能下手的地方。
        snap0 = guard_snapshot(self.probe)
        self.set_guard = None

        def guard_line(snap):
            """守护状态 + 启停按钮。按钮按状态换装（见 _paint_guard_action）。"""
            row = row_box()
            tk.Label(row, text='守护状态', bg=bg, fg=c['fg'], font=self.f_body,
                     anchor='w', padx=pad_l).pack(side='left', fill='y')
            act = tk.Label(row, font=self.f_small, padx=self.px(10),
                           pady=self.px(1))
            # pack 先来先占：按钮先 pack 才贴最右，值再排它左边。
            act.pack(side='right', padx=(self.px(6), pad_r))
            val = tk.Label(row, text=snap['state'], bg=bg, fg=c['inv'],
                           font=mono, anchor='e')
            val.pack(side='right', fill='y')
            self.set_guard = {'row': row, 'val': val, 'act': act}
            self._paint_guard_action(snap)
            act.bind('<Button-1>', lambda e: self.on_guard_action())
            act.bind('<Enter>', lambda e: self._hover_guard_action(True),
                     add='+')
            act.bind('<Leave>', lambda e: self._hover_guard_action(False),
                     add='+')
            self._keyboard(row, self.on_guard_action)
            self._set_focusables.append(row)
            tk.Frame(wrap, bg=c['line'], height=1).pack(fill='x')
            return row

        guard_line(snap0)
        # v2.11.3：把"守护活着"拆成两件可核对的事——进程事件通道是否在工作、
        # 插件是否都加载起来了。两者都不影响守护进程存在，但都影响功能是否生效。
        ev_row = line('进程事件', snap0['events'])
        pl_row = line('插件健康', snap0['plugins'])
        self.set_guard['ev'] = ev_row.value_label
        self.set_guard['pl'] = pl_row.value_label
        if snap0['note']:
            tk.Label(wrap, text=snap0['note'], bg=bg, fg=c['t3'],
                     font=self.f_small, anchor='w', justify='left',
                     wraplength=self.px(250)).pack(
                fill='x', padx=pad_l, pady=(0, self.px(4)))

        group('内核')
        line('游戏检测间隔', '%d 毫秒' % int(cfg.get('poll_idle_ms') or 2000))
        line('日志保留', '30 天')

        group('关于')
        line('WBOX', VERSION)
        line('打开日志目录', '', self.on_log)
        line('编辑配置文件', '', self.on_cfg)
        # v2.18.0：齿轮浮层删除后，「导出诊断」原本只在那个不可达的菜单里，
        # 这边补回设置页——诊断包是排障用的，不该因为换个入口就消失。
        line('导出诊断', '', self.on_diag)
        # 窗口按内容长高，最后一行贴着窗口边会看起来像被裁掉：留一条呼吸位
        tk.Frame(wrap, bg=bg, height=self.px(10)).pack(fill='x')

    def _bind_wheel(self, cv, inner):
        def scroll(ev):
            step = int(-ev.delta / 120) or (-1 if ev.delta > 0 else 1)
            cv.yview_scroll(step, 'units')
            return 'break'
        for w in (cv, inner) + tuple(walk(inner)):
            try:
                w.bind('<MouseWheel>', scroll)
            except Exception:
                pass

    def open_skin_settings(self):
        """皮肤面板：卡片列所有皮肤 + 明暗分段 + 安装/卸载。

        与插件设置浮层同一套规矩：无标题栏浮层、topmost、点外部收起。
        点皮肤立即换（不等「保存」），换完面板留在原地——用户可以连着试两套再决定。
        内容超屏时走 Canvas 视口滚轮，不截断（v2.9.0 对齐设计稿）。
        """
        if getattr(self, 'skin_win', None) and self.skin_win.winfo_exists():
            self.place_skin_settings()
            return
        self.close_plugin_settings()
        c = self.colors
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        win.configure(bg=c['line'])
        win.resizable(False, False)
        try:
            win.wm_attributes('-topmost', True)
        except Exception:
            pass
        if self.probe:
            try:
                win.attributes('-alpha', 0.0)
            except Exception:
                pass
        c = self.popup_colors('pop_base')
        outer = tk.Frame(win, bg=c['line'], padx=1, pady=1)
        outer.pack(fill='both', expand=True)
        cv = tk.Canvas(outer, bg=c['bg'], highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        body = tk.Frame(cv, bg=c['bg'])
        self.skin_win, self.skin_body, self.skin_outer = win, body, outer
        self.skin_canvas = cv
        self.skin_window_id = cv.create_window(0, 0, window=body, anchor='nw')
        self.build_skin_list(body)
        self.place_skin_settings()
        win.bind('<Escape>', lambda e: self.close_skin_settings())
        self._pop_in(win)

    def _draw_skin_thumb(self, cv, st, w, h):
        """112×63 迷你窗口缩略图：全部用该皮肤自己的 token 画，不用截图。"""
        s = w / 112.0
        cv.create_rectangle(0, 0, w, h, fill=st['glass'], width=0)
        for i in range(3):
            x = (10 + i * 8) * s
            cv.create_oval(x - 2 * s, 6 * s, x + 2 * s, 10 * s,
                           fill=st['t3'], width=0)
        round_rect(cv, w - 30 * s, 5 * s, w - 10 * s, 11 * s, 3 * s,
                   fill=st['fill_2'], width=0)
        cv.create_line(0, 17 * s, w, 17 * s, fill=st['hair'])
        for i, y in enumerate((25, 41)):
            yy = y * s
            round_rect(cv, 10 * s, yy, w * 0.5, yy + 7 * s, 3 * s,
                       fill=st['t2'] if i == 0 else st['t3'], width=0)
            on = i == 0
            tx0, tx1 = w - 34 * s, w - 10 * s
            round_rect(cv, tx0, yy - 1 * s, tx1, yy + 9 * s, 5 * s,
                       fill=st['on'] if on else st['switch_off'], width=0)
            kx = tx1 - 5 * s if on else tx0 + 5 * s
            cv.create_oval(kx - 4 * s, yy, kx + 4 * s, yy + 8 * s,
                           fill=st['on_knob'] if on else st['knob'], width=0)

    def build_skin_list(self, body):
        c = self.colors
        chain = []
        tk.Frame(body, height=self.px(6), bg=c['bg']).pack(fill='x')
        tk.Label(body, text='外观', bg=c['bg'], fg=c['t3'], font=self.f_small,
                 anchor='w').pack(fill='x', padx=self.px(14))
        for item in skins.catalog(BASE, log=self.note):
            chain.append(self._skin_card(body, item))
        tk.Label(body, text='明暗', bg=c['bg'], fg=c['t3'], font=self.f_small,
                 anchor='w').pack(fill='x', padx=self.px(14),
                                  pady=(self.px(8), 0))
        seg = tk.Frame(body, bg=c['bg'])
        seg.pack(fill='x', padx=self.px(14), pady=(self.px(4), self.px(6)))
        for key, label in (('system', '跟随系统'), ('light', '浅色'),
                           ('dark', '深色')):
            chain.append(self.capsule_button(
                seg, label, lambda k=key: self.on_pick_appearance(k),
                primary=(self.appearance_pref == key)))
            chain[-1].pack(side='left', padx=(0, self.px(6)))
        # v2.12.0：窗口大小。三段与明暗同一种胶囊，默认「小」= 升级前的尺寸。
        tk.Label(body, text='窗口大小', bg=c['bg'], fg=c['t3'],
                 font=self.f_small, anchor='w').pack(
                     fill='x', padx=self.px(14), pady=(self.px(8), 0))
        size_seg = tk.Frame(body, bg=c['bg'])
        size_seg.pack(fill='x', padx=self.px(14),
                      pady=(self.px(4), self.px(6)))
        for key, label, _zoom in UI_SIZES:
            chain.append(self.capsule_button(
                size_seg, label, lambda k=key: self.on_pick_ui_size(k),
                primary=(self.ui_size == key)))
            chain[-1].pack(side='left', padx=(0, self.px(6)))
        self.sep(body)

        act = tk.Frame(body, bg=c['bg'], padx=self.px(14), pady=self.px(8))
        act.pack(fill='x')
        install = tk.Label(act, text='安装皮肤（.wbskin / .zip）…', bg=c['bg'],
                           fg=c['t2'], font=self.f_small, cursor='hand2')
        install.pack(side='left')
        for ev, col in (('<Enter>', c['fg']), ('<Leave>', c['t2'])):
            install.bind(ev, lambda e, x=col: e.widget.configure(fg=x))
        install.bind('<Button-1>', lambda e: self.on_install_skin())
        self._keyboard(install, self.on_install_skin)
        chain.append(install)
        # 内置皮肤在磁盘上没有目录，不给「卸载」；用户装的才给
        if not self.skin.builtin:
            unins = tk.Label(act, text='卸载此皮肤', bg=c['bg'], fg=c['danger'],
                             font=self.f_small, cursor='hand2')
            unins.pack(side='right')
            unins.bind('<Button-1>', lambda e: self.on_uninstall_skin())
            self._keyboard(unins, self.on_uninstall_skin)
            chain.append(unins)
        if getattr(self, 'skin_win', None) is not None:
            self._set_chain(self.skin_win, chain)

    def _skin_card(self, body, item):
        """一张皮肤卡：缩略图 + 名称/性能pill + 来源·版本·描述 + 当前勾。

        reason='api' 的卡画「需要更新」pill、缩略图灰化、点了不生效
        （SKIN-SPEC §10：api 过新的皮肤要露出来告诉用户升级，不能装死）。
        """
        c = self.colors
        skin = item['skin']
        too_new = item['reason'] == 'api'
        on = skin is not None and skin.id == self.skin.id
        row = tk.Frame(body, bg=c['bg'],
                       cursor='arrow' if too_new else 'hand2')
        row.pack(fill='x', padx=self.px(10), pady=self.px(3))
        card = tk.Frame(row, bg=c['bg'], padx=self.px(10), pady=self.px(8),
                        highlightbackground=c['fg'] if on else c['hair'],
                        highlightcolor=c['fg'] if on else c['hair'],
                        highlightthickness=self.px(2) if on else 1)
        card.pack(fill='x')
        tw, th = self.px(112), self.px(63)
        thumb = tk.Canvas(card, width=tw, height=th, bg=c['bg'],
                          highlightthickness=0, bd=0)
        thumb.pack(side='left')
        if skin is not None:
            st = skin.tokens(self.theme)
        else:
            st = dict(self.tok)
            st.update(fg=st['t3'], t2=st['t4'], t3=st['t4'],
                      on=st['switch_off'], on_knob=st['knob'])
        self._draw_skin_thumb(thumb, st, tw, th)
        if on:
            Glyph(card, self, 'check', size=self.px(16)).pack(
                side='right', padx=(self.px(6), 0))
        txt = tk.Frame(card, bg=c['bg'])
        txt.pack(side='left', fill='both', expand=True, padx=(self.px(12), 0))
        head = tk.Frame(txt, bg=c['bg'])
        head.pack(fill='x')
        tk.Label(head, text=item['name'], bg=c['bg'],
                 fg=c['fg'] if not too_new else c['t3'],
                 font=self.f_body, anchor='w').pack(side='left')
        # 性能标签是硬要求（SKIN-SPEC 5）：用户有权知道这套皮肤贵不贵
        pill = '需要更新' if too_new else skin.perf_label
        tk.Label(head, text=pill, bg=c['fill'],
                 fg=c['danger'] if too_new else c['t2'],
                 font=self.f_small, padx=self.px(6), pady=self.px(1)).pack(
                     side='left', padx=(self.px(8), 0))
        if too_new:
            sub = '要求 api %d，当前面板只支持 api %d · 更新面板后可用' % (
                item['api'], skins.API_VERSION)
        else:
            sub = '%s · v%s · %s' % ('内置' if item['builtin'] else '第三方',
                                    item['version'], item['description'])
        tk.Label(txt, text=sub, bg=c['bg'], fg=c['t3'], font=self.f_small,
                 anchor='w', justify='left', wraplength=self.px(330)).pack(
                     fill='x', pady=(self.px(4), 0))
        if too_new:
            def reject(e, it=item):
                self.note('皮肤 %s 要求 api %d（当前 %d），未启用'
                          % (it['id'], it['api'], skins.API_VERSION))
            for w in (row, card, thumb, txt, head) + tuple(head.winfo_children()):
                w.bind('<Button-1>', reject)
            self._keyboard(row, lambda it=item: reject(None, it))
            return row
        for w in (row, card, thumb, txt, head) + tuple(head.winfo_children()):
            w.bind('<Button-1>', lambda e, d=item['id']: self.on_pick_skin(d))
        self._keyboard(row, lambda d=item['id']: self.on_pick_skin(d))
        if not on:
            card.bind('<Enter>', lambda e: card.configure(
                highlightbackground=c['t3']))
            card.bind('<Leave>', lambda e: card.configure(
                highlightbackground=c['hair']))
        return row

    def on_pick_skin(self, skin_id):
        self.set_skin(skin_id)
        self.refresh_skin_list()

    def on_pick_appearance(self, key):
        self.set_theme(key)
        self.refresh_skin_list()

    def on_pick_ui_size(self, key):
        """点窗口大小：set_ui_size 内部重建，皮肤面板随 rebuild 一起刷新。"""
        self.set_ui_size(key)

    def refresh_skin_list(self):
        """换完皮肤后重建面板内容：勾要跟着走，配色也是新的。"""
        body = getattr(self, 'skin_body', None)
        if not (body and body.winfo_exists()):
            return
        for w in list(body.winfo_children()):
            w.destroy()
        self.build_skin_list(body)
        try:
            self.skin_outer.configure(bg=self.colors['line'])
            self.skin_win.configure(bg=self.colors['line'])
            if self.skin_canvas is not None:
                self.skin_canvas.configure(bg=self.colors['bg'])
        except Exception:
            pass
        self.place_skin_settings()

    def on_install_skin(self):
        while True:
            path = filedialog.askopenfilename(
                title='选择皮肤文件', parent=self.parent(),
                # v2.15.0：.wbskin 本身就是 zip，包里多出来的这个后缀只是
                # 品牌；作者直接用 .zip 分发很自然，别逼他改名或切「所有文件」。
                filetypes=[('皮肤包', '*.wbskin *.zip'),
                           ('所有文件', '*.*')])
            if not path:
                return
            try:
                skin = skin_package.install_zip(path, BASE, overwrite=False,
                                                log=self.note)
                break
            except Exception as e:
                # 拒绝原因已由 skin_package 记进日志（skin.reject reason=…）
                if not self.dialog('未能安装',
                                   '皮肤没能安装：%s\n'
                                   '请确认文件完整后重试，或换一个文件。' % e,
                                   [('重新选择', True), ('关闭', False)]):
                    return
        self.set_skin(skin.id)
        self.refresh_skin_list()
        self.dialog('已安装', '皮肤「%s」已装好并启用。' % skin.name,
                    [('好', True)])

    def on_uninstall_skin(self):
        if self.skin.builtin:
            return
        if not self.confirm('卸载皮肤',
                            '卸载「%s」后外观会回到极简。要卸载吗？' % self.skin.name,
                            '卸载'):
            return
        sid = self.skin.id
        while True:
            try:
                skin_package.remove_skin(sid, BASE)
                break
            except Exception as e:
                self.note('卸载皮肤失败：%r' % e)
                if not self._error('未能卸载',
                                   '皮肤没能卸载：%s\n请稍后重试，详情可查看日志。' % e,
                                   can_retry=True):
                    return
        self.set_skin(skins.FALLBACK_ID)
        self.refresh_skin_list()

    def place_skin_settings(self):
        """贴字标下方、左沿对齐展开；内容超屏改滚轮，不截断卡片。"""
        win = getattr(self, 'skin_win', None)
        body = getattr(self, 'skin_body', None)
        cv = getattr(self, 'skin_canvas', None)
        if not (win and body and win.winfo_exists()):
            return
        win.update_idletasks()
        lx, ly, sw, sh = work_area()
        w = min(self.px(600), max(self.px(260), sw - self.px(20)))
        need_h = body.winfo_reqheight() + 2
        anchor = self.brand_wrap if (self.brand_wrap and
                                     self.brand_wrap.winfo_exists()) else self.root
        ax, ay = anchor.winfo_rootx(), anchor.winfo_rooty()
        x = max(lx + self.px(8), min(ax, lx + sw - w - self.px(8)))
        below = ay + anchor.winfo_height() + self.px(4)
        avail = max(self.px(160), ly + sh - below - self.px(8))
        h = min(need_h, self.px(560), avail)
        win.geometry('%dx%d+%d+%d' % (w, h, x, below))
        if cv is not None and self.skin_window_id is not None:
            cv.itemconfigure(self.skin_window_id, width=w - 2)
            win.update_idletasks()
            cv.configure(scrollregion=(0, 0, w - 2, body.winfo_reqheight()))
            self._bind_wheel(cv, body)
        try:
            win.lift()
            win.wm_attributes('-topmost', True)
        except Exception:
            pass

    def close_skin_settings(self):
        win = getattr(self, 'skin_win', None)
        if win is not None:
            self.cancel_tween(('pop', id(win)))
        self.skin_win = None
        self.skin_canvas = None
        self.skin_window_id = None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        self._focus_brand()

    def on_uninstall_from_popup(self, entry):
        """浮层里的卸载：先收浮层，再走原来的确认流程（含备份）。"""
        self.close_plugin_settings()
        self.on_delete_plugin(entry)

    # ---- 行为 ----
    def parent(self):
        # 弹框一律挂主窗口：浮层是无标题栏 Toplevel，挂它一旦关闭弹框会跟着没
        return self.root

    def refresh(self):
        # 一次心跳只开一次管道（400 ms 预算）：设置页三行、启停按钮、托盘
        # 监控全部吃同一份快照，各连一次会肉眼看得出迟钝。
        snap = guard_snapshot(self.probe)
        self._sync_set_guard(snap)
        self._tray_watch()
        if not self.probe:
            self.root.after(2000, self.refresh)

    # ---- 设置页上的守护启停 ----
    def _paint_guard_action(self, snap):
        """启停按钮换装。

        没运行 → 强调色底（这页唯一"该立刻做的事"，和分段控件选中格同源）；
        运行中 → fill 底 + t2 字（停止是次要动作，不该一直有块黑在那儿抢眼）；
        刚点完 → 「启动中… / 停止中…」，等心跳反映过来。
        """
        g = getattr(self, 'set_guard', None)
        if not g:
            return
        c = self.colors
        run = bool(snap.get('running'))
        want = self._guard_want
        if run:
            text = '停止中…' if want == 'down' else '停止守护'
            bgc, fgc = c['fill'], c['t2']
        elif want == 'up':
            text, bgc, fgc = '启动中…', c['fill'], c['t3']
        else:
            text, bgc, fgc = '启动守护', c['inv'], c['invfg']
        if self.readonly and run:
            fgc = c['t4']              # 只读：停止不可用，别画成可点的样子
        try:
            g['act'].configure(
                text=text, bg=bgc, fg=fgc,
                cursor=('arrow' if (self.readonly and run) else 'hand2'))
        except Exception:
            pass

    def _hover_guard_action(self, on):
        g = getattr(self, 'set_guard', None)
        if not g:
            return
        c = self.colors
        try:
            stopping = g['act'].cget('text').startswith('停止')
            if self.readonly and stopping:
                return
            if stopping:
                g['act'].configure(bg=(c['line'] if on else c['fill']))
            else:
                g['act'].configure(
                    bg=(mix_hex(c['inv'], c['bg'], 0.15) if on else c['inv']))
        except Exception:
            pass

    def on_guard_action(self):
        """设置页的守护一键启停：没运行就启动，运行中就走停止（含确认）。

        启动不需要二次确认（恢复动作），停止仍然要——它会暂停所有插件。
        返回 False 表示这一步没做成（用户取消 / 写入失败）：按钮立刻回到
        真实状态，不显示「启动中…」这种没兑现的进行态。
        """
        run = guard_running()
        if run and self.readonly:
            self.dialog('需要管理员权限',
                        '停止守护需要在安装目录里写一个标记文件，当前权限不够。\n'
                        '请右键程序图标，以管理员身份重新打开后再试。',
                        [('知道了', True)])
            return
        if run:
            if not self.on_stop():
                self._guard_want = None
                self._sync_set_guard()
                return
            self._guard_want = 'down'
        else:
            if not self.on_start():
                self._guard_want = None
                self._sync_set_guard()
                return
            self._guard_want = 'up'
        self._guard_since = time.time()
        self._sync_set_guard()
        # 起/停不是瞬时的：守护要先被计划任务拉起来、写第一拍心跳，
        # 或者收到 stop.flag 后自行退出。这几秒加密刷新，别让用户干等。
        self.root.after(700, self._guard_poll)

    def _guard_poll(self, tries=10):
        g = getattr(self, 'set_guard', None)
        try:
            alive = bool(g and g['row'].winfo_exists())
        except Exception:
            alive = False
        if not alive:
            self._guard_want = None
            return
        snap = guard_snapshot(self.probe)
        self._sync_set_guard(snap)
        want = self._guard_want
        done = (want == 'up' and snap['running']) or \
               (want == 'down' and not snap['running'])
        if done or want is None or tries <= 0:
            self._guard_want = None
            self._sync_set_guard(snap)
            return
        self.root.after(600, lambda: self._guard_poll(tries - 1))

    def _sync_set_guard(self, snap=None):
        """把设置页的守护三行刷成最新事实（每 2 秒一次）。

        设置页是常驻内容不是弹框：点了启动，守护要一两秒才写心跳，没有这条
        刷新，页面会永远停在「守护没在运行」，用户以为没点成功。
        只改文字不重建控件——重建会丢焦点、跳滚动位置。
        """
        g = getattr(self, 'set_guard', None)
        if not g:
            return
        try:
            if not g['row'].winfo_exists():
                return
        except Exception:
            return
        # 起/停 8 秒还没变成想要的状态：不再显示「启动中…」，交回真实状态，
        # 免得按钮一直卡在一个假的进行态（失败的原因由 on_start 自己弹框讲）。
        if self._guard_want and time.time() - self._guard_since > 8:
            self._guard_want = None
        if snap is None:
            snap = guard_snapshot(self.probe)
        try:
            g['val'].configure(text=snap['state'])
            g['ev'].configure(text=snap['events'])
            g['pl'].configure(text=snap['plugins'])
        except Exception:
            return
        self._paint_guard_action(snap)

    def save_features(self, show_msg=False, quiet=False):
        """把各插件开关 + 主题写回 config.json。

        开关的落点由插件自己的 gui_toggle() 决定（ace 一个开关写两处），
        面板不再知道 `afterburner_enabled` / `ace_*` 这些具体键名。
        必须用 load_cfg() 读回整份再写：只写开关会抹掉路径、RTSS 名单等参数
        （test_throttle_state.py 第 [9] 组盯着这条）。
        """
        if self.readonly:
            if not quiet:
                self.dialog('需要管理员权限',
                            '这项改动需要管理员权限。\n'
                            '请右键程序图标，以管理员身份重新打开后再试。',
                            [('知道了', True)])
            return False
        try:
            cfg = load_cfg()
            for entry in self.entries:
                plugin_switch_write(entry, cfg, bool(self.vars[entry.id].get()))
            save_cfg(cfg)
            if show_msg:
                self.dialog('已保存', '设置已保存，无需重启。', [('好', True)])
            return True
        except Exception as e:
            self.note('保存设置失败：%r' % e)
            if self._error('未能保存',
                           '设置没能保存。请检查是否有写入权限后重试，详情可查看日志。',
                           can_retry=True):
                return self.save_features(show_msg=show_msg, quiet=quiet)
            return False

    def on_toggle(self):
        # 记录点击前的状态：写盘失败必须把开关视觉状态回滚，
        # 不允许出现"开关动了但 config.json 没变"的假象。
        before = {k: bool(v.get()) for k, v in self.vars.items()}
        for key, sw in self.switches.items():
            if key in self.vars:
                self.vars[key].set(sw.value)
        if self.save_features():
            return
        for key, value in before.items():
            self.vars[key].set(value)
            sw = self.switches.get(key)
            if sw is not None:
                sw.set_value(value)

    # ---- 托盘与游戏结束提示（v2.9.0，设计稿设置页「启动与守护」） ----
    def on_tray_toggle(self):
        sw = self.switches.get('tray')
        val = bool(sw.value) if sw is not None else bool(self.tray_var.get())
        self.tray_var.set(val)
        if self.probe:
            return
        if not self.write_setting(None, {'key': 'start_in_tray'}, val):
            self.tray_var.set(not val)
            if sw is not None:
                sw.set_value(not val)
            return
        self.apply_tray_pref()

    def on_notify_toggle(self):
        sw = self.switches.get('notify')
        val = bool(sw.value) if sw is not None else bool(self.notify_var.get())
        self.notify_var.set(val)
        if self.probe:
            return
        if not self.write_setting(None, {'key': 'notify_game_end'}, val):
            self.notify_var.set(not val)
            if sw is not None:
                sw.set_value(not val)

    def apply_tray_pref(self, retry=0):
        """托盘图标存在性默认常驻：面板进程活着就该在通知区有一枚 WBOX，
        关窗即退出时随进程摘除。「启动后收进托盘」只决定关窗行为（收托盘 vs
        退出）与气泡前提，不再决定图标有没有——v2.11.1 前图标也 opt-in，
        用户默认看不到任何托盘存在，被当成没做。

        v2.12.2：开机自启时 explorer 常常还没建好通知区，NIM_ADD 会失败。
        旧实现在这里一次性放弃（TrayIcon.start 之后不再重试），于是整个会话
        没有图标；偏偏关窗又按「收进托盘」隐藏窗口，结果窗口和图标一起消失，
        只能重新启动面板。现在按 1/2/4/8/15 秒退避重试，每次失败写一行日志。
        """
        if self.probe:
            return
        icon = ICON_DARK if self.theme == 'dark' else ICON_LIGHT
        delays = (1.0, 2.0, 4.0, 8.0, 15.0)
        if self.tray is None:
            t = tray_icon.TrayIcon(icon, tip='WBOX')
            if t.start():
                self.tray = t
                self._close_without_tray = False
                self.log_line('tray', '托盘图标已就绪')
                self.root.after(200, self._tray_pump)
            elif retry < len(delays):
                self.log_line('tray', '托盘图标创建失败（%s），%.0f 秒后重试'
                              % (t.error or '原因未知', delays[retry]))
                try:
                    self.root.after(int(delays[retry] * 1000),
                                    lambda: self.apply_tray_pref(retry + 1))
                except Exception:
                    pass
            else:
                self.log_line('tray', '重试 %d 次仍失败（%s），本会话按无托盘运行'
                              % (len(delays), t.error or '原因未知'))
        else:
            self.tray.set_icon(icon)
        if not bool(self.tray_var.get()) and self.root.state() == 'withdrawn':
            self.root.deiconify()

    def _tray_shutdown(self):
        t, self.tray = self.tray, None
        if t is not None:
            t.stop()

    def _tray_pump(self):
        t = self.tray
        if t is None:
            return
        if not t.active:
            # 消息线程退出（explorer 重启、会话切换、Windows 清了通知区）：
            # 图标已经不在，自愈重建一枚，别让面板悄悄变成"没有托盘"。
            self.tray = None
            self.log_line('tray', '托盘线程已退出，重建图标')
            self.apply_tray_pref()
            return
        try:
            while True:
                cmd = t.commands.get_nowait()[0]
                if cmd == tray_icon.CMD_OPEN:
                    self.tray_open()
                elif cmd == tray_icon.CMD_EXIT:
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(200, self._tray_pump)

    def tray_open(self):
        self.root.deiconify()
        self._close_without_tray = False
        self.root.lift()
        try:
            self.root.focus_force()
        except Exception:
            pass

    def _tray_watch(self):
        """游戏结束气泡：心跳 active->grace/idle 且窗口正收在托盘里才弹。

        窗口可见时状态行已经写着「游戏中/游戏刚退出」，再弹气泡是打扰；
        但会话跟踪不能停，否则收托盘那一刻会拿陈旧 prev 误报。
        """
        if self.probe or self.tray is None or not bool(self.notify_var.get()):
            return
        st = guard_beat.read_status()
        cur = ''
        if isinstance(st, dict) and st.get('pid'):
            cur = str(st.get('session') or '')
        prev, self._last_session = self._last_session, cur
        if prev == 'active' and cur in ('grace', 'idle') \
                and self.root.state() == 'withdrawn':
            self.tray.balloon('游戏结束', '游戏已全部退出，守护回到空闲待命。')

    def on_boot_toggle(self):
        sw = self.switches.get('boot')
        if not sw:
            return
        val = bool(sw.value)
        self.boot_var.set(val)
        if not self.probe:
            while True:
                rc, out = set_autostart(val)
                if rc == 0:
                    break
                self.note('设置开机自启失败：%s' % out)
                if not self._error('未能设置开机自启',
                                   '开机自动运行没能设置成功。可以再试一次，'
                                   '或稍后回到这一页重新勾选。',
                                   can_retry=True):
                    sw.set_value(self.boot_initial)
                    self.boot_var.set(self.boot_initial)
                    return
            self.boot_initial = val
        self.save_features()

    def on_save(self, show_msg=True):
        if not self.save_features(show_msg=False):
            return
        boot = bool(self.boot_var.get())
        msg = '设置已保存，无需重启。'
        if boot != self.boot_initial and not self.probe:
            rc, out = set_autostart(boot)
            if rc == 0:
                self.boot_initial = boot
                msg += '\n开机自启已' + ('开启' if boot else '关闭') + '。'
            else:
                self.note('设置开机自启失败：%s' % out)
                msg += '\n开机自启没能改成功，可以再试一次。'
        if show_msg:
            self.dialog('已保存', msg, [('好', True)])

    def on_start(self):
        """启动守护。返回是否真的发出去了（设置页的按钮要据此收「启动中…」）。"""
        while True:
            try:
                rc, out = start_guard()
            except Exception as e:
                rc, out = 1, repr(e)
            if rc == 0:
                # 成功不打断（文案规范 §2.3）：设置页那行变「运行中」就够了
                self._sync_set_guard()
                return True
            self.note('启动守护失败：%s' % out)
            if not self._error('未能启动',
                               '后台守护没能启动。可以重试一次，或查看日志了解原因。',
                               can_retry=True):
                return False

    def on_stop(self):
        """停守护。返回是否写了 stop.flag（用户取消返回 False）。"""
        # 影响面大（所有插件暂停），L2 二次确认，按钮用具体动词
        if not self.confirm('停止守护？',
                            '停止后所有插件都会暂停工作，直到你再次启动。要停止吗？',
                            '停止'):
            return False
        while True:
            try:
                with open(STOP_FILE, 'w', encoding='ascii') as f:
                    f.write('stop')
            except Exception as e:
                self.note('停止守护失败：%r' % e)
                if self._error('未能停止',
                               '后台守护没能停止。可以重试一次，或查看日志了解原因。',
                               can_retry=True):
                    continue
                return False
            self._sync_set_guard()
            return True

    def _open_failed(self, what, e):
        """打开文件/目录失败：人话 + 下一步，原始异常只进日志。返回是否重试。"""
        self.note('打开%s失败：%r' % (what, e))
        return self._error('打开失败',
                           '%s没能打开。可以重试一次，详情可查看日志。' % what,
                           can_retry=True)

    def on_log(self):
        while True:
            try:
                if not os.path.exists(LOG):
                    open(LOG, 'a', encoding='utf-8').close()
                os.startfile(LOG)
                return
            except Exception as e:
                if not self._open_failed('日志', e):
                    return

    def on_diag(self):
        """导出诊断包（v2.13.0 审查项 8）：日志/事件流/配置/插件状态一个 zip。

        与 --diag 同一条路径（events.bundle）。安装目录只读时退到桌面；
        成功后直接打开所在文件夹，用户拿着 zip 就能发给人排查。
        """
        import events
        path = ''
        try:
            path = events.bundle(BASE)
        except Exception as e:
            self.log_line('diag', '诊断包生成失败：%r' % e)
        if not path:
            try:
                desktop = os.path.join(os.environ.get('USERPROFILE', ''),
                                       'Desktop')
                if os.path.isdir(desktop):
                    path = events.bundle(BASE, os.path.join(
                        desktop, 'wbox-diag.zip'))
            except Exception as e:
                self.log_line('diag', '桌面回退也失败：%r' % e)
        if not path:
            self._error('导出诊断',
                        '诊断包没能生成。安装目录和桌面都写不进去，'
                        '可以右键程序图标以管理员身份重新打开后再试。')
            return
        self.log_line('diag', '诊断包已生成：%s' % path)
        try:
            os.startfile(os.path.dirname(path))
        except Exception as e:
            self._open_failed('诊断包所在文件夹', e)

    def on_guide(self):
        """打开随安装包分发的《插件开发指南》。

        指南是自包含的单个网页（样式内嵌、不联网），双击即可用系统默认浏览器
        打开，所以这里直接 os.startfile，不拉起任何浏览器参数。
        文件缺失说明安装目录被裁剪过（或跑的是源码而非安装版），提示重装即可，
        不把路径里的技术细节写到界面上（铁律 13：界面上不出现目录名与扩展名）。
        """
        path = os.path.join(BASE, 'docs', '插件开发指南.html')
        if not os.path.isfile(path):
            self.dialog('指南缺失',
                        '这台机器上没有找到插件开发指南。'
                        '重新安装一次就能拿到。', [('好', True)])
            return
        while True:
            try:
                os.startfile(path)
                return
            except Exception as e:
                if not self._open_failed('指南', e):
                    return

    def on_cfg(self):
        while True:
            try:
                os.startfile(CFG)
                return
            except Exception as e:
                if not self._open_failed('设置文件', e):
                    return

    def on_plugin_dir(self, path):
        """打开某个插件的目录。只读模式下同样允许：只是浏览，不写任何配置。"""
        while True:
            try:
                if not os.path.isdir(path):
                    os.makedirs(path)
                os.startfile(path)
                return
            except Exception as e:
                if not self._open_failed('插件文件夹', e):
                    return

    def on_plugins_dir(self):
        """打开插件目录；第一次用时目录可能还不存在，先建出来。"""
        self.on_plugin_dir(PLUGINS_DIR)

    def on_plugin_repo(self):
        """打开官方插件库（GitHub 仓库）。

        本体 v2.11.3 起不带插件，装好程序是"空面板"；这里把下载入口放在
        与「打开插件目录」相邻的位置，用户不必去翻 README。
        """
        url = core_defaults.PLUGIN_REPO
        while True:
            try:
                os.startfile(url)      # ShellExecute：交给默认浏览器
                self.note('已打开插件库：%s' % url)
                return
            except Exception as e:
                if not self._open_failed('插件库页面', e):
                    return

    def on_rescan(self):
        """通知守护立即强制重扫（缓存删不掉也不影响，守护会带 force=True）。"""
        while True:
            try:
                failed = []
                for name in ('games_cache.json', 'rescan_games.flag'):
                    p = os.path.join(BASE, name)
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            failed.append(name)
                with open(os.path.join(BASE, 'rescan_games.flag'), 'w', encoding='ascii') as f:
                    f.write('1')
                msg = ('游戏扫描已开始，列表会自动更新。\n'
                       '详情可查看日志。')
                if failed:
                    msg += '\n\n部分旧缓存没能清掉，不影响这次扫描。'
                self.dialog('已开始', msg, [('好', True)])
                return
            except Exception as e:
                self.note('重新扫描游戏失败：%r' % e)
                if not self._error('扫描未完成',
                                   '游戏扫描没能完成。可以重试，或查看日志了解原因。',
                                   can_retry=True):
                    return

    # ---- 插件管理（v2.1.0）：＋ 装 zip 包 / 垃圾桶卸插件 / 通知守护热重载 ----
    def _plugins_readonly_notice(self):
        self.dialog('需要管理员权限',
                    '这项改动需要管理员权限。\n'
                    '请右键程序图标，以管理员身份重新打开后再试。',
                    [('知道了', True)])

    def on_add_plugin(self):
        """选一个规范 .zip 插件包，校验通过后安装并让守护下一轮重载。"""
        if self.probe:
            return
        if self.plugins_ro:
            self._plugins_readonly_notice()
            return
        while True:
            path = filedialog.askopenfilename(
                parent=self.parent(), title='选择插件文件',
                filetypes=[('插件文件', '*.zip'), ('所有文件', '*.*')])
            if not path:
                return
            try:
                info = plugin_package.inspect_zip(path)
                break
            except Exception as e:
                self.note('插件包校验失败：%r' % e)
                # 「重新选择」回到文件框，「取消」结束——不给裸「确定」
                if not self.dialog('这个文件不能用',
                                   '选中的文件不是有效的插件文件。'
                                   '请确认文件完整后重试，或换一个文件。',
                                   [('重新选择', True), ('取消', False)]):
                    return
        overwrite = False
        # 装之前把"这是谁做的、要碰什么、包有没有被换过"摆给用户看。
        # 插件运行在管理员进程内，这一屏是用户唯一能判断来源的机会。
        if not self.confirm('安装这个插件？',
                            plugin_package.describe_install(info),
                            '安装'):
            return
        if os.path.isdir(os.path.join(PLUGINS_DIR, info['id'])):
            overwrite = self.confirm(
                '已安装过这个插件',
                '同名插件已经装过了。\n'
                '继续安装会先备份旧版本，之后仍可找回。要继续吗？',
                '覆盖安装')
            if not overwrite:
                return
        while True:
            try:
                result = plugin_package.install_zip(
                    path, PLUGINS_DIR, base=BASE, overwrite=overwrite)
                plugin_package.notify_reload(BASE)
                break
            except Exception as e:
                self.note('安装插件失败：%r' % e)
                if not self._error('安装未完成',
                                   '插件没能安装。请确认文件完整后重试，详情可查看日志。',
                                   can_retry=True):
                    return
        self.reload_registry()
        msg = '插件已安装，现在就能在列表里启用它。'
        if result['replaced']:
            msg += '\n\n旧版本已备份，之后可以找回。'
        self.dialog('安装完成', msg, [('好', True)])

    # ---- 每插件设置弹框 ----
    def on_plugin_settings(self, entry):
        """点插件行的滑块：同一插件再点一次收起，换一个插件则切换内容。

        v2.7.0：没有 settings 的插件也开浮层——浮层里至少还有「卸载此插件」。
        原来这里弹一句「没有可调项」就结束，用户想删这个插件还得去翻插件目录。
        """
        if self.probe:
            return
        win = self.plugin_win
        if win and win.winfo_exists():
            same = (self.plugin_entry is not None
                    and self.plugin_entry.id == entry.id)
            self.close_plugin_settings()
            if same:
                return
        self.open_plugin_settings(entry)

    def open_plugin_settings(self, entry):
        """按 manifest.settings 生成浮层。与皮肤面板互斥，不叠两层浮层。"""
        c = self.colors
        # 同一插件因写盘重开（选完路径要按新值重画）时保留「已摊全量」状态，
        # 否则用户点一次 rp-more、选一次路径就被打回前 3 项。
        keep = (self.plugin_entry is not None
                and self.plugin_entry.id == entry.id
                and self._plugin_expanded)
        # 只允许一层：先前已经有插件浮层（比如路径选完要按新值重画）就先拆掉，
        # 否则旧窗会变成没人认领的孤儿 Toplevel，重建时也不会被回收。
        if self.plugin_win is not None and self.plugin_win.winfo_exists():
            self.close_plugin_settings()
        win = tk.Toplevel(self.root)
        win.wm_overrideredirect(True)
        win.configure(bg=c['line'])
        win.resizable(False, False)
        try:
            win.wm_attributes('-topmost', True)
        except Exception:
            pass
        if self.probe:
            try:
                win.attributes('-alpha', 0.0)
            except Exception:
                pass
        c = self.popup_colors('pop_base')
        outer = tk.Frame(win, bg=c['line'], padx=1, pady=1)
        outer.pack(fill='both', expand=True)
        # 内容放在 Canvas 视口里：装得下时和整块 Frame 无异，装不下时滚轮滚，
        # 不再把浮层底部（卸载 / 关闭）硬截掉。
        cv = tk.Canvas(outer, bg=c['bg'], highlightthickness=0, bd=0)
        cv.pack(fill='both', expand=True)
        self.plugin_win = win
        self.plugin_canvas = cv
        self.plugin_entry = entry
        self.plugin_anchor = self.plugin_gears.get(entry.id)
        self._plugin_expanded = bool(keep)
        self._fill_plugin_body(entry)
        win.bind('<Escape>', lambda e: self.close_plugin_settings())
        self._plugin_was_pressed = False
        win.after(120, self._plugin_watch)
        self._pop_in(win)

    def _fill_plugin_body(self, entry, expanded=None):
        """浮层内容：默认前 3 项 + 「全部 N 项设置…」（设计稿 rp-more），
        点过之后摊全量；卸载与脚注始终在最底，靠滚动可达。"""
        if expanded is not None:
            self._plugin_expanded = bool(expanded)
        cv = self.plugin_canvas
        if self.plugin_body is not None and self.plugin_body.winfo_exists():
            self.plugin_body.destroy()
        c = self.popup_colors('pop_base')
        body = tk.Frame(cv, bg=c['bg'])
        self.plugin_body = body
        self._plugin_window_id = cv.create_window(0, 0, window=body, anchor='nw')
        self._setting_switches = {}

        head = tk.Frame(body, bg=c['bg'])
        head.pack(fill='x', padx=self.px(14), pady=(self.px(12), self.px(6)))
        Glyph(head, self, entry.icon, size=self.px(20)).pack(side='left')
        tk.Label(head, text=entry.name, bg=c['bg'], fg=c['fg'],
                 font=self.f_head).pack(side='left', padx=(self.px(10), 0))
        # v2.12.0：版本号从「几乎看不见的小灰字」改成右侧底片胶囊，并补一行
        # 接口 / 发布者。用户排查"功能不对"时第一件事就是核对装的是哪一版，
        # 这行必须能一眼读到，不能靠猜。
        ver = ('版本 v%s' % entry.version) if entry.version else '版本未知'
        tk.Label(head, text=ver, bg=c['fill'], fg=c['t2'], font=self.f_small,
                 padx=self.px(6), pady=self.px(1)).pack(side='right')
        meta = []
        if entry.api:
            meta.append('插件接口 API %d' % entry.api)
        if entry.publisher:
            meta.append('发布者 %s' % entry.publisher)
        if entry.requires_admin and not is_admin():
            meta.append('需要管理员权限')
        if meta:
            tk.Label(body, text=' · '.join(meta), bg=c['bg'], fg=c['t3'],
                     font=self.f_small, anchor='w', justify='left',
                     wraplength=self.px(300)).pack(
                         fill='x', padx=self.px(14), pady=(0, self.px(10)))
        self.sep(body)

        # 显示值一律走三段合并后的配置：面板显示「开」而守护按默认值跑「关」
        # 是这套体系里最招人烦的老坑，这里同一个口径。
        cfg = effective_cfg(entries=self.entries_all)
        specs = list(entry.settings or [])
        show = specs if self._plugin_expanded else specs[:3]
        for spec in show:
            self.build_setting_row(body, entry, spec, cfg)
            self.sep(body)
        if len(specs) > 3:
            more = tk.Frame(body, bg=c['bg'], cursor='hand2')
            more.pack(fill='x', padx=self.px(7), pady=(0, self.px(2)))
            txt = ('收起额外设置' if self._plugin_expanded
                   else '全部 %d 项设置…' % len(specs))
            ml = tk.Label(more, text=txt, bg=c['bg'], fg=c['t2'],
                          font=self.f_small, anchor='w')
            ml.pack(side='left', padx=self.px(8), pady=self.px(5))
            for w in (more, ml):
                w.bind('<Button-1>',
                       lambda e: self._fill_plugin_body(
                           entry, not self._plugin_expanded))
                w.bind('<Enter>', lambda e: [x.configure(bg=c['hover'])
                                             for x in (more, ml)], add='+')
                w.bind('<Leave>', lambda e: [x.configure(bg=c['bg'])
                                             for x in (more, ml)], add='+')
            self.sep(body)

        # 卸载沉到浮层底部（方案 4）：行尾不再单独挂垃圾桶，
        # 破坏性操作离手指远一点，误触概率也低。
        if not self.plugins_ro:
            del_row = tk.Frame(body, bg=c['bg'], cursor='hand2')
            del_row.pack(fill='x', padx=self.px(14),
                         pady=(self.px(4), self.px(2)))
            del_lbl = tk.Label(del_row, text='卸载此插件', bg=c['bg'],
                               fg=c['danger'], font=self.f_body, anchor='w')
            del_lbl.pack(fill='x')
            for w in (del_row, del_lbl):
                w.bind('<Button-1>',
                       lambda e, ent=entry: self.on_uninstall_from_popup(ent))
            self._keyboard(del_lbl,
                           lambda ent=entry: self.on_uninstall_from_popup(ent))
            self.sep(body)

        foot = tk.Frame(body, bg=c['bg'])
        foot.pack(fill='x', padx=self.px(14), pady=(self.px(9), self.px(12)))
        tk.Label(foot, text='改动立即生效', bg=c['bg'], fg=c['t3'],
                 font=self.f_small).pack(side='left')
        if self.readonly:
            tk.Label(foot, text='需要管理员权限', bg=c['bg'], fg=c['t3'],
                     font=self.f_small).pack(side='right')
        else:
            close = tk.Label(foot, text='关闭', bg=c['bg'], fg=c['t2'],
                             font=self.f_small, cursor='hand2')
            close.pack(side='right')
            close.bind('<Button-1>', lambda e: self.close_plugin_settings())
            close.bind('<Enter>', lambda e: close.configure(fg=c['fg']))
            close.bind('<Leave>', lambda e: close.configure(fg=c['t2']))

        self.place_plugin_settings()

    def place_plugin_settings(self):
        """右边缘贴行尾箭头、向下展开；下面放不开就向上，仍不行就截到屏幕内。"""
        win = self.plugin_win
        body = self.plugin_body
        if not (win and body and win.winfo_exists()):
            return
        win.update_idletasks()
        lx, ly, sw, sh = work_area()
        # 设计稿 .row-pop width:296
        w = min(self.px(296), max(self.px(240), sw - self.px(24)))
        need_h = body.winfo_reqheight() + 2
        a = self.plugin_anchor
        row = None
        if a is not None and a.winfo_exists():
            # 浮层贴的是「整行」的下沿（设计稿 top:calc(100% - 4px)），
            # 挂到行尾滑块上会从行中间长出来，压住同行的名称。
            row = a.master if isinstance(a.master, tk.Canvas) else None
            ax, ay = (row or a).winfo_rootx(), (row or a).winfo_rooty()
            aw, ah = (row or a).winfo_width(), (row or a).winfo_height()
        else:
            ax, ay = self.root.winfo_rootx(), self.root.winfo_rooty()
            aw, ah = 0, 0
        x = min(ax + aw - w, lx + sw - w - self.px(8))
        x = max(lx + self.px(8), x)
        below = ay + ah - self.px(4)
        avail_below = ly + sh - below - self.px(8)
        if need_h <= avail_below:
            y, h = below, need_h
        else:
            avail_above = ay - ly - self.px(8)
            if need_h <= avail_above:
                y, h = ay - need_h - self.px(6), need_h
            elif avail_below >= avail_above:
                y, h = below, max(self.px(140), avail_below)
            else:
                h = max(self.px(140), avail_above)
                y = ay - h - self.px(6)
        h = min(h, max(self.px(140), sh - self.px(16)))
        y = max(ly + self.px(8), min(y, ly + sh - h - self.px(8)))
        win.geometry('%dx%d+%d+%d' % (w, h, x, y))
        cv = self.plugin_canvas
        if cv is not None and cv.winfo_exists():
            cv.itemconfigure(self._plugin_window_id, width=w - 2)
            win.update_idletasks()
            cv.configure(scrollregion=(0, 0, w - 2, body.winfo_reqheight()))
            self._bind_wheel(cv, body)
            # 路径胶囊的换行在拿到最终宽度后才发生（Configure），内容高度会
            # 晚于本次 place 变化；scrollregion 不跟着更新就会滚不到底。
            body.bind('<Configure>', lambda e: cv.configure(
                scrollregion=(0, 0, w - 2, body.winfo_reqheight())))
        try:
            win.lift()
            win.wm_attributes('-topmost', True)
        except Exception:
            pass

    def _plugin_watch(self):
        """全局左键：按在弹框之外就收起（与皮肤面板同一套判定）。"""
        win = self.plugin_win
        if not (win and win.winfo_exists()):
            return
        try:
            u = ctypes.windll.user32
            u.GetAsyncKeyState.argtypes = [ctypes.c_int]
            u.GetAsyncKeyState.restype = ctypes.c_short
            pressed = bool(int(u.GetAsyncKeyState(0x01)) & 0x8000)
            if pressed and not self._plugin_was_pressed and not self.probe:
                u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
                pt = POINT()
                u.GetCursorPos(ctypes.byref(pt))
                x, y = pt.x, pt.y
                pad = self.px(4)
                mx, my = win.winfo_rootx(), win.winfo_rooty()
                inside = (mx - pad <= x <= mx + win.winfo_width() + pad and
                          my - pad <= y <= my + win.winfo_height() + pad)
                in_anchor = False
                a = self.plugin_anchor
                if a is not None and a.winfo_exists():
                    ax, ay = a.winfo_rootx(), a.winfo_rooty()
                    in_anchor = (ax <= x <= ax + a.winfo_width() and
                                 ay <= y <= ay + a.winfo_height())
                if not inside and not in_anchor:
                    self.close_plugin_settings()
                    return
            self._plugin_was_pressed = pressed
        except Exception:
            pass
        try:
            win.after(120, self._plugin_watch)
        except Exception:
            pass

    def close_plugin_settings(self):
        win = self.plugin_win
        if win is not None:
            self.cancel_tween(('pop', id(win)))
        self.plugin_win = None
        self.plugin_body = None
        self.plugin_canvas = None
        self._plugin_window_id = None
        self.plugin_entry = None
        self.plugin_anchor = None
        if win is not None:
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
        self._focus_brand()

    def build_setting_row(self, master, entry, spec, cfg, bg=None):
        """一行设置：左标题（可带小字说明）+ 右控件。

        bg 给极简档的行内展开用：展开块底色和浮层不同，控件必须跟着换，
        否则会出现一块白底方片压在灰底上。
        """
        c = dict(self.colors)
        if bg:
            c['bg'] = bg
        ro = self.readonly
        typ = spec['type']
        if typ == 'path':
            # 路径行单独排版：主标题一行，路径做成整行灰底胶囊放在下面。
            # 设计稿 .rp-col 就是这个结构——296 宽的单行里塞不下路径 + 选择按钮。
            row = tk.Frame(master, bg=c['bg'])
            row.pack(fill='x', padx=self.px(14), pady=(self.px(9), self.px(9)))
            tk.Label(row, text=spec['label'], bg=c['bg'],
                     fg=(c['t3'] if ro else c['fg']), font=self.f_body,
                     anchor='w').pack(anchor='w')
            if spec.get('hint'):
                tk.Label(row, text=spec['hint'], bg=c['bg'], fg=c['t3'],
                         font=self.f_small, anchor='w', justify='left',
                         wraplength=self.px(230)).pack(anchor='w')
            pill = tk.Frame(row, bg=c['inset'])
            pill.pack(fill='x', pady=(self.px(6), 0))
            cur = str(cfg_get_path(cfg, spec['key'], '') or '')
            shown = cur if cur else '留空 → 自动查找'
            btn = tk.Label(pill, text='选择', bg=c['inset'],
                           fg=(c['t3'] if ro else c['t2']), font=self.f_small,
                           cursor=('arrow' if ro else 'hand2'))
            # 按钮先 pack 占住右侧：pack 的空腔按顺序切，长路径后 pack 才能
            # 拿到剩余宽度；反过来长路径会把按钮顶出胶囊（240dpi 实测越界）。
            btn.pack(side='right', padx=self.px(8))
            lab = tk.Label(pill, text=shown, bg=c['inset'], fg=c['t2'],
                           font=self.f_small, anchor='w', justify='left')
            lab.pack(side='left', fill='x', expand=True,
                     padx=self.px(8), pady=self.px(4))

            def _wrap_path(e):
                lab.configure(wraplength=max(
                    self.px(40),
                    e.width - btn.winfo_reqwidth() - self.px(32)))
            pill.bind('<Configure>', _wrap_path)
            if not ro:
                # 和弹框里其它控件（＋/−、选项）一致用按下即触发：
                # 这是一枚小字按钮，没有"误拖"风险，等到松开会显得迟滞。
                btn.bind('<Button-1>', lambda e: self._pick_path(entry, spec))
            return row
        row = tk.Frame(master, bg=c['bg'])
        row.pack(fill='x', padx=self.px(14), pady=(self.px(9), self.px(9)))
        left = tk.Frame(row, bg=c['bg'])
        left.pack(side='left', fill='x', expand=True)
        tk.Label(left, text=spec['label'], bg=c['bg'],
                 fg=(c['t3'] if ro else c['fg']), font=self.f_body,
                 anchor='w', justify='left',
                 wraplength=self.px(160)).pack(anchor='w')
        if spec.get('hint'):
            tk.Label(left, text=spec['hint'], bg=c['bg'], fg=c['t3'],
                     font=self.f_small, anchor='w', justify='left',
                     wraplength=self.px(160)).pack(anchor='w')
        right = tk.Frame(row, bg=c['bg'])
        right.pack(side='right', padx=(self.px(10), 0))
        if typ == 'bool':
            cur = bool(cfg_get_path(cfg, spec['key'], False))
            sw = Switch(right, self, value=cur,
                        command=lambda spec=spec: self.on_setting_bool(
                            entry, spec, self.switch_of(spec['key'])))
            sw.set_bg(c['bg'])
            sw.pack()
            if ro:
                sw.set_enabled(False)
            self._setting_switches[spec['key']] = sw
        elif typ == 'int':
            self.build_setting_int(right, entry, spec, cfg, ro)
        elif typ == 'choice':
            self.build_setting_choice(right, entry, spec, cfg, ro)
        elif typ == 'path':
            self.build_setting_path(right, entry, spec, cfg, ro)
        return row

    def switch_of(self, key):
        return self._setting_switches.get(key)

    def on_setting_bool(self, entry, spec, sw):
        if sw is None:
            return
        value = bool(sw.value)
        if not self.write_setting(entry, spec, value):
            sw.set_value(not value)

    def build_setting_int(self, master, entry, spec, cfg, ro):
        c = self.colors
        try:
            cur = int(cfg_get_path(cfg, spec['key'], spec['min']))
        except (TypeError, ValueError):
            cur = spec['min']
        cur = max(spec['min'], min(spec['max'], cur))
        unit = spec.get('unit') or ''
        # 用单元素列表持有当前值：闭包里要能改，又不引入 nonlocal
        state = [cur]
        var = tk.StringVar(master, value=('%d %s' % (cur, unit)).strip())
        ink = c['t3'] if ro else c['fg']
        minus = tk.Label(master, text='−', bg=c['bg'], fg=ink, font=self.f_head,
                         padx=self.px(6), cursor=('arrow' if ro else 'hand2'))
        minus.pack(side='left')
        tk.Label(master, textvariable=var, bg=c['bg'], fg=ink,
                 font=self.f_body, width=5).pack(side='left')
        plus = tk.Label(master, text='+', bg=c['bg'], fg=ink, font=self.f_head,
                        padx=self.px(6), cursor=('arrow' if ro else 'hand2'))
        plus.pack(side='left')

        def step(delta):
            new = max(spec['min'], min(spec['max'], state[0] + delta))
            if new == state[0]:
                return
            if self.write_setting(entry, spec, new):
                state[0] = new
                var.set(('%d %s' % (new, unit)).strip())

        if not ro:
            minus.bind('<Button-1>', lambda e: step(-spec.get('step', 1)))
            plus.bind('<Button-1>', lambda e: step(spec.get('step', 1)))

    def build_setting_choice(self, master, entry, spec, cfg, ro):
        c = self.colors
        cur = str(cfg_get_path(cfg, spec['key'], spec['options'][0][0]))
        buttons = {}

        def paint(btn, on):
            btn.configure(bg=(c['fg'] if on else c['bg']),
                          fg=(c['bg'] if on else c['t2']))

        def pick(value):
            if ro:
                return
            if self.write_setting(entry, spec, value):
                for v, b in buttons.items():
                    paint(b, v == value)

        # 选项排不下时换行：浮层宽度是设计稿定死的 px(296)，选项一多
        # （如桌面监控 8 个位置）单行 pack 会把尾部选项裁到窗外点不到。
        _, _, sw, _ = work_area()
        w = min(self.px(296), max(self.px(240), sw - self.px(24)))
        left_req = min(self.f_body.measure(spec['label']), self.px(160))
        if spec.get('hint'):
            left_req = max(left_req, min(
                self.f_small.measure(spec['hint']), self.px(160)))
        avail = w - 2 - 2 * self.px(14) - left_req - self.px(10) - self.px(2)
        line = None
        used = 0
        for value, label in spec['options']:
            need = self.f_small.measure(label) + 2 * self.px(8) + self.px(3)
            if line is None or (used and used + need > avail):
                line = tk.Frame(master, bg=c['bg'])
                line.pack(side='top', anchor='w')
                used = 0
            b = tk.Label(line, text=label, font=self.f_small,
                         padx=self.px(8), pady=self.px(3),
                         cursor=('arrow' if ro else 'hand2'))
            b.pack(side='left', padx=(self.px(3), 0))
            used += need
            buttons[value] = b
            paint(b, value == cur)
            if not ro:
                b.bind('<Button-1>', lambda e, v=value: pick(v))

    def _pick_path(self, entry, spec):
        """选文件 → 落盘 → 重画浮层（路径胶囊的宽度随内容变，重建最省心）。"""
        if self.readonly:
            return
        try:
            path = filedialog.askopenfilename(
                parent=self.plugin_win or self.root,
                title='选择「%s」' % spec['label'],
                filetypes=[('程序', '*.exe'), ('所有文件', '*.*')])
        except Exception:
            return
        if not path:
            return
        if self.write_setting(entry, spec, path.replace('/', '\\')):
            if self.plugin_win and self.plugin_win.winfo_exists():
                saved = self.plugin_anchor
                self.open_plugin_settings(entry)
                self.plugin_anchor = saved

    def build_setting_path(self, master, entry, spec, cfg, ro):
        c = self.colors
        cur = str(cfg_get_path(cfg, spec['key'], '') or '')
        shown = os.path.basename(cur) if cur else '自动查找'
        lab = tk.Label(master, text=shown, bg=c['bg'], fg=c['t3'],
                       font=self.f_small)
        lab.pack(side='left', padx=(0, self.px(8)))
        btn = tk.Label(master, text='选择', bg=c['bg'],
                       fg=(c['t3'] if ro else c['t2']), font=self.f_small,
                       cursor=('arrow' if ro else 'hand2'))
        btn.pack(side='left')

        def choose():
            if ro:
                return
            try:
                path = filedialog.askopenfilename(
                    parent=self.root, title='选择「%s」' % spec['label'],
                    filetypes=[('程序', '*.exe'), ('所有文件', '*.*')])
            except Exception:
                return
            if not path:
                return
            if self.write_setting(entry, spec, path.replace('/', '\\')):
                lab.configure(text=os.path.basename(path) or path)

        if not ro:
            btn.bind('<Button-1>', lambda e: choose())

    def write_setting(self, entry, spec, value):
        """单项写盘：只改这个叶子键，其它键保持不动。失败要把控件状态回滚。"""
        if self.readonly:
            return False
        try:
            cfg = load_cfg()
            cfg_set_path(cfg, spec['key'], value)
            save_cfg(cfg)
            return True
        except Exception as e:
            self.note('保存设置失败：%r' % e)
            self._error('未能保存',
                        '设置没能保存。请检查是否有写入权限后重试，详情可查看日志。')
            return False

    def on_delete_plugin(self, entry):
        """把插件目录移进备份区（不硬删），再通知守护热重载。"""
        if self.probe:
            return
        if self.plugins_ro:
            self._plugins_readonly_notice()
            return
        # v2.11.3：本体不再附带插件，卸载后想再用去插件库重装即可，
        # 不再有"内置插件卸载后要重装程序"这条提示。
        if not self.confirm('卸载「%s」？' % entry.name,
                            '卸载后这个插件会停止工作，文件会先备份而不是直接删除，'
                            '之后可以找回。要卸载吗？',
                            '卸载'):
            return
        while True:
            try:
                plugin_package.remove_plugin(entry.id, PLUGINS_DIR, base=BASE)
                plugin_package.notify_reload(BASE)
                break
            except Exception as e:
                self.note('卸载插件失败：%r' % e)
                if not self._error('未能卸载',
                                   '插件没能卸载，可能正在被占用。请稍后重试，详情可查看日志。',
                                   can_retry=True):
                    return
        self.reload_registry()
        self.dialog('已卸载', '插件已卸载并备份，之后可以找回，列表已更新。',
                    [('好', True)])

    def reload_registry(self):
        """装/卸之后重读注册表：主页面行数与开关都跟着变。

        不整体重启面板进程，只重建控件；vars 必须重建成新的 BooleanVar，
        否则旧开关被 destroy 后仍引用旧变量，拨动时写不到 config.json。
        """
        self.hide_tips()
        self.close_plugin_settings()
        self.entries_all = plugin_entries()
        self.runtime_state = read_plugin_states()
        self.entries = [e for e in self.entries_all
                        if e.status == plugin_host.STATUS_OK and e.home]
        self.entries.sort(key=lambda e: (e.order, e.id))
        cfg = effective_cfg(entries=self.entries_all)
        self.vars = {}
        for entry in self.entries:
            self.vars[entry.id] = tk.BooleanVar(
                self.root, value=plugin_switch_state(entry, cfg))
        self.rebuild()


# ---------------- 无窗口自检 / 布局检查 ----------------

def self_check():
    enable_dpi_awareness()
    dpi = system_dpi()
    wa = work_area()
    lines = [
        'OK',
        'BASE=%s' % BASE,
        'DPI_MODE=%s' % DPI_MODE,
        'real_dpi=%d (96 means still virtualized => blurry)' % dpi,
        'scale=%.2f' % (dpi / 96.0),
        'work_area=%dx%d' % (wa[2], wa[3]),
        'frozen=%s' % getattr(sys, 'frozen', False),
        'version=%s' % VERSION,
        'exe_bytes=%d' % (os.path.getsize(sys.executable) if getattr(sys, 'frozen', False) else 0),
        # 装在 Program Files 时这里通常是 False：面板要么提权，要么功能置灰
        'cfg_writable=%s' % can_write_config(),
        'elevated=%s' % is_admin(),
    ]
    try:
        entries = plugin_entries()
        cfg = effective_cfg(entries=entries)
        lines.append('AB=%s ACE=%s THROTTLE=%s' % (
            cfg.get('afterburner_enabled'),
            cfg['ace_cleanup'].get('enabled'),
            cfg['ace_throttle'].get('enabled')))
        lines.append('theme=%s' % cfg.get('ui_theme', 'light'))
        # 主页面显示的功能行 = 加载成功且 home=true 的插件，名字取自 manifest
        lines.append('features=%s' % '|'.join(
            e.name for e in entries
            if e.status == plugin_host.STATUS_OK and e.home))
        _skin, _appearance = skins.resolve(cfg, base=BASE)
        # 入口固定：右上角「设置」→ 同窗换页（v2.17.0 起没有字标菜单那条路）。
        lines.append('second_level=set_button')
        lines.append('skin=%s/%s perf=%s' % (
            _skin.id, _appearance, _skin.perf_label))
        _size = ui_size_from_cfg(cfg)
        lines.append('ui_size=%s zoom=%.2f' % (_size, UI_SIZE_ZOOM[_size]))
        # 注册表全景：id:状态，自检时一眼能看出哪个插件没加载起来
        lines.append('plugins=%s' % (','.join(
            '%s:%s' % (e.id, e.status) for e in entries) or 'none'))
        lines.append('plugins_dir=%s exists=%s' % (
            PLUGINS_DIR, os.path.isdir(PLUGINS_DIR)))
        lines.append('plugins_state=%s' % os.path.isfile(STATE_FILE))
        lines.append('menu_entry=label:设置')
        lines.append('plugin_actions=add:plus,row:chev_r,inline_open edit=%s'
                     % plugin_package.plugins_writable(PLUGINS_DIR))
        lines.append('plugin_package=%s' % plugin_package.FORMAT_ID)
        lines.append('icons=%s' % ','.join(
            '%s:%s' % (n, os.path.isfile(p)) for n, p in
            (('light', ICON_LIGHT), ('dark', ICON_DARK))))
    except Exception as e:
        lines.append('CFG_FAIL=%r' % (e,))
    lines.append('guard=%s' % guard_state())
    try:
        r = tk.Tk()
        r.geometry('1x1+0+0')
        r.attributes('-alpha', 0.0)
        r.update()
        fam = pick_family(r)
        f = tkfont.Font(r, family=fam, size=10)
        r.tk.call('tk', 'scaling', dpi / 72.0)
        lines.append('tkinter=%s font=%s font_px=%d' % (tk.TkVersion, fam, f.metrics('linespace')))
        set_window_icons(r, ICON_LIGHT)
        wi = get_window_icons(r)
        lines.append('window_icons=small:%s,big:%s' % wi)
        lines.append('icon_files=%s' % ','.join(
            '%s:%s' % (n, os.path.isfile(p)) for n, p in
            (('light', ICON_LIGHT), ('dark', ICON_DARK))))
        r.destroy()
        lines.append('TK_WINDOW_OK=True')
    except Exception as e:
        lines.append('TK_WINDOW_FAIL=%r' % (e,))
    with open(SELFCHECK, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def layout_check():
    """模拟 96/120/144/168/192/240 DPI，检查控件是否溢出窗口。"""
    enable_dpi_awareness()
    lines = ['OK', 'DPI_MODE=%s' % DPI_MODE]
    try:
        root = tk.Tk()
        root.attributes('-alpha', 0.0)
        app = App(root, probe=True)

        def overflow(base):
            rx, ry = base.winfo_rootx(), base.winfo_rooty()
            w, h = base.winfo_width(), base.winfo_height()
            bad = []
            for c in walk(base):
                try:
                    x = c.winfo_rootx() - rx
                    y = c.winfo_rooty() - ry
                    cw, ch = c.winfo_width(), c.winfo_height()
                    if cw > 1 and ch > 1 and (x + cw > w + 2 or y + ch > h + 2):
                        bad.append('%s(%d,%d,%d,%d)' % (
                            c.winfo_class(), x, y, cw, ch))
                except Exception:
                    pass
            return w, h, bad

        def scan(size):
            # v2.12.0：窗口大小三档都要量。persist=False：自检不改用户配置。
            app.set_ui_size(size, persist=False)
            for dpi in (96, 120, 144, 168, 192, 240):
                app.apply_dpi(dpi)
                root.update_idletasks()
                root.update()
                # 列表页 + 设置页各量一次：两页共用同一个窗口，谁溢出都要报出来。
                # v2.18.0 删掉齿轮浮层菜单后，这里原来那套「菜单在屏内 / 逐行不越界」
                # 的量测随之退役——第二级已经不是一个浮层，而是同一扇窗的另一页。
                w, h, bad = overflow(root)
                app.toggle_pane()
                root.update_idletasks()
                root.update()
                # 设置页量它的页内容器（Canvas 的 wrap Frame）：窗口本身不裁
                # 设置页内容，滚轮视口才承接超高；拿 root 量会把视口内正常
                # 滚动的内容误报成溢出。
                sw_, sh_, sbad = overflow(app.set_inner)
                app.toggle_pane()
                root.update_idletasks()
                root.update()
                lines.append(
                    'size=%s dpi=%-3d scale=%.2f win=%dx%d overflow=%d '
                    'set=%dx%d soverflow=%d font_px=%-3d plugins=%d %s'
                    % (size, dpi, dpi / 96.0 * app.ui_zoom, w, h, len(bad),
                       sw_, sh_, len(sbad),
                       app.f_body.metrics('linespace'), len(app.entries),
                       ';'.join((bad + sbad)[:3])))
                for e in app.entries:
                    if e.status != plugin_host.STATUS_OK or not e.settings:
                        continue
                    app.open_plugin_settings(e)
                    root.update_idletasks()
                    root.update()
                    ph = app.plugin_win.winfo_height()
                    need = app.plugin_body.winfo_reqheight() + 2
                    _, _, pbad = overflow(app.plugin_body)
                    lines.append('plugin_settings=%s size=%s dpi=%d items=%d '
                                 'need=%d win_h=%d clipped=%s overflow=%d'
                                 % (e.id, size, dpi, len(e.settings), need, ph,
                                    need > ph + 2, len(pbad)))
                    app.close_plugin_settings()

        lines.append('pass skin=%s' % app.skin.id)
        for _size in tuple(k for k, _l, _z in UI_SIZES):
            scan(_size)
        # v2.17.0：骨架只留默认一套。第三方包若仍声明 performance=standard，
        # 也按默认骨架渲染（旧字段只作兼容，不再换骨架）。这里换一枚这种包
        # 再扫一遍，确认降级不会报错、也不会把窗口撑破（persist=False）。
        tok = dict.fromkeys(skins.TOKENS, '#808080')
        tok.update(fg='#000000', glass='#FFFFFF', on='#000000',
                   on_knob='#FFFFFF', knob='#FFFFFF')
        legacy = skins.from_dict({
            'id': 'layout-legacy', 'name': '自检老包', 'version': '1.0.0',
            'api': 1, 'engine': 'flat', 'performance': 'standard',
            'chrome': 'custom',
            'metrics': {'row': 44, 'font': 14, 'icon': 18, 'titlebar': 40},
            'radius': {'window': 26, 'card': 20, 'row': 14},
            'light': tok})
        app.apply_skin(legacy, persist=False)
        lines.append('pass skin=layout-legacy(standard→默认骨架)')
        for _size in tuple(k for k, _l, _z in UI_SIZES):
            scan(_size)
        root.destroy()
    except Exception as e:
        lines[0] = 'FAIL'
        lines.append('ERROR=%r' % (e,))
    with open(LAYOUTCHECK, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def main():
    global READONLY
    args = sys.argv[1:]
    if '--self-check' in args:
        self_check()
        return
    if '--layout-check' in args:
        layout_check()
        return
    # 单实例：重复启动时通知已有面板弹出并立即退出。必须赶在提权重开之前，
    # 否则重复副本会再弹一次 UAC；也赶在建 Tk 窗口之前，避免第二枚托盘图标。
    if not panel_single_instance():
        return 0
    # 默认目录装在 Program Files 下时，普通用户改不了 config.json：先自我提权重开；
    # 用户拒绝 UAC（或提权后仍不可写）才退回"功能置灰 + 状态行提示需要管理员权限"。
    if not can_write_config():
        if not is_admin() and elevate_settings(args):
            return 0
        READONLY = True
    enable_dpi_awareness()
    # 上一次安装（换入过程中断电/被杀）留下的现场先收干净：面板可能正是断电
    # 之后第一个启动的东西，不能等下一次装插件才处理。失败静默，只影响"能不能
    # 自动挽回"，不影响面板打开。
    try:
        plugin_package.recover(PLUGINS_DIR, base=BASE, log=lambda m: None)
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == '__main__':
    main()
