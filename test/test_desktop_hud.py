# -*- coding: utf-8 -*-
"""离线验证 desktop_hud 插件：manifest/注册表加载 + 胶水逻辑（不建真实窗口）。

跑法：python test\\test_desktop_hud.py
只读仓库 plugins\\desktop_hud，复制到临时目录加载；不碰 app\\、不建窗口、
不写真实 config.json / data\\。悬浮窗本体的可视化验证见插件 README 与
hud_overlay.py 的 __main__ 独立自测。
"""
import os
import shutil
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_src  # noqa: E402  插件源码在独立仓库 wbox-plugins

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import plugin_api                      # noqa: E402
import plugin_host                     # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


TMP = tempfile.mkdtemp(prefix='abg_hud_')
PLUGINS = os.path.join(TMP, 'plugins')
dst = os.path.join(PLUGINS, 'desktop_hud')
os.makedirs(dst)
src = os.path.join(_plugin_src.require('desktop_hud'), 'desktop_hud')
for fn in ('plugin.py', 'plugin.json', 'hud_metrics.py', 'hud_overlay.py'):
    shutil.copy(os.path.join(src, fn), os.path.join(dst, fn))

print('[A] 注册表加载（面板路径，不驱动钩子）')
entries = plugin_host.load_registry(TMP, plugins_dir=PLUGINS, cfg={})
check(len(entries) == 1, '发现 1 个插件：%r' % [e.id for e in entries])
e = entries[0]
check(e.id == 'desktop_hud' and e.usable, 'desktop_hud 加载 usable：%r' % e)
check(e.plugin is not None, 'plugin 实例已创建')
check(e.icon == 'pulse', 'icon=pulse 在白名单内')
check(e.plugin.gui_state({'desktop_hud_enabled': True}) is True, 'gui_state 默认开')
check(e.plugin.gui_state({'desktop_hud_enabled': False}) is False, 'gui_state 关')
c2 = {}
check(e.plugin.gui_toggle(c2, False) is True
      and c2.get('desktop_hud_enabled') is False, 'gui_toggle 写入总开关')

print('[B] 胶水逻辑（FakeCtx + StubOverlay，不建窗口）')


class FakeCtx(object):
    def __init__(self):
        self.base = TMP
        self.toggle = ['desktop_hud_enabled']
        self.written = {}
        self.logs = []
        self.store = {}

    def log(self, m):
        self.logs.append(m)

    def read_state(self, name, default=None):
        return self.store.get(name, default)

    def write_state(self, name, obj):
        self.written[name] = obj
        self.store[name] = obj


class StubOverlay(object):
    def __init__(self, request_cb=None):
        self.prefs = None
        self.visible = None
        self.paused = None
        self.stopped = False

    def start(self, prefs=None, visible=True, timeout=1.5):
        self.prefs = prefs
        self.visible = visible
        return True

    def stop(self, timeout=1.5):
        self.stopped = True

    def set_prefs(self, p):
        self.prefs = p

    def set_visible(self, v):
        self.visible = v

    def set_paused(self, v):
        self.paused = v

    def is_running(self):
        return not self.stopped


import importlib.util
spec = importlib.util.spec_from_file_location('hud_plugin_under_test',
                                              os.path.join(dst, 'plugin.py'))
mod = importlib.util.module_from_spec(spec)
sys.path.insert(0, dst)   # 供 plugin.py 的绝对导入 fallback 命中 hud_overlay
spec.loader.exec_module(mod)

ctx = FakeCtx()
p = mod.Plugin(ctx)
stub = StubOverlay()
p._overlay = stub          # 先放桩，避免 on_config 真的建窗口
p.on_config({'desktop_hud_enabled': True, 'desktop_hud': {'show_gpu': False}})
p.on_load()
p.on_start()
check(stub.prefs is not None and stub.prefs.get('show_gpu') is False,
      'on_config 的基础偏好推送到悬浮窗')
check(stub.prefs.get('hide_in_game') is True, 'on_config 自行补齐默认偏好')


