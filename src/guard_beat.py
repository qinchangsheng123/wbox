# -*- coding: utf-8 -*-
"""WBOX - 守护↔面板心跳管道（v2.5.0-②）。

为什么不用文件：guard.pid 只能证明"有个进程写过 pid"，不能证明它活着；
plugins_state.json 要等守护落盘才有新状态。named pipe 是唯一"连上=活着"
的通道：守护不在时客户端立即拿到 ERROR_FILE_NOT_FOUND，没有轮询空转。

CPU 口径（本模块存在的意义）：
  - 服务端**单线程阻塞在 ConnectNamedPipe**：没有客户端连接时线程睡在内核
    等待队列上，CPU 时间为 0（实测见 test/ 与交付报告）。
  - 只有面板真的来连（面板 2 秒一次）才唤醒服务端写一小行 JSON，写完立刻
    DisconnectNamedPipe 回到阻塞等待。空闲节拍与 v2.4 的 sleep 轮询完全无关。
  - 管道名固定 `\\.\pipe\WBOXGuardBeat`，与单实例互斥量
    `Local\\WBOXSingleton` 互不相干；每轮重建管道实例（MaxInstances=1）。

零依赖：只 import 标准库（ctypes/json/threading/time）。任何 Win32 调用失败
都按"读不到"处理，绝不抛异常打断面板 / 守护主循环。

协议：一行 UTF-8 JSON + '\\n'，字段：
    pid / version / session（idle|active|grace）/ seq / started_at
    plugins: [{id, status, ticks, last_tick}]
    events（v2.11.3）: {ok, mode(event|poll), wakes, resubscribes, hint,
                       start/stop: {hr, stage}（失败时才有）}
    health（v2.11.3）: {degraded: bool, reasons: [一句人话, ...]}
守护侧只写不读（PIPE_ACCESS_OUTBOUND），面板侧只读不写（GENERIC_READ）。
面板必须容忍字段缺失：旧守护没有 events/health，读到就当"未上报"，不能因此
报"守护没运行"。
"""
import ctypes
import json
import threading
import time
from ctypes import wintypes

PIPE_NAME = r'\\.\pipe\WBOXGuardBeat'

# ---- Win32 常量（只挑用到的，别整包抄） ----
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PIPE_ACCESS_OUTBOUND = 0x00000002
GENERIC_READ = 0x80000000
OPEN_EXISTING = 3
PIPE_NOWAIT = 0x00000001
ERROR_FILE_NOT_FOUND = 2
ERROR_PATH_NOT_FOUND = 3
ERROR_PIPE_BUSY = 231
ERROR_PIPE_CONNECTED = 535
ERROR_NO_DATA = 232
ERROR_ACCESS_DENIED = 5
MAX_LINE = 65536

# 默认客户端预算：面板每 2 秒刷一次状态，一次连接必须在半秒内出结果，
# 否则宁可显示"没在运行"也不能让菜单卡住。
DEFAULT_DEADLINE_MS = 400


def _kernel():
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateNamedPipeW.restype = wintypes.HANDLE
    k.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.DWORD,
                                   wintypes.DWORD, wintypes.LPVOID]
    k.ConnectNamedPipe.restype = wintypes.BOOL
    k.ConnectNamedPipe.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    k.DisconnectNamedPipe.restype = wintypes.BOOL
    k.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
    k.FlushFileBuffers.restype = wintypes.BOOL
    k.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
                              wintypes.HANDLE]
    k.ReadFile.restype = wintypes.BOOL
    k.ReadFile.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                           ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
    k.SetNamedPipeHandleState.restype = wintypes.BOOL
    k.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                          ctypes.POINTER(wintypes.DWORD),
                                          ctypes.POINTER(wintypes.DWORD)]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


# ============================================================ 服务端（守护）

