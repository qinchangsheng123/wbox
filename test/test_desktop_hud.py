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
import time
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


class FakeU2(FakeU):
    """加 PostMessageW / 扩展样式 / 分层 alpha：偏好投递与采样线程要的。"""

    def PostMessageW(self, hwnd, msg, wp, lp):
        self.calls.append(('post', msg))
        return True

    def GetWindowLongPtrW(self, hwnd, idx):
        return 0

    def SetWindowLongPtrW(self, hwnd, idx, val):
        return 0

    def SetLayeredWindowAttributes(self, hwnd, key, alpha, flags):
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

print('[D] v1.6.0 游戏中停采样 = 停采样线程（FakeU + 桩采样器）')


class StubSampler(object):
    def __init__(self, tag=''):
        self.n = 0
        self.cfg = []
        self.tag = tag
        self.closed = False

    def sample(self):
        self.n += 1
        return {'cpu': 1}

    def set_gpu_clock_enabled(self, on):
        self.cfg.append(('clock', on))

    def set_gpu_target(self, mode, luid=None):
        self.cfg.append(('target', mode, luid))

    def adapters(self, ttl=None):
        return []

    def diagnostics(self):
        return ['stub=called']

    def close(self):
        self.closed = True


def _worker_alive(o):
    return bool(o._worker is not None and o._worker.is_alive())


MADE = []


def install_stub(o, factory=None):
    """把采样器工厂换成桩：记录每条线程自己创建的那个实例。"""
    made = []

    def make(cfg):
        s = StubSampler(tag='s%d' % (len(made) + 1))
        made.append(s)
        if factory is not None:
            factory(s)
        return s
    o._make_sampler = make
    return made


o6 = make_ov()
o6._u = FakeU2()
o6._interval = 200
made6 = install_stub(o6)
o6._pending_sampler_cfg = {'gpu_clock': True, 'gpu_select': 'auto',
                           'gpu_luid': ''}
o6._start_worker()
time.sleep(0.3)
check(_worker_alive(o6) and made6 and made6[0].n >= 1,
      '采样线程起来并产出读数（窗口线程不参与采样）')
o6._apply_paused(True)
check(o6._paused is True and not _worker_alive(o6),
      '暂停 -> 采样线程退出')
n = made6[0].n
time.sleep(0.3)
check(made6[0].n == n, '暂停期间不再采样')
check(made6[0].closed, '采样器由自己的线程关闭（暂停后句柄已释放）')
o6._apply_paused(False)
time.sleep(0.3)
check(o6._paused is False and _worker_alive(o6) and made6[-1].n >= 1,
      '恢复 -> 重新起采样线程并继续采样')
check(len(made6) == 2 and made6[0] is not made6[1],
      '恢复时新建采样器（不与上一条线程共用句柄）')
check([c for c in made6[-1].cfg if c[0] == 'clock'],
      '采样器配置在采样线程里应用（GPU 频率开关）')
o6._stop_worker()

print('[D2] v1.6.1 回归：旧采样线程卡住时禁止起第二条线程')


class StuckSampler(StubSampler):
    """sample() 卡住不返回，模拟驱动调用/PDH 卡死。"""

    def __init__(self, gate, tag=''):
        StubSampler.__init__(self, tag)
        self.gate = gate
        self.entered = False

    def sample(self):
        self.entered = True
        self.gate.wait(2.0)
        return StubSampler.sample(self)


gate = __import__('threading').Event()
o8 = make_ov()
o8._u = FakeU2()
o8._interval = 200
made8 = []
o8._make_sampler = lambda cfg: (made8.append(StuckSampler(gate, 'stuck'))
                                or made8[-1])
o8._start_worker()
for _ in range(40):
    if made8 and made8[0].entered:
        break
    time.sleep(0.05)
check(bool(made8 and made8[0].entered), '第一线程已卡在采样里')
o8._stop_worker(timeout=0.2)
check(_worker_alive(o8) and o8._sampler_state == 'orphan',
      '卡住的线程被标为 orphan（引用保留，不丢线程）')
check(o8._start_worker({'gpu_clock': False, 'gpu_select': 'auto',
                        'gpu_luid': ''}) is False,
      '旧线程未退出 -> 拒绝起新采样线程')
check(len(made8) == 1 and o8._restart_pending is True,
      '拒绝期间不新建采样器（无句柄共享）')
gate.set()
time.sleep(0.4)
check(not _worker_alive(o8), '放行后旧线程自行退出')
check(made8[0].closed, '旧线程退出时关掉自己的采样器')
check(o8._start_worker({'gpu_clock': False, 'gpu_select': 'auto',
                        'gpu_luid': ''}) is True,
      '旧线程退出后才允许新建采样器')