def tick(state):
    return plugin_api.Tick(session=plugin_api.SessionView(state=state))


p.on_tick(tick('idle'))
check(stub.visible is True, 'idle 时显示')
p.on_tick(tick('active'))
check(stub.visible is False, 'active 且 hide_in_game 默认开 -> 隐藏')
p.on_tick(tick('grace'))
check(stub.visible is False, 'grace 仍隐藏')
p.on_tick(tick('idle'))
check(stub.visible is True, '回到 idle 重新显示')
check(stub.paused is False, 'idle 恢复采样')
p.on_tick(tick('active'))
check(stub.paused is True, 'active 且 pause_in_game 默认开 -> 停采样')
p.on_tick(tick('idle'))
check(stub.paused is False, '退出游戏恢复采样')

p._on_request({'action': 'prefs',
               'prefs': dict(mod.hud_overlay.DEFAULT_PREFS,
                             pause_in_game=False, hide_in_game=False)})
p.on_tick(tick('idle'))
p.on_tick(tick('active'))
check(stub.paused is False and stub.visible is True,
      '两开关都关 -> 游戏中继续监控且显示')
p.on_tick(tick('idle'))

p._on_request({'action': 'prefs', 'prefs': dict(mod.hud_overlay.DEFAULT_PREFS,
                                               position='bottom-left')})
p.on_tick(tick('idle'))
check(ctx.written.get('hud', {}).get('prefs', {}).get('position') == 'bottom-left',
      '菜单 prefs 经 on_tick 落盘 data/hud.json')
check(p._effective().get('position') == 'bottom-left', '运行期覆盖生效')

p._state['prefs'] = {'position': 'bottom-left'}   # 模拟旧版快照：缺新键
check(p._effective().get('desktop_only') is True, '旧快照缺 desktop_only 回退默认 True')
check(p._effective().get('show_gpu') is False, '旧快照缺键回退 config 基础值')

p._on_request({'action': 'close'})
p.on_tick(tick('idle'))
check(ctx.written.get('hud', {}).get('hidden') is True, 'close 持久化 hidden')
check(stub.visible is False, 'close 后隐藏')

p._state['prefs'] = {}
check(p._effective().get('show_gpu') is False, '清空覆盖后回到 config 基础值')

p.on_config({'desktop_hud_enabled': False, 'desktop_hud': {}})
check(stub.stopped is True and p._overlay is None, '总开关关 -> stop 悬浮窗')

p.on_exit('guard_stop')
check(True, 'on_exit 安全返回')

print('[C] v1.2.0 桌面子窗机制（FakeU，不建真实窗口）')
ov_mod = mod.hud_overlay


class FakeU(object):
    def __init__(self, progman=555, workerw=777):
        self.calls = []
        self._progman = progman
        self._workerw = workerw

    def ShowWindow(self, hwnd, cmd):
        self.calls.append(('show', cmd))

    def SetWindowPos(self, hwnd, after, x, y, w, h, flags):
        self.calls.append(('pos', after, x, y, flags))

    def InvalidateRect(self, hwnd, rect, erase):
        self.calls.append(('inv',))

    def KillTimer(self, hwnd, tid):
        self.calls.append(('kill', tid))
        return True

    def SetTimer(self, hwnd, tid, ms, proc):
        self.calls.append(('timer', tid, ms))
        return tid

    def SetParent(self, hwnd, parent):
        self.calls.append(('parent', parent))
        return None

    def FindWindowW(self, cls, title):
        return self._progman if cls == 'Progman' else None

    def SendMessageTimeoutW(self, hwnd, msg, wp, lp, flags, ms, out):
        self.calls.append(('spawn_workerw', msg))
        return 1

    def FindWindowExW(self, parent, after, cls, title):
        if cls == 'SHELLDLL_DefView':
            return 888 if parent == 666 else None
        if cls == 'WorkerW':
            return self._workerw if after == 666 else None
        return None

    def EnumWindows(self, cb, lp):
        cb(666, lp)
        return True

    def GetWindowRect(self, hwnd, ref):
        r = ref._obj
        r.left, r.top, r.right, r.bottom = -100, -50, 3740, 2110
        return True


