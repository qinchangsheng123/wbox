# -*- coding: utf-8 -*-
"""WBOX - 进程启动/停止事件唤醒器（v2.5.0-③）。

三条候选路线的取舍（评估结论见 design/proc-events.md）：
  a) ctypes 直连 WMI COM（本文件）—— 零新依赖，Setup 增量为 0（不含本模块
     自身字节码）；Win32_ProcessStartTrace / StopTrace 是系统推送事件，
     空闲时守护侧唯一开销是内核级等待，CPU 不升反降。
  b) ctypes SetWinEventHook —— 只覆盖窗口事件，无窗口进程（反作弊服务、
     小飞机后台组件）收不到；且回调是全系统窗口事件风暴，空闲 CPU 反升。
  c) `import wmi` —— 拖整条 pywin32（pythoncom/win32com/PyWinTypesse…），
     实测打包增量远超 60 KB 预算，否决。

语义约定（与闸门/插件的兼容边界）：
  - 本模块只做「唤醒器」：进程事件到达 -> SetEvent。判定逻辑一行不改，
    还是守护主循环那一轮 tick（`procs is None` 降级规则原样保留）。
  - 轮询保留为低频兜底；本模块任何环节失败（未提权 / COM 不可用 / 订阅被拒），
    `ok` 置 False，守护主循环自动回落纯轮询并记一行日志——绝不能因此不工作。
  - 过滤在回调侧做（watch 名单比对）：全系统进程事件频率低（每秒个位数），
    线程唤醒开销可忽略；换来的是 watch 名单变化不用重新订阅。

COM 细节（为什么代码长）：不用 comtypes / pywin32，全部 vtable 手写。
  CoInitializeEx(MTA) -> CoInitializeSecurity（进程级一次，重复调用容忍）
  -> CoCreateInstance(CLSID_WbemLocator) -> ConnectServer(ROOT\\CIMV2)
  -> CoSetProxyBlanket -> ExecNotificationQuery(WQL, Win32_Process{Start,Stop}Trace)
  -> 循环 Next(timeout=10000ms)：超时=查停机位；有事件=比对 watch 名单。

可诊断性（v2.11.3）：
  - 每次失败都带**阶段名 + HRESULT**（CoCreateInstance / ConnectServer /
    ExecNotificationQuery / Next），并由 describe_hr() 翻成一句中文原因：
    "WMI 服务未启动" / "拒绝访问（需要管理员权限）" / "RPC 服务不可用"…
  - 订阅失败不放弃：按 60s 起、最长 600s 的退避重试。WMI 服务被临时停掉、
    开机早期 RPC 未就绪这类问题，等服务回来后自动回到事件模式，不用重启守护。
  - status() 输出一条可直接塞进心跳的降级说明；守护把它透到面板，
    用户看到的是「进程事件不可用，当前使用轮询」而不是只有一个"运行中"。
"""
import ctypes
import ctypes.wintypes as wt
import threading
import time

# ---- Win32 / COM 常量 ----
COINIT_MULTITHREADED = 0x0
RPC_E_TOO_LATE = 0x80010119
WBEM_S_NO_ERROR = 0
# v2.18.2 修正：微软 MS-WMI 定义 WBEM_S_TIMEDOUT = 0x00040004（非错误状态）。
# 旧值 0x00040001 是 WBEM_S_ALREADY_EXISTS，导致每次正常超时都被 _consume
# 当成硬错误，订阅反复重建、面板误报「进程事件监听不可用」。
WBEM_S_TIMEDOUT = 0x00040004
WBEM_S_FALSE = 1
WBEM_E_ACCESS_DENIED = 0x80041003
VT_BSTR = 8
VT_I4 = 3
VT_UI4 = 19
NEXT_TIMEOUT_MS = 10000         # watcher 线程的停机检查粒度。
# 为什么是 10s 而不是 2s：Next 每次超时返回都是一次 WMI 往返，2s 粒度实测
# 把空闲 CPU 从 0.14% 抬到 0.24%（每秒 2 次 × 2 线程）。10s 粒度下停机由
# daemon 线程随进程退出兜底（stop() 只等 1.5s，不阻塞守护退出）。

CLSID_WbemLocator = '{4590F811-1D3A-11D0-891F-00AA004B2E24}'
IID_IWbemLocator = '{DC12A687-737F-11CF-884D-00AA004B2E24}'