check(len(made8) == 2 and made8[1] is not made8[0] and not made8[1].closed,
      '第二条线程拿到的是全新采样器（旧的那个已关闭）')
o8._stop_worker()

print('[E] v1.6.0 节拍改动即时生效（FakeU，不建真实窗口）')


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
o7._make_font = lambda prefs: None      # 隔离 GDI
o7._apply_style = lambda prefs: None
o7._sync_parent = lambda v: None
o7._apply_prefs(dict(ov_mod.DEFAULT_PREFS), initial=False)
check(o7._interval == 1000, '默认节拍 1000ms')
o7._apply_prefs(dict(ov_mod.DEFAULT_PREFS, refresh_ms=2000), initial=False)
check(o7._interval == 2000, 'refresh_ms 变化 -> 节拍即时更新（无需重装定时器）')
o7._apply_prefs(dict(ov_mod.DEFAULT_PREFS, refresh_ms=50), initial=False)
check(o7._interval == 200, 'refresh_ms 下限钳到 200ms')
o7._apply_prefs(dict(ov_mod.DEFAULT_PREFS, refresh_ms=99999), initial=False)
check(o7._interval == 10000, 'refresh_ms 上限钳到 10000ms')
check(o7._pending_sampler_cfg is not None
      and o7._pending_sampler_cfg['gpu_select'] == 'auto',
      '采样器参数排队给采样线程（窗口线程不直接调原生）')

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

print('[I] v1.6.0 选卡 / 重枚举 / 退避 / 诊断 / 阈值精度')

# I1 节拍与阈值钳制
check(ov_mod._interval_ms({'refresh_ms': 100}) == 200, '节拍下限 200ms')
check(ov_mod._interval_ms({'refresh_ms': 'abc'}) == 1000, '坏值回默认 1000ms')
check(ov_mod._interval_ms({'refresh_ms': 12000}) == 10000, '节拍上限 10000ms')
check(ov_mod._pct_num({'warn_percent': 0}, 'warn_percent', 70) == 1,
      '阈值下限钳到 1')
check(ov_mod._pct_num({}, 'high_percent', 90) == 90, '阈值缺省 90')

# I2 百分比小数位
o10 = make_ov()
o10._sample = {'cpu': 42.4, 'gpu': 90.2, 'ram_percent': 30.0,
               'vram_percent': 5.0, 'cpu_mhz': 3800.0, 'gpu_mhz': 250.0,
               'vram_used': 1.0 * 1073741824, 'vram_total': 8 * 1073741824,
               'ram_used': 10.0 * 1073741824, 'ram_total': 32 * 1073741824}
o10._prefs['pct_decimals'] = 1
_lines = dict((label, val) for label, val, _p in o10._build_lines())
check(_lines['CPU'].startswith(' 42.4%'), 'pct_decimals=1 -> 42.4%')
o10._prefs['pct_decimals'] = 0
_lines = dict((label, val) for label, val, _p in o10._build_lines())
check(_lines['CPU'].strip() == '42%', 'pct_decimals=0 -> 整数百分比')


# I3 选卡规则：auto 按显存总量、load 按占用、luid 指定
class StubAdapters(object):
    def __init__(self, ads):
        self.ads = list(ads)
        self.last_error = None
        self.list_calls = 0

    def list(self, ttl=None):
        self.list_calls += 1
        return list(self.ads)

    def name_of(self, luid):
        for a in self.ads:
            if a['luid'] == luid:
                return a['name']
        return None


BIG = {'luid': '0x00000000_0x00001111', 'name': 'Big GPU', 'vram': 12 << 30}
SMALL = {'luid': '0x00000000_0x00002222', 'name': 'Small GPU', 'vram': 4 << 30}
_mem = [('luid_0x00000000_0x00001111_phys_0', 100.0),
        ('luid_0x00000000_0x00002222_phys_0', 900.0)]
_eng = [('luid_0x00000000_0x00001111_phys_0_eng_0_engtype_3D', 10.0),
        ('luid_0x00000000_0x00002222_phys_0_eng_1_engtype_3D', 50.0)]


def mk_pdh(mode='auto', luid=None, ads=(BIG, SMALL)):
    p = hm.PdhCounters()
    p.adapters = StubAdapters(ads)
    p.set_target_pref(mode, luid)
    return p


