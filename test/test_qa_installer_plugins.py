# -*- coding: utf-8 -*-
"""QA 独立验证 E：安装 / 卸载时用户自装插件的保留（离线，临时目录）。

只测 installer\\setup_abg.py 的三个函数，不跑 Setup_WBOX.exe、
不写注册表、不动计划任务、不碰 %LOCALAPPDATA% 真实目录（改环境变量指到临时目录）。

  [1] 升级安装：用户插件 plugins\\myplug 暂存 + 还原后仍在，内容逐字节一致
  [2] 内置三个被新版覆盖；用户插件不被覆盖
  [3] 还原的兜底路径：解包后用户插件目录丢了 -> 从暂存区复制回来
  [4] 卸载：myplug 备份到 <LOCALAPPDATA>\\WBOX\\removed-plugins-<时间戳>\\，日志含路径
  [5] plugins\\ 不存在 / 空目录 -> 三个函数都不抛异常
  [6] 结构检查：install() 里 restore 在 extract 之后；uninstall() 里 backup 在 rmtree 之前
  [7] 延时删除命令行：_detached_cmd 真的脱离跑一遍 ping 延时 rmdir（v2.6.0 实测坑）

用法：C:\\Python38\\python.exe test\\test_qa_installer_plugins.py
"""
import hashlib
import inspect
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'installer'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import setup_abg  # noqa: E402
import uninstall_core  # noqa: E402  v2.6.0 卸载逻辑单一来源

PASSED = []
FAILED = []
TMP = tempfile.mkdtemp(prefix='abg_qa_installer_')
LOG = os.path.join(TMP, 'setup.log')


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)


def digest(root):
    """目录内容指纹：{相对路径: sha256}。"""
    out = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != '__pycache__']
        for f in fn:
            full = os.path.join(dp, f)
            rel = os.path.relpath(full, root).replace('\\', '/')
            with open(full, 'rb') as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


# v2.11.3：本体不再带插件，安装目录里的每个插件目录都属于用户。这里仍然造三个
# "老版本就装着的"官方插件目录，用来证明升级/卸载会像对待用户插件一样对待它们。
LEGACY_PLUGIN_IDS = ('afterburner', 'ace', 'popup')


def make_install_dir(marker='OLD'):
    target = os.path.join(TMP, 'target')
    shutil.rmtree(target, ignore_errors=True)
    os.makedirs(os.path.join(target, 'plugins'))
    for pid in LEGACY_PLUGIN_IDS:
        write(os.path.join(target, 'plugins', pid, 'plugin.json'),
              '{"id":"%s","version":"1.0.0"}' % pid)
        write(os.path.join(target, 'plugins', pid, 'plugin.py'),
              '# %s %s\n' % (marker, pid))
    # 用户自装插件（目录 + 自己的 data 状态文件）
    write(os.path.join(target, 'plugins', 'myplug', 'plugin.json'),
          '{"id":"myplug","name":"我的插件","version":"0.1.0","api":1}')
    write(os.path.join(target, 'plugins', 'myplug', 'plugin.py'),
          '# my plug\nVALUE = "keep-me"\n')
    write(os.path.join(target, 'plugins', 'myplug', 'data', 'state.json'),
          '{"counter": 42}')
    # 散落在 plugins\ 根上的零散文件（不属于任何插件）
    write(os.path.join(target, 'plugins', 'readme.txt'), 'loose file\n')
    return target