def make_ov(progman=555, workerw=777):
    o = ov_mod.HudOverlay()
    o._u = FakeU(progman, workerw)
    o._hwnd = 12345
    o._visible = True
    o._prefs = dict(ov_mod.DEFAULT_PREFS)
    o._relayout = lambda: None
    return o


def last(o, kind):
    for c in reversed(o._u.calls):
        if c[0] == kind:
            return c
    return None


o = make_ov()
o._sync_parent(True)
check(last(o, 'parent') == ('parent', 777), 'desktop_only -> 挂到壁纸 WorkerW 下')
check(o._parent == 777, '_parent 记录 WorkerW')
check(last(o, 'pos')[1] is ov_mod.HWND_NOTOPMOST, '挂桌面后清除 TOPMOST')
check(('spawn_workerw', 0x052C) in o._u.calls, '发 0x052C 生成壁纸层 WorkerW')
n = len(o._u.calls)
o._sync_parent(True)
check(len(o._u.calls) == n, '重复同步不重复 SetParent')

o._sync_parent(False)
check(last(o, 'parent') == ('parent', None) and o._parent is None,
      '关闭 desktop_only -> 回到顶层')
check(last(o, 'pos')[1] is ov_mod.HWND_TOPMOST, '回顶层后恢复始终置顶')

o2 = make_ov(workerw=0)      # 枚举不到壁纸 WorkerW
o2._sync_parent(True)
check(o2._parent == 555, '找不到 WorkerW 回退 Progman')

o3 = make_ov(progman=0)      # 连 Progman 都没有
o3._sync_parent(True)
check(o3._parent is None and last(o3, 'parent') is None,
      '无桌面壳时不挂父窗（fail-open）')

o4 = make_ov()
o4._update_visibility()
check(o4._shown is True
      and ('show', ov_mod.SW_SHOWNOACTIVATE) in o4._u.calls, '请求显示 -> 显示')
check(last(o4, 'parent') is None, '显隐不碰父子层级')
o4._visible = False
o4._update_visibility()
check(o4._shown is False and ('show', ov_mod.SW_HIDE) in o4._u.calls,
      '守护请求隐藏 -> SW_HIDE（游戏隐藏路径）')
o4._visible = True
o4._update_visibility()
check(o4._shown is True, '隐藏后可重新显示')

o5 = make_ov()
o5._parent = 777
o5._prefs['position'] = 'custom'
o5._prefs['x'], o5._prefs['y'] = 100, 200
o5._work_area = lambda: (0, 0, 1920, 1080)
o5._reposition()
c = last(o5, 'pos')
check(c[2] == 200 and c[3] == 250, '子窗坐标换算父窗原点偏移 (100,200)->(200,250)')
o5._parent = None
o5._reposition()
c = last(o5, 'pos')
check(c[2] == 100 and c[3] == 200, '顶层时坐标不换算')

print('[D] 游戏中停采样（FakeU + 桩采样器）')


class StubSampler(object):
    def __init__(self):
        self.n = 0

    def sample(self):
        self.n += 1
        return {'cpu': 1}


o6 = make_ov()
o6._sampler = StubSampler()
o6._apply_paused(True)
check(o6._paused is True and last(o6, 'kill') == ('kill', 1),
      '暂停 -> KillTimer 停采样节拍')
n = len(o6._u.calls)
o6._apply_paused(True)
check(len(o6._u.calls) == n, '重复暂停无冗余 API')
o6._apply_paused(False)
check(o6._paused is False and last(o6, 'timer')[1] == 1
      and o6._sampler.n == 1 and last(o6, 'inv') == ('inv',),
      '恢复 -> 重建定时器 + 立即补采样 + 重绘')

print('[E] v1.4.0 刷新节拍不被守护 tick 劫持（FakeU，不建真实窗口）')