p_auto = mk_pdh('auto')
_l = p_auto._luids(_mem, _eng)
check(p_auto._pick_target(_mem, _l) == BIG['luid']
      and p_auto._target_source.startswith('auto'),
      'auto 选显存总量最大的卡（12G），而不是占用最高的卡（900MB 那张）')
check(p_auto._target_name == 'Big GPU', '目标卡名称来自 DXGI 映射')

p_load = mk_pdh('load')
_l = p_load._luids(_mem, _eng)
check(p_load._pick_target(_mem, _l) == SMALL['luid'],
      'load 沿用旧规则：显存占用最大')
check(p_load._target_name == 'Small GPU', 'load 也补齐显卡名')

p_luid = mk_pdh('luid', SMALL['luid'])
_l = p_luid._luids(_mem, _eng)
check(p_luid._pick_target(_mem, _l) == SMALL['luid'], 'luid 模式尊重指定卡')

p_gone = mk_pdh('luid', '0x00000000_0x00009999')
_l = p_gone._luids(_mem, _eng)
check(p_gone._pick_target(_mem, _l) == BIG['luid']
      and 'luid_gone' in (p_gone.last_event or ''),
      '指定卡不在了 -> 回落 auto 并记录事件')

# I4 目标卡连续消失 -> 重枚举
class FakePdhObj(object):
    def PdhCollectQueryData(self, q):
        return 0


def drive(p, mem, eng):
    p._query = object()
    p._pdh = FakePdhObj()
    p._collect_array = (lambda c: mem if c == 'mem' else
                        (eng if c == 'eng' else []))
    p._c_mem, p._c_engine, p._c_perf = 'mem', 'eng', 'perf'
    return p


p_miss = mk_pdh('luid', BIG['luid'])
drive(p_miss, [], [])
p_miss._target = BIG['luid']        # 模拟上一拍的选卡结果
p_miss.sample()
p_miss.sample()
check(p_miss.target_rebuilds == 0 and p_miss._misses == 2,
      '连续 2 轮找不到目标卡仍保留（还没到 MISS_LIMIT）')
p_miss.sample()
check(p_miss.target_rebuilds == 1 and p_miss._target is None,
      '第 3 轮判缺失 -> 作废目标并计一次重枚举')
drive(p_miss, _mem, _eng)
p_miss.sample()
check(p_miss._target == BIG['luid'], '设备恢复后重新选到卡')

# I5 PDH 初始化失败退避后能重建（原来永久放弃）
p_re = hm.PdhCounters()
p_re._fail('probe')
check(p_re.sample() is None and p_re._failed, '退避期内直接返回 None')
p_re._next_retry = 0.0


def _fake_setup():
    p_re._query = object()
    p_re._failed = False
    p_re._pdh = FakePdhObj()
    p_re._c_mem = p_re._c_engine = p_re._c_perf = None


p_re._setup = _fake_setup
p_re._collect_array = lambda c: []
p_re.sample()
check(p_re._failed is False and p_re.reinit_count == 1,
      '退避到期 -> 重建查询并恢复采样（reinit 计数 +1）')

# I6 分项状态
st = hm.PdhCounters().status()
for k in ('ok', 'init_rc', 'collect_rc', 'reinit', 'rebuilds', 'target',
          'target_source', 'error', 'event', 'adapters'):
    check(k in st, 'status() 含分项字段 %s' % k)

# I7 真实 DXGI 枚举（本机；拿不到就说明原因，不判红）
ads_real = hm.DxgiAdapters()
_lst = ads_real.list(ttl=0.0)
if _lst:
    check(all(a['luid'].startswith('0x') and a['name'] for a in _lst),
          'DXGI 枚举出 %d 个适配器：%s'
          % (len(_lst), ', '.join(a['name'] for a in _lst)))
    check(all(_lst[i]['vram'] >= _lst[i + 1]['vram'] for i in range(len(_lst) - 1)),
          '适配器按显存总量降序')
else:
    print('  skip DXGI 枚举（%s）' % (ads_real.last_error or '无适配器'))

# I8 菜单：选卡与诊断落 prefs / 日志
REQS = []
o11 = ov_mod.HudOverlay(request_cb=lambda r: REQS.append(r))
o11._u = FakeU2()
o11._hwnd = 12345
o11._prefs = dict(ov_mod.DEFAULT_PREFS)
o11._make_font = lambda prefs: None
o11._apply_style = lambda prefs: None
o11._sync_parent = lambda v: None
o11._relayout = lambda: None
o11._menu_adapters = [dict(BIG), dict(SMALL)]
o11._apply_menu(ov_mod.M_GPU_BASE + 1)
check(o11._prefs['gpu_select'] == 'luid'
      and o11._prefs['gpu_luid'] == SMALL['luid'],
      '菜单选第 2 张卡 -> gpu_select=luid + 对应 LUID')
