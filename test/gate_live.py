# -*- coding: utf-8 -*-
"""闸门 4 真实链路验证：真进程快照 + 真配置 + 假"更新组件"进程。

验证 observe() 在 guard 主循环里被调用时，能否真的用真实进程表推进时间基准，
以及 verdict() 在真实配置下给出正确的 QUIET / CLEAN。
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, 'app')
sys.path.insert(0, os.path.join(ROOT, 'src'))

import guard_core as g

g.BASE = APP
g.CONFIG_PATH = os.path.join(APP, 'config.json')
g.LOG_PATH = os.path.join(APP, 'gate_probe.log')      # 不污染 guard.log
g.PID_PATH = os.path.join(APP, 'gate_probe.pid')
g.STOP_PATH = os.path.join(APP, 'gate_probe.flag')

print('BASE =', g.BASE)
guard = g.Guard()
guard.reload_config()
ac = guard.cfg.get('ace_cleanup', {}) or {}
# S3-S5 之后 ACE 清理逻辑搬进了 plugins\ace，本脚本要的 cleaner 从插件里取
ace_entry = guard.host.entry_by_id('ace')
guard.cleaner = ace_entry.plugin.cleaner if ace_entry else None
print('cleaner =', guard.cleaner is not None,
      '| quiet_after_update_sec =', ac.get('quiet_after_update_sec'),
      '| grace =', ac.get('grace_sec'), '| min_age =', ac.get('min_ace_age_sec'))

fake = os.path.join(os.environ.get('TEMP', '.'), 'sguardupdate64.exe')
src = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'ping.exe')
shutil.copy2(src, fake)
proc = subprocess.Popen([fake, '-n', '40', '127.0.0.1'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(1.2)
    procs = g.snapshot()
    seen = 'sguardupdate64.exe' in procs.values()
    print('假更新组件在快照里 =', seen)
    hit = guard.cleaner.observe(procs)
    gap = time.time() - guard.cleaner.updater_seen_at
    print('observe 命中 =', hit, '| 时间基准距今 %.1fs' % gap)

    code, reason = guard.cleaner.verdict(procs, time.time() - 400)
    print('verdict（更新组件仍在跑）=', code, '|', reason)

    code, reason = guard.cleaner.verdict(
        {pid: n for pid, n in procs.items() if n != 'sguardupdate64.exe'},
        time.time() - 400)
    print('verdict（组件已退、仍在 30s 内）=', code, '|', reason)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

time.sleep(0.5)
left = g.snapshot()

# 本机此刻没有 ACE 在跑，用"长寿进程改名"合成一条 ACE 进程记录，
# 这样闸门 3（进程年龄 > 300s）能过，真正走到闸门 4。pid 必须真实存在，
# 否则 process_age_sec 返回 None 会让闸门 3 直接跳过 —— 结果一样，但不够真实。
long_pid = None
for pid, name in left.items():
    if name in ('explorer.exe', 'svchost.exe', 'winlogon.exe'):
        long_pid = pid
        break
synth = dict(left)
if long_pid:
    synth[long_pid] = 'sguard64.exe'
    age = __import__('ace_native').process_age_sec(long_pid)
    print('合成 ACE 进程 pid=%d（真实年龄 %.0fs）' % (long_pid, age or -1))
else:
    print('WARN 没找到长寿进程，闸门 3 会跳过年龄检查')

code, reason = guard.cleaner.verdict(synth, time.time() - 400)
print('verdict（30s 窗口内、有 ACE）=', code, '|', reason)

# 把基准往前推，模拟"更新已经过去很久"
guard.cleaner.updater_seen_at -= int(ac.get('quiet_after_update_sec', 30)) + 5
code, reason = guard.cleaner.verdict(synth, time.time() - 400)
print('verdict（更新已过去、有 ACE）=', code, '|', reason)

# 冷却期内应被 COOLDOWN 拦住（模拟刚清理过）
guard.cleaner.last_clean_at = time.time() - 120
code, reason = guard.cleaner.verdict(synth, time.time() - 400)
print('verdict（冷却期内）=', code, '|', reason)
guard.cleaner.last_clean_at = 0.0

try:
    os.remove(fake)
except OSError:
    pass
try:
    os.remove(g.LOG_PATH)
except OSError:
    pass
print('DONE')
