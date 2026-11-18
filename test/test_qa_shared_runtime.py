# -*- coding: utf-8 -*-
"""v2.1.1 分发瘦身回归：payload 省掉的运行时必须能由 Setup 补齐，且装出来能跑。

不跑真实 Setup（那会改注册表 / 快捷方式 / 计划任务），而是复刻 PyInstaller
onefile 的 _MEIPASS：从 Setup 的 CArchive 里把共享文件按条目名解到临时目录，
再调用 setup_abg.extract_payload + restore_shared_runtime，最后直接用装出来的
WBOX.exe 跑 --self-check / --layout-check。这样既覆盖真实文件路径，又零污染。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'installer'))

import setup_abg          # noqa: E402
import shared_runtime     # noqa: E402

PAYLOAD = os.path.join(ROOT, 'installer', 'payload.zip')
SETUP = os.path.join(ROOT, 'installer', 'dist', 'Setup_WBOX.exe')

PASS = [0]
FAIL = [0]


def step(ok, msg):
    if ok:
        PASS[0] += 1
        print('  ok  %s' % msg)
    else:
        FAIL[0] += 1
        print('  FAIL %s' % msg)


def md5(data):
    return hashlib.md5(data).hexdigest()


def main():
    if not (os.path.isfile(PAYLOAD) and os.path.isfile(SETUP)):
        print('SKIP 需要先跑 installer\\build_setup.py 生成 payload.zip 与 Setup_WBOX.exe')
        return 0
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError:
        print('SKIP 当前解释器没有 PyInstaller')
        return 0

    with zipfile.ZipFile(PAYLOAD) as z:
        if shared_runtime.MARKER not in z.namelist():
            print('SKIP payload 不是瘦身版（旧构建）')
            return 0
        names = json.loads(z.read(shared_runtime.MARKER).decode('utf-8'))
        payload_names = z.namelist()

    tmp = tempfile.mkdtemp(prefix='wbox-shared-')
    mei = os.path.join(tmp, '_MEIPASS')
    target = os.path.join(tmp, 'installed')
    os.makedirs(mei)
    os.makedirs(target)
    try:
        print('[1] payload 只带非共享文件')
        step('WBOX.exe' in payload_names, 'payload 里有 WBOX.exe')
        step('base_library.zip' in payload_names, 'base_library.zip 留在 payload（两侧内容不同）')
        step(not [n for n in payload_names if n in shared_runtime.SHARED_BINARIES],
             '9 个运行时二进制没有重复打包')
        step(not [n for n in payload_names if n.startswith('tcl/') or n.startswith('tk/')],
             'Tcl/Tk 数据没有重复打包')
        step(len(names) >= 50, '共享清单有 %d 项' % len(names))

        print('[2] 从 Setup 的 CArchive 复刻 _MEIPASS')
        reader = CArchiveReader(SETUP)
        toc = {}
        for key in reader.toc:
            toc[str(key).replace('\\', '/')] = key
        missing = []
        for rel in names:
            key = toc.get(rel)
            if key is None:
                missing.append(rel)
                continue
            data = reader.extract(key)
            dst = os.path.join(mei, rel.replace('/', os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, 'wb') as f:
                f.write(data)
        step(not missing, 'Setup 内嵌里能找到全部 %d 项（缺 %r）' % (len(names), missing[:5]))
        shutil.copy2(PAYLOAD, os.path.join(mei, 'payload.zip'))

        print('[3] 复刻安装：解包 + 补齐运行时')
        old_meipass = getattr(sys, '_MEIPASS', None)
        old_frozen = getattr(sys, 'frozen', None)
        sys._MEIPASS = mei
        sys.frozen = True
        try:
            n = setup_abg.extract_payload(target)
            m = setup_abg.restore_shared_runtime(target, os.path.join(tmp, 'install.log'))
        finally:
            if old_meipass is None:
                del sys._MEIPASS
            else:
                sys._MEIPASS = old_meipass
            if old_frozen is None:
                del sys.frozen
            else:
                sys.frozen = old_frozen
        step(n == len(payload_names), '解出 %d 个文件' % n)
        step(m == len(names), '补齐 %d 个共享运行时文件' % m)
        step(not os.path.exists(os.path.join(target, shared_runtime.MARKER)),
             '清单文件用完已删除')
        bad = []
        for rel in names:
            p = os.path.join(target, rel.replace('/', os.sep))
            if not os.path.isfile(p):
                bad.append(rel + '(缺)')
                continue
            with open(p, 'rb') as f:
                got = md5(f.read())
            with open(os.path.join(mei, rel.replace('/', os.sep)), 'rb') as f:
                want = md5(f.read())
            if got != want:
                bad.append(rel + '(不一致)')
        step(not bad, '落地的 %d 个文件与安装器内嵌逐字节一致（%r）'
             % (len(names), bad[:5]))

        print('[4] base_library.zip 落地后重压')
        bl = os.path.join(target, 'base_library.zip')
        with zipfile.ZipFile(bl) as z:
            infos = z.infolist()
            step(len(infos) > 100 and all(i.compress_type == zipfile.ZIP_DEFLATED
                                         for i in infos),
                 '%d 个 pyc 全为 DEFLATE' % len(infos))
            step(os.path.getsize(bl) < 600000,
                 '磁盘占用 %d B（STORED 版是 1,032,264 B）' % os.path.getsize(bl))

        print('[5] 装出来的程序真能跑')
        exe = os.path.join(target, 'WBOX.exe')
        step(os.path.isfile(exe), 'WBOX.exe 存在')
        # v2.3.0 复核补丁：别的测试（GUI 那几个）会把 ABG_BASE 指向自己的临时目录，
        # 同进程顺序跑时这个环境变量会留到这儿，让刚解出来的 exe 去读一个已经删掉的
        # base。这里清掉它，强制用「装出来的这一份」——与产品代码无关，是测试卫生。
        env = dict(os.environ)
        env.pop('ABG_BASE', None)
        rc = subprocess.call([exe, '--self-check'], cwd=target, env=env)
        step(rc == 0, '--self-check rc=%d' % rc)
        sc = os.path.join(target, 'settings_selfcheck.txt')
        if os.path.isfile(sc):
            text = open(sc, encoding='utf-8', errors='replace').read()
            step('frozen=True' in text, '自检确认 frozen 环境')
            # v2.11.3：payload 不带插件，装出来的目录里自然 0 个插件。
            # 这里只验证"注册表这条链路还在"，不假设装完就有插件。
            step(('plugins=none' in text) or ('plugins=' in text),
                 '插件注册表可用（%r）'
                 % [ln for ln in text.splitlines()
                    if ln.startswith('plugins=')][:1])
        rc = subprocess.call([exe, '--layout-check'], cwd=target, env=env)
        step(rc == 0, '--layout-check rc=%d' % rc)
        lc = os.path.join(target, 'layout_check.txt')
        if os.path.isfile(lc):
            text = open(lc, encoding='utf-8', errors='replace').read()
            step('ERROR=' not in text and text.count('overflow=0') >= 6,
                 '六档 DPI 无溢出、Tcl 初始化没报错')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\nRESULT %d passed, %d failed' % (PASS[0], FAIL[0]))
    return 1 if FAIL[0] else 0


if __name__ == '__main__':
    sys.exit(main())