START_QUERY = 'SELECT * FROM Win32_ProcessStartTrace'
STOP_QUERY = 'SELECT * FROM Win32_ProcessStopTrace'

# vtable 下标（IUnknown 占 0-2）。IWbemServices 的权威顺序来自 MS-WMI 协议
# 规范的 opnum 表：…CreateInstanceEnumAsync(21) -> ExecQuery(20 之后)…
# 实测对照：ExecQuery=20（hr=0 验证）、ExecNotificationQuery=22。
# 注意 ExecNotificationQuery 的 lFlags 必须 WBEM_FLAG_RETURN_IMMEDIATELY(0x10)
# | WBEM_FLAG_FORWARD_ONLY(0x20)，否则恒返回 WBEM_E_INVALID_PARAMETER。
IDX_RELEASE = 2
IDX_CONNECT_SERVER = 3          # IWbemLocator
IDX_ENUM_NEXT = 4               # IEnumWbemClassObject
IDX_OBJ_GET = 4                 # IWbemClassObject（GetQualifierSet=3, Get=4）
IDX_EXEC_NOTIFICATION_QUERY = 22  # IWbemServices
EXEC_FLAGS = 0x30               # RETURN_IMMEDIATELY | FORWARD_ONLY

# v2.11.4：COM 方法返回的 HRESULT 必须用 c_long/c_ulong 接。
# ctypes.HRESULT 作 restype 时，失败码会被 ctypes 自动转成 OSError 抛出——
# 于是 _consume 里 `hr == WBEM_S_TIMEDOUT` / `hard_errors` / SUB_ERROR_LIMIT
# 这一整套"订阅被踢就重建"的判断永远走不到；OSError 冒泡到 _run 外层 except
# 直接结束线程，退避重连也一起失效。改用整数 restype 后失败码以返回值拿到，
# 上述自愈路径全部复活。Release 返回 ULONG 引用计数，也用 c_ulong 接。
_HRESULT = ctypes.c_long
_ULONG = ctypes.c_ulong
# 明确的 RPC/COM 断连码：命中即无等待重建订阅（不必凑满 SUB_ERROR_LIMIT）。
_DISCONNECT_HRS = frozenset((0x800706BA, 0x800706BE, 0x80010108, 0x80041032))

# 重订阅退避：60s 起、翻倍到 600s 封顶。WMI 服务被停一下、开机早期 RPC 未就绪
# 都能自动恢复；守护不需要为了"事件通道"重启（重启会丢会话状态）。
RETRY_MIN_SEC = 60.0
RETRY_MAX_SEC = 600.0
# Next 连续硬失败到这个次数就重建订阅（枚举器被 WMI 踢掉时的自救）。
SUB_ERROR_LIMIT = 3

# HRESULT -> 一句人话。诊断日志与面板降级说明都用它，避免让人去查错误码。
HR_HINTS = {
    0x00040004: 'WMI 调用超时（非错误，继续等待）',
    0x80041001: 'WMI 调用失败（WBEM_E_FAILED）',
    0x80041003: '拒绝访问：当前进程权限不够，需要以管理员身份运行',
    0x8004100E: 'WMI 命名空间无效：WMI 仓库可能损坏（可运行 winmgmt /verifyrepository）',
    0x80041010: 'WMI 类不存在：系统不支持 Win32_ProcessStartTrace（极少见的精简版系统）',
    0x80041032: 'WMI 调用被取消（查询句柄失效，稍后自动重建订阅）',
    0x80010108: 'COM 对象已断开（RPC 连接中断，稍后自动重建订阅）',
    0x80010119: 'COM 安全设置已被别的组件设过（无害，按已初始化处理）',
    0x80070422: 'WMI 服务（Winmgmt）未启动',
    0x800706BA: 'RPC 服务器不可用（WMI 服务正在重启）',
    0x80070005: '拒绝访问（E_ACCESSDENIED）',
}

_INVALID = ctypes.c_void_p(-1).value


def _raise_stage(err, stage):
    """给异常打上"失败发生在哪一步"，日志与 status() 都要用。"""
    if not getattr(err, 'stage', ''):
        try:
            err.stage = stage
        except Exception:
            pass
    raise err