class FakeU2(FakeU):
    def PostMessageW(self, hwnd, msg, wp, lp):
        self.calls.append(('post', msg))
        return True

    def GetWindowLongPtrW(self, hwnd, idx):
        return 0

    def SetWindowLongPtrW(self, hwnd, idx, val):
        return 0

    def SetLayeredWindowAttributes(self, hwnd, key, alpha, flags):
        return True


ovr = ov_mod.HudOverlay()
ovr._u = FakeU2()
ovr._hwnd = 12345
ovr._prefs = dict(ov_mod.DEFAULT_PREFS)
ovr.set_prefs(dict(ov_mod.DEFAULT_PREFS))
check(last(ovr, 'post') is not None, '首次偏好请求投递刷新')
n = len(ovr._u.calls)
for _ in range(5):
    ovr.set_prefs(dict(ov_mod.DEFAULT_PREFS))
check(len(ovr._u.calls) == n, '相同偏好重复推送不再投递（守护每 tick 都推）')
ovr.set_prefs(dict(ov_mod.DEFAULT_PREFS, opacity=100))
check(last(ovr, 'post') == ('post', ov_mod.WM_APP_REFRESH), '偏好真变化仍投递')

o7 = make_ov()
o7._u = FakeU2()
o7._make_font = lambda prefs: None      # 隔离 GDI，只测定时器装载逻辑
o7._apply_style = lambda prefs: None
o7._sync_parent = lambda v: None
o7._apply_prefs(dict(ov_mod.DEFAULT_PREFS), initial=False)
t1 = [c for c in o7._u.calls if c[0] == 'timer']
check(len(t1) == 1, '首次 _apply_prefs 装载定时器一次')
o7._apply_prefs(dict(ov_mod.DEFAULT_PREFS), initial=False)
t2 = [c for c in o7._u.calls if c[0] == 'timer']
check(len(t2) == 1, 'refresh_sec 未变 -> 不重装定时器（同 id 重装会重置计时）')
o7._apply_prefs(dict(ov_mod.DEFAULT_PREFS, refresh_sec=2.0), initial=False)
t3 = [c for c in o7._u.calls if c[0] == 'timer']
check(len(t3) == 2 and t3[-1][2] == 2000, 'refresh_sec 变化 -> 重装为新节拍')
o7._apply_paused(True)
check(o7._timer_ms == 0, '暂停后节拍清零，恢复时会重新装载')

print('[F] v1.4.0 退出前排空请求队列（菜单改动不丢）')
ctx2 = FakeCtx()
p2 = mod.Plugin(ctx2)
stub2 = StubOverlay()
p2._overlay = stub2
p2.on_config({'desktop_hud_enabled': True, 'desktop_hud': {}})
p2.on_load()
p2.on_start()
p2._on_request({'action': 'prefs',
                'prefs': dict(mod.hud_overlay.DEFAULT_PREFS, opacity=123)})
check(ctx2.written.get('hud') is None, '入队后未到 on_tick，尚未落盘')
p2.on_exit('guard_stop')
check(ctx2.written.get('hud', {}).get('prefs', {}).get('opacity') == 123,
      'on_exit 排空队列 -> 偏好已落盘')

print('[G] v1.4.0 取数模块回归（_cdll 大小写 / PMLog 量纲 / 故障可见）')
import ctypes                      # noqa: E402
import importlib.util as _ilu      # noqa: E402
import types as _types             # noqa: E402

_spec = _ilu.spec_from_file_location('hud_metrics_under_test',
                                     os.path.join(dst, 'hud_metrics.py'))
hm = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(hm)
try:
    hm._cdll('kernel32.dll')
    _ok = True
except Exception:
    _ok = False
check(_ok, '_cdll 不再 NameError（_CDECL 大小写回归）')


def _mk_adl(pmlog, fn_name, fn):
    a = hm.AdlClock()
    a._ready = True
    a._pmlog = pmlog
    a._idx = 0
    a._pmlog_buf = hm.ADLPMLogDataOutput()
    a._pmlog_buf.size = ctypes.sizeof(a._pmlog_buf)
    a._adl = _types.SimpleNamespace(**{fn_name: fn})
    return a


