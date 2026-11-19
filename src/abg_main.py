# -*- coding: utf-8 -*-
"""WBOX 统一入口（单 exe）。

双击 / 无参数        -> 设置面板
--guard              -> 无窗口常驻守护（计划任务用这个）
--settings           -> 设置面板（显式；面板自我提权重开时也会带上）
--settings --elevated-> 已提权过的设置面板（--elevated 只用于防无限提权循环）
--self-test          -> 守护只读自检，结果写 self_test.txt
--self-check         -> 面板/依赖/DPI 自检，结果写 settings_selfcheck.txt
--layout-check       -> 多分辨率布局检查，结果写 layout_check.txt
--diag               -> 生成诊断包（日志/事件流/崩溃记录/配置/插件状态）并打印路径
/uninstall [/S]      -> 卸载（v2.6.0 起注册表 UninstallString 指向这里，
                        不再放 Uninstall_Wingman.exe 全量副本）

设置面板、常驻守护、三类自检共用一份 Python 运行时，避免重复打包
5MB 的 bootloader 与标准库。
"""
import os
import sys


def main():
    args = sys.argv[1:]
    base = os.path.dirname(os.path.abspath(sys.executable)) \
        if getattr(sys, 'frozen', False) \
        else os.path.dirname(os.path.abspath(__file__))

    if '/uninstall' in [a.lower() for a in args] or \
            '--uninstall' in [a.lower() for a in args]:
        import uninstall_core
        return uninstall_core.main()

    # v2.13.0：诊断包。日志/事件流/崩溃记录/配置/插件状态打成一个 zip，
    # 方便用户直接把文件发过来，不必手工翻安装目录。
    if '--diag' in args:
        import events
        path = ''
        try:
            path = events.bundle(base)
        except Exception:
            path = ''
        if not path:
            try:                      # 安装目录只读时退到桌面
                desktop = os.path.join(os.environ.get('USERPROFILE', ''),
                                       'Desktop')
                if os.path.isdir(desktop):
                    path = events.bundle(base, os.path.join(
                        desktop, 'wbox-diag.zip'))
            except Exception:
                path = ''
        try:
            with open(os.path.join(base, 'diag_path.txt'), 'w',
                      encoding='utf-8') as f:
                f.write(path or '')
        except Exception:
            pass
        print(path or '诊断包生成失败（安装目录不可写？）')
        return 0 if path else 1

    if '--guard' in args or '--self-test' in args:
        # noconsole 打包后 sys.stdout 可能为 None，自检输出重定向到文件
        if '--self-test' in args:
            try:
                sys.stdout = open(os.path.join(base, 'self_test.txt'), 'w', encoding='utf-8')
                sys.stderr = sys.stdout
            except Exception:
                pass
        # v2.5.0：守护进程的崩溃落盘（faulthandler + excepthook）必须赶在
        # guard_core 之前装好——晚了就接不住启动期的崩溃。
        try:
            import crash_log
            crash_log.install(base)
            import atexit
            atexit.register(crash_log.clear_if_empty)
        except Exception:
            pass
        import guard_core
        return guard_core.main()

    # 无参数 / --settings / --elevated 一律进设置面板。
    # 面板自己判断 config.json 是否可写：不可写且未提权时用 ShellExecuteW(runas)
    # 带上 "--settings --elevated" 重开自己；--elevated 只用于防止无限提权循环。
    import settings_gui
    return settings_gui.main()


if __name__ == '__main__':
    sys.exit(main() or 0)
