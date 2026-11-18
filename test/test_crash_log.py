# -*- coding: utf-8 -*-
"""v2.5.0-① 崩溃落盘测试：faulthandler / excepthook / 线程兜底 / 轮转 / 正常退出零残留。

三层证据：
  [1] 单元层：install 后占位文件存在；excepthook 写入含时间戳/版本/traceback；
      线程异常含线程名；clear_if_empty 删 0 字节占位；超限轮转出 .1。
  [2] 子进程层：临时目录造假守护入口（与 abg_main --guard 同一套 bootstrap），
      抛 RuntimeError -> crash log 落盘且含关键字；正常退出 -> 不产生 crash log。
  [3] 真崩溃层：子进程里 faulthandler._sigsegv() 模拟段错误（Python 层拦不住
      的死法）-> 原生栈落盘。

不碰生产 app\\、不写仓库根的 guard_crash.log（BASE 全部指向临时目录）。
用法：C:\\Python38\\python.exe test\\test_crash_log.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'src')
sys.path.insert(0, SRC)

import crash_log  # noqa: E402

PASSED = []
FAILED = []
TMP = tempfile.mkdtemp(prefix='abg_crash_')


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


def read(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        return f.read()


def restore_hooks(orig_sys, orig_thread):
    sys.excepthook = orig_sys
    if orig_thread is not None:
        threading.excepthook = orig_thread


def main():
    orig_sys_hook = sys.excepthook
    orig_thread_hook = getattr(threading, 'excepthook', None)
    base = os.path.join(TMP, 'base1')
    os.makedirs(base)
    path = crash_log.crash_path(base)
    try:
        print('[1] 单元层：安装 / 写入 / 清理 / 轮转')
        crash_log.install(base)
        step(os.path.isfile(path), 'install 后 guard_crash.log 占位文件存在')
        step(os.path.getsize(path) == 0, '占位文件是 0 字节（还没崩过）')

        # 主线程 excepthook：用真实 traceback 对象驱动
        try:
            raise RuntimeError('boom-main-12345')
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
        text = read(path)
        step('UNCAUGHT EXCEPTION (main thread)' in text,
             'excepthook 写入了主线程异常头')
        step('boom-main-12345' in text and 'Traceback' in text,
             'traceback 与异常消息都在')
        step('WBOX v' in text and 'pid' in text,
             '头部带版本号与 pid（版本惰性取 settings_gui）')
        step('==== ' in text and text.count('====') >= 1,
             '带 ==== 分隔头（含时间戳格式位）')

        # 后台线程 excepthook
        def bad_thread():
            raise ValueError('boom-thread-67890')
        t = threading.Thread(target=bad_thread, name='fake-worker')
        t.start()
        t.join()
        text = read(path)
        step('boom-thread-67890' in text and 'fake-worker' in text,
             '后台线程异常落盘且含线程名')

        # 正常退出清理只删 0 字节占位；本轮真崩过（有内容）的文件必须保留
        crash_log.clear_if_empty()
        step(os.path.isfile(path) and os.path.getsize(path) > 0,
             '退出清理不动有内容的 crash log（真崩过的现场保留）')
        base2 = os.path.join(TMP, 'base2')
        os.makedirs(base2)
        p2 = crash_log.crash_path(base2)
        crash_log._fh = None            # 重置模块态，模拟"干净安装"
        crash_log.install(base2)
        step(os.path.isfile(p2) and os.path.getsize(p2) == 0,
             '第二次安装：0 字节占位')
        crash_log.clear_if_empty()
        step(not os.path.exists(p2),
              '正常退出（clear_if_empty）不产生 crash log')

        # 轮转：把上限改小，写超限 -> .1 出现
        crash_log._fh = None
        crash_log.install(base)
        old_max = crash_log.MAX_BYTES
        crash_log.MAX_BYTES = 200
        crash_log._write('rotation-test', 'x' * 400)
        crash_log.MAX_BYTES = old_max
        step(os.path.isfile(path + '.1'),
             '超过 1 MB（此处打桩为 200 B）轮转出 .1')
        step(os.path.getsize(path) < 500,
             '主文件轮转后回到小体量')

        print('[2] 子进程层：造假守护入口 抛异常 / 正常退出')
        # 与 abg_main.py --guard 分支同一套 bootstrap：install + atexit 清理
        fake_guard = os.path.join(TMP, 'fake_guard.py')
        with open(fake_guard, 'w', encoding='ascii') as f:
            f.write(
                'import atexit, sys\n'
                'sys.path.insert(0, r\'%s\')\n'
                'import crash_log\n'
                'crash_log.install(sys.argv[1])\n'
                'atexit.register(crash_log.clear_if_empty)\n'
                'if len(sys.argv) > 2 and sys.argv[2] == \'crash\':\n'
                '    raise RuntimeError(\'fake-guard-boom-24680\')\n'
                'print(\'clean-exit\')\n' % SRC)
        env = dict(os.environ)
        env['PYTHONIOENCODING'] = 'utf-8'
        cbase = os.path.join(TMP, 'cbase1')
        os.makedirs(cbase)
        p = subprocess.run([sys.executable, fake_guard, cbase, 'crash'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, env=env)
        cpath = crash_log.crash_path(cbase)
        step(p.returncode != 0, '子进程带异常退出（rc=%d）' % p.returncode)
        step(os.path.isfile(cpath), '异常退出 -> crash log 落盘')
        if os.path.isfile(cpath):
            text = read(cpath)
            step('fake-guard-boom-24680' in text and 'UNCAUGHT EXCEPTION' in text,
                 'crash log 含异常消息与主线程标记')
            # 版本号随面板 VERSION 走（与 test_glyph_v22 同口径，不写死数字）
            import settings_gui  # noqa: E402
            step('WBOX v%s' % settings_gui.VERSION in text,
                 'crash log 含版本号 v%s' % settings_gui.VERSION)
        cbase2 = os.path.join(TMP, 'cbase2')
        os.makedirs(cbase2)
        p = subprocess.run([sys.executable, fake_guard, cbase2],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, env=env)
        step(p.returncode == 0 and b'clean-exit' in p.stdout,
             '子进程正常退出（rc=%d）' % p.returncode)
        step(not os.path.exists(crash_log.crash_path(cbase2)),
             '正常退出 -> 不产生 crash log（0 字节占位已被清理）')

        print('[3] 真崩溃层：段错误走 faulthandler')
        cbase3 = os.path.join(TMP, 'cbase3')
        os.makedirs(cbase3)
        seg = os.path.join(TMP, 'seg.py')
        with open(seg, 'w', encoding='ascii') as f:
            f.write(
                'import sys\n'
                'sys.path.insert(0, r\'%s\')\n'
                'import crash_log, faulthandler\n'
                'crash_log.install(sys.argv[1])\n'
                'faulthandler._sigsegv()\n' % SRC)
        p = subprocess.run([sys.executable, seg, cbase3],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=60, env=env)
        spath = crash_log.crash_path(cbase3)
        step(p.returncode != 0, '段错误子进程非零退出（rc=%s）' % p.returncode)
        step(os.path.isfile(spath) and os.path.getsize(spath) > 0,
             '段错误 -> faulthandler 原生栈落盘')
        if os.path.isfile(spath):
            text = read(spath)
            step('Current thread' in text or 'fatal exception' in text.lower(),
                 '原生栈内容可读（faulthandler 格式）')
    finally:
        crash_log.clear_if_empty()
        restore_hooks(orig_sys_hook, orig_thread_hook)
        shutil.rmtree(TMP, ignore_errors=True)

    print('')
    if FAILED:
        print('FAILED %d 项：%s' % (len(FAILED), FAILED))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
