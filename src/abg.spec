# -*- mode: python ; coding: utf-8 -*-
"""单 exe 打包：砍掉无用标准库 + 裁剪 Tcl/Tk 资源 + UPX。"""
import os
from PyInstaller.building.datastruct import TOC

HERE = os.path.abspath(SPECPATH)
block_cipher = None

# 注意：urllib / http / email 属于 PyInstaller 的 base_library.zip 基础模块，
# 排除会让 Analysis 直接抛 ImportError，不要放进这里。
# v2.3.0 复核补丁 R3：bz2 / lzma 以前在这里和末尾各写了一次（33 项 vs
# installer/setup_abg.spec 的 31 项，集合相同、纯噪声）。去重后两份项数一致；
# test_qa_build_setup_guard 的 [5c] 会逐块比对两份 spec 的裁剪规则，
# 再只改一份就会直接红。
EXCLUDES = [
    'unittest', 'pydoc', 'doctest', 'test', 'xml', 'xmlrpc',
    'sqlite3', 'multiprocessing', 'asyncio', 'concurrent',
    'selectors', 'socketserver', 'lib2to3', 'distutils', 'setuptools',
    'pkg_resources', 'idlelib', 'turtledemo', 'ensurepip', 'venv', 'curses',
    # tkinter 子模块一律不进排除清单：PyInstaller 静态分析漏掉的惰性导入
    # （filedialog / simpledialog / colorchooser / scrolledtext）一旦被砍，
    # 冻结后就是运行时 ModuleNotFoundError，几百 KB 换 GUI 崩溃不划算。
    # 只排除纯测试/废弃的 test 与 tix。
    'tkinter.test', 'tkinter.tix',
    # 源码全是本地文件/进程/窗口操作，不联网、不解压、不用 Unicode 数据库。
    # 这几个会把 libcrypto-1_1.dll(1.1MB)+libssl+_ssl+_hashlib+unicodedata
    # 一起拖进来；排除后已实测 --self-test / --self-check / --layout-check 全过。
    'ssl', '_ssl', '_hashlib', 'unicodedata', 'lzma', '_lzma', 'bz2', '_bz2',
]

# Tcl 启动必需的编码表，其余 .enc / demos / 多语言消息 / 时区数据都删掉。
# v2.1.1 再收窄到四张：Tcl 只用 utf-8 + ASCII + cp936(GBK) + latin-1，
# 其余 8 张（GB2312 / 西欧 / 符号 / 丁贝兹…）实测删掉后 --layout-check
# 六档 DPI 全过。若以后要显示其它语言，往这里加回对应 .enc 即可。
KEEP_ENC = set([
    'utf-8.enc', 'ascii.enc', 'iso8859-1.enc', 'cp936.enc',
])

# tcl8/*.tm 是 Tcl 的 module path（http / tcltest / msgcat / platform），
# 这个程序一个都不 package require；tcl/http1.0 同理。tk 侧这些是 Unix
# 控制台、safe 解释器、PostScript 打印、字体选择器、过时别名与许可证文本。
# 注意：tk/ttk/* 主题文件**不能**删 —— ttk.tcl 的 ttk::LoadThemes 会照
# pkgIndex.tcl 逐个 source，删任一个都会让 Tk 起不来（2026-11-12 实测）。
DROP_DIR_PREFIX = ('tcl8/', 'tcl/http1.0/')
# v2.3.0 增补六个 tk/*.tcl —— 判据只有一条：该文件在 tk/tclIndex 里只作为
# 按需自动加载的条目出现，**没有任何 .tcl 在启动或建窗时 source 它**，而它提供
# 的 Tcl 命令本程序从不调用（面板只用 Canvas / Toplevel / messagebox /
# filedialog.askopenfilename 四种，见 settings_gui.py）。
#   tk/xmfbox.tcl   26,075 B  Mac 专用 ::tk::mac::* 文件对话框，Windows 永不可达
#   tk/iconlist.tcl 15,978 B  8.4 时代的 IconList 废弃控件，8.6 的 tkfbox 不再引用
#   tk/clrpick.tcl  21,432 B  取色对话框，全工程无 colorchooser 调用
#   tk/palette.tcl   8,174 B  仅被 clrpick 使用，与 clrpick 同生共死
#   tk/choosedir.tcl 9,652 B  askdirectory（选文件夹），全工程只用 askopenfilename
#   tk/optMenu.tcl   1,586 B  tk_optionMenu，面板的齿轮菜单是自绘 Toplevel
# 合计 82,897 B。删掉的只是"命令被调用时才会 source"的文件：万一以后有人调
# tk_chooseColor / tk_optionMenu 之类，症状是 TclError: invalid command name，
# 而不是启动即崩，排查成本可控；反过来留下来的文件一个都没动。
# 注意：tk/ttk/* 仍然一个都不能删；tk/tclIndex 本身必须留（auto_index 靠它）。
DROP_FILES = frozenset((
    'tcl/safe.tcl',
    'tk/console.tcl', 'tk/safetk.tcl', 'tk/unsupported.tcl',
    'tk/obsolete.tcl', 'tk/license.terms', 'tk/mkpsenc.tcl',
    'tk/fontchooser.tcl',
    # v2.3.0 增补（与 installer/setup_abg.spec 必须逐字一致）
    # 注意 trim_tk_data 会把 dest 整串 lower() 之后再比，清单里必须写小写：
    # 写成 'tk/optMenu.tcl' 是永远匹配不上的死条目（实测踩过）。
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
    print('[spec] tcl/tk data kept %d/%d; dropped %r' % (len(out), len(datas), dropped))
    return TOC(out)


a = Analysis(
    [os.path.join(HERE, 'abg_main.py')],
    pathex=[HERE],
    binaries=[],
    datas=[],
    # tkinter 子模块必须显式列出：modulegraph 对 `from tkinter import X` 这种
    # 惰性/函数内导入并不可靠，实测（2026-11-10 PYZ 审计）漏掉 filedialog 时
    # 包里只有 6 个 tkinter 模块。filedialog 顶层又 import 了 simpledialog，
    # 少一个就是冻结后 ModuleNotFoundError。几百 KB 换 GUI 稳定，值得。
    #
    # 插件内核（v2.0.0）：guard_core 已不再静态 import ace_native / popup_guard，
    # 插件在钩子里用 ctx.import_core('ace_native') 动态取，PyInstaller 的分析
    # 根本看不到这条路径。不写进 hiddenimports 的话，冻结后插件一调就是
    # ModuleNotFoundError，且只在真的开游戏时才炸——必须在这里钉死。
    hiddenimports=['guard_core', 'settings_gui', 'tkinter',
                   'tkinter.ttk', 'tkinter.font', 'tkinter.messagebox',
                   'tkinter.filedialog', 'tkinter.simpledialog',
                   'tkinter.colorchooser', 'tkinter.scrolledtext',
                   # 插件宿主契约 + 内核共享低层库（ctx.import_core 白名单）
                   'plugin_api', 'plugin_host', 'plugin_package', 'core_defaults',
                   'ace_native', 'popup_guard', 'game_lib',
                   # v2.6.0 卸载入口（abg_main 里函数内 import，钉死防漏）
                   'uninstall_core'],
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
    [],
    exclude_binaries=True,      # onedir：运行时不再自解压派生第二个进程
    name='WBOX',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(HERE, 'abg.ico'),
    version=None,
    manifest=os.path.join(HERE, 'app.manifest'),
)

# 单目录发布：exe + _internal/ 同放一处，进程表里只有一个 WBOX.exe
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['vcruntime140.dll'],
    name='WBOX',
)
