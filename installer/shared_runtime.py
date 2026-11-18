# -*- coding: utf-8 -*-
"""v2.1.1 分发瘦身：payload.zip 不再重复携带安装器自己已经内嵌的运行时。

Setup_WBOX.exe 是 PyInstaller onefile，运行时会把自己的
python38.dll / tcl86t.dll / tk86t.dll / *.pyd / tcl\\ tk\\ 数据解压到
sys._MEIPASS。这些文件与 app\\ 里的对应文件是同一份 PyInstaller bincache
产物，实测（2026-11-12）字节完全一致，于是 payload 里可以省掉这 3 MB，
安装时从 _MEIPASS 复制过来即可。

唯一不能共享的是 base_library.zip：安装器与主程序 import 的模块集合不同，
PyInstaller 为两者生成的内容不一样，实测 md5 不同，必须留在 payload 里。

清单不写死在两处：build_setup.py 按实际跳过的条目生成 MARKER（JSON），
setup_abg.py 照单复制；构建结束再用 CArchiveReader 逐项比对 Setup 内嵌
副本与 app 原文件的 md5，不一致直接判定构建失败，绝不交付坏包。
"""
import io
import os
import zipfile

# 二进制清单：顺序只影响日志，不影响正确性
SHARED_BINARIES = (
    'python38.dll', 'tcl86t.dll', 'tk86t.dll', 'VCRUNTIME140.dll',
    'libffi-7.dll', '_ctypes.pyd', '_socket.pyd', 'select.pyd', '_tkinter.pyd',
)
SHARED_DIRS = ('tcl', 'tk', 'tcl8')
MARKER = '.wbox-shared-runtime'
LEGACY_MARKER = '.wingman-shared-runtime'   # v2.11.0 改名前的清单名，升级时清残留
BASE_LIBRARY = 'base_library.zip'


def is_shared(rel):
    """payload 里的相对路径是否属于「安装器自带、无需再带一份」的文件。"""
    rel = rel.replace('\\', '/')
    if rel in SHARED_BINARIES:
        return True
    return any(rel.startswith(d + '/') for d in SHARED_DIRS)


def _rewrite(src_path, compress_type):
    """把 zip 重写成指定压缩方式，返回字节。

    注意必须先 read 再改 ZipInfo.compress_type：ZipFile 按文件名查到的
    是同一个对象，先改会让 read() 拿旧数据去做新算法的解压，直接
    zlib.error: invalid code lengths set。
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(src_path) as zi, \
            zipfile.ZipFile(buf, 'w', compress_type,
                            compresslevel=9 if compress_type == zipfile.ZIP_DEFLATED else None) as zo:
        for info in zi.infolist():
            data = zi.read(info.filename)
            info.compress_type = compress_type
            zo.writestr(info, data)
    return buf.getvalue()


def stored_base_library(path):
    """分发用：把 base_library.zip 还原成 STORED，让外层 payload 的 deflate
    和 Setup 的 CArchive zlib 能压到最小（1.03 MB -> 307 KB）。

    若分发的是 DEFLATE 版，已压缩数据再压几乎压不动，包里反而变成 468 KB。
    """
    return _rewrite(path, zipfile.ZIP_STORED)


def compact_base_library(path):
    """落地用：把 STORED 版重压成 DEFLATE，安装后磁盘占用 1.03 MB -> 468 KB。

    分发与磁盘的最优策略相反（见 stored_base_library），所以两个方向都要有。
    zipimporter 支持 deflate，--self-check / --layout-check 已实测通过。
    """
    if not os.path.isfile(path):
        return False
    with zipfile.ZipFile(path) as zi:
        if zi.infolist() and all(i.compress_type == zipfile.ZIP_DEFLATED
                                 for i in zi.infolist()):
            return False
    data = _rewrite(path, zipfile.ZIP_DEFLATED)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)
    return True


def outdir_busy(outdir):
    """构建前的占用探测：<outdir>\\WBOX.exe 是否正被运行中的守护映射着。

    v2.3.0 复核补丁（QA P1-3）：build_exe.bat 的 OUTDIR 默认就是 <repo>\\app，而生产守护
    正是从那里启动的。被映射的 exe 删不掉、也覆盖不了，但同一目录下的
    *.dll / *.pyd / tcl\\ / tk\\ 换得掉 —— 于是一次"顺手重建"会静默产出
    半个新版本 + 半个旧版本，脚本还照样打印 BUILD OK。这比构建失败危险得多。

    判定方式是独占打开（`r+b`，一个字节都不写）：运行中的 exe 由加载器以
    允许读/删、不允许写的方式映射，第二个写句柄会拿到共享冲突。
    不用 `tasklist` 或 `guard.pid` 做主判据：pid 文件可能是上一次崩溃留下的，
    而"exe 能不能被替换"才是真正要回答的问题（pid 只用于打印线索）。

    返回 0 = 可以安全覆盖；1 = 被占用，且已打印可执行的两条处置办法。
    """
    exe = os.path.join(outdir, 'WBOX.exe')
    if not os.path.isfile(exe):
        # v2.11.0 改名：旧 OUTDIR 里可能还躺着被锁的 Wingman.exe，同样不能覆盖
        exe = os.path.join(outdir, 'Wingman.exe')
    if not os.path.isfile(exe):
        return 0
    try:
        with open(exe, 'r+b'):
            pass
    except OSError as e:
        print('BUILD ABORT: 目标 exe 正被占用，拒绝覆盖 -> %s' % exe)
        print('  原因：%s' % (e,))
        pid = _read_pid(os.path.join(outdir, 'guard.pid'))
        if pid:
            print('  guard.pid 记录的守护进程号：%s（可用 tasklist /FI "PID eq %s" 确认）'
                  % (pid, pid))
        print('  守护正在运行，先停任务或改用其他输出目录：')
        print('    powershell -NoProfile -Command "Stop-ScheduledTask -TaskName WBOX"')
        print('    set OUTDIR=D:\\06-Temp\\wbox-build\\app && build_exe.bat')
        print('  为什么必须硬失败：exe 换不掉、dll/tcl 却能换，硬着头皮构建只会得到')
        print('  半新半旧的混合安装，而构建脚本不会报任何错。')
        return 1
    return 0


def _read_pid(path):
    """读 guard.pid 的第一行（内容不可信，只用来打印线索，不影响判定）。"""
    try:
        with io.open(path, 'r', encoding='utf-8', errors='replace') as f:
            return (f.readline() or '').strip()[:16] or ''
    except OSError:
        return ''
