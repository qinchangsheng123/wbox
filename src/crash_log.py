# -*- coding: utf-8 -*-
"""WBOX - 崩溃落盘（v2.5.0-①）。

背景：noconsole 打包后 sys.stderr 是 None，未捕获异常直接进黑洞。11-15 的
守护被强杀时连一行日志都没有，事后无从复盘。本模块给守护进程装三层兜底：

  1. `faulthandler.enable`  —— 段错误 / 致命信号 / 解释器本身崩掉时，把原生
     栈写进 guard_crash.log。Python 层的 try/except 根本拦不住这类死法。
  2. `sys.excepthook`       —— 主线程未捕获异常：traceback + 时间戳 + 版本号。
  3. `threading.excepthook` —— 后台线程未捕获异常（默认只打到 stderr=黑洞）。

落盘策略（写代码时定死，别再改口径）：
  - 文件固定叫 `guard_crash.log`，与 guard.pid / guard.log 同目录（安装根）。
  - **追加写 + 1 MB 上限轮转，保留一代**：启动与每次写入前检查大小，超过
    上限就 `guard_crash.log -> guard_crash.log.1`（os.replace 直接覆盖旧的
    .1）。不搞多代滚动——崩溃日志是排查现场，不是档案；两代足够定位
    "最近两次怎么死的"。faulthandler 那一路没有大小检查（它绕过 Python），
    但原生栈通常几 KB，实际到不了上限。
  - **正常退出不留文件**：无崩溃时本模块只在启动时建出 0 字节占位文件
    （faulthandler 需要一个已经打开的句柄），`clear_if_empty()` 在退出清理
    时把 0 字节的占位删掉。guard_core.cleanup() 与 atexit 都会调它。
    所以"活着的日子"里安装根不会出现 guard_crash.log。
  - 写入全程 try/except 吞掉：崩溃落盘自己不能再炸出第二个异常。

本模块只依赖标准库与 core_defaults（纯常量），不 import Win32 / tkinter
（faulthandler、traceback 都是内置），离线测试可以直接驱动。版本号直接取
core_defaults.VERSION，守护正常运行永远不碰 tkinter。
"""
import faulthandler
import os
import sys
import threading
import time
import traceback

import core_defaults

# 1 MB 上限：一轮完整 traceback + 原生栈撑不满它；超过说明日志在反复崩溃。
MAX_BYTES = 1024 * 1024

_CRASH_NAME = 'guard_crash.log'
_fh = None
_path = None
_lock = threading.Lock()


def crash_path(base):
    return os.path.join(base, _CRASH_NAME)


def _close_fh():
    global _fh
    try:
        faulthandler.disable()
    except Exception:
        pass
    try:
        if _fh is not None:
            _fh.close()
    except Exception:
        pass
    _fh = None


def _reopen(path):
    """轮转后重开句柄并重新挂 faulthandler。

    为什么不能只 os.replace：Windows 上改名的只是目录项，**旧句柄仍指向那个
    inode**——继续用旧句柄写，字节会追到 .1 里而不是新的 guard_crash.log；
    faulthandler 持有的 fd 同理。必须关掉重开、重新 enable。
    """
    global _fh
    try:
        _fh = open(path, 'a', encoding='utf-8', errors='replace')
        faulthandler.enable(_fh)
    except Exception:
        _fh = None


def _maybe_rotate(path):
    """超限时 guard_crash.log -> guard_crash.log.1（覆盖上一代），返回是否轮转。

    install（还没开句柄）与写前检查共用。调用方持有 _lock。
    """
    try:
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            _close_fh()
            os.replace(path, path + '.1')
            _reopen(path)
            return True
    except OSError:
        pass
    return False


def _version():
    return core_defaults.VERSION


def _write(header, text):
    """带时间戳/版本号的追加写。自带 1 MB 轮转；任何失败静默。"""
    if _fh is None:
        return
    try:
        with _lock:
            _maybe_rotate(_path)
            if _fh is None:
                return
            stamp = time.strftime('%Y-%m-%d %H:%M:%S')
            _fh.write(
                u'==== %s | WBOX v%s | pid %d | %s ====\n%s\n'
                % (stamp, _version(), os.getpid(), header, text))
            _fh.flush()
    except Exception:
        pass


def install(base):
    """给守护进程装三层兜底。返回 crash log 的绝对路径。

    只应在守护入口调用一次（guard 进程）；面板进程不装——面板有自己的
    messagebox / 日志出口，且不该在设置目录里常驻一个占位文件。
    """
    global _fh, _path
    if _fh is not None:
        return _path
    _path = crash_path(base)
    try:
        _maybe_rotate(_path)
        # 追加模式：上一轮崩溃的现场保留在本文件里，配合 ==== 分隔行可读。
        _fh = open(_path, 'a', encoding='utf-8', errors='replace')
        faulthandler.enable(_fh)
    except Exception:
        _fh = None            # 打不开文件（只读盘等）也不该拦住守护启动
    # 主线程未捕获异常：原 excepthook 无处可去（noconsole stderr=None），
    # 直接替换，不链式调用。
    sys.excepthook = _excepthook
    # Python 3.8 起支持；老解释器没有这个属性就跳过（本仓库锁 3.8.10，恒有）。
    if hasattr(threading, 'excepthook'):
        threading.excepthook = _thread_excepthook
    return _path


def _format(tp, val, tb):
    return ''.join(traceback.format_exception(tp, val, tb))


def _excepthook(tp, val, tb):
    _write('UNCAUGHT EXCEPTION (main thread)',
           _format(tp, val, tb) or '%s: %s' % (tp.__name__, val))


def _thread_excepthook(args):
    name = getattr(args.thread, 'name', '?') if args.thread else '?'
    _write('UNCAUGHT EXCEPTION (thread %s)' % name,
           ''.join(traceback.format_exception(args.exc_type, args.exc_value,
                                              args.exc_traceback)))


def clear_if_empty():
    """退出清理：0 字节的占位文件删掉，正常退出的机器不留 crash 文件。

    有内容的（本轮真的崩过）原样保留。guard_core.cleanup / atexit 调用。
    """
    _close_fh()
    try:
        if _path and os.path.exists(_path) and os.path.getsize(_path) == 0:
            os.remove(_path)
    except OSError:
        pass