def describe_hr(hr):
    """HRESULT -> 一句中文原因（查不到就说明是哪个错误码，别编原因）。"""
    hr = int(hr or 0) & 0xFFFFFFFF
    if not hr:
        return ''
    hint = HR_HINTS.get(hr)
    if hint:
        return hint
    if (hr >> 16) == 0x8007:                 # FACILITY_WIN32
        return 'Win32 错误码 %d' % (hr & 0xFFFF)
    return '错误码 0x%08x' % hr


class GUID(ctypes.Structure):
    _fields_ = [('Data1', wt.DWORD), ('Data2', wt.WORD), ('Data3', wt.WORD),
                ('Data4', ctypes.c_ubyte * 8)]


def _guid(text):
    g = GUID()
    hr = ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text),
                                             ctypes.byref(g))
    return g


class _VarUnion(ctypes.Union):
    # union 必须能容下 DECIMAL（16 B）：真实 VARIANT 的值区在偏移 8、总长 24，
    # 值区放错位置会拿到垃圾指针（WBEM_E_TYPE_MISMATCH / 崩溃）。
    _fields_ = [('bstr', ctypes.c_void_p), ('lVal', wt.LONG),
                ('ulVal', wt.ULONG), ('dec', ctypes.c_ubyte * 16)]


class VARIANT(ctypes.Structure):
    _fields_ = [('vt', ctypes.c_ushort), ('r1', wt.USHORT),
                ('r2', wt.USHORT), ('r3', wt.USHORT), ('u', _VarUnion)]


assert ctypes.sizeof(VARIANT) == 24, 'VARIANT 布局与 Win64 ABI 不符'


class OVERLAPPED_UNUSED(object):    # 占位：本模块不用 overlapped
    pass


_ole32 = ctypes.oledll.ole32
# 注意 oleaut32 必须用 windll：oledll 会把返回值当 HRESULT 校验，而
# SysAllocStringLen 返回的是 BSTR 指针（64 位大数 -> OverflowError）。
_oleaut32 = ctypes.windll.oleaut32
_kernel32 = ctypes.windll.kernel32

_oleaut32.SysAllocStringLen.restype = ctypes.c_void_p
_oleaut32.SysAllocStringLen.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
_oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
_oleaut32.VariantClear.argtypes = [ctypes.c_void_p]

_kernel32.CreateEventW.restype = wt.HANDLE
_kernel32.SetEvent.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]


def _bstr(text):
    return _oleaut32.SysAllocStringLen(ctypes.c_wchar_p(text), len(text))


def _free_bstr(ptr):
    if ptr:
        try:
            _oleaut32.SysFreeString(ptr)
        except Exception:
            pass


def _vtbl_entry(iface, index):
    """COM 接口指针 -> vtable 第 index 项的函数地址（c_void_p）。"""
    vtbl = ctypes.cast(iface, ctypes.POINTER(ctypes.c_void_p)).contents.value
    entries = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
    return entries[index]


def _com_call(iface, index, restype, argtypes, args):
    """按 vtable 下标调用 COM 方法；iface 自动作为第一个 this 参数。"""
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    fn = proto(_vtbl_entry(iface, index))
    return fn(iface, *args)


def _release(iface):
    """Release 一个 COM 接口；用 c_ulong 接引用计数（不是 HRESULT）。

    v2.11.4：旧代码用 ctypes.HRESULT 作 restype，配合 oledll/vtable 调用时，
    失效 proxy 上的 Release 会抛原生 SEH（0x800706ba = RPC_S_SERVER_UNAVAILABLE），
    faulthandler 因此反复打印全栈。这里做成永不外抛：SEH 无法用 except Exception
    捕获，真正 remedy 是不对失效 proxy 调 Release（见 ProcEventWatcher._run 的
    stale 标记）；本函数只兜住 Python 层的异常。
    """
    if iface:
        try:
            _com_call(iface, IDX_RELEASE, _ULONG, (), ())
        except Exception:
            pass


def _variant_str(v):
    if v.vt == VT_BSTR and v.u.bstr:
        return ctypes.wstring_at(v.u.bstr)
    return ''


def _variant_uint(v):
    if v.vt == VT_UI4:
        return int(v.u.ulVal)
    if v.vt == VT_I4:
        return int(v.u.lVal) & 0xFFFFFFFF
    return 0


# vtable 签名表（一次定义，两处线程共用）
_CONNECT_ARGS = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wt.LONG,
                 wt.LONG, ctypes.c_void_p, ctypes.c_void_p,
                 ctypes.POINTER(ctypes.c_void_p)]
