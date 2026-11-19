# -*- coding: utf-8 -*-
"""安装/卸载事务状态与中断恢复（审查项 7）离线验证。

只驱动 install_txn 的状态机：不安装、不卸载、不碰 ProgramData（事务目录
被替换成临时目录）。
跑法：python test\\test_install_txn.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import install_txn   # noqa: E402

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


TMP = tempfile.mkdtemp(prefix='abg_txn_')
install_txn._data_dir = lambda: TMP          # 隔离 ProgramData
TARGET = os.path.join(TMP, 'Program Files', 'WBOX')
LOGS = []


def reset():
    install_txn.clear()
    LOGS[:] = []


def mk_target(with_exe=True):
    os.makedirs(TARGET, exist_ok=True)
    exe = os.path.join(TARGET, 'WBOX.exe')
    if with_exe:
        with open(exe, 'wb') as f:
            f.write(b'MZ')
    elif os.path.isfile(exe):
        os.remove(exe)


print('[1] 正常完成 -> 事务日志清理')
reset()
mk_target()
txn = install_txn.begin('install', TARGET, 'WBOX', '9.9.9')
check(os.path.isfile(install_txn.journal_path()), 'begin 后事务日志落盘')
install_txn.step(txn, 'extract', 'shared_runtime', files=24)
check(install_txn.read()['phase'] == 'shared_runtime', 'step 推进 phase')
check(install_txn.read()['steps'][-1]['files'] == 24, 'step 记录附加信息')
install_txn.finish(txn)
check(not os.path.isfile(install_txn.journal_path()), 'finish 清掉事务日志')
check(install_txn.recover(TARGET, log=LOGS.append)['action'] == '',
      '没有事务时 recover 静默')

print('[2] 安装中断：程序文件已就位 -> 报"可续做"，不谎报完成')
reset()
mk_target()
txn = install_txn.begin('install', TARGET, 'WBOX', '9.9.9')
install_txn.step(txn, 'extract', 'plugins')
install_txn.step(txn, 'plugins_restored', 'shortcuts')
res = install_txn.recover(TARGET, log=LOGS.append)
check(res['action'] == 'incomplete', '动作=incomplete（%r）' % (res,))
check('第 2 步' in res['text'] and 'plugins_restored' in res['text'],
      '文案写清停在第几步、哪一步（%r）' % res['text'])
check(any('上次安装' in m for m in LOGS), '日志留痕')
check(not os.path.isfile(install_txn.journal_path()), '报告后清理状态，避免刷屏')

print('[3] 安装中断：目标缺 WBOX.exe -> 报"需要重新安装"')
reset()
mk_target(with_exe=False)
txn = install_txn.begin('install', TARGET, 'WBOX', '9.9.9')
install_txn.step(txn, 'extract', 'shared_runtime')
res = install_txn.recover(TARGET, log=LOGS.append)
check(res['action'] == 'broken' and '重新安装' in res['text'],
      '动作=broken（%r）' % (res,))

print('[4] 安装中断且配置写坏 -> 自动从备份恢复')
reset()
mk_target()
good_cfg = os.path.join(TMP, 'good.json')
with open(good_cfg, 'w', encoding='utf-8') as f:
    json.dump({'afterburner_path': 'D:/AB/MSIAfterburner.exe'}, f)
bad_cfg = os.path.join(TARGET, 'config.json')
with open(bad_cfg, 'w', encoding='utf-8') as f:
    f.write('{ this is not json')
txn = install_txn.begin('install', TARGET, 'WBOX', '9.9.9')
bk = install_txn.backup_file('config', good_cfg)
install_txn.remember_backup(txn, 'config', bad_cfg, bk)
check(bool(bk) and os.path.isfile(bk), '覆盖前备份成功')
res = install_txn.recover(TARGET, log=LOGS.append)
check(res['action'] == 'restored_config', '动作=restored_config（%r）' % (res,))
with open(bad_cfg, 'r', encoding='utf-8') as f:
    restored = json.load(f)
check(restored.get('afterburner_path') == 'D:/AB/MSIAfterburner.exe',
      '配置内容确实被还原（%r）' % (restored,))

print('[5] 卸载中断')
reset()
mk_target()
txn = install_txn.begin('uninstall', TARGET, 'WBOX')
install_txn.step(txn, 'task_deleted', 'shortcuts')
res = install_txn.recover(TARGET, log=LOGS.append)
check(res['action'] == 'uninstall_incomplete' and '重新卸载' in res['text'],
      '目录还在 -> 提示重新卸载（%r）' % (res,))
reset()
install_txn.begin('uninstall', os.path.join(TMP, 'gone'), 'WBOX')
res = install_txn.recover(os.path.join(TMP, 'gone'), log=LOGS.append)
check(res['action'] == 'cleanup', '目录已删 -> 只清残留状态（%r）' % (res,))

print('[6] 状态文案与异常容错')
reset()
txn = install_txn.begin('install', TARGET, 'WBOX', '9.9.9')
install_txn.step(txn, 'extract', 'shared_runtime')
text = install_txn.status_text(install_txn.read(), TARGET)
check('安装' in text and 'extract' in text, 'status_text 可读（%r）' % text)
with open(install_txn.journal_path(), 'w', encoding='utf-8') as f:
    f.write('garbage')
check(install_txn.recover(TARGET, log=LOGS.append)['action'] == '',
      '事务日志损坏时安静返回，不抛异常')
check(install_txn.backup_file('config', os.path.join(TMP, 'nope.json')) == '',
      '备份不存在的文件返回空串')

if FAILS:
    print('\nFAILED %d 项：%r' % (len(FAILS), FAILS))
    sys.exit(1)
print('\nALL PASS')
