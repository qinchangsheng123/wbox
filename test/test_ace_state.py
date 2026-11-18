# -*- coding: utf-8 -*-
"""ACE 清理状态机离线测试（对应 P0-6：冷却 / 宽限被未来时间戳锁死）。

覆盖：
  1. 状态文件里的 last_clean_at 是未来时间 → 加载时夹到 now，冷却不再永久卡死
  2. 状态文件是坏 JSON → 不抛异常，last_clean_at 归零
  3. 正常冷却（上次清理 100 秒前，冷却 600 秒）→ COOLDOWN
  4. verdict 收到未来的 last_exit_at → 夹到 now，判 GRACE 而不是异常
  5. 取不到进程创建时间（含时钟回拨算出负数）→ 一律按保护期 YOUNG

全部用临时目录里的状态文件，不碰仓库里的 ace_state.json。

单进程卫生（v2.3.0 复核补丁 / QA P2-5）：本文件以前在模块级把
`ace_native.process_age_sec` 换成替身且**不还原**，于是同进程里后跑的
`test_quiet_gate.py` 抓到的 `REAL_AGE` 已经是替身，第 [8] 组把"取不到创建
时间"误判成 CLEAN。现在改用 main() + try/finally 还原，跑完即物归原主。
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import ace_native

FAILED = []
LOGS = []

TMP = tempfile.mkdtemp(prefix='abg_ace_state_')


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILED.append(msg)


def cfg(**kw):
    base = {
        'enabled': True,
        'grace_sec': 180,
        'min_ace_age_sec': 300,
        'quiet_after_update_sec': 30,
        'cooldown_sec': 600,
        'eval_interval_sec': 60,
        'max_idle_sec': 1200,
        'dry_run': True,
        'ace_processes': ['SGuard64.exe'],
        'ace_services': [],
        'ace_dirs': [],
        'blockers': ['SGuardUpdate64.exe'],
    }
    base.update(kw)
    return base


def make(name, payload=None, raw=None, **kw):
    """在临时目录里写一份状态文件，再据此构造 cleaner。"""
    path = os.path.join(TMP, name)
    if raw is not None:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(raw)
    elif payload is not None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
    return ace_native.AceCleaner(cfg(**kw), lambda m: LOGS.append(m), path,
                                 games_present=lambda: False)


def main():
    # 闸门 3 需要真实进程年龄；假 pid 一律返回"跑够久了"，只验闸门 4 与时间夹取。
    # 这条替身是**本进程全局**的，必须在 finally 里还原（见文件头）。
    real_age = ace_native.process_age_sec
    ace_native.process_age_sec = lambda pid: 9999.0
    try:
        return run_cases()
    finally:
        ace_native.process_age_sec = real_age
        shutil.rmtree(TMP, ignore_errors=True)


def run_cases():
    ACE_PROCS = {999999: 'sguard64.exe'}
    FUTURE = time.time() + 86400          # 一天后
    EXIT_AGO = time.time() - 400          # 游戏已退出 400 秒，越过宽限 180 秒

    print('[1] last_clean_at 在未来 → 加载时夹到 now，冷却不再被永久锁死')
    c = make('future.json', {'last_clean_at': FUTURE})
    check(abs(c.last_clean_at - time.time()) < 5,
          'last_clean_at 已被夹到 now（差 %.0fs）' % (c.last_clean_at - time.time()))
    check(any('在未来' in m for m in LOGS), '日志点明了状态里的时间是未来')
    c.updater_seen_at = time.time() - 600
    code, reason = c.verdict(ACE_PROCS, EXIT_AGO)
    # 夹到 now 后仍在一个 cooldown 之内（600s），但已从"永远 COOLDOWN"变成有界等待：
    # 关键是没有负的时间差（旧实现会算出 -86400s，永远 < cooldown_sec）。
    check(code == 'COOLDOWN' and '距上次清理 0s' in reason,
          'code=%s reason=%s（时间差必须 ≥ 0）' % (code, reason))
    c.last_clean_at = time.time() - 601     # 冷却走完 → 必须能放行
    code, reason = c.verdict(ACE_PROCS, EXIT_AGO)
    check(code == 'CLEAN', '冷却 600 秒后放行：code=%s reason=%s' % (code, reason))

    print('[2] 状态文件是坏 JSON → 不抛异常，冷却基准归零')
    LOGS.clear()
    c = make('broken.json', raw='{"last_clean_at": ')
    check(c.last_clean_at == 0.0, 'last_clean_at=%r' % c.last_clean_at)
    c.updater_seen_at = time.time() - 600
    code, reason = c.verdict(ACE_PROCS, EXIT_AGO)
    check(code == 'CLEAN', '坏状态不影响判定：code=%s' % code)

    print('[3] 正常冷却：上次清理 100 秒前，冷却 600 秒 → COOLDOWN')
    c = make('cooling.json', {'last_clean_at': time.time() - 100})
    c.updater_seen_at = time.time() - 600
    code, reason = c.verdict(ACE_PROCS, EXIT_AGO)
    check(code == 'COOLDOWN', 'code=%s reason=%s' % (code, reason))
    check('冷却期内' in reason, '理由里点明冷却期')

    print('[4] 冷却已过（700 秒前）→ 放行 CLEAN')
    c = make('cooled.json', {'last_clean_at': time.time() - 700})
    c.updater_seen_at = time.time() - 600
    code, reason = c.verdict(ACE_PROCS, EXIT_AGO)
    check(code == 'CLEAN', 'code=%s reason=%s' % (code, reason))

    print('[5] last_exit_at 在未来 → 夹到 now，判 GRACE 而不是算出负的空闲时长')
    c = make('ok.json', {'last_clean_at': 0})
    c.updater_seen_at = time.time() - 600
    code, reason = c.verdict(ACE_PROCS, FUTURE)
    check(code == 'GRACE', 'code=%s reason=%s' % (code, reason))
    check('未满' in reason, '理由里说明了宽限期未满')

    print('[6] verdict 里的 last_clean_at 被写成未来 → 直接判定时也夹到 now')
    c = make('ok2.json', {'last_clean_at': 0})
    c.updater_seen_at = time.time() - 600
    c.last_clean_at = time.time() + 86400
    code, reason = c.verdict(ACE_PROCS, EXIT_AGO)
    check(code == 'COOLDOWN' and '距上次清理 0s' in reason,
          'code=%s reason=%s（不再是 -86400s 的永久冷却）' % (code, reason))
    check(c.last_clean_at <= time.time() + 1,
          '判定后 last_clean_at 已夹到 now（%r）' % c.last_clean_at)

    print('[7] 取不到进程创建时间 → 按保护期 YOUNG（时钟回拨的负数同样按取不到处理）')
    stub = ace_native.process_age_sec
    try:
        ace_native.process_age_sec = lambda pid: None      # 打不开 / 时钟回拨
        c = make('ok3.json', {'last_clean_at': 0})
        c.updater_seen_at = time.time() - 600
        code, reason = c.verdict(ACE_PROCS, EXIT_AGO)
        check(code == 'YOUNG', 'code=%s reason=%s' % (code, reason))
        check('取不到创建时间' in reason, '理由点明了原因')
    finally:
        ace_native.process_age_sec = stub

    print('[8] _clamp_clock 对正常时间原样返回')
    c = make('ok4.json', {'last_clean_at': 0})
    past = time.time() - 1234
    check(abs(c._clamp_clock(past, 'x') - past) < 1, '过去时间不被改写')

    print('')
    print('ALL PASS' if not FAILED else 'FAILED: %d -> %s' % (len(FAILED), FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
