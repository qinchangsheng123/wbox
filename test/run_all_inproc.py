# -*- coding: utf-8 -*-
"""把 test\\test_*.py 全部放进**同一个解释器**顺序跑一遍（单进程卫生检查）。

为什么需要它：
  这些 test_*.py 测试是脚本式的（顶层执行断言、末尾 `sys.exit()`），`unittest discover`
  只能 import、不会执行，而且遇到模块级 `sys.exit()` 还会记成 error —— 它测不到
  任何东西。真正能暴露"跨模块污染"的方式是：同一个进程里按顺序把它们跑完。

  这正是 v2.3.0 复核补丁修掉的那类 bug：`test_ace_state.py` 在模块级把
  `ace_native.process_age_sec` 换成替身却不还原，于是同进程后跑的
  `test_quiet_gate.py` 第 [8] 组抓到的"原始函数"已经是替身，把"取不到创建时间"
  误判成 CLEAN。按文件独立进程跑是看不见这个问题的。

唯一例外的清理：`settings_gui` 及它拆分出来的五个模块（v2.13.0 审查项 ⑨）都在
import 时就把 `ABG_BASE` 读进模块常量（BASE / CFG / PLUGINS_DIR…），两个 GUI
测试各有各的临时目录，所以每跑完一个文件就把它们从 sys.modules 里摘掉，让下一个
文件按自己的 ABG_BASE 重新 import。其余模块（含 ace_native / guard_core）**一律
不清理**，这样才留得住"谁偷偷改了别人的全局"这类问题。

用法：C:\\Python38\\python.exe test\\run_all_inproc.py
退出码：0 = 全部通过；1 = 有文件失败。
"""
import io
import gc
import os
import runpy
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))

# 只摘这一组：它们都在 import 时固化了 ABG_BASE（见文件头）。顺序无关，
# 但必须整组一起摘——留着 panel_env 而摘掉 settings_gui，下一个文件会拿到
# 上一个测试的临时目录，症状是"单跑全绿、一起跑全红"（v2.13.0 踩过）。
RESET_MODULES = ('settings_gui', 'panel_env', 'win_env', 'panel_state',
                 'ui_gfx', 'ui_widgets')


def pick_files():
    names = sorted(n for n in os.listdir(HERE)
                   if n.startswith('test_') and n.endswith('.py'))
    return names


def run_one(path):
    """在同一解释器里执行一个测试文件，返回 (rc, 失败摘要)。

    `runpy.run_path(..., run_name='__main__')` 让脚本的 `if __name__ == '__main__'`
    分支生效；脚本自己的 `sys.exit(n)` 会抛 SystemExit，这里拦下来当返回值。
    """
    try:
        runpy.run_path(path, run_name='__main__')
        return 0, ''
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0, ''
        if isinstance(code, int):
            return code, ('' if code == 0 else 'sys.exit(%d)' % code)
        return 1, 'sys.exit(%r)' % (code,)
    except BaseException:
        return 1, traceback.format_exc(limit=4).strip().splitlines()[-1]
    finally:
        for name in RESET_MODULES:
            sys.modules.pop(name, None)
        # 上一个 GUI 测试留下的 Tk / PhotoImage 若等到下一个文件的守护线程里
        # 才被 GC，Tcl 解释器会在错误线程被删（Tcl_AsyncDelete 直接 abort）。
        # 在主线程主动收一次，把 Tcl 对象的销毁钉在主线程。
        gc.collect()


def main():
    files = pick_files()
    print('单进程顺序执行 %d 个测试文件（不清理 ace_native 等模块的全局状态）'
          % len(files))
    bad = []
    for name in files:
        path = os.path.join(HERE, name)
        buf_out, buf_err = sys.stdout, sys.stderr
        # 每个文件的输出太多，只在失败时回放
        out = io.StringIO()
        sys.stdout = sys.stderr = out
        try:
            rc, why = run_one(path)
        finally:
            sys.stdout, sys.stderr = buf_out, buf_err
        mark = 'ok  ' if rc == 0 else 'FAIL'
        print('  %s %-34s rc=%d %s' % (mark, name, rc, why))
        if rc != 0:
            bad.append((name, why, out.getvalue()))

    print('')
    print('%d / %d 通过' % (len(files) - len(bad), len(files)))
    if bad:
        for name, why, text in bad:
            print('')
            print('=' * 70)
            print('FAILED %s（%s）最后 25 行输出：' % (name, why))
            print('\n'.join(text.splitlines()[-25:]))
        return 1
    print('ALL PASS（单进程）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
