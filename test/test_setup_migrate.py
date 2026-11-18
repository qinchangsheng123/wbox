# -*- coding: utf-8 -*-
"""安装器旧名迁移回归（v2.11.0）：migrate_legacy 的三条路径，不碰生产安装。

LEGACY_DIR / LEGACY_TASK / LEGACY_KEY / LOCALAPPDATA 全部 monkeypatch 到临时
目录与不存在的任务/键名：真机的 Wingman 任务、注册表键、备份目录一个都不动。
guard_pids 按路径过滤，临时目录里没有进程，不会误停真守护。
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'installer'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import setup_abg  # noqa: E402
import uninstall_core  # noqa: E402

PASSED, FAILED = [], []


def check(cond, msg):
    (PASSED if cond else FAILED).append(msg)
    print(('  ok   ' if cond else '  FAIL ') + msg)


def make_plugin(root, pid):
    d = os.path.join(root, 'plugins', pid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, 'plugin.json'), 'w', encoding='utf-8') as f:
        json.dump({'id': pid, 'name': pid, 'version': '1.0.0', 'api': 1}, f)
    with open(os.path.join(d, 'plugin.py'), 'w', encoding='utf-8') as f:
        f.write('from plugin_api import WBOXPlugin\n'
                'class Plugin(WBOXPlugin):\n    pass\n')


TMP = tempfile.mkdtemp(prefix='wbox-migrate-')
OLD = os.path.join(TMP, 'Wingman')
NEW = os.path.join(TMP, 'WBOX')
LOCAL = os.path.join(TMP, 'local')
LOG = []


def log(msg):
    LOG.append(str(msg))


ORIG = (uninstall_core.LEGACY_DIR, uninstall_core.LEGACY_TASK,
        uninstall_core.LEGACY_KEY, os.environ.get('LOCALAPPDATA'))
try:
    uninstall_core.LEGACY_DIR = OLD
    uninstall_core.LEGACY_TASK = 'WBOXMigrateProbeTask'
    uninstall_core.LEGACY_KEY = (
        r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\WBOXMigrateProbe')
    os.environ['LOCALAPPDATA'] = LOCAL

    # ---- [1] 只有旧目录：整目录搬移 + 备份目录归并 ----
    print('\n[1] 旧目录存在、新目录不存在 → 搬移')
    os.makedirs(OLD)
    with open(os.path.join(OLD, 'Wingman.exe'), 'wb') as f:
        f.write(b'fake')
    with open(os.path.join(OLD, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump({'afterburner_path': 'D:/MSI Afterburner'}, f)
    make_plugin(OLD, 'userplug')
    oldbak = os.path.join(LOCAL, 'Wingman', 'removed-plugins-20260101-000000')
    os.makedirs(oldbak)
    with open(os.path.join(oldbak, 'keep.txt'), 'w') as f:
        f.write('x')
    stash, remove_old = setup_abg.migrate_legacy(NEW, log)
    check(stash is None and remove_old is False, '搬移路径不暂存、不标记删旧')
    check(not os.path.isdir(OLD), '旧目录已搬走')
    check(os.path.isfile(os.path.join(NEW, 'config.json')), '用户配置随目录保留')
    check(os.path.isdir(os.path.join(NEW, 'plugins', 'userplug')),
          '用户插件随目录保留')
    check(os.path.isfile(os.path.join(
        LOCAL, 'WBOX', 'removed-plugins-20260101-000000', 'keep.txt')),
          '旧备份目录归并到 %LOCALAPPDATA%\\WBOX')
    check(not os.path.isdir(os.path.join(LOCAL, 'Wingman')), '旧备份目录不留残壳')

    # ---- [2] 新旧并存：旧目录用户插件暂存并入，收尾删旧 ----
    print('\n[2] 新旧目录并存 → 暂存并入 + 收尾删旧')
    os.makedirs(OLD)
    make_plugin(OLD, 'userplug2')
    stash, remove_old = setup_abg.migrate_legacy(NEW, log)
    check(stash is not None and remove_old is True, '并存路径暂存并标记删旧')
    check(os.path.isdir(os.path.join(NEW, 'plugins', 'userplug')),
          '新目录原有用户插件不受影响')
    setup_abg.restore_user_plugins(stash, NEW, log)
    check(os.path.isdir(os.path.join(NEW, 'plugins', 'userplug2')),
          '旧目录用户插件并入新目录')
    if remove_old:
        shutil.rmtree(uninstall_core.LEGACY_DIR, ignore_errors=True)
    check(not os.path.isdir(OLD), '收尾删除旧目录')

    # ---- [3] 没有旧安装：什么都不做 ----
    print('\n[3] 无旧安装 → 空操作')
    before = sorted(os.listdir(NEW))
    stash, remove_old = setup_abg.migrate_legacy(NEW, log)
    check(stash is None and remove_old is False, '无旧安装返回空操作')
    check(sorted(os.listdir(NEW)) == before, '新目录内容不变')

    # ---- [4] 目标目录里跑着旧名守护：停进程（生产 InstallLocation 场景）----
    print('\n[4] 目标目录里的旧名守护 → 停止')
    import subprocess
    exe = os.path.join(NEW, 'Wingman.exe')
    shutil.copy(sys.executable, exe)
    p = subprocess.Popen([exe, '-c', 'import time; time.sleep(120)'])
    try:
        setup_abg.migrate_legacy(NEW, log)
        check(p.poll() is not None, '旧名守护进程已被停止（pid %d）' % p.pid)
    finally:
        if p.poll() is None:
            p.kill()
        p.wait(timeout=10)
        if os.path.isfile(exe):
            os.remove(exe)
finally:
    (uninstall_core.LEGACY_DIR, uninstall_core.LEGACY_TASK,
     uninstall_core.LEGACY_KEY, local0) = ORIG
    if local0 is None:
        os.environ.pop('LOCALAPPDATA', None)
    else:
        os.environ['LOCALAPPDATA'] = local0
    shutil.rmtree(TMP, ignore_errors=True)

print('\nPASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
for m in FAILED:
    print('  - ' + m)
sys.exit(1 if FAILED else 0)