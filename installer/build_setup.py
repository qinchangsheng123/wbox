# -*- coding: utf-8 -*-
"""生成安装程序：把 app\\ 打成 payload.zip，再用 PyInstaller 封成单文件 Setup。

v2.1.1 起 payload.zip **不是自包含的**：安装器（PyInstaller onefile）运行时
自带的 python38.dll / *.pyd / tcl\\ tk\\ 数据与 app\\ 完全同字节，这些不再重复
塞进 payload，改由 setup_abg.restore_shared_runtime() 从 _MEIPASS 补齐。
所以 payload.zip 必须由 Setup_WBOX.exe 使用，不能单独解包当程序跑。

默认就地生成 installer\\payload.zip 与 installer\\dist\\Setup_WBOX.exe。
三项都可以用环境变量改指到临时目录，方便冒烟验证不动正式产物：
  ABG_APP      -> 取哪个 app 目录当 payload（默认 <repo>\\app）
  ABG_PAYLOAD  -> payload.zip 写到哪儿（默认 installer\\payload.zip）
  ABG_OUT      -> setup exe / 中间目录的根（默认 installer\\，即 dist 与 _build）
  --payload-only -> 只重打 payload.zip，不封 setup exe

注意：setup_abg.spec 的 datas 里写死了 installer\\payload.zip（spec 不在可改范围内），
所以 ABG_PAYLOAD 只对 --payload-only 有效；真要封 setup 时仍读 installer\\payload.zip。

硬约束（v2.0.0 交付事故的补丁）：ABG_APP 里必须有 WBOX.exe 与 plugins\\，且
plugins\\*\\plugin.json 至少 1 个；缺任一项直接 exit(1) 并删掉半成品 payload。
宁可打包失败，也不要交付一个装完三个功能全丢的安装包。
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

import shared_runtime
import setup_abg          # 只为了取 VERSION，让产物尾缀与面板/注册表同源
import factory_config     # payload config.json 的出厂模板单一来源

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.abspath(os.environ.get('ABG_APP') or os.path.join(ROOT, 'app'))
INST = os.path.join(ROOT, 'installer')
PAYLOAD = os.path.abspath(os.environ.get('ABG_PAYLOAD')
                          or os.path.join(INST, 'payload.zip'))
OUT = os.path.abspath(os.environ.get('ABG_OUT') or INST)
DIST = os.path.join(OUT, 'dist')
BUILD = os.path.join(OUT, '_build')
PY = sys.executable        # 用当前解释器，别把打包机路径写死

SKIP_NAMES = {'guard.log', 'guard.log.1', 'guard.pid', 'stop.flag',
              'self_test.txt', 'self_test_run.log', 'settings_selfcheck.txt',
              'layout_check.txt', 'self_test_run.log.1', 'guard.log.2',
              'rescan_games.flag', 'ace_state.json', 'games_cache.json',
              # 面板保存时生成的上一版配置备份，含用户旧参数，不该分发给别的机器。
              'config.json.bak', 'config.json.bak.1',
              # 运行期状态，属于"这一台机器上的这个守护"：装到新机器必须从空开始，
              # 带上旧状态会让面板显示上一台机器的异常次数 / pid。
              'plugins_state.json',
              # 面板装/卸插件写的热重载标记与写权限探针：都属于本机运行期状态
              'plugins_reload.flag', '.wbox-write-test',
              # v2.11.0 改名前的探针/清单/卸载副本残留：旧 OUTDIR 升级构建时
              # 不许把它们塞进 payload（新名字在上面，旧名字只作清理口径）。
              '.wingman-write-test', '.wingman-shared-runtime',
              'Wingman.exe',
              # 卸载器是安装时从"当前 setup 自身"复制过去的，不是 payload 的资产。
              # 不排除它会把上一版安装器（9 MB）整个塞进 payload，安装包白白翻倍。
              'Uninstall_Wingman.exe'}
# 注意：plugins 不能进 SKIP_DIRS/ SKIP_NAMES —— 三个内置插件必须随 payload 一起装，
# 它们靠"目录存在"被发现（丢目录加功能），塞进 _internal\ 反而找不到。
SKIP_DIRS = {'__pycache__'}

# 排除模块清单、Tcl/Tk 数据裁剪、UPX 参数统一放在 installer/setup_abg.spec，
# 与 app 的 abg.spec 用同一套规则，避免命令行打包漏掉 tzdata / 编码表裁剪。

# ---- 可复现构建（v2.3.0 终版） ---------------------------------------------
# 这两个值在 build_exe.bat 里也写了一遍（主程序那一步要），这里再写一遍并
# **显式传进 PyInstaller 子进程**：别人从 IDE / 计划任务 / CI 直接调本脚本时，
# 继承不到 bat 里 set 的变量，产物会悄无声息地变回不可复现。
# 1000000000 = 2001-09-09T01:46:40Z。改它等于让所有历史体积/md5 对不上。
SOURCE_DATE_EPOCH = 1000000000
HASH_SEED = 0
_ZIP_MIN_EPOCH = 315532800        # 1980-01-01T00:00:00Z：zip 时间戳的合法下限


def build_env():
    """PyInstaller 子进程的环境：覆盖两个可复现变量，其余原样继承。

    只覆盖不替换：PATH / PYTHONPATH / 代理等还是要从父进程继承，否则
    PyInstaller 找不到 upx 或第三方库。
    """
    env = dict(os.environ)
    inherited = (env.get('PYTHONHASHSEED'), env.get('SOURCE_DATE_EPOCH'))
    env['PYTHONHASHSEED'] = str(HASH_SEED)
    env['SOURCE_DATE_EPOCH'] = str(SOURCE_DATE_EPOCH)
    if inherited != (str(HASH_SEED), str(SOURCE_DATE_EPOCH)):
        print('注意：父进程环境是 PYTHONHASHSEED=%r SOURCE_DATE_EPOCH=%r，'
              '这里强制覆盖成 (%d, %d) 以保证可复现'
              % (inherited[0], inherited[1], HASH_SEED, SOURCE_DATE_EPOCH))
    return env


def zip_stamp():
    """payload.zip 每个条目统一用的 date_time（UTC，不含时区歧义）。

    以前用 z.write()，时间戳取自文件 mtime；换成 z.writestr() 的默认行为则是
    "当前本地时间"。两种都会把构建时刻/时区写进 payload，于是同样的 app 目录
    打两次 md5 都不一样。这里用 gmtime(SOURCE_DATE_EPOCH) 算死，并把下限卡在
    1980-01-01 —— zip 的 DOS 时间字段不接受 1970 年，SOURCE_DATE_EPOCH 写小了
    不能直接塞进去。
    """
    t = time.gmtime(max(SOURCE_DATE_EPOCH, _ZIP_MIN_EPOCH))
    return (t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec)


def zip_info(rel, compress_type=zipfile.ZIP_DEFLATED):
    """构造元数据全部写死的 ZipInfo，让两次构建的 payload 逐字节相同。

    create_system / external_attr 也要钉死：PyInstaller 之外的这层 zip 是我们
    自己写的，默认会带上"当前操作系统 + 文件权限"，在 Linux 上打包就会和
    Windows 上打包字节不同。这里统一成 MS-DOS(0) + 0644。
    """
    zi = zipfile.ZipInfo(rel, date_time=zip_stamp())
    zi.compress_type = compress_type
    zi.create_system = 0                 # 0 = MS-DOS，别写构建机的 unix 属性
    zi.external_attr = 0o644 << 16
    zi._compresslevel = 9                # writestr/open 只认这个私有字段
    return zi


def write_entry(z, rel, full=None, data=None):
    """把 rel 写进 payload.zip，时间戳/权限位/压缩级别全部固定。

    full 给的是磁盘文件（整个读进来再写：payload 里最大的单文件是 1 MB 量级的
    base_library.zip，python38.dll 之类的共享运行时根本不进来）；
    data 给的是已经算好的字节（base_library 的 STORED 版本、共享运行时清单）。
    """
    if data is None:
        with open(full, 'rb') as f:
            data = f.read()
    z.writestr(zip_info(rel), data)


def missing_app_parts():
    """打包前先确认 ABG_APP 真的是"跑过一次 build_exe.bat"的产物目录。

    v2.11.3 起主程序**不带任何插件**：插件在独立仓库（wbox-plugins）里维护，
    由用户在面板里按需安装。`plugins\\` 因此既不是必需项、也不是禁项 ——
    OUTDIR 指向现网安装目录时，它装着用户自己装的插件，绝不能因为"打包"就被
    删掉。真正保证"不带插件"的是 build_payload 的整目录排除 + check_payload
    的零条目断言。

    返回问题列表（空列表 = 可以打包）。插件目录不再要求存在；它可能装着用户
    自己装的插件（OUTDIR 就是现网安装目录时），打包阶段会把它整目录排除，
    既不会随包分发，也不会被构建脚本破坏。
    """
    bad = []
    if not os.path.isdir(APP):
        bad.append('app 目录不存在：%s' % APP)
        return bad
    exe = os.path.join(APP, 'WBOX.exe')
    if not os.path.isfile(exe):
        bad.append('缺少 %s' % exe)
    return bad


def plugin_manifests():
    """`plugins\\<id>\\plugin.json` 的相对路径列表 —— 只用来证明 **0 个**。

    payload 里出现任何一个都算失败：本体的边界就是"内核 + 面板 + 文档"。
    """
    root = os.path.join(APP, 'plugins')
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for name in names:
        if os.path.isfile(os.path.join(root, name, 'plugin.json')):
            out.append('plugins/%s/plugin.json' % name)
    return out


def build_payload():
    """v2.1.1：payload 只带安装器自己没有的东西。

    安装器是 PyInstaller onefile，_MEIPASS 里已经有全套 Python/Tcl 运行时；
    这些文件与 app\\ 里的字节一致，再往 payload 里塞一份纯属浪费（约 3 MB）。
    跳过的相对路径写进 MARKER，安装时 setup_abg.restore_shared_runtime()
    照单从 _MEIPASS 复制；构建结束后 verify_shared_runtime() 会逐项比对
    Setup 内嵌副本与 app 原文件，不一致就不许交付。
    """
    count = 0
    shared = []
    skipped_plugins = 0
    payload_dir = os.path.dirname(PAYLOAD)
    if payload_dir and not os.path.isdir(payload_dir):
        os.makedirs(payload_dir, exist_ok=True)
    if os.path.exists(PAYLOAD):
        os.remove(PAYLOAD)
    with zipfile.ZipFile(PAYLOAD, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, dns, fns in os.walk(APP):
            # 插件安装失败时可能留下 .plugin-staging-* 暂存目录，绝不能分发。
            dns[:] = [d for d in dns if d not in SKIP_DIRS
                      and not d.startswith('.plugin-staging-')]
            for fn in fns:
                if fn in SKIP_NAMES or fn.startswith('guard.log'):
                    continue
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, APP).replace('\\', '/')
                if rel.startswith('plugins/'):
                    # v2.11.3：本体不带插件（插件在 wbox-plugins 仓库单独分发）。
                    # 排除而不是让构建失败：OUTDIR 可能就是现网安装目录，里面的
                    # plugins\ 是用户自己装的，既不能随包走，也不能被删。
                    skipped_plugins += 1
                    continue
                if shared_runtime.is_shared(rel):
                    shared.append(rel)
                    continue
                if rel == 'config.json':
                    # 源目录的配置可能含本机数据（ABG_APP 默认就是生产 app\）：
                    # payload 模板一律用出厂生成件，与 build_exe.bat 同源。
                    write_entry(z, rel, data=factory_config.factory_bytes())
                elif rel == shared_runtime.BASE_LIBRARY:
                    # 分发侧最优是 STORED：外层 deflate 能压到 307 KB；
                    # 装到磁盘后 setup_abg 会重压成 DEFLATE（468 KB）。
                    write_entry(z, rel, data=shared_runtime.stored_base_library(full))
                else:
                    write_entry(z, rel, full=full)
                count += 1
        write_entry(z, shared_runtime.MARKER,
                    data=json.dumps(sorted(shared), ensure_ascii=False)
                    .encode('utf-8'))
        count += 1
    size = os.path.getsize(PAYLOAD) / 1048576.0
    print('payload.zip: %d 个文件（已排除插件 %d 个 / 共享运行时省去 %d 个）, '
          '%.2f MB -> %s' % (count, skipped_plugins, len(shared), size, PAYLOAD))
    print('payload.zip 条目时间戳统一为 %s UTC（条目数 %d）'
          % ('-'.join('%02d' % x for x in zip_stamp()[:3]), count))
    return count


def check_payload():
    """payload 必须**不含**插件，否则立刻失败，并把半成品 payload 删掉。

    删掉的理由和 v2.0.0 那次相反但同样重要：不合格的 payload 留着，下一步封
    setup 时它会照常被打进安装包，于是交付一个"本体夹带插件"的安装器 —— 这
    正是 v2.11.3 要划清的边界。返回实际插件条目数（正常恒为 0）。
    """
    # 只看 payload 本身：源目录里装着用户插件是正常情况（OUTDIR 可能就是现网
    # 目录），只有"插件条目混进了包"才是错误。
    with zipfile.ZipFile(PAYLOAD) as z:
        names = z.namelist()
    leaked = [n for n in names if n.startswith('plugins/')]
    if leaked:
        print('错误：payload 里出现插件条目，本体不允许附带插件')
        for item in leaked[:10]:
            print('   ', item)
        print('      ABG_APP = %s' % APP)
        print('      这是打包逻辑的 bug：build_payload 应当排除整个 plugins\\ 目录，'
              '不该靠删用户插件来绕过')
        try:
            os.remove(PAYLOAD)
            print('      已删除半成品 payload：%s' % PAYLOAD)
        except OSError:
            pass
        sys.exit(1)
    print('payload 插件：0 个（本体不带插件，符合 v2.11.3 约定）')
    try:
        with zipfile.ZipFile(PAYLOAD) as z:
            names = json.loads(z.read(shared_runtime.MARKER).decode('utf-8'))
    except Exception as e:
        print('错误：payload 缺少共享运行时清单 %s：%r' %
              (shared_runtime.MARKER, e))
        sys.exit(1)
    if not names:
        print('错误：共享运行时清单为空，装出来的程序会缺 python38.dll / Tcl 数据')
        sys.exit(1)
    print('payload 共享运行时清单：%d 项' % len(names))
    return 0


def verify_shared_runtime(setup_exe):
    """交付前硬校验：Setup 里内嵌的每一份，必须与 payload 跳过的那份字节一致。

    两份东西来自同一次 bincache 与同一套 UPX/Tcl 裁剪。哪天升级 Python /
    PyInstaller 或只改了一份 spec 导致不一致，装出来的主程序会缺 dll 或
    Tcl 脚本 —— 这种错在构建机上不报，到用户机器才炸，所以这里直接拦下。
    """
    from PyInstaller.archive.readers import CArchiveReader
    with zipfile.ZipFile(PAYLOAD) as z:
        names = json.loads(z.read(shared_runtime.MARKER).decode('utf-8'))
    r = CArchiveReader(setup_exe)
    toc = {}
    for key in r.toc:
        toc[str(key).replace('\\', '/')] = key
    bad = []
    for rel in names:
        key = toc.get(rel)
        if key is None:
            bad.append('%s（安装器内嵌里没有）' % rel)
            continue
        try:
            embedded = r.extract(key)
        except Exception as e:
            bad.append('%s（读取失败 %r）' % (rel, e))
            continue
        path = os.path.join(APP, rel.replace('/', os.sep))
        try:
            with open(path, 'rb') as f:
                original = f.read()
        except OSError as e:
            bad.append('%s（app 原文件读不到 %r）' % (rel, e))
            continue
        if embedded is None or \
                hashlib.md5(embedded).digest() != hashlib.md5(original).digest():
            bad.append('%s（字节不一致）' % rel)
    if bad:
        print('错误：安装器内嵌运行时与 payload 排除项不一致，禁止交付：')
        for item in bad[:20]:
            print('   ', item)
        try:
            os.remove(setup_exe)
            print('已删除不合格产物：%s' % setup_exe)
        except OSError:
            pass
        sys.exit(1)
    print('共享运行时校验通过：%d 项与 app 字节一致' % len(names))


def build_setup():
    # 主程序打包一直用这份 UPX，安装器以前漏了，白带 11 MB 未压缩运行时
    cmd = [PY, '-m', 'PyInstaller', '--noconfirm', '--clean',
           '--distpath', DIST,
           '--workpath', BUILD,
           '--upx-dir', os.path.join(ROOT, 'src', 'tools'),
           os.path.join(INST, 'setup_abg.spec')]
    print(' '.join(cmd))
    rc = subprocess.call(cmd, env=build_env())
    if rc != 0:
        print('PyInstaller 失败 rc=%d' % rc)
        return rc
    out = os.path.join(DIST, 'Setup_WBOX.exe')
    print('OK ->', out, '%.1f MB' % (os.path.getsize(out) / 1048576.0))
    # v2.3.0：产物尾缀带版本号（vMAJOR.MINOR.PATCH，与面板/注册表同源）。
    # 保留不带版本号的那一份：QA 测试都按 Setup_WBOX.exe 这个名字找文件。
    versioned = os.path.join(DIST, 'Setup_WBOX_v%s.exe' % setup_abg.VERSION)
    try:
        shutil.copy2(out, versioned)
        print('版本化产物 ->', versioned,
              '%.1f MB' % (os.path.getsize(versioned) / 1048576.0))
    except OSError as e:
        print('复制版本化产物失败：%r（不影响 Setup_WBOX.exe）' % (e,))
    return 0


if __name__ == '__main__':
    print('可复现构建：PYTHONHASHSEED=%d SOURCE_DATE_EPOCH=%d（zip 时间戳 UTC %s）'
          % (HASH_SEED, SOURCE_DATE_EPOCH, zip_stamp()))
    bad = missing_app_parts()
    if bad:
        print('ABG_APP 不是可用的构建目录：%s' % APP)
        for item in bad:
            print('  缺少：', item)
        print('  先跑 build_exe.bat（默认 OUTDIR=<repo>\\app），再跑本脚本；')
        print('  临时目录构建用：set ABG_APP=D:\\...\\app  后重跑。')
        sys.exit(1)
    if not build_payload():
        print('payload 为空，先跑 build_exe.bat')
        sys.exit(1)
    check_payload()
    # --payload-only：只重打 payload.zip 不封 setup exe（冒烟验证 payload 内容用）
    if '--payload-only' in sys.argv[1:]:
        sys.exit(0)
    rc = build_setup()
    if rc:
        sys.exit(rc)
    verify_shared_runtime(os.path.join(DIST, 'Setup_WBOX.exe'))
    sys.exit(0)
