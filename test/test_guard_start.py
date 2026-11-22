# -*- coding: utf-8 -*-
"""v2.16.0 守护启停：命令构造与「任务被禁用」自愈（不碰真实计划任务）。

为什么单列一条：一键启动的价值全在"什么状态都拉得起来"。此前「关掉开机
自动启动」是把计划任务**整套**禁用，副作用是手动启动一起失效——
Start-ScheduledTask 报 0x80041326「任务已禁用」，用户在设置页点「启动守护」
只会看到「未能启动」。这条守着三个行为：

  1. 关开机自启只切触发器，绝不再 Disable-ScheduledTask 整套任务；
  2. start_guard 撞上"任务已禁用"时自己 Enable + 关触发器 + 再启动；
  3. autostart_state 两处都看（任务 + 触发器），任一被关就算没开。

全程用替身 ps_run，不改用户机器上的 WBOX 任务、不动开机自启设置。
用法：C:\\Python38\\python.exe test\\test_guard_start.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

import panel_state as ps  # noqa: E402

PASSED = []
FAILED = []


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


class FakeRun(object):
    """ps_run 的替身：记录命令，按队列回放 (rc, out)。"""

    def __init__(self):
        self.calls = []
        self.replies = []

    def __call__(self, cmd, timeout=25):
        self.calls.append(cmd)
        if self.replies:
            return self.replies.pop(0)
        return 0, ''


def main():
    real = ps.ps_run
    fake = FakeRun()
    ps.ps_run = fake
    try:
        print('[1] 关开机自启：只切触发器，不动任务本身')
        fake.calls, fake.replies = [], [(0, '')]
        rc, _out = ps.set_autostart(False)
        cmd = fake.calls[0] if fake.calls else ''
        step(rc == 0, '返回 rc=0')
        step('Enabled = $false' in cmd, '把触发器设成 Disabled')
        step('Disable-ScheduledTask' not in cmd,
             '不再整套禁用任务（否则手动启动一起废）')
        step('Set-ScheduledTask' in cmd and '-Trigger' in cmd,
             '改完触发器回写任务')

        print('[2] 开开机自启：同一套命令，只把触发器打开')
        fake.calls, fake.replies = [], [(0, '')]
        ps.set_autostart(True)
        cmd = fake.calls[0] if fake.calls else ''
        step('Enabled = $true' in cmd, '把触发器设成 Enabled')
        step('Enable-ScheduledTask' in cmd,
             '顺便启用任务（修老版本遗留的禁用状态）')

        print('[3] autostart_state：任务与触发器都要开')
        fake.replies = [(0, 'on')]
        step(ps.autostart_state() is True, "命令回 'on' => 开")
        fake.replies = [(0, 'off')]
        step(ps.autostart_state() is False, "命令回 'off' => 关")
        fake.replies = [(1, 'Get-ScheduledTask : 找不到任务')]
        step(ps.autostart_state() is False, '任务不存在/查询失败 => 关，不抛异常')

        print('[4] 任务可用时：一次启动，不额外折腾')
        fake.calls, fake.replies = [], [(0, '')]
        rc, _out = ps.start_guard()
        step(rc == 0 and len(fake.calls) == 1,
             '只发一条 Start-ScheduledTask（%d 条）' % len(fake.calls))

        print('[5] 任务被禁用（老版本关过开机自启）=> 自动启用后启动')
        fake.calls = []
        fake.replies = [(1, 'Start-ScheduledTask : 无法启动服务\n'
                            '  HRESULT 0x80041326'), (0, '')]
        rc, out = ps.start_guard()
        step(rc == 0, '最终 rc=0（用户点一下就能起来）')
        step(len(fake.calls) == 2, '第二条命令做修复（共 %d 条）' % len(fake.calls))
        fix = fake.calls[1] if len(fake.calls) > 1 else ''
        step('Enable-ScheduledTask' in fix, '先启用任务')
        step('Enabled = $false' in fix,
             '启用后仍关掉触发器：保持用户「开机不自启」的原意')
        step('Start-ScheduledTask' in fix, '最后再启动')

        print('[6] 其他启动失败：原样上报，不瞎重试')
        fake.calls, fake.replies = [], [(1, 'access denied')]
        rc, out = ps.start_guard()
        step(rc == 1 and len(fake.calls) == 1, '只试一次就交卷')
        step('access denied' in out, '原始错误原样带回给面板记日志')
    finally:
        ps.ps_run = real

    print('')
    if FAILED:
        print('FAILED %d 项：%s' % (len(FAILED), FAILED))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