_EXECQ_ARGS = [ctypes.c_void_p, ctypes.c_void_p, wt.LONG, ctypes.c_void_p,
               ctypes.POINTER(ctypes.c_void_p)]
_NEXT_ARGS = [wt.LONG, wt.ULONG, ctypes.POINTER(ctypes.c_void_p),
              ctypes.POINTER(wt.ULONG)]
_GET_ARGS = [ctypes.c_wchar_p, wt.LONG, ctypes.POINTER(VARIANT),
             ctypes.POINTER(wt.LONG), ctypes.POINTER(wt.LONG)]


def _init_security():
    """进程级一次；别的组件已设过（RPC_E_TOO_LATE）就直接放行。"""
    try:
        _ole32.CoInitializeSecurity.argtypes = [ctypes.c_void_p, wt.LONG,
                                                ctypes.c_void_p, ctypes.c_void_p,
                                                wt.DWORD, wt.DWORD,
                                                ctypes.c_void_p, wt.DWORD,
                                                ctypes.c_void_p]
        _ole32.CoInitializeSecurity(None, -1, None, None,
                                    0,          # RPC_C_AUTHN_LEVEL_DEFAULT
                                    3,          # RPC_C_IMP_LEVEL_IMPERSONATE
                                    None, 0,    # EOAC_NONE
                                    None)
    except OSError as e:
        if getattr(e, 'winerror', 0) & 0xFFFFFFFF != RPC_E_TOO_LATE:
            raise