class BeatServer(object):
    """守护侧管道服务：一个后台线程，阻塞等待面板来取状态。

    provider 是无参函数，返回要发的 dict（见模块 docstring 的协议字段）。
    返回 None / 抛异常都按"这一轮不发内容"处理：照常断开连接，面板显示
    「守护没在运行」——心跳机制任何一环坏掉都必须以这种方式降级，不能卡死。
    """

    def __init__(self, pipe_name=PIPE_NAME, provider=None):
        self.pipe_name = pipe_name
        self.provider = provider
        self._running = False
        self._thread = None
        self._handle = None
        self._kernel = _kernel()
        self._fail_note_at = 0.0
        self.log = None           # 守护可注入 log；None = 静默

    # ---- 对外 ----
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._serve, name='guard-beat')
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        """正常停机：置停机位 + 一次自连接把阻塞中的 ConnectNamedPipe 叫醒，
        服务线程醒来看到停机位自己收尾退出。主线程**绝不**直接碰服务线程
        的句柄——DisconnectNamedPipe/CloseHandle 会与阻塞中的 ConnectNamedPipe
        死锁（v2.5.0 实测：外线程 DisconnectNamedPipe 挂死，见测试 [3]）。"""
        self._running = False
        self._wake()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    def kill(self):
        """模拟守护崩溃：同样走"自连接叫醒"让服务线程退出（管道随收尾消失），
        但不 join——客户端视角就是"管道没了"。"""
        self._running = False
        self._wake()

    def _wake(self):
        """自己连一次管道再立刻断开：ConnectNamedPipe 醒来后按停机位退出。

        服务线程可能恰好在两次 ConnectNamedPipe 之间（管道实例还没建好），
        所以最多重试 1 秒；连不上也没关系——它下一轮循环就会看到停机位。
        """
        k = self._kernel
        for _ in range(20):
            h = k.CreateFileW(self.pipe_name, GENERIC_READ, 0, None,
                              OPEN_EXISTING, 0, None)
            if h not in (None, INVALID_HANDLE_VALUE):
                try:
                    k.CloseHandle(h)
                except Exception:
                    pass
                return
            time.sleep(0.05)

    def _note(self, msg):
        if callable(self.log):
            now = time.time()
            if now - self._fail_note_at > 60.0:      # 同类失败一分钟只记一条
                self._fail_note_at = now
                try:
                    self.log(msg)
                except Exception:
                    pass

    def _open_pipe(self):
        h = self._kernel.CreateNamedPipeW(
            self.pipe_name, PIPE_ACCESS_OUTBOUND, 0,   # byte 模式 + 阻塞
            1, 4096, 4096, 0, None)
        if h in (None, INVALID_HANDLE_VALUE):
            return None
        return h

    def _serve(self):
        k = self._kernel
        while self._running:
            h = self._open_pipe()
            if h is None:
                # 管道实例被占（第二个守护 / 句柄泄漏）。不忙转：睡 2 秒，
                # 这条路径在正常单实例下永远不该走到。
                self._note('[beat] 管道实例创建失败，2 秒后重试（err=%d）'
                           % k.GetLastError())
                time.sleep(2.0)
                continue
            self._handle = h
            connected = k.ConnectNamedPipe(h, None)
            if not self._running:
                k.DisconnectNamedPipe(h)
                k.CloseHandle(h)
                self._handle = None
                break
            if not connected and k.GetLastError() != ERROR_PIPE_CONNECTED:
                k.CloseHandle(h)
                self._handle = None
                self._note('[beat] ConnectNamedPipe 失败，0.5 秒后重试（err=%d）'
                           % k.GetLastError())
                time.sleep(0.5)
                continue
            # 面板连上了：取一次状态、写一行、断开、回到阻塞等待。
            try:
                payload = self.provider() if callable(self.provider) else None
                if isinstance(payload, dict):
                    line = (json.dumps(payload, ensure_ascii=False)
                            + '\n').encode('utf-8')
                    written = ctypes.c_ulong(0)
                    buf = ctypes.create_string_buffer(line, len(line))
                    if k.WriteFile(h, buf, len(line), ctypes.byref(written), None):
                        k.FlushFileBuffers(h)
            except Exception:
                pass
            finally:
                try:
                    k.DisconnectNamedPipe(h)
                except Exception:
                    pass
                try:
                    k.CloseHandle(h)
                except Exception:
                    pass
                self._handle = None


# ============================================================ 客户端（面板）

def read_status(pipe_name=None, deadline_ms=DEFAULT_DEADLINE_MS):
    """连一次心跳管道，读一行状态。返回 dict 或 None。

    pipe_name 缺省取模块常量 PIPE_NAME（函数体内取值而不是默认参数：
    测试要换测试管道名时改 guard_beat.PIPE_NAME 即可生效）。

    None 的含义固定是"守护没在运行"（管道不存在 / 忙 / 超时 / 数据读不全）。
    绝不抛异常、绝不无限阻塞：所有等待都受 deadline 约束，客户端句柄设了
    PIPE_NOWAIT，ReadFile 拿不到数据立即返回。
    """
    k = _kernel()
    pipe_name = pipe_name or PIPE_NAME
    deadline = time.time() + max(0.05, deadline_ms / 1000.0)
    h = None
    while True:
        h = k.CreateFileW(pipe_name, GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
        if h not in (None, INVALID_HANDLE_VALUE):
            break
        err = k.GetLastError()
        # 管道不存在 / 忙都允许在预算内重试：守护刚起、面板连发两次的竞态。
        # 预算耗尽就按"没在运行"交卷。
        if err not in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND,
                       ERROR_PIPE_BUSY, ERROR_ACCESS_DENIED):
            return None
        if time.time() >= deadline:
            return None
        time.sleep(0.03)
    data = b''
    try:
        mode = ctypes.c_ulong(PIPE_NOWAIT)
        k.SetNamedPipeHandleState(h, ctypes.byref(mode), None, None)
        buf = ctypes.create_string_buffer(MAX_LINE)
        while time.time() < deadline:
            got = ctypes.c_ulong(0)
            ok = k.ReadFile(h, buf, MAX_LINE, ctypes.byref(got), None)
            if ok and got.value:
                data += buf.raw[:got.value]
                if b'\n' in data:
                    break
            else:
                err = k.GetLastError()
                if err in (ERROR_NO_DATA, 996):      # NO_DATA / IO pending
                    time.sleep(0.015)
                    continue
                break
    except Exception:
        data = b''
    finally:
        try:
            k.CloseHandle(h)
        except Exception:
            pass
    line = data.split(b'\n')[0].strip()
    if not line:
        return None
    try:
        payload = json.loads(line.decode('utf-8', 'replace'))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