def fake_payload():
    """v2.11.3 的 payload：只有程序文件，**不含任何插件**。"""
    zp = os.path.join(TMP, 'payload.zip')
    with zipfile.ZipFile(zp, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('WBOX.exe', 'fake exe')
        z.writestr('docs/PLUGIN-SPEC.md', '# spec')
    return zp


def main():
    try:
        # ---------------- [1] 升级：暂存 + 还原 ----------------
        print('[1] 升级安装：用户插件暂存 + 还原')
        target = make_install_dir('OLD')
        before_user = digest(os.path.join(target, 'plugins', 'myplug'))

        stash = setup_abg.stash_user_plugins(target, LOG)
        step(stash is not None, 'stash_user_plugins 返回了暂存区（%r）'
             % (stash[0] if stash else None,))
        expect = sorted(LEGACY_PLUGIN_IDS + ('myplug',))
        step(stash and stash[1] == expect,
             '所有插件目录都暂存（本体不再有"内置"概念）：%r'
             % (stash[1] if stash else None,))
        step('readme.txt' not in (stash[1] if stash else []),
             'plugins\\ 根上的零散文件不算插件')

        # 模拟 extract_payload：整目录解包覆盖（不删除多余目录）
        zipfile.ZipFile(fake_payload()).extractall(target)
        after_unpack_user = digest(os.path.join(target, 'plugins', 'myplug'))
        step(after_unpack_user == before_user,
             '解包后用户插件内容原封不动（%d 个文件哈希一致）' % len(before_user))
        with open(os.path.join(target, 'plugins', 'ace', 'plugin.py'),
                  encoding='utf-8') as f:
            ace_txt = f.read()
        step('OLD ace' in ace_txt,
             'payload 不含插件，解包不会动已装插件：%r' % ace_txt.strip())

        setup_abg.restore_user_plugins(stash, target, LOG)
        after_restore = digest(os.path.join(target, 'plugins', 'myplug'))
        step(after_restore == before_user,
             '还原后 myplug 仍在且内容逐字节一致（含 data\\state.json）')
        step(os.path.isdir(os.path.join(target, 'plugins', 'myplug')),
             'myplug 目录存在，未被安装流程删掉')
        with open(LOG, encoding='utf-8') as f:
            log_txt = f.read()
        step('已暂存用户自装插件 4 个' in log_txt
             and '用户自装插件保留 4/4 个' in log_txt,
             '安装日志里有"已暂存 4 个 / 保留 4 个"记录')

        # ---------------- [2] 用户替换了内置插件（同 id） ----------------
        print('[2] 用户改过的插件：升级后原样保留（v2.11.3 起本体不再覆盖插件）')
        target2 = make_install_dir('USER-EDITED')
        stash2 = setup_abg.stash_user_plugins(target2, LOG)
        zipfile.ZipFile(fake_payload()).extractall(target2)
        setup_abg.restore_user_plugins(stash2, target2, LOG)
        with open(os.path.join(target2, 'plugins', 'popup', 'plugin.py'),
                  encoding='utf-8') as f:
            step('USER-EDITED' in f.read(),
                 '用户改过的 popup 升级后仍是原样（风险 5 随插件分仓一起消失）')

        # ---------------- [3] 还原兜底：解包后目录丢了 ----------------
        print('[3] 万一解包把用户插件目录删了：restore 从暂存区复制回来')
        target3 = make_install_dir('OLD')
        stash3 = setup_abg.stash_user_plugins(target3, LOG)
        shutil.rmtree(os.path.join(target3, 'plugins', 'myplug'))
        setup_abg.restore_user_plugins(stash3, target3, LOG)
        step(digest(os.path.join(target3, 'plugins', 'myplug')) == before_user,
             'restore 把丢失的 myplug 从暂存区完整复制回来')

        # ---------------- [4] 卸载备份 ----------------
        print('[4] 卸载：用户插件备份到 LOCALAPPDATA\\WBOX\\removed-plugins-<时间戳>')
        lad = os.path.join(TMP, 'localappdata')
        os.makedirs(lad, exist_ok=True)
        old_env = os.environ.get('LOCALAPPDATA')
        os.environ['LOCALAPPDATA'] = lad
        try:
            target4 = make_install_dir('OLD')
            backup = setup_abg.backup_user_plugins(target4, LOG)
            step(bool(backup), 'backup_user_plugins 返回备份目录：%r' % (backup,))
            step(bool(re.search(r'[\\/]WBOX[\\/]removed-plugins-\d{8}-\d{6}$',
                                backup.replace('/', '\\'))),
                 '备份目录名形如 removed-plugins-YYYYmmdd-HHMMSS：%r'
                 % os.path.basename(backup))
            step(os.path.normcase(backup).startswith(os.path.normcase(lad)),
                 '备份落在 <LOCALAPPDATA>\\WBOX 下')
            step(digest(os.path.join(backup, 'myplug')) == before_user,
                 '备份内容与用户插件逐字节一致')
            step(os.path.isdir(os.path.join(backup, 'afterburner')),
                 '升级前随包自带的插件也一并备份（现在都算用户的）')
            step(sorted(os.listdir(backup))
                 == ['ace', 'afterburner', 'myplug', 'popup'],
                 '备份目录内容 = 卸载前的插件目录：%r'
                 % (sorted(os.listdir(backup)),))
            with open(LOG, encoding='utf-8') as f:
                log_txt = f.read()
            step('已备份 4 个用户自装插件到：' in log_txt and backup in log_txt,
                 '卸载日志里有备份路径')
        finally:
            if old_env is None:
                os.environ.pop('LOCALAPPDATA', None)
            else:
                os.environ['LOCALAPPDATA'] = old_env

        # ---------------- [5] plugins 目录不存在 / 空 ----------------
        print('[5] plugins\\ 不存在或为空：三个函数都不抛异常')
        target5 = os.path.join(TMP, 'no_plugins')
        os.makedirs(target5, exist_ok=True)
        try:
            s5 = setup_abg.stash_user_plugins(target5, LOG)
            setup_abg.restore_user_plugins(s5, target5, LOG)
            b5 = setup_abg.backup_user_plugins(target5, LOG)
            setup_abg.restore_user_plugins(None, target5, LOG)
            ok5 = (s5 is None and b5 == '')
            step(ok5, 'plugins\\ 不存在：stash=None、backup=""，restore(None) 不抛异常')
        except BaseException as ex:
            step(False, 'plugins\\ 不存在时抛异常了：%r' % (ex,))

        os.makedirs(os.path.join(target5, 'plugins'), exist_ok=True)
        try:
            s6 = setup_abg.stash_user_plugins(target5, LOG)
            b6 = setup_abg.backup_user_plugins(target5, LOG)
            step(s6 is None and b6 == '', 'plugins\\ 为空目录：stash=None、backup=""')
        except BaseException as ex:
            step(False, '空 plugins\\ 抛异常：%r' % (ex,))

        # ---------------- [6] 结构检查（调用顺序） ----------------
        print('[6] 结构检查：备份/还原的调用时机')
        src_install = inspect.getsource(setup_abg.install)
        src_uninstall = inspect.getsource(uninstall_core.uninstall)
        step(src_install.index('stash_user_plugins') < src_install.index('extract_payload')
             < src_install.index('restore_user_plugins'),
             'install()：stash -> extract -> restore 顺序正确')
        step(src_uninstall.index('backup_user_plugins') < src_uninstall.index('rmtree')
             or src_uninstall.index('backup_user_plugins')
             < src_uninstall.index('rmdir /s /q'),
             'uninstall()：备份在删除目录之前（否则来不及）')

        # ---------------- [7] 延时删除命令行（v2.6.0 实测坑） ----------------
        # Popen 列表形式被 list2cmdline 转义内嵌引号 -> cmd 静默失败；
        # cmd /c 多引号剥首尾 -> 语法破碎；timeout 无控制台立即退出 -> 延时归零。
        # 这里不只查源码，真的脱离跑一遍延时 rmdir，目录必须消失。
        print('[7] _detached_cmd：ping 延时 + /s /c 外引号，目录真的被删掉')
        step('timeout.exe' not in src_uninstall,
             'uninstall() 不用 timeout 延时（无控制台进程里立即退出）')
        step('/s /c' in inspect.getsource(uninstall_core._detached_cmd),
             '_detached_cmd 用 /s /c + 外层引号（列表形式会被 list2cmdline 破坏）')
        victim = os.path.join(TMP, 'delayed_rmdir')
        os.makedirs(os.path.join(victim, 'sub'))
        write(os.path.join(victim, 'sub', 'f.txt'), 'x')
        script = '"%s" -n 2 127.0.0.1 >nul & rmdir /s /q "%s"' % (
            os.path.join(uninstall_core.SYS32, 'ping.exe'), victim)
        uninstall_core._detached_cmd(script)
        deadline = time.time() + 15
        while os.path.isdir(victim) and time.time() < deadline:
            time.sleep(0.5)
        step(not os.path.isdir(victim),
             '脱离 cmd 延时后目录整体删除：%s' % victim)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print('\nPASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
    if FAILED:
        for m in FAILED:
            print('  - ' + m)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