class ProcEventWatcher(object):
    """进程启动/停止事件 -> SetEvent 唤醒守护主循环。

    names_fn 返回当轮要盯的进程名集合（小写）；名单变化无需重新订阅。
    ok 为 True 表示 start/stop 两条订阅都健康；任何一条失败即 False，
    守护主循环据此回落纯轮询。inject() 给离线测试用（不走真 WMI）。
    """

    def __init__(self, names_fn, wake, log=None):
        self.names_fn = names_fn
        self.wake = wake
        self.log = log or (lambda m: None)
        self.ok = False
        self._kinds_ok = {}
        self._stop = threading.Event()
        self._threads = []
        # v2.11.4：两条 watcher 线程（start/stop）共用实例，日志抑制时间戳必须
        # 按 kind 分开，否则一条线程失败会刷新时间戳、吃掉另一条的失败日志。
        self._note_at = {}
        # 每条订阅的诊断快照：{ok, hr, stage, reason, at}。status() / 心跳都用它，
        # 面板因此能说"进程事件不可用，当前使用轮询"而不是只有一个"运行中"。
        self.diag = {}
        self.resubscribes = 0        # 订阅失败后重建的次数（自检/日志用）
        # 诊断计数（交付报告 / 自检用）
        self.wakes = 0
        self.last_wake_name = ''
        self.last_wake_at = 0.0

    # ---- 生命周期 ----
    def start(self):
        # v2.11.4：CoInitializeSecurity 是进程级一次调用，必须在任何工作线程
        # 启动前由主线程完成。旧代码放在 _subscribe 里、由两条线程各自调用，
        # 首次存在并发窗口（靠 RPC_E_TOO_LATE 容忍，但不是设计本意）。
        try:
            _init_security()
        except OSError as e:
            self.log('[proc-events] CoInitializeSecurity 失败：%r（订阅线程继续尝试）'
                     % (e,))
        for kind in ('start', 'stop'):
            t = threading.Thread(target=self._run, args=(kind,),
                                 name='proc-ev-%s' % kind)
            t.daemon = True
            self._threads.append(t)
            t.start()

    def stop(self):
        self._stop.set()
        for t in self._threads:
            # 只等一小步：线程最长 NEXT_TIMEOUT_MS 才检查停机位，但都是
            # daemon——守护进程退出时内核会带走它们，不值得为此拖住退出。
            t.join(timeout=1.5)
        self._threads = []

    def inject(self, name, pid=0):
        """离线测试注入：把一个进程事件直接送进唤醒判定（不经过 WMI）。"""
        self._maybe_wake(name, pid)

    # ---- 内部 ----
    def _mark(self, kind, ok, reason, hr=None, stage=''):
        """记一条订阅状态。成功/失败**状态变化**立即记日志，同类失败 60 秒一条。"""
        now = time.time()
        prev = self.diag.get(kind) or {}
        hr = int(hr or 0) & 0xFFFFFFFF
        self.diag[kind] = {'ok': bool(ok), 'reason': reason or '',
                           'hr': hr, 'stage': stage or '',
                           'at': round(now, 3)}
        self._kinds_ok[kind] = bool(ok)
        self.ok = bool(self._kinds_ok.get('start') and self._kinds_ok.get('stop'))
        if ok:
            if not prev.get('ok'):
                self.log('[proc-events] %s 订阅已建立' % kind)
            return
        same = (prev.get('ok') is False and prev.get('hr') == hr
                and prev.get('stage') == (stage or ''))
        if same and now - self._note_at.get(kind, 0.0) < 60.0:
            return
        self._note_at[kind] = now
        hint = describe_hr(hr)
        self.log('[proc-events] %s 订阅不可用（%s%s）：%s；%s主循环回落纯轮询'
                 % (kind, stage or 'subscribe',
                    (' hr=0x%08x' % hr) if hr else '', reason or '-',
                    ('%s，' % hint) if hint else ''))

    def status(self):
        """一条可直接塞进心跳的降级说明（字段只增不改，面板缺字段要能回落）。"""
        kinds = {}
        for kind in ('start', 'stop'):
            d = self.diag.get(kind) or {}
            kinds[kind] = {'ok': bool(d.get('ok')), 'hr': int(d.get('hr') or 0),
                           'stage': str(d.get('stage') or ''),
                           'reason': str(d.get('reason') or '')}
        bad = [k for k in ('start', 'stop') if not kinds[k]['ok']]
        hint = ''
        if bad:
            hr = max(kinds[k]['hr'] for k in bad)
            detail = describe_hr(hr)
            hint = '进程事件监听不可用，当前使用轮询'
            if detail:
                hint += '（%s）' % detail
        return {'ok': bool(self.ok),
                'mode': 'event' if self.ok else 'poll',
                'kinds': kinds,
                'wakes': self.wakes,
                'last_wake': self.last_wake_name,
                'resubscribes': self.resubscribes,
                'hint': hint}

    def _maybe_wake(self, name, pid):
        try:
            names = set(self.names_fn() or ())
        except Exception:
            names = set()
        if not names or not name:
            return
        low = name.lower()
        if low in names:
            self.wakes += 1
            self.last_wake_name = low
            self.last_wake_at = time.time()
            try:
                _kernel32.SetEvent(self.wake)
            except Exception:
                pass

    def _subscribe(self, kind):
        """建立一条订阅。成功返回 (services, enum)，失败抛 OSError（带 stage/hr）。"""
        locator = services = enum = None
        b_ns = b_lang = b_query = None
        handoff = False
        hr = 0
        try:
            try:
                _init_security()
            except OSError as e:
                _raise_stage(e, 'CoInitializeSecurity')
            clsid = _guid(CLSID_WbemLocator)
            iid = _guid(IID_IWbemLocator)
            out = ctypes.c_void_p()
            _ole32.CoCreateInstance.argtypes = [ctypes.POINTER(GUID), ctypes.c_void_p,
                                                wt.DWORD, ctypes.POINTER(GUID),
                                                ctypes.POINTER(ctypes.c_void_p)]
            try:
                hr = _ole32.CoCreateInstance(ctypes.byref(clsid), None,
                                            1,   # CLSCTX_INPROC_SERVER
                                            ctypes.byref(iid), ctypes.byref(out))
            except OSError as e:
                _raise_stage(e, 'CoCreateInstance')
            if not out.value:
                _check_hr(hr or 0x80004005, 'CoCreateInstance', 'CoCreateInstance')
            locator = out
            b_ns = _bstr('ROOT\\CIMV2')
            svcs = ctypes.c_void_p()
            hr = _com_call(locator, IDX_CONNECT_SERVER, _HRESULT,
                           _CONNECT_ARGS,
                           (b_ns, None, None, 0, 0, None, None,
                            ctypes.byref(svcs)))
            _check_hr(hr, 'ConnectServer', 'ConnectServer')
            services = svcs
            _ole32.CoSetProxyBlanket.argtypes = [ctypes.c_void_p, wt.DWORD,
                                                 wt.DWORD, ctypes.c_void_p,
                                                 wt.DWORD, wt.DWORD,
                                                 ctypes.c_void_p, wt.DWORD]
            try:
                _ole32.CoSetProxyBlanket(services, 0xFFFFFFFF, 0xFFFFFFFF, None,
                                         0, 3, None, 0)
            except OSError as e:
                _raise_stage(e, 'CoSetProxyBlanket')
            b_lang = _bstr('WQL')
            b_query = _bstr(START_QUERY if kind == 'start' else STOP_QUERY)
            penum = ctypes.c_void_p()
            hr = _com_call(services, IDX_EXEC_NOTIFICATION_QUERY, _HRESULT,
                           _EXECQ_ARGS,
                           (b_lang, b_query, EXEC_FLAGS, None, ctypes.byref(penum)))
            _check_hr(hr, 'ExecNotificationQuery', 'ExecNotificationQuery')
            enum = penum
            handoff = True
            return services, enum
        finally:
            _free_bstr(b_ns)
            _free_bstr(b_lang)
            _free_bstr(b_query)
            if not handoff:
                # 失败路径必须自己收干净：v2.11.3 起失败会退避重订阅，
                # 漏一个 IWbemServices 就是每次重试泄一份 COM 引用。
                if enum:
                    _release(enum)
                if services:
                    _release(services)
                if locator:
                    _release(locator)
            # handoff=True：services/enum 的释放责任移交调用方（_run 的 finally）

    def _run(self, kind):
        try:
            _ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
        except OSError as e:
            # 线程内 COM 都起不来（模式冲突等），重试也不会变好：直接交卷。
            self._mark(kind, False, 'CoInitializeEx %r' % e,
                       hr=getattr(e, 'winerror', 0), stage='CoInitializeEx')
            return
        backoff = RETRY_MIN_SEC
        try:
            while not self._stop.is_set():
                services = enum = None
                stale = False   # v2.11.4: proxy 已被 WMI 踢断 -> 禁止 Release
                try:
                    try:
                        services, enum = self._subscribe(kind)
                    except OSError as e:
                        hr = getattr(e, 'hr', 0) or getattr(e, 'winerror', 0)
                        self._mark(kind, False, str(e), hr=hr,
                                   stage=getattr(e, 'stage', 'subscribe'))
                        # 退避重试：WMI 服务被停、RPC 未就绪、权限稍后到位都能自愈。
                        if self._stop.wait(backoff):
                            return
                        backoff = min(RETRY_MAX_SEC, backoff * 2)
                        self.resubscribes += 1
                        continue
                    self._mark(kind, True, '')
                    backoff = RETRY_MIN_SEC
                    result = self._consume(kind, enum)
                    if result == 'stop':
                        return
                    # result == 'resub'：WMI 枚举器/服务已断连。失效 proxy 上
                    # 再调 Release 会触发 0x800706ba 原生 SEH（faulthandler 反复
                    # 全栈的根因），标记 stale 让 finally 跳过——断连 proxy 的
                    # 引用计数已无意义，OS 会随线程/进程回收其 RPC 通道。
                    stale = True
                    self.resubscribes += 1
                    self._mark(kind, False, '订阅断连，准备重建',
                               stage='resub')
                    # v2.11.4：resub 路径此前没有任何等待，一旦 Next 快速失败
                    # 就会零间隔反复重建订阅（忙等 + COM 对象反复创建销毁）。
                    # 与订阅失败路径共用同一套退避。
                    if self._stop.wait(backoff):
                        return
                    backoff = min(RETRY_MAX_SEC, backoff * 2)
                finally:
                    if not stale:
                        if enum:
                            _release(enum)
                        if services:
                            _release(services)
        except Exception as e:
            self._mark(kind, False, '运行期异常 %r' % (e,), stage='loop')
        finally:
            try:
                _ole32.CoUninitialize()
            except Exception:
                pass

    def _consume(self, kind, enum):
        """消费事件直到停机位或订阅失效。返回 'stop' / 'resub'。"""
        obj = ctypes.c_void_p()
        returned = wt.ULONG(0)
        hard_errors = 0
        while not self._stop.is_set():
            hr = _com_call(enum, IDX_ENUM_NEXT, _HRESULT, _NEXT_ARGS,
                           (NEXT_TIMEOUT_MS, 1, ctypes.byref(obj),
                            ctypes.byref(returned)))
            hr = int(hr or 0) & 0xFFFFFFFF
            if self._stop.is_set():
                break
            if hr == WBEM_S_NO_ERROR and returned.value:
                hard_errors = 0
                try:
                    # 注意 pType / pFlavor 必须给真实缓冲区：trace 事件对象
                    # （fastprox 实现）对 NULL 出参返回 WBEM_E_TYPE_MISMATCH，
                    # 类对象却容忍 NULL——这是 Get 报类型不匹配的唯一原因。
                    v = VARIANT()
                    ptype = wt.LONG(0)
                    pflavor = wt.LONG(0)
                    name, pid = '', 0
                    try:
                        _com_call(obj, IDX_OBJ_GET, _HRESULT, _GET_ARGS,
                                  ('ProcessName', 0, ctypes.byref(v),
                                   ctypes.byref(ptype), ctypes.byref(pflavor)))
                        name = _variant_str(v)
                    finally:
                        _oleaut32.VariantClear(ctypes.byref(v))
                    v2 = VARIANT()
                    try:
                        _com_call(obj, IDX_OBJ_GET, _HRESULT, _GET_ARGS,
                                  ('ProcessID', 0, ctypes.byref(v2),
                                   ctypes.byref(ptype), ctypes.byref(pflavor)))
                        pid = _variant_uint(v2)
                    finally:
                        _oleaut32.VariantClear(ctypes.byref(v2))
                    self._maybe_wake(name, pid)
                except Exception:
                    pass
                finally:
                    _release(obj)
                    obj = ctypes.c_void_p()
                continue
            # v2.11.4 判断顺序：先认失败码，再认超时。
            # 旧代码把 `not returned.value` 放在最前——而 RPC 断连时 WMI 根本
            # 不填 returned（恒 0），于是断连被当成"正常超时"吞掉，重连逻辑
            # 永远不触发（guard.log 里 RPC 失败后再无重连记录）。必须先查 hr。
            if hr in _DISCONNECT_HRS:
                self._mark(kind, False, 'Next 断连 hr=0x%08x' % hr,
                           hr=hr, stage='Next')
                return 'resub'
            if hr not in (WBEM_S_NO_ERROR, WBEM_S_TIMEDOUT):
                # 其它 HRESULT：枚举器可能已被 WMI 踢掉。连续几次就重建订阅。
                hard_errors += 1
                self._mark(kind, False, 'Next 返回 hr=0x%08x' % hr,
                           hr=hr, stage='Next')
                if hard_errors >= SUB_ERROR_LIMIT:
                    return 'resub'
                self._stop.wait(1.0)
                continue
            # WBEM_S_NO_ERROR 但 returned=0，或明确的 TIMEDOUT：正常空轮。
            hard_errors = 0
        return 'stop'


