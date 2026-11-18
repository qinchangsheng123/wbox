# -*- mode: python ; coding: utf-8 -*-
"""安装器专用 spec：瘦身版 onefile。

与 app 的 abg.spec 用同一套裁剪规则：Tcl/Tk 只留程序真正会用到的编码表，
tzdata / 多语言消息 / demos / images 全删；再用 src/tools/upx.exe 压二进制。
以前直接用命令行打，导致 tzdata(606 个文件) + 全套东亚编码表跟着进包。

构建：python installer/build_setup.py （不要直接跑 PyInstaller 命令行）
"""
import os
from PyInstaller.building.datastruct import TOC

HERE = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(HERE)
block_cipher = None

# 安装器只用 os/sys/json/zipfile/subprocess/ctypes/tkinter/winreg/shutil/time。
# urllib / http / email 属于 PyInstaller 基础模块，排除会让 Analysis 抛 ImportError。
EXCLUDES = [
    'unittest', 'pydoc', 'doctest', 'test', 'xml', 'xmlrpc',
    'sqlite3', 'multiprocessing', 'asyncio', 'concurrent',
    'selectors', 'socketserver', 'lib2to3', 'distutils', 'setuptools',
    'pkg_resources', 'idlelib', 'turtledemo', 'ensurepip', 'venv',
    'curses',
    # tkinter 子模块一律不进排除清单：PyInstaller 静态分析漏掉的惰性导入
    # （filedialog / simpledialog / colorchooser / scrolledtext）一旦被砍，
    # 冻结后就是运行时 ModuleNotFoundError。只排除纯测试/废弃的 test 与 tix。
    'tkinter.test', 'tkinter.tix',
    'ssl', '_ssl', '_hashlib', 'lzma', '_lzma', 'bz2', '_bz2',
    'unicodedata',
]

# 与 abg.spec 保持完全一致：共享运行时校验要求两份 Tcl 数据逐字节相同。
# 只处理中文(GBK)+UTF-8 文本（v2.1.1 收窄到四张编码表）。
KEEP_ENC = set([
    'utf-8.enc', 'ascii.enc', 'iso8859-1.enc', 'cp936.enc',
])

DROP_DIR_PREFIX = ('tcl8/', 'tcl/http1.0/')
# v2.3.0 增补六个 tk/*.tcl，判据与 src/abg.spec 完全相同（那份注释写了逐文件
# 理由与字节数），两处清单必须逐字一致：verify_shared_runtime() 会拿 Setup
# 内嵌副本与 app\ 原文件做 md5 比对，只改一份就会判定构建失败。
DROP_FILES = frozenset((
    'tcl/safe.tcl',
    'tk/console.tcl', 'tk/safetk.tcl', 'tk/unsupported.tcl',
    'tk/obsolete.tcl', 'tk/license.terms', 'tk/mkpsenc.tcl',
    'tk/fontchooser.tcl',
    # v2.3.0 增补（与 src/abg.spec 必须逐字一致）
    # trim_tk_data 比较的是 lower() 之后的相对路径，这里必须小写。
    'tk/xmfbox.tcl', 'tk/iconlist.tcl', 'tk/clrpick.tcl',
    'tk/palette.tcl', 'tk/choosedir.tcl', 'tk/optmenu.tcl',
))


def trim_tk_data(datas):
    out, dropped = [], {}
    for entry in datas:
        dest, src, typecode = entry[:3]
        parts = dest.replace('\\', '/').lower().split('/')
        name = parts[-1]
        rel = '/'.join(parts)
        drop = None
        if 'demos' in parts:
            drop = 'demos'
        elif 'msgs' in parts and name.endswith('.msg'):
            drop = 'msgs'
        elif 'tzdata' in parts:
            drop = 'tzdata'
        elif 'images' in parts:
            drop = 'images'
        elif 'encoding' in parts and name not in KEEP_ENC:
            drop = 'encoding'
        elif rel.startswith(DROP_DIR_PREFIX):
            drop = 'unused-tcl-modules'
        elif rel in DROP_FILES:
            drop = 'unused-tcl-scripts'
        if drop:
            dropped[drop] = dropped.get(drop, 0) + 1
        else:
            out.append((dest, src, typecode))
    print('[setup spec] tcl/tk data kept %d/%d; dropped %r' %
          (len(out), len(datas), dropped))
    return TOC(out)


a = Analysis(
    [os.path.join(HERE, 'setup_abg.py')],
    # v2.6.0：卸载逻辑抽到 src/uninstall_core.py（与主 exe 共用单一来源），
    # setup_abg 顶层 import 它，src 必须进搜索路径。
    pathex=[HERE, os.path.join(ROOT, 'src')],
    binaries=[],
    # setup.ico 进包：GUI 用 root.iconbitmap(default=res('setup.ico')) 设任务栏图标
    datas=[(os.path.join(HERE, 'payload.zip'), '.'),
           (os.path.join(HERE, 'setup.ico'), '.')],
    # gui() 里是 `from tkinter import filedialog, messagebox, ttk`，藏在函数体内，
    # modulegraph 不保证抓到；而 tkinter.filedialog 顶层还 import 了
    # tkinter.simpledialog —— 这就是 2026-11-09/10 两次 GUI 崩溃的根因
    # （warn 原文：excluded module named tkinter.simpledialog - imported by
    # tkinter.filedialog (top-level)）。这里全部显式列出，宁可多带几十 KB。
    hiddenimports=['tkinter', 'tkinter.ttk', 'tkinter.font',
                   'tkinter.messagebox', 'tkinter.filedialog',
                   'tkinter.simpledialog', 'tkinter.colorchooser',
                   'tkinter.scrolledtext'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
a.datas = trim_tk_data(a.datas)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Setup_WBOX',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(HERE, 'setup.ico'),
    uac_admin=True,
)
