# -*- coding: utf-8 -*-
"""v2.5.0-② 心跳管道测试：client 读状态 / 杀守护不卡死 / 停止发心跳会变红。

全部用临时管道名（test 专用），不碰生产守护（pid 2376）、不碰全局管道名——
否则和别的测试 / 真守护抢 `\\.\pipe\WBOXGuardBeat`。面板侧 helper
（settings_gui.short_guard_state）只验证文案口径，不建 Tk 窗口。

关键回归点：心跳机制任何一环坏掉（守护退出 / 崩溃 / provider 不给数据），
客户端都必须在 < 2 秒内给出 None 并显示「守护没在运行」，绝不挂死菜单。
用法：C:\\Python38\\python.exe test\\test_guard_beat.py
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))

import guard_beat  # noqa: E402

# 本测试专用管道名：与生产 PIPE_NAME 隔离，随便抢都伤不到真守护
TEST_PIPE = r'\\.\pipe\WBOXGuardBeatTest'

PASSED = []
FAILED = []


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


def fake_payload(pid=4321, session='idle'):
    return {
        'pid': pid, 'version': '2.5.0-test', 'session': session, 'seq': 7,
        'started_at': 0.0,
        'plugins': [
            {'id': 'demo_a', 'status': 'ok', 'ticks': 3, 'last_tick': 1.5},
            {'id': 'demo_b', 'status': 'degraded', 'ticks': 2, 'last_tick': 1.4},
        ],
    }


def read_checked(deadline_ms=1500):
    """测试用放宽预算的读：隔离管道 + 慢机器 CI 下也不至于误报。"""
    return guard_beat.read_status(TEST_PIPE, deadline_ms=deadline_ms)


def main():
    print('[1] 假守护在跑 -> 客户端读到一行状态')
    srv = guard_beat.BeatServer(TEST_PIPE, provider=lambda: fake_payload())
    srv.start()
    st = read_checked()
    step(isinstance(st, dict), 'read_status 返回 dict：%r' % (st,))
    step(isinstance(st, dict) and st.get('pid') == 4321
         and st.get('version') == '2.5.0-test'
         and st.get('session') == 'idle',
         '状态含 pid / 版本 / 会话')
    step(isinstance(st, dict) and isinstance(st.get('plugins'), list)
         and len(st['plugins']) == 2
         and st['plugins'][0].get('last_tick') == 1.5,
         '状态含各插件最近 tick 时间')

    print('[2] 连续两次读：每轮都新连接，服务端不粘人')
    st2 = read_checked()
    step(isinstance(st2, dict) and st2.get('pid') == 4321,
          '第二次读取同样成功（服务端循环重建管道实例）')

    print('[3] 注入「停止发心跳」（provider 返回 None）-> 客户端读不到')
    srv_empty = guard_beat.BeatServer(TEST_PIPE, provider=lambda: None)
    srv.stop()
    srv_empty.start()
    t0 = time.time()
    st3 = read_checked()
    dt = time.time() - t0
    step(st3 is None, '没有心跳内容 -> read_status 返回 None')
    step(dt < 2.0, '读失败不卡死（%.2fs < 2s）' % dt)
    srv_empty.stop()

    print('[4] 杀守护（只砸句柄，模拟崩溃）-> 客户端立即读不到')
    srv_kill = guard_beat.BeatServer(TEST_PIPE, provider=lambda: fake_payload())
    srv_kill.start()
    st4 = read_checked()
    step(isinstance(st4, dict) and st4.get('pid') == 4321,
         '崩溃前状态可读（对照组）')
    srv_kill.kill()
    t0 = time.time()
    st5 = read_checked()
    dt = time.time() - t0
    step(st5 is None, '杀守护后 read_status 返回 None')
    step(dt < 2.0, '杀守护后读失败不卡死（%.2fs < 2s）' % dt)

    print('[5] 守护没起 -> 面板文案「守护没在运行」，不报错')
    import settings_gui  # noqa: E402  只用模块级函数，不建窗口
    # 生产守护可能正在跑（真管道有心跳），[5] 也必须指到空的测试管道
    orig_pipe5 = guard_beat.PIPE_NAME
    guard_beat.PIPE_NAME = TEST_PIPE
    try:
        text = settings_gui.short_guard_state()
        step(text == '守护没在运行', '无心跳时状态行 = %r' % text)
        text_probe = settings_gui.short_guard_state(probe=True)
        step(text_probe == '（自检）', 'probe 模式状态行 = %r' % text_probe)
    finally:
        guard_beat.PIPE_NAME = orig_pipe5

    print('[6] 守护在跑 -> 面板文案「运行中 · 会话白话」')
    # 面板读的是生产管道名：测试把模块常量指到测试管道，用完恢复
    orig_pipe = guard_beat.PIPE_NAME
    guard_beat.PIPE_NAME = TEST_PIPE
    try:
        srv2 = guard_beat.BeatServer(TEST_PIPE,
                                     provider=lambda: fake_payload(session='active'))
        srv2.start()
        text2 = settings_gui.short_guard_state()
        step(text2.startswith('运行中'), '有心跳时状态行 = %r' % text2)
        step('游戏中' in text2, '会话映射成白话（active -> 游戏）')
        srv2.stop()
    finally:
        guard_beat.PIPE_NAME = orig_pipe

    print('[7] 面板 2 秒节拍连读 3 次：服务端线程仍活着（无句柄泄漏）')
    srv3 = guard_beat.BeatServer(TEST_PIPE, provider=lambda: fake_payload())
    srv3.start()
    oks = 0
    for _ in range(3):
        if isinstance(read_checked(), dict):
            oks += 1
        time.sleep(0.05)
    step(oks == 3, '3 次连读全部成功（%d/3）' % oks)
    srv3.stop()
    step(not srv3._thread.is_alive() if srv3._thread else True,
         'stop() 后服务线程已退出')

    print('[8] 降级状态：心跳带 events/health，"进程在"与"全功能在"分开说')
    import settings_gui  # noqa: E402
    orig_pipe8 = guard_beat.PIPE_NAME
    guard_beat.PIPE_NAME = TEST_PIPE
    try:
        payload = fake_payload(session='idle')
        payload['events'] = {'ok': False, 'mode': 'poll', 'wakes': 0,
                             'resubscribes': 2,
                             'hint': '进程事件监听不可用，当前使用轮询（WMI 服务'
                                     '（Winmgmt）未启动）'}
        payload['health'] = {'degraded': True,
                             'reasons': ['进程事件监听不可用，当前使用轮询']}
        srv4 = guard_beat.BeatServer(TEST_PIPE, provider=lambda: payload)
        srv4.start()
        text = settings_gui.short_guard_state()
        step('运行中' in text and '轮询兜底' in text,
             '事件降级时状态行点明轮询兜底：%r' % text)
        ev_text, pl_text, note = settings_gui.guard_diagnostics()
        step(ev_text == '轮询兜底', '设置页「进程事件」= %r' % ev_text)
        step('WMI 服务' in note, '补充说明给出具体原因：%r' % note[:24])
        step(pl_text == '1 个运行异常',
             '设置页「插件健康」按心跳里的插件状态统计：%r' % pl_text)

        payload['events'] = {'ok': True, 'mode': 'event', 'wakes': 3,
                             'resubscribes': 0, 'hint': ''}
        payload['health'] = {'degraded': False, 'reasons': []}
        text2 = settings_gui.short_guard_state()
        step('轮询兜底' not in text2 and text2.startswith('运行中'),
             '事件健康时不误报降级：%r' % text2)
        ev_text2, _pl, note2 = settings_gui.guard_diagnostics()
        step(ev_text2 == '事件驱动' and note2 == '', '正常态：%r' % ev_text2)

        # 旧版守护没有 events 字段：按老文案显示，不能凭空报降级
        del payload['events']
        text3 = settings_gui.short_guard_state()
        step('轮询兜底' not in text3, '旧版守护（无 events 字段）不误报：%r' % text3)
        step(settings_gui.guard_diagnostics()[0] == '未上报',
             '旧版守护的进程事件显示「未上报」')
        srv4.stop()
    finally:
        guard_beat.PIPE_NAME = orig_pipe8

    print('[9] 设置页快照：一次读心跳交出「是否运行 / 状态行 / 诊断」')
    # v2.16.0 起设置页三行 + 启停按钮共用 guard_snapshot()，必须同源：
    # 状态行说「没在运行」而按钮显示「停止」是最糟的错配，这条守着它。
    import panel_state  # noqa: E402
    orig_pipe9 = guard_beat.PIPE_NAME
    guard_beat.PIPE_NAME = TEST_PIPE
    try:
        snap = panel_state.guard_snapshot()
        step(snap['running'] is False and snap['state'] == '守护没在运行',
             '无心跳：running=False / state=%r' % snap['state'])
        step(snap['events'] == '未运行' and snap['plugins'] == '未运行',
             '无心跳时诊断两行也说「未运行」，不编造正常')
        payload = fake_payload(session='idle')
        payload['events'] = {'ok': True, 'mode': 'event', 'wakes': 1,
                             'resubscribes': 0, 'hint': ''}
        srv5 = guard_beat.BeatServer(TEST_PIPE, provider=lambda: payload)
        srv5.start()
        snap2 = panel_state.guard_snapshot()
        step(snap2['running'] is True, '有心跳：running=True')
        step(snap2['state'].startswith('运行中'), '同一快照的状态行 = %r'
             % snap2['state'])
        step(snap2['events'] == '事件驱动', '同一快照的进程事件 = %r'
             % snap2['events'])
        srv5.stop()
    finally:
        guard_beat.PIPE_NAME = orig_pipe9

    print('')
    if FAILED:
        print('FAILED %d 项：%s' % (len(FAILED), FAILED))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
