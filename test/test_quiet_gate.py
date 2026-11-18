# -*- coding: utf-8 -*-
"""闸门 4（QUIET）口径离线测试：只看"最后一次见到更新组件的时刻"，不看文件 mtime。

验证三件事：
  1. 刚见过更新组件 → QUIET（不动手）
  2. 距上次见到已超过 quiet_after_update_sec → 放行到 CLEAN（其余条件都满足时）
  3. ACE 目录里有新鲜 mtime 但没见过更新组件 → 仍应 CLEAN（旧口径会误拦）

单进程卫生（v2.3.0 复核补丁 / QA P2-5）：本文件以前用
`REAL_AGE = ace_native.process_age_sec` 取"原始函数"，但如果前一个模块
（test_ace_state）打了替身且没还原，这里抓到的是替身，第 [8] 组就会把
"取不到创建时间"误判成 CLEAN。现在 [8] 改为直接装 `lambda pid: None`
（它要的语义本来就是"取不到"），并在 finally 里还原进本文件时拿到的函数，
与文件执行顺序无关。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import ace_native

FAILED = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILED.append(msg)


def make(quiet=30, ace_dirs=None):
    cfg = {
        'enabled': True,
        'grace_sec': 180,
        'min_ace_age_sec': 300,
        'quiet_after_update_sec': quiet,
        'cooldown_sec': 600,
        'eval_interval_sec': 60,
        'max_idle_sec': 1200,
        'dry_run': True,
        'ace_processes': ['SGuard64.exe', 'SGuardSvc64.exe'],
        'ace_services': [],                  # 不查真实服务
        'ace_dirs': ace_dirs or [],
        'blockers': ['SGuardUpdate64.exe', 'ACE-Setup64.exe'],
    }
    return ace_native.AceCleaner(cfg, lambda m: None, None, games_present=lambda: False)


def main():
    # 闸门 3 现在把「取不到创建时间」判为保护期内；本测试只验闸门 4，
    # 所以给假 pid 一个足够大的年龄，让闸门 3 正常放行。
    # 这条替身是进程全局的，跑完必须还原（见文件头与 finally）。
    entry_age = ace_native.process_age_sec
    ace_native.process_age_sec = lambda pid: 9999.0
    try:
        return run_cases()
    finally:
        ace_native.process_age_sec = entry_age


def run_cases():
    # 假 pid：年龄由上面的替身固定为 9999 秒，闸门 3 不干预本测试
    ACE_PROCS = {999999: 'sguard64.exe'}
    UPD_PROCS = dict(ACE_PROCS)
    UPD_PROCS[999998] = 'sguardupdate64.exe'
    EXIT_AT = time.time() - 400          # 游戏已退出 400 秒，越过宽限 180 秒
    tmp = os.path.join(os.environ.get('TEMP', '.'), 'abg_quiet_probe')
    probe = os.path.join(tmp, 'ACE-BASE.sys')

    print('[1] 刚见过更新组件 → QUIET')
    c = make()
    c.observe(UPD_PROCS)
    code, reason = c.verdict(ACE_PROCS, EXIT_AT)
    check(code == 'QUIET', 'code=%s reason=%s' % (code, reason))
    check('更新组件' in reason, '理由里点明了更新组件')

    print('[2] 更新组件退出 31 秒后 → 放行 CLEAN')
    c.updater_seen_at = time.time() - 31
    code, reason = c.verdict(ACE_PROCS, EXIT_AT)
    check(code == 'CLEAN', 'code=%s reason=%s' % (code, reason))

    print('[3] 更新组件退出 20 秒（仍在 30 秒窗口内）→ QUIET')
    c.updater_seen_at = time.time() - 20
    code, reason = c.verdict(ACE_PROCS, EXIT_AT)
    check(code == 'QUIET', 'code=%s' % code)

    print('[4] 更新组件仍在跑 → 先被闸门 2 BLOCKER 拦住')
    c = make()
    c.observe(UPD_PROCS)
    code, reason = c.verdict(UPD_PROCS, EXIT_AT)
    check(code == 'BLOCKER', 'code=%s reason=%s' % (code, reason))

    print('[5] 目录里有新鲜 mtime 但没见过更新组件 → 仍 CLEAN（旧口径会误拦）')
    os.makedirs(tmp, exist_ok=True)
    with open(probe, 'w') as f:
        f.write('x')
    now = time.time()
    os.utime(probe, (now, now))          # mtime = 刚刚
    c = make(ace_dirs=[tmp])
    c.updater_seen_at = time.time() - 600
    code, reason = c.verdict(ACE_PROCS, EXIT_AT)
    check(code == 'CLEAN', 'code=%s reason=%s' % (code, reason))
    check(bool(c._recent_writes(10)), '诊断接口仍能看到新鲜写入（只作参考）')

    print('[6] observe 在没更新组件时不推进时间基准')
    c = make()
    c.updater_seen_at = 1000.0
    hit = c.observe(ACE_PROCS)
    check(hit is False, 'observe 返回 False')
    check(c.updater_seen_at == 1000.0, '时间基准没被改写')

    print('[7] 构造后默认静默一个窗口（守护刚起来不立刻动手）')
    c = make()
    code, reason = c.verdict(ACE_PROCS, EXIT_AT)
    check(code == 'QUIET', 'code=%s' % code)

    print('[8] 取不到 ACE 创建时间 → 闸门 3 拦住，绝不返回 CLEAN')
    stub = ace_native.process_age_sec
    try:
        # 直接装"取不到"的替身，不去猜本进程里谁动过这个函数
        ace_native.process_age_sec = lambda pid: None
        c = make()
        c.updater_seen_at = time.time() - 600
        code, reason = c.verdict(ACE_PROCS, EXIT_AT)
        check(code == 'YOUNG', 'code=%s reason=%s' % (code, reason))
        check('取不到创建时间' in reason, '理由里点明了原因')
    finally:
        ace_native.process_age_sec = stub

    try:
        os.remove(probe)
        os.rmdir(tmp)
    except OSError:
        pass

    print('')
    print('ALL PASS' if not FAILED else 'FAILED: %d' % len(FAILED))
    return 1 if FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