def _check_hr(hr, what, stage=''):
    """非 0 HRESULT -> OSError，带上 hr / stage / 中文原因（日志与 status 直接用）。"""
    hr = int(hr or 0) & 0xFFFFFFFF
    if hr == 0:
        return
    err = OSError('%s 失败 (hr=0x%08x)：%s' % (what, hr, describe_hr(hr)))
    try:
        err.hr = hr
        err.stage = stage or what
        err.winerror = hr
    except Exception:
        pass
    raise err


def make_wake_event():
    """自动复位事件：主循环 WaitForSingleObject 消费一次唤醒。"""
    h = _kernel32.CreateEventW(None, False, False, None)
    return h if h not in (None, _INVALID) else None


def poke(watcher):
    """外部线程手动置一次唤醒（守护的标记看门狗用）。失败静默。"""
    try:
        _kernel32.SetEvent(watcher.wake)
    except Exception:
        pass


def wait_for_wake(wake, timeout_ms):
    """主循环一轮之间的等待。返回 True=被事件唤醒，False=超时/出错。"""
    if not wake:
        return False
    try:
        rc = _kernel32.WaitForSingleObject(wt.HANDLE(wake), wt.DWORD(int(timeout_ms)))
        return rc == 0           # WAIT_OBJECT_0
    except Exception:
        return False


def attach(names_fn, log=None):
    """建 wake 事件 + 启动两条订阅线程。失败返回 None（守护回落纯轮询）。"""
    try:
        wake = make_wake_event()
        if not wake:
            return None
        watcher = ProcEventWatcher(names_fn, wake, log=log)
        watcher.start()
        return watcher
    except Exception:
        return None