def _q(ctx, idx, bufptr):
    b = bufptr._obj
    b.sensors[hm.PMLOG_CLK_GFXCLK].supported = 1
    b.sensors[hm.PMLOG_CLK_GFXCLK].value = 2500
    return 0


check(_mk_adl(True, 'ADL2_New_QueryPMLogData_Get', _q).sample() == 2500.0,
      'PMLog 读数按 MHz 原样返回，不除 100')


def _od5(ctx, idx, actptr):
    actptr._obj.iEngineClock = 250000
    return 0


check(_mk_adl(False, 'ADL2_Overdrive5_CurrentActivity_Get', _od5).sample() == 2500.0,
      'Overdrive5 回退路径仍按 10kHz /100')


def _boom(ctx, idx, bufptr):
    raise RuntimeError('probe')


_a3 = _mk_adl(True, 'ADL2_New_QueryPMLogData_Get', _boom)
check(_a3.sample() is None and 'RuntimeError' in (_a3.last_error or ''),
      '异常记入 last_error，不再被宽 except 静默吞掉')

print('[H] v1.5.0 GDI 缓存 / 多显示器定位 / 模块缓存不跨插件污染')


def _deref(p):
    """ctypes.byref(x) 传进来的是 CArgObject，取 ._obj 才是结构体本身。"""
    return getattr(p, '_obj', p)


class FakeG(object):
    def __init__(self):
        self.fonts = self.deletes = self.brushes = self.extents = 0

    def CreateFontW(self, *a):
        self.fonts += 1
        return 0xF000 + self.fonts

    def DeleteObject(self, h):
        self.deletes += 1
        return True

    def CreateSolidBrush(self, color):
        self.brushes += 1
        return 0xB000 + self.brushes

    def SelectObject(self, hdc, obj):
        return 1

    def GetTextExtentPoint32W(self, hdc, text, n, out):
        self.extents += 1
        o = _deref(out)
        o.cx, o.cy = 8 * n, 16
        return True

    def SetBkMode(self, hdc, mode):
        return 0

    def SetTextColor(self, hdc, col):
        return 0


class FakeU3(FakeU2):
    """副屏在 (1920,0)-(4480,1440)；主屏工作区故意设成不同值以便区分。"""

    def GetDC(self, hwnd):
        return 999

    def ReleaseDC(self, hwnd, hdc):
        return 1

    def MonitorFromWindow(self, hwnd, flags):
        self.calls.append(('mon', flags))
        return 4242

    def GetMonitorInfoW(self, hmon, mi):
        rc = _deref(mi).rcWork
        rc.left, rc.top, rc.right, rc.bottom = 1920, 0, 4480, 1440
        return True

    def SystemParametersInfoW(self, action, p, out, f):
        rc = _deref(out)
        rc.left, rc.top, rc.right, rc.bottom = 0, 0, 1920, 1040
        return True

    def BeginPaint(self, hwnd, ps):
        return 999

    def EndPaint(self, hwnd, ps):
        return True

    def GetClientRect(self, hwnd, rc):
        r = _deref(rc)
        r.left, r.top, r.right, r.bottom = 0, 0, 200, 90
        return True

    def FillRect(self, hdc, rc, brush):
        self.calls.append(('fill', brush))
        return 1

    def DrawTextW(self, hdc, text, n, rc, fmt):
        return 1


o8 = ov_mod.HudOverlay()
o8._u, o8._g = FakeU3(), FakeG()
o8._hwnd = 12345
o8._prefs = dict(ov_mod.DEFAULT_PREFS)
o8._make_font(o8._prefs)
check(o8._g.fonts == 1, '首次建字体一次')
for _ in range(5):
    o8._make_font(dict(o8._prefs))
check(o8._g.fonts == 1 and o8._g.deletes == 0,
      '字号/DPI 未变 -> 不重建字体（原来每次偏好都 DeleteObject+CreateFontW）')