check([r for r in REQS if r.get('action') == 'prefs'][-1]['prefs']['gpu_select']
      == 'luid', '选卡结果回传插件持久化')
o11._apply_menu(ov_mod.M_GPU_AUTO)
check(o11._prefs['gpu_select'] == 'auto' and o11._prefs['gpu_luid'] == '',
      '菜单「自动」清掉指定 LUID')
o11._menu_adapters = []             # 模拟菜单快照过期（换卡 / 拔卡）
o11._apply_menu(ov_mod.M_GPU_BASE)
check(o11._prefs['gpu_select'] == 'auto' and o11._prefs['gpu_luid'] == '',
      '适配器表为空时不改偏好（防越界写入错误 LUID）')

o11._backend_lines = ['stub=called']
o11._slow_events = 2
o11._apply_menu(ov_mod.M_DIAG)
_diag = [r['msg'] for r in REQS if r.get('action') == 'log']
check(_diag and 'hud running=' in _diag[-1] and 'stub=called' in _diag[-1],
      '诊断项把分项状态写进日志（含采样线程节拍与后端）')

# I9 refresh_sec → refresh_ms 迁移（config 与运行期快照两条路）
ctx3 = FakeCtx()
p3 = mod.Plugin(ctx3)
p3._overlay = StubOverlay()
p3.on_config({'desktop_hud_enabled': True,
              'desktop_hud': {'refresh_sec': 0.4}})
check(p3._effective().get('refresh_ms') == 400
      and 'refresh_sec' not in p3._effective(),
      'config 里的 refresh_sec=0.4 迁移为 refresh_ms=400 并去掉旧键')
ctx3.store['hud'] = {'prefs': {'refresh_sec': 2.5}}
p3.on_load()
check(p3._effective().get('refresh_ms') == 2500,
      '运行期快照里的 refresh_sec=2.5 迁移为 2500ms')
ctx4 = FakeCtx()
p4 = mod.Plugin(ctx4)
p4._overlay = StubOverlay()
p4.on_config({'desktop_hud_enabled': True,
              'desktop_hud': {'refresh_sec': 0.4, 'refresh_ms': 800}})
check(p4._effective().get('refresh_ms') == 800,
      '两项同时存在时 refresh_ms 优先')

# J v1.6.0 自定义坐标落到屏外 -> 回收到可见区（显示器布局变化的善后）
class FakeU4(FakeU2):
    """单屏 2560x1440；MonitorFromWindow 故意失败，走 SPI 回退。"""

    def MonitorFromWindow(self, hwnd, flags):
        return 0

    def SystemParametersInfoW(self, action, p, out, f):
        rc = _deref(out)
        rc.left, rc.top, rc.right, rc.bottom = 0, 0, 2560, 1440
        return True

    def GetSystemMetrics(self, idx):
        return {76: 0, 77: 0, 78: 2560, 79: 1440}.get(idx, 0)

REQS.clear()
o12 = ov_mod.HudOverlay(request_cb=lambda r: REQS.append(r))
u12 = FakeU4()
o12._u = u12
o12._hwnd = 4321
o12._w, o12._h = 220, 140
o12._prefs = dict(ov_mod.DEFAULT_PREFS)
o12._prefs.update({'position': 'custom', 'x': 1779, 'y': 1956, 'margin': 16})
o12._shown = True
o12._reposition()
hits = [c for c in u12.calls if c[0] == 'pos']
check(bool(hits) and hits[-1][2] == 2560 - 220 - 16 and hits[-1][3] == 16,
      '屏外自定义坐标回收到右上角')
check(any(r.get('action') == 'log' and '在可见屏幕之外' in (r.get('msg') or '')
          for r in REQS), '回收时写一行日志说明原因')
REQS.clear()
o12._last_pos = None
o12._reposition()
check(not [r for r in REQS if r.get('action') == 'log'],
      '回收日志只记一次（不刷屏）')
REQS.clear()
o12._prefs.update({'x': 100, 'y': 120})
o12._last_pos = None
u12.calls = []
o12._reposition()
hits = [c for c in u12.calls if c[0] == 'pos']
check(bool(hits) and hits[-1][2] == 100 and hits[-1][3] == 120,
      '屏内自定义坐标原样保留（不误搬）')

shutil.rmtree(TMP, ignore_errors=True)
print('')
if FAILS:
    print('FAILED %d 项：%s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')