# -*- coding: utf-8 -*-
"""进程身份校验（proc_util v2.0.0）离线验证。

背景：只看「pid 在 + 名字对」不足以防 PID 复用——进程退出后 PID 会被系统
立刻重新分配，同名进程重新出现在同一个号上时，按 pid 强杀就会误关用户后来
自己开的那一份。本用例只验证判据与"身份不明就不强杀"的兜底，不碰任何真实
用户进程（关闭目标用不存在的 pid，taskkill 用假 core 记录）。

跑法：python test\\test_proc_identity.py
"""
import importlib.util
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_src  # noqa: E402

PLUGINS = _plugin_src.require('proc_util 身份校验')
_src = os.path.join(PLUGINS, 'afterburner', 'proc_util.py')
_spec = importlib.util.spec_from_file_location('proc_util_ident', _src)
proc_util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proc_util)

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


print('[1] FILETIME 换算')
check(proc_util.filetime_to_time(116444736000000000) == 0.0,
      '1601 起点换算为 Unix 0')
check(proc_util.filetime_to_time(None) is None, 'None -> None（不抛异常）')

print('[2] 真实进程身份（本进程）')
ident = proc_util.process_identity(os.getpid())
check(ident is not None and ident['pid'] == os.getpid(),
      '能读到本进程身份：%r' % (ident,))
check(bool(ident.get('created')), '创建时间为正整数')
check(ident.get('created_at') and abs(ident['created_at'] - time.time()) < 86400,
      '创建时刻换算成 Unix 秒（近期）')
check((ident.get('name') or '').lower().endswith('.exe'),
      '映像名带 .exe（%r）' % ident.get('name'))

print('[3] 已退出进程 -> None')
check(proc_util.process_identity(0x7FFFFFF0) is None,
      '不存在的 pid 返回 None（调用方按"未知"处理）')
check(proc_util.process_created(0x7FFFFFF0) is None, 'process_created 同样返回 None')

print('[4] 身份复核判据')
ok, why = proc_util._same_process(os.getpid(), 'definitely-not-us.exe', None)
check(ok is False and 'pid 现在是' in why, '名字不符 -> 判定不是同一进程（%s）' % why)
ok, why = proc_util._same_process(os.getpid(), ident['name'], None)
check(ok is False and '创建时间不可用' in why,
      '缺创建时间 -> 只算"名字相同"，不允许强杀（%s）' % why)
ok, why = proc_util._same_process(os.getpid(), ident['name'], ident['created'])
check(ok is True and why == '', '创建时间一致 -> 确认同一进程')


print('[5] 关闭流程：没有句柄/身份不明时只请求退出，不强杀')


class Ctx(object):
    def __init__(self, procs):
        self.procs = procs
        self.logs = []

    def snapshot(self):
        return dict(self.procs)

    def log(self, msg):
        self.logs.append(msg)

    def games_present(self, procs=None):
        return False


class Core(object):
    TASKKILL = 'taskkill'

    def __init__(self):
        self.calls = []

    def run_hidden(self, args):
        self.calls.append(list(args))


ctx = Ctx({0x7FFFFFF0: 'ghost.exe'})
core = Core()
res = proc_util.close_processes(ctx, core, [(0x7FFFFFF0, 'ghost.exe')], 0.15, True)
check(res.get(0x7FFFFFF0) == 'left',
      '身份不明 -> verdict=left（%r）' % (res,))
check(not any('/F' in c for c in core.calls),
      '没有发出 taskkill /F（%r）' % (core.calls,))
check(any('身份未确认' in m for m in ctx.logs), '日志写明"身份未确认"（%r）' % (ctx.logs,))
check(any('/PID' in c for c in core.calls), '仍发过一次优雅关闭请求')

print('[6] 名字已被换掉时直接跳过（快照层就拦下）')
ctx2 = Ctx({4321: 'other.exe'})
core2 = Core()
res2 = proc_util.close_processes(ctx2, core2, [(4321, 'afterburner.exe')], 0.05, True)
check(res2.get(4321) == 'skipped' and core2.calls == [],
      'snapshot 名字不符 -> skipped 且不发任何关闭请求')

if FAILS:
    print('\nFAILED %d 项：%r' % (len(FAILS), FAILS))
    sys.exit(1)
print('\nALL PASS')