o8._make_font(dict(o8._prefs, font_size=20))
check(o8._g.fonts == 2 and o8._g.deletes == 1, '字号变化 -> 重建并释放旧字体')
o8._make_font(dict(o8._prefs, font_size=20))

_GIB = 1073741824.0
o8._sample = {'cpu': 2.0, 'cpu_mhz': 3800.0, 'gpu': 6.0, 'gpu_mhz': 200.0,
              'vram_percent': 13.0, 'vram_used': 1.5 * _GIB,
              'vram_total': 12 * _GIB, 'ram_percent': 35.0,
              'ram_used': 11.2 * _GIB, 'ram_total': 32 * _GIB}
o8._relayout()
_e1 = o8._g.extents
check(_e1 > 0, '首次布局量过文本')
o8._sample = dict(o8._sample, cpu=7.0, gpu=9.0, ram_percent=41.0)
o8._relayout()
check(o8._g.extents == _e1, '数值变了但位数没变 -> 不重新量文本（等宽字体）')
_p1 = len([c for c in o8._u.calls if c[0] == 'pos'])
o8._relayout()
check(len([c for c in o8._u.calls if c[0] == 'pos']) == _p1,
      '位置尺寸未变 -> 不重复 SetWindowPos')
o8._sample = dict(o8._sample, vram_used=10.5 * _GIB)   # 1.5/12G -> 10.5/12G，位数变了
o8._relayout()
check(o8._g.extents > _e1, '位数变化 -> 重新量文本')
# 注意：VRAM 行变长后仍不超过最长的 RAM 行，窗口宽度不变，不该重设位置。
# 要让宽度真变，得把最长行（RAM）撑长：32G -> 128G。
o8._sample = dict(o8._sample, ram_total=128 * _GIB)
o8._relayout()
check(len([c for c in o8._u.calls if c[0] == 'pos']) > _p1,
      '窗口宽度真的变化 -> 重新 SetWindowPos')

check(o8._work_area() == (1920, 0, 4480, 1440),
      '取窗口所在显示器的工作区（副屏），不再固定用主屏')
check(('mon', ov_mod.MONITOR_DEFAULTTONEAREST) in o8._u.calls,
      '走 MonitorFromWindow + GetMonitorInfoW')

o9 = ov_mod.HudOverlay()
o9._u, o9._g = FakeU3(), FakeG()
o9._hwnd = 12345
o9._u.MonitorFromWindow = None      # 模拟拿不到显示器 API
check(o9._work_area() == (0, 0, 1920, 1040), '拿不到显示器信息时回退主屏工作区')

o8._paint(12345)
o8._paint(12345)
o8._paint(12345)
check(o8._g.brushes == 1, '背景刷只建一次（原来每次 WM_PAINT 都建了再删）')
_fills = [c for c in o8._u.calls if c[0] == 'fill']
check(len(_fills) == 3 and all(c[1] == _fills[0][1] for c in _fills),
      '三次绘制复用同一个刷句柄')
o8._teardown()
check(o8._bg_brush is None and o8._font_key is None and o8._last_pos is None,
      '销毁时释放缓存并作废，下次建窗会重新初始化')

# 包上下文下不得踢掉顶层同名模块（跨插件污染）
_sentinel = object()
sys.modules['hud_overlay'] = _sentinel
_pkgname = 'hud_pkg_probe'
_pkg = _types.ModuleType(_pkgname)
_pkg.__path__ = [dst]
sys.modules[_pkgname] = _pkg
_pspec = _ilu.spec_from_file_location(_pkgname + '.plugin',
                                      os.path.join(dst, 'plugin.py'))
_pmod = _ilu.module_from_spec(_pspec)
sys.modules[_pkgname + '.plugin'] = _pmod
_pspec.loader.exec_module(_pmod)
check(sys.modules.get('hud_overlay') is _sentinel,
      '包上下文下只摘本包内缓存，不动顶层同名模块')
sys.modules.pop('hud_overlay', None)

shutil.rmtree(TMP, ignore_errors=True)
print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')