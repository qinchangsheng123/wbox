# -*- coding: utf-8 -*-
"""WBOX 安装程序（自包含单文件，内置 payload.zip）。

用法：
  双击                                图形安装（选择目录、开机自启、立即启动）
  /S /DIR=... /AUTO=1 /LAUNCH=1        静默安装（批量部署）
  /S /uninstall [/DIR=...]             静默卸载
  /TASK=任务名                         覆盖计划任务名（测试用，默认 WBOX）
  /NOTASK                              完全不碰计划任务（调试用）
  /AUTO=0 /LAUNCH=0                    安装后不启动守护

安装做的事：停旧守护 → 解包到目标目录 → 保留旧 config 并补齐新增项、
修配置（小飞机路径、游戏库根）→ 建登录自启计划任务 → 开始菜单快捷方式 →
注册卸载项 → 刷新 Shell 图标缓存 → 启动守护。
卸载做相反的事，并删除整个安装目录（含 config.json）。
"""
import ctypes
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import shared_runtime

# v2.6.0：卸载入口并进主 exe（WBOX.exe /uninstall），卸载与共用系统动作
# 迁到 src/uninstall_core.py 单一来源；Setup 与主 exe 冻结同一份。
# 源码模式补 src 路径；冻结模式由 setup_abg.spec 的 pathex 提供。
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'src'))
import install_txn
import uninstall_core
import skins
import sprite
from uninstall_core import (APP_EXE, DEFAULT_TASK, UNINST_KEY, DEFAULT_DIR,
                            SYS32, TASKLIST, TASKKILL, SCHTASKS,
                            POWERSHELL, CMD, is_admin, ensure_admin, run, ps,
                            log_line, flag, arg_map, installed_dir, resolve_target,
                            resolve_task, guard_pids, stop_guard, delete_task,
                            user_plugin_names, backup_user_plugins,
                            write_uninstall_key, drop_uninstall_key,
                            _spawn, _delete_on_reboot, uninstall)
from core_defaults import VERSION

ICACLS = os.path.join(SYS32, 'icacls.exe')


# ---------------- 基础 ----------------
def res(name):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def refresh_shell_icons():
    """SHCNE_ASSOCCHANGED：让 Shell 丢弃按路径缓存的旧 exe 图标。

    覆盖安装时路径不变，任务管理器可能仍显示上一版图标；通知一次即可，
    不删 IconCache.db、不重启 explorer，属于无副作用的系统通知。
    """
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)
        return True
    except Exception:
        return False


def current_user():
    dom = os.environ.get('USERDOMAIN', '')
    usr = os.environ.get('USERNAME', '')
    return ('%s\\%s' % (dom, usr)) if dom else usr


def create_task(task, exe):
    """优先 schtasks；被安全策略拦掉时退回 PowerShell 的 ScheduledTasks 模块。"""
    tr = '"%s" --guard' % exe
    user = current_user()
    cmd = [SCHTASKS, '/Create', '/TN', task, '/TR', tr, '/SC', 'ONLOGON',
           '/RL', 'HIGHEST', '/F']
    if user:
        cmd += ['/RU', user]
    rc, out = run(cmd)
    if rc == 0:
        return True, 'schtasks'
    rc2, out2 = run([SCHTASKS, '/Create', '/TN', task, '/TR', tr,
                     '/SC', 'ONLOGON', '/RL', 'HIGHEST', '/F'])
    if rc2 == 0:
        return True, 'schtasks(无RU)'

    script = (
        "$ErrorActionPreference='Stop';"
        "$a=New-ScheduledTaskAction -Execute '%s' -Argument '--guard' -WorkingDirectory '%s';"
        "$t=New-ScheduledTaskTrigger -AtLogOn -User '%s';"
        "$p=New-ScheduledTaskPrincipal -UserId '%s' -RunLevel Highest;"
        "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -StartWhenAvailable;"
        "Register-ScheduledTask -TaskName '%s' -Action $a -Trigger $t -Principal $p "
        "-Settings $s -Force | Out-Null"
    ) % (exe.replace("'", "''"), os.path.dirname(exe).replace("'", "''"),
         user.replace("'", "''"), user.replace("'", "''"), task.replace("'", "''"))
    rc3, out3 = ps(script, timeout=90)
    if rc3 == 0:
        return True, 'PowerShell Register-ScheduledTask'
    return False, (out or out2 or out3)[:300]


def start_task(task):
    """启动计划任务。本机 schtasks 常被安全策略拦，必须保留 PowerShell 回退。

    注意：这里曾出现过同名函数的重复定义，后一个简版会静默覆盖带回退的版本，
    导致默认静默安装（/AUTO=1）在 schtasks 被拦时守护一个都起不来。
    """
    if run([SCHTASKS, '/Run', '/TN', task])[0] == 0:
        return True
    return ps("Start-ScheduledTask -TaskName '%s'" % task.replace("'", "''"),
              timeout=60)[0] == 0


# ---------------- 快捷方式：原生 IShellLinkW ----------------
# v2.3.0：以前靠 `powershell -Command (New-Object -ComObject WScript.Shell)` 生成，
# 每次要拉起一个 PowerShell 进程（提权环境里还受执行策略 / 安全软件影响）。
# 现在直连 ole32 + shell32 的 COM 接口，纯 ctypes、零第三方依赖、零子进程。
# 失败时仍回落到原来的 PowerShell 实现（行为与 v2.2.0 完全一致），
# 不允许"快捷方式没了"这种安装事故。
_CLSID_SHELLLINK = '00021401-0000-0000-C000-000000000046'
_IID_ISHELLLINKW = '000214F9-0000-0000-C000-000000000046'
_IID_IPERSISTFILE = '0000010B-0000-0000-C000-000000000046'


class _GUID(ctypes.Structure):
    _fields_ = [('Data1', ctypes.c_uint32), ('Data2', ctypes.c_uint16),
                ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8)]


def _guid(text):
    """'{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}' -> _GUID（手写十六进制解析）。

    v2.3.0 复核补丁：这里以前用 `uuid.UUID(text)`，为了一个 16 字节结构把
    `uuid` 整个模块拖进了安装器 —— `uuid.py` 顶层 `import platform`，且
    `_netbios_getnode()` 里 `import win32wnet, netbios`，PyInstaller 顺着这两条
    把 pywin32 三件（pywintypes38.dll +48,447 / win32wnet.pyd +16,360 /
    VCRUNTIME140_1.dll +19,727）+ platform + ctypes.util + ctypes.macholib
    全打了进去，Setup_WBOX.exe 白胖 ≈125 KB。
    GUID 的文本格式是固定的 8-4-4-4-12 十六进制，手写解析 8 行就够，零依赖。
    """
    raw = str(text).strip().strip('{}').replace('-', '')
    if len(raw) != 32:
        raise ValueError('GUID 格式非法：%r' % (text,))
    g = _GUID()
    g.Data1 = int(raw[0:8], 16)
    g.Data2 = int(raw[8:12], 16)
    g.Data3 = int(raw[12:16], 16)
    for i in range(8):
        g.Data4[i] = int(raw[16 + i * 2:18 + i * 2], 16)
    return g


def _slot(ptr, index, argtypes):
    """取 COM 接口虚表第 index 项，包成可直接调用的函数。

    IShellLinkW 前三个槽是 IUnknown（QueryInterface / AddRef / Release），
    所以业务方法从 3 开始：SetDescription=7, SetWorkingDirectory=9,
    SetArguments=11, SetIconLocation=17, SetPath=20。
    """
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return proto(vtbl[index])


def _shortcut_native(link, target, arguments, desc, icon):
    """用 IShellLinkW + IPersistFile 直接写 .lnk，返回 True。

    任何一步 HRESULT 不为 0 都抛 OSError，由 make_shortcut 决定是否回落。
    """
    ole32 = ctypes.WinDLL('ole32', use_last_error=True)
    # IShellLink 只认反斜杠：传 'D:/x/y.lnk' 会让 IPersistFile::Save 返回
    # 0x80070002（找不到文件）。这里统一规范成 Windows 路径。
    link = os.path.normpath(link)
    target = os.path.normpath(target)
    icon = os.path.normpath(icon) if icon else ''
    hr_init = None
    try:
        hr_init = ole32.CoInitialize(None)
    except Exception:
        pass
    clsid = _GUID()          # 真正的值在 try 里解析，保证异常时 finally 仍会配对
    iid = _GUID()
    ptr = ctypes.c_void_p()
    pf = ctypes.c_void_p()
    try:
        # R2：三个 GUID 的解析放在 try 里面 —— 常量写错会抛 ValueError，
        # 放在 try 之外就会跳过 finally，把上面 CoInitialize 的配对一起丢掉。
        clsid = _guid(_CLSID_SHELLLINK)
        iid = _guid(_IID_ISHELLLINKW)
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p,
                                          ctypes.c_uint32, ctypes.POINTER(_GUID),
                                          ctypes.POINTER(ctypes.c_void_p)]
        ole32.CoCreateInstance.restype = ctypes.c_long
        CLSCTX_INPROC_SERVER = 1
        hr = ole32.CoCreateInstance(ctypes.byref(clsid), None,
                                    CLSCTX_INPROC_SERVER,
                                    ctypes.byref(iid), ctypes.byref(ptr))
        if hr != 0 or not ptr:
            raise OSError('CoCreateInstance(ShellLink) hr=0x%08X'
                          % (hr & 0xFFFFFFFF))
        set_path = _slot(ptr, 20, [ctypes.c_wchar_p, ctypes.c_uint32])
        set_args = _slot(ptr, 11, [ctypes.c_wchar_p])
        set_desc = _slot(ptr, 7, [ctypes.c_wchar_p])
        set_dir = _slot(ptr, 9, [ctypes.c_wchar_p])
        set_icon = _slot(ptr, 17, [ctypes.c_wchar_p, ctypes.c_int])
        query = _slot(ptr, 0, [ctypes.POINTER(_GUID), ctypes.c_void_p])
        for hr in (set_path(ptr, target, 0),
                   set_args(ptr, arguments or ''),
                   set_desc(ptr, desc or '')):
            if hr != 0:
                raise OSError('IShellLink 设置失败 hr=0x%08X' % (hr & 0xFFFFFFFF))
        if icon:
            set_icon(ptr, icon, 0)
        # 工作目录留空：Windows 会沿用目标所在目录，和 WScript.Shell 的默认一致
        set_dir(ptr, '')
        iid_pf = _guid(_IID_IPERSISTFILE)
        hr = query(ptr, ctypes.byref(iid_pf), ctypes.byref(pf))
        if hr != 0 or not pf:
            raise OSError('QueryInterface(IPersistFile) hr=0x%08X'
                          % (hr & 0xFFFFFFFF))
        # IPersistFile 的虚表（IUnknown 0-2 / GetClassID 3 / IsDirty 4 /
        # Load 5 / **Save 6** / SaveCompleted 7 / GetCurFile 8）。
        # 注意是 6 而不是 5：槽 5 是 Load，拿不存在的 .lnk 去 Load 会返回
        # 0x80070002（ERROR_FILE_NOT_FOUND），看起来像"保存失败"，很坑。
        save = _slot(pf, 6, [ctypes.c_wchar_p, ctypes.c_int])
        hr = save(pf, link, 1)          # fRemember = TRUE
        if hr != 0:
            raise OSError('IPersistFile::Save hr=0x%08X' % (hr & 0xFFFFFFFF))
        return True
    finally:
        try:
            if pf:
                _slot(pf, 2, [])(pf)
        except Exception:
            pass
        try:
            _slot(ptr, 2, [])(ptr)
        except Exception:
            pass
        # S_OK(0) 表示这次是"本线程首次初始化"，配对反初始化；
        # S_FALSE(1) 说明此前已经初始化过，这时不能替别人反初始化。
        try:
            if hr_init == 0:
                ole32.CoUninitialize()
        except Exception:
            pass


def _shortcut_powershell(link, target, arguments, desc, icon):
    """v2.2.0 的老实现：WScript.Shell 走 PowerShell。仅作回落保留。"""
    lnk = link.replace("'", "''")
    tgt = target.replace("'", "''")
    cursor = (icon or target).replace("'", "''")
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
              "$s.TargetPath='%s';$s.Arguments='%s';$s.Description='%s';"
              "$s.IconLocation='%s,0';$s.Save()") % (lnk, tgt, arguments, desc,
                                                     cursor)
    return ps(script)


def make_shortcut(link, target, arguments, desc, icon=''):
    """生成一个 .lnk：先走原生 COM，失败再回落 PowerShell。

    icon 为空时用目标文件自己的图标（v2.2.0 的行为）。
    返回 (ok, how)：ok=False 时调用方只记日志，不打断安装。
    """
    try:
        _shortcut_native(link, target, arguments, desc, icon or target)
        return (True, 'native')
    except Exception as e:
        print('原生 IShellLink 失败（%r），回落 PowerShell' % (e,))
    rc, out = _shortcut_powershell(link, target, arguments, desc, icon)
    return (rc == 0, 'powershell')


def start_menu_link_name():
    """开始菜单里的指南快捷方式文件名（安装与卸载两处共用一份）。"""
    return '插件开发指南.lnk'


def guide_path(target):
    """安装目录里《插件开发指南》的位置（自包含 HTML，双击即可阅读）。"""
    return os.path.join(target, 'docs', '插件开发指南.html')


def start_menu_dir():
    base = os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'),
                        'Microsoft', 'Windows', 'Start Menu', 'Programs')
    # v2.10.0 改名：用户可见目录叫 WBOX；旧 Wingman 目录在快捷方式段清理。
    d = os.path.join(base, 'WBOX')
    os.makedirs(d, exist_ok=True)
    return d


# ---------------- 配置 ----------------
def detect_afterburner():
    """按卸载项 DisplayIcon、常见目录、运行中进程三路找小飞机。"""
    cands = []
    try:
        import winreg
        base = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0,
                                    winreg.KEY_READ | view) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        sub = winreg.EnumKey(k, i)
                        if 'afterburner' not in sub.lower():
                            continue
                        with winreg.OpenKey(k, sub) as sk:
                            try:
                                icon = winreg.QueryValueEx(sk, 'DisplayIcon')[0]
                            except OSError:
                                icon = ''
                            if icon:
                                cands.append(os.path.dirname(icon.strip('"')))
            except OSError:
                pass
    except Exception:
        pass
    for d in (r'C:\Program Files (x86)\MSI Afterburner',
              r'C:\Program Files\MSI Afterburner',
              r'D:\MSI Afterburner', r'D:\Program Files\MSI Afterburner',
              r'E:\MSI Afterburner'):
        cands.append(d)
    for d in cands:
        exe = os.path.join(d, 'MSIAfterburner.exe')
        if os.path.isfile(exe):
            return exe
    return ''


def detect_roots(target):
    """借 payload 里的 game_lib 找 Steam/WeGame 库根。"""
    try:
        if target not in sys.path:
            sys.path.insert(0, target)
        import game_lib
        return game_lib.platform_roots()
    except Exception:
        return []


def patch_config(target, ab_path):
    cfg_path = os.path.join(target, 'config.json')
    try:
        with open(cfg_path, 'r', encoding='utf-8-sig') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    if ab_path:
        cfg['afterburner_path'] = ab_path
    elif not os.path.isfile(str(cfg.get('afterburner_path') or '')):
        # 旧配置可能带着别的机器上的路径；探测不到就清空，让守护运行时兜底
        # 并给出明确报错，而不是默默指向一个不存在的盘符。
        cfg['afterburner_path'] = ''
    roots = detect_roots(target)
    if roots:
        old = [r for r in (cfg.get('game_roots') or []) if os.path.isdir(r)]
        merged = []
        for r in list(old) + list(roots):
            if os.path.isdir(r) and os.path.normcase(r) not in \
                    [os.path.normcase(x) for x in merged]:
                merged.append(r)
        cfg['game_roots'] = merged
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


# ---------------- 安装 / 卸载 ----------------
def read_json(path):
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return None


def merge_config(target, old):
    """解包会覆盖 config.json；把用户改过的键逐层合并回来，模板负责补新增项。

    只补缺失项、不覆盖旧值；段里的新键也一并补齐（例如新增的限速参数）。
    `_` 开头的说明性注释始终用包内最新文案。
    """
    path = os.path.join(target, 'config.json')
    tpl = read_json(path)
    if not isinstance(tpl, dict) or not isinstance(old, dict):
        return
    merged = dict(tpl)
    for key, value in old.items():
        if key.startswith('_'):
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            seg = dict(merged[key])
            seg.update(value)
            merged[key] = seg
        else:
            merged[key] = value
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


def extract_payload(target):
    with zipfile.ZipFile(res('payload.zip')) as z:
        names = z.namelist()
        z.extractall(target)
    return len(names)


def restore_shared_runtime(target, log):
    """v2.1.1：把 payload 里省掉的运行时从安装器自己的 _MEIPASS 补齐。

    Setup 是 PyInstaller onefile，运行时已经解压出一整套 Python/Tcl 文件；
    payload 里那 3 MB 是重复的。MARKER 是构建时按实际跳过项生成的清单，
    照单复制即可，不用在两处维护同一个名单。旧 payload / 测试 payload 没有
    MARKER，走原来的老逻辑，行为不变。

    构建机上有 verify_shared_runtime() 逐项 md5 校验，这里只做存在性兜底：
    少一个都不许装出残废程序，直接抛错让安装失败。
    """
    marker = os.path.join(target, shared_runtime.MARKER)
    # v2.11.0 改名前的旧清单残留：升级覆盖时清掉，避免目录里留两份标记。
    legacy_marker = os.path.join(target, shared_runtime.LEGACY_MARKER)
    if os.path.isfile(legacy_marker):
        try:
            os.remove(legacy_marker)
        except OSError:
            pass
    if not os.path.isfile(marker):
        return 0
    with open(marker, 'r', encoding='utf-8') as f:
        names = json.load(f)
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('瘦身 payload 必须由打包后的 Setup_WBOX.exe 安装')
    # 升级安装时旧版可能留下已被裁掉的 Tcl 文件；这些目录是纯运行时资源，
    # 没有用户数据，先清掉才能让新版的裁剪真正落到磁盘上。
    for d in shared_runtime.SHARED_DIRS:
        old = os.path.join(target, d)
        if os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
    missing = []
    for rel in names:
        src = res(rel.replace('/', os.sep))
        dst = os.path.join(target, rel.replace('/', os.sep))
        if not os.path.isfile(src):
            missing.append(rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    if missing:
        raise RuntimeError('安装器内嵌运行时不完整：%s' % '、'.join(missing[:8]))
    try:
        os.remove(marker)
    except OSError:
        pass
    log_line('已从安装器复用 %d 个运行时文件（payload 不再重复打包）' % len(names), log)
    if shared_runtime.compact_base_library(
            os.path.join(target, shared_runtime.BASE_LIBRARY)):
        log_line('base_library.zip 已重压，磁盘占用减半', log)
    return len(names)


# ---------------- 用户自装插件（升级保留 / 卸载备份）----------------
def stash_user_plugins(target, log):
    """升级安装前把用户自装插件复制到临时目录，返回 (临时目录, 名称列表) 或 None。

    为什么要暂存：解包是整目录覆盖，万一以后改成"先清目录再解包"（或者用户插件
    目录名与新版内置插件重名被覆盖），用户的东西就没了。这里先复制一份，
    解包后再放回去，成本只有几十 KB。
    """
    names = user_plugin_names(target)
    if not names:
        return None
    tmp = tempfile.mkdtemp(prefix='wbox_plugins_')
    saved = []
    for name in names:
        try:
            shutil.copytree(os.path.join(target, 'plugins', name),
                            os.path.join(tmp, name))
            saved.append(name)
        except Exception as e:
            log_line('用户插件 %s 暂存失败：%r（继续安装）' % (name, e), log)
    if not saved:
        shutil.rmtree(tmp, ignore_errors=True)
        return None
    log_line('已暂存用户自装插件 %d 个：%s' % (len(saved), '、'.join(saved)), log)
    return tmp, saved


def restore_user_plugins(stash, target, log):
    """解包后把暂存的用户插件放回去。

    已经存在的目录不覆盖（解包不会删目录，用户插件的文件也从来没进过 payload，
    所以正常情况下它原封不动地还在）；只有确实丢了才从暂存区复制回来。
    """
    if not stash:
        return
    tmp, names = stash
    root = os.path.join(target, 'plugins')
    ok = 0
    for name in names:
        dst = os.path.join(root, name)
        if os.path.isdir(dst):
            ok += 1
            continue
        try:
            shutil.copytree(os.path.join(tmp, name), dst)
            ok += 1
        except Exception as e:
            log_line('用户插件 %s 还原失败：%r（备份仍在 %s）' % (name, e, tmp), log)
    log_line('用户自装插件保留 %d/%d 个' % (ok, len(names)), log)
    if ok == len(names):
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_dir_acl(target, log):
    """安装目录继承被禁用时恢复成"继承父目录"。

    2026-11-11 在 Win11 虚拟机实测：安装被强杀后留下的 Program Files 子目录
    可能只剩 SYSTEM + Administrators 两条 ACE，且继承标记被去掉。这种目录
    普通用户连 cd / 读取都做不到，而 `os.makedirs(exist_ok=True)` 遇到已存在
    目录会直接沿用这份脏 ACL，导致重装也救不回来，必须先手动删目录。

    只在解包前检查一次，正常的目录一次 Get-Acl 就返回，开销毫秒级。
    """
    q = target.replace("'", "''")
    rc, out = ps("$a=Get-Acl -LiteralPath '%s';"
                 "if($a.AreAccessRulesProtected){'BROKEN'}else{'OK'}" % q, timeout=30)
    if rc != 0:
        return False          # PowerShell 不可用：不阻塞安装
    if 'BROKEN' not in (out or ''):
        return True
    # 只能用 /reset：它把目录 ACL 整体替换成"从父容器继承的默认 ACL"。
    # 不能和 /inheritance:e 一起给（icacls 会报"无效参数 /reset"），
    # 而单独的 /inheritance:e 只重新打开继承标志，不会清掉那两条显式 ACE。
    rc2, out2 = run([ICACLS, target, '/reset', '/T', '/C', '/Q'], timeout=120)
    log_line('安装目录 ACL 异常（继承被禁用），已重置为继承父目录：%s'
             % ('成功' if rc2 == 0 else '失败 ' + (out2 or '')[:120]), log)
    return rc2 == 0


def migrate_legacy(target, log):
    """v2.11.0 全量改名：把旧 Wingman 安装一次性迁到 WBOX 标识。

    覆盖：旧进程名、旧计划任务、旧注册表卸载键、旧默认安装目录、旧备份目录。
    用户原本装在自定义目录时 installed_dir() 已把 target 指过去，这里只换
    标识不换目录名。返回 (旧目录暂存的用户插件, 收尾是否删除旧目录)。
    """
    old = uninstall_core.LEGACY_DIR
    same = os.path.normcase(old) == os.path.normcase(target)
    stash = None
    remove_old = False
    # 旧守护跑的是 Wingman.exe，按新名字查进程查不到；不停它目录就搬不动。
    # 目标目录里也可能跑着旧名守护（生产 InstallLocation 可能就是旧目录本身），
    # 不停它覆盖解包会撞 exe 占用锁。
    for d in (old, target):
        if os.path.isdir(d) and guard_pids(d, uninstall_core.LEGACY_EXE):
            log_line('停止旧名守护（Wingman）：%s' % d, log)
            stop_guard(d, uninstall_core.LEGACY_EXE)
    if run([SCHTASKS, '/Query', '/TN', uninstall_core.LEGACY_TASK])[0] == 0:
        ok = delete_task(uninstall_core.LEGACY_TASK)
        log_line('旧计划任务 Wingman 删除：%s' % ('成功' if ok else '失败'), log)
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, uninstall_core.LEGACY_KEY)
        log_line('旧注册表卸载键 Wingman 已删除', log)
    except Exception:
        pass
    if os.path.isdir(old) and not same:
        if not os.path.isdir(target):
            shutil.move(old, target)
            log_line('旧安装目录已迁移：%s -> %s' % (old, target), log)
        else:
            # 新旧目录并存（例如用户先手动装过新版）：旧目录里的用户插件
            # 暂存后并入新目录，旧目录收尾删除，不留两套程序。
            stash = stash_user_plugins(old, log)
            remove_old = True
            log_line('新旧目录并存：旧目录用户插件已暂存，收尾删除旧目录', log)
    oldb = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Wingman')
    newb = uninstall_core.backup_root()
    if os.path.isdir(oldb):
        if not os.path.isdir(newb):
            shutil.move(oldb, newb)
        else:
            for name in os.listdir(oldb):
                dst = os.path.join(newb, name)
                if not os.path.exists(dst):
                    shutil.move(os.path.join(oldb, name), dst)
            shutil.rmtree(oldb, ignore_errors=True)
        log_line('旧备份目录已并入 %s' % newb, log)
    return stash, remove_old


def install(target, task, auto_start, launch, log, no_task=False, progress=None):
    def step(frac, text, detail=''):
        # GUI 的进度回调（工作线程里调用，只往队列塞事件，不碰 Tk）；
        # 静默安装不传 progress，一行都不多执行。
        if progress is not None:
            progress(frac, text, detail)

    # v2.13.0：事务状态先落盘。断电/被杀后下次运行安装器或守护时，
    # install_txn.recover() 能说清停在哪一步、要不要恢复配置。
    prev = install_txn.recover(target, log=log)
    if prev.get('action'):
        log_line('上次安装未正常结束：%s' % prev.get('text'), log)
    txn = install_txn.begin('install', target, task, VERSION)
    step(0.05, '正在检查安装环境…')
    legacy_stash, legacy_remove = migrate_legacy(target, log)
    legacy_exe = os.path.join(target, uninstall_core.LEGACY_EXE)
    if os.path.isfile(legacy_exe):
        try:
            os.remove(legacy_exe)
            log_line('旧名主程序已删除：%s' % legacy_exe, log)
        except OSError as e:
            log_line('旧名主程序删除失败：%r（覆盖解包可能受阻）' % (e,), log)
    os.makedirs(target, exist_ok=True)
    ensure_dir_acl(target, log)
    if guard_pids(target):
        log_line('停止正在运行的守护', log)
        stop_guard(target)
    old_cfg = read_json(os.path.join(target, 'config.json'))
    # 覆盖前备份配置：合并/迁移写坏时能原地恢复（事务日志里记着路径）
    cfg_path = os.path.join(target, 'config.json')
    bk = install_txn.backup_file('config', cfg_path)
    if bk:
        install_txn.remember_backup(txn, 'config', cfg_path, bk)
        log_line('已备份原配置：%s' % bk, log)
    # v2.11.3：本体不带插件（插件在 wbox-plugins 仓库单独分发）。升级时仍然
    # 先把用户已装的插件整目录暂存 —— payload 不会再写入任何 plugins\ 内容，
    # 所以这一步只防"解包把旧目录清掉"，还原后版本与内容逐字节不变。
    stash = stash_user_plugins(target, log)
    install_txn.step(txn, 'stash_plugins', 'extract')
    step(0.15, '正在复制程序文件…')
    n = extract_payload(target)
    log_line('已解包 %d 个文件到 %s' % (n, target), log)
    install_txn.step(txn, 'extract', 'shared_runtime', files=n)
    restore_shared_runtime(target, log)
    install_txn.step(txn, 'shared_runtime', 'plugins')
    step(0.50, '正在还原插件…', '已写入 %d 个文件' % n)
    restore_user_plugins(stash, target, log)
    restore_user_plugins(legacy_stash, target, log)
    step(0.62, '正在保存设置…')
    if isinstance(old_cfg, dict):
        merge_config(target, old_cfg)
        log_line('已保留原有 config.json 设置并补齐新增项', log)
    install_txn.step(txn, 'config_merged', 'shortcuts')
    # 只统计、不改动：告诉安装日志"这台机器上现在有几个插件"，出问题时一眼
    # 看出是本体没装插件还是用户没装插件（v2.11.3 起两者的责任边界更清楚了）。
    root = os.path.join(target, 'plugins')
    try:
        have = sorted(d for d in os.listdir(root)
                      if os.path.isfile(os.path.join(root, d, 'plugin.json')))
    except OSError:
        have = []
    log_line('已安装插件：%s' % ('、'.join(have) or
                                 '无（本体不附带插件，可在面板里安装）'), log)

    ab = detect_afterburner()
    cfg = patch_config(target, ab)
    log_line('小飞机路径：%s' % (ab or '未找到（请在设置面板或 config.json 里改）'), log)
    log_line('游戏库根目录：%s' % (', '.join(cfg.get('game_roots') or []) or '无'), log)

    # v2.6.0：卸载入口并进主 exe（/uninstall），不再放置 Uninstall_Wingman.exe
    # 全量副本（旧版是 Setup 自拷贝，+5.81 MB）。旧安装遗留的副本在这里清掉，
    # 否则覆盖升级后体积永远降不下来。
    legacy = os.path.join(target, 'Uninstall_Wingman.exe')
    if os.path.isfile(legacy):
        try:
            os.remove(legacy)
            log_line('已移除旧版卸载副本：%s' % legacy, log)
        except OSError as e:
            log_line('旧版卸载副本移除失败：%r' % (e,), log)

    install_txn.step(txn, 'plugins_restored', 'shortcuts')
    step(0.72, '正在生成快捷方式…')
    exe = os.path.join(target, APP_EXE)
    sm = start_menu_dir()
    make_shortcut(os.path.join(sm, 'WBOX 设置.lnk'), exe, '', '小飞机关联启动 / ACE 防护 / 弹窗关闭')
    make_shortcut(os.path.join(sm, '查看运行日志.lnk'),
                  os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'notepad.exe'),
                  '"%s"' % os.path.join(target, 'guard.log'), '查看守护日志')
    # v2.3.0：随安装包分发的《插件开发指南》。快捷方式直接指向那份自包含 HTML，
    # 双击用系统默认浏览器打开（安装包不注册文件关联、不依赖外部阅读器）。
    # 目录内容按 posix 拼接（zip 里就是 '/'），落地时再换成本地分隔符。
    guide = guide_path(target)
    if os.path.isfile(guide):
        make_shortcut(os.path.join(sm, start_menu_link_name()), guide, '',
                      'WBOX 插件开发指南', icon=exe)
    else:
        log_line('注意：安装包里没有插件开发指南（docs 缺失）', log)
    desktop = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop',
                           'WBOX 设置.lnk')
    if os.path.isdir(os.path.dirname(desktop)):
        make_shortcut(desktop, exe, '', '小飞机关联启动 / ACE 防护 / 弹窗关闭')
    # v2.10.0 改名：覆盖升级时清掉旧名入口（桌面 lnk + 开始菜单 Wingman 目录），
    # 否则开始菜单会同时出现 WBOX 和 僚机 Wingman 两份。失败只记日志。
    legacy_desktop = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop',
                                  '僚机 Wingman 设置.lnk')
    legacy_sm = os.path.join(os.path.dirname(sm), 'Wingman')
    for p in (legacy_desktop, legacy_sm):
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.isfile(p):
                os.remove(p)
        except OSError as e:
            log_line('旧名快捷方式清理失败：%s（%r）' % (p, e), log)

    install_txn.step(txn, 'shortcuts', 'task')
    step(0.82, '正在设置开机自动运行…')
    ok, how = (False, 'skip')
    if not no_task:
        ok, how = create_task(task, exe)
    log_line('计划任务 %s：%s' % (task, ('已创建（%s）' % how) if ok else
                                ('跳过' if no_task else '创建失败 ' + str(how)[:160])), log)
    step(0.92, '正在收尾，请稍候…')
    install_txn.step(txn, 'task', 'registry')
    write_uninstall_key(target)
    log_line('已写入卸载项', log)
    # 同路径覆盖 exe 后，Windows 可能继续拿图标缓存里的旧图标；通知 Shell
    # 关联变更，让任务管理器 / 资源管理器立刻改用新 exe 内嵌的设计图标。
    refresh_shell_icons()

    # 任务启动失败（schtasks 被拦、任务刚建好尚未注册完等）必须兜底直启，
    # 否则默认静默安装完成后守护一个都没跑，日志却写着"安装完成"。
    started = False
    if auto_start and ok:
        started = start_task(task)
        log_line('启动守护（计划任务 %s）：%s' % (task, '成功' if started else '失败'), log)
    if not started and (launch or (auto_start and ok)):
        try:
            subprocess.Popen([exe, '--guard'], cwd=target,
                             creationflags=0x08000000)
            log_line('兜底：已直接启动守护', log)
            started = True
        except Exception as e:
            log_line('直接启动失败 %r' % (e,), log)
    if legacy_remove:
        shutil.rmtree(uninstall_core.LEGACY_DIR, ignore_errors=True)
        log_line('旧名安装目录已删除：%s' % uninstall_core.LEGACY_DIR, log)
    install_txn.finish(txn)
    return True


def _relaunch_from_temp(target, task):
    """卸载程序的自我搬迁：从安装目录里运行时先复制到 %TEMP% 再执行。

    目标目录里的 exe 正被自己占用，Windows 删不掉，只能靠"延时 rmdir"，
    这一步很容易被安全软件拦掉，结果留下一整套文件。搬到 %TEMP% 后目标目录
    就没人占用了，可以同步 shutil.rmtree 干净删除。
    """
    if not getattr(sys, 'frozen', False):
        return False
    me = os.path.abspath(sys.executable)
    if not os.path.normcase(me).startswith(os.path.normcase(target) + os.sep):
        return False
    tmp = os.path.join(tempfile.gettempdir(), 'abg_uninstall.exe')
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
        shutil.copy2(me, tmp)
    except Exception:
        return False
    ok = _spawn([tmp, '/uninstall', '/S', '/DIR=%s' % target,
                 '/TASK=%s' % task, '/FROMTEMP=1'])
    # 父进程交棒后立刻退出，副本若没能起来（受限会话会清理分离出去的子进程），
    # 现象是"点了卸载什么都没发生且日志无卸载记录"。留一行交棒记录便于定位。
    try:
        log_line('卸载副本 %s：%s' % (tmp, '已启动' if ok else '启动失败'),
                 os.path.join(tempfile.gettempdir(), 'wbox_setup.log'))
    except Exception:
        pass
    return ok


# ---------------- 图形界面 ----------------
def _icon_prims(kind, w, h, hw):
    """24×24 设计坐标的线性小图标 → 超采样线段图元（斜边无阶梯）。"""
    s = min(w, h)
    u = s / 24.0
    ox, oy = (w - s) / 2.0, (h - s) / 2.0

    def P(seq):
        return [(ox + v * u) if i % 2 == 0 else (oy + v * u)
                for i, v in enumerate(seq)]

    segs = []
    if kind == 'close':
        segs = [(5, 5, 19, 19), (19, 5, 5, 19)]
    elif kind == 'check':
        segs = [(4, 12, 9.5, 17.5), (9.5, 17.5, 20, 6)]
    elif kind == 'folder':
        pts = P((3, 7, 9, 7, 11, 9, 21, 9, 21, 19, 3, 19, 3, 7))
        return [(0, pts[i], pts[i + 1], pts[i + 2], pts[i + 3], hw)
                for i in range(0, len(pts) - 2, 2)]
    out = []
    for sq in segs:
        a = P(sq)
        out.append((0, a[0], a[1], a[2], a[3], hw))
    return out


def gui(default_dir, task, no_task=False, selftest=False, log_path=None):
    """WBOX 安装器：与设置面板同一套极简语言（v2.11.0 起不再用液态玻璃）。

    一窗四态：首屏 / 安装中 / 完成 / 失败。标题栏恒定、只切中部，避免跳版。
    安装在后台线程跑，主线程 80ms 轮询进度事件，窗口不冻结；安装中禁止关闭。
    开关 / 按钮 / 图标共用 src/sprite.py 超采样抗锯齿（与面板单一来源）。
    """
    import tkinter as tk
    from tkinter import filedialog
    import threading
    from collections import deque

    # DPI 必须先于 Tk 创建，否则整个界面被系统位图拉伸，字发虚
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    root = tk.Tk()
    root.title('WBOX 安装')
    root.resizable(False, False)

    dpi = 96
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
    except Exception:
        pass
    scale = max(1.0, dpi / 96.0)
    try:
        root.tk.call('tk', 'scaling', dpi / 72.0)
    except Exception:
        pass

    def px(v):
        return max(1, int(round(v * scale)))

    # 色板与面板同一来源（src/skins.py plain 档 token），明暗跟随系统应用主题。
    appearance = skins.detect_appearance()
    skin = skins.get(skins.DEFAULT_ID) or skins.builtin_skins()[-1]
    tok = skin.tokens(appearance)
    bg = tok['glass']

    def F(sz, bold=False):
        if bold:
            return ('Microsoft YaHei UI', -px(sz), 'bold')
        return ('Microsoft YaHei UI', -px(sz))

    F_WORD = F(15, True)
    F_TB = F(11)
    F_TITLE = F(16, True)
    F_SUB = F(11)
    F_ROW = F(13)
    F_BTN = F(13, True)
    F_BUSY = F(15, True)
    F_DONE = F(16, True)
    F_INPUT = ('Consolas', -px(12))
    F_LOG = ('Consolas', -px(11))

    root.configure(bg=bg)
    try:
        ico = res('setup.ico')
        if os.path.isfile(ico):
            root.iconbitmap(default=ico)
    except Exception:
        pass

    W, H = px(480), px(460)
    INNER = px(440)                    # 480 - 2×20，与面板极简档同宽
    PAD = px(20)
    HW = max(0.6, 0.9 * scale)         # 图标线宽半宽（设备像素）

    def mix(c1, c2, p):
        a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
        b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
        return '#%02X%02X%02X' % tuple(int(round(x + (y - x) * p))
                                       for x, y in zip(a, b))

    class Sw(tk.Canvas):
        """36×18 胶囊开关（面板极简档同几何），超采样抗锯齿、即时切换。"""

        def __init__(self, master, var, rowbg):
            self.var = var
            self.rowbg = rowbg
            self.w, self.h = px(36), px(18)
            self._focused = False
            tk.Canvas.__init__(self, master, width=self.w, height=self.h,
                               bg=rowbg, highlightthickness=0, bd=0,
                               cursor='hand2', takefocus=1)
            self.bind('<Button-1>', self.toggle)
            self.bind('<Return>', self.toggle)
            self.bind('<space>', self.toggle)
            self.bind('<FocusIn>', lambda e: self._focus(True), add='+')
            self.bind('<FocusOut>', lambda e: self._focus(False), add='+')
            self.draw()

        def _focus(self, on):
            self._focused = on
            self.draw()

        def toggle(self, ev=None):
            self.var.set(not self.var.get())
            self.draw()

        def draw(self):
            self.delete('all')
            w, h = self.w, self.h
            on = bool(self.var.get())
            fill = tok['on'] if on else tok['switch_off']
            knob = tok['on_knob'] if on else tok['knob']
            pad = px(2)
            k = max(px(6), h - 2 * pad)
            x0 = pad + (w - 2 * pad - k) * (1.0 if on else 0.0)
            layers = [
                (sprite.cov_cached(('swt', w, h), w, h,
                                   [(0, h / 2.0, h / 2.0, w - h / 2.0,
                                     h / 2.0, h / 2.0)]), fill),
                (sprite.cov_cached(('swk', w, h, k, int(round(x0))), w, h,
                                   [(1, x0 + k / 2.0, h / 2.0, k / 2.0)]), knob)]
            self._img = sprite.sprite(self, w, h, layers, self.rowbg)
            self.create_image(0, 0, anchor='nw', image=self._img)
            if self._focused:
                col = self.rowbg if on else tok['fg']
                self.create_rectangle(0.5, 0.5, w - 0.5, h - 0.5,
                                      outline=col, width=1)

    def chip(master, text, command, primary=True, h=36, width=None, bgc=None):
        """极简按钮：6px 圆角实心chip（超采样边缘），主 = fg 底反色字。"""
        base = bgc or bg
        hp = px(h)
        cv = tk.Canvas(master, bg=base, highlightthickness=0, bd=0,
                       height=hp, cursor='hand2', takefocus=1)
        lbl = tk.Label(cv, text=text, bg=base, fg=base,
                       font=F_BTN if primary else F_ROW)
        cv.update_idletasks()
        w = px(width) if width else lbl.winfo_reqwidth() + px(28)
        cv.configure(width=w)
        cv.lbl = lbl
        r = px(6)
        cov = sprite.cov_cached(('chip', w, hp, r), w, hp,
                                [(4, sprite.round_rect_pts(0.5, 0.5,
                                                           w - 0.5, hp - 0.5, r))])
        fill0 = tok['fg'] if primary else tok['fill']
        fg0 = base if primary else tok['fg']
        cv._focused = False

        def paint(fill, fg):
            cv.delete('all')
            cv._img = sprite.sprite(cv, w, hp, [(cov, fill)], base)
            cv.create_image(0, 0, anchor='nw', image=cv._img)
            cv.create_window(w // 2, hp // 2, window=lbl)
            lbl.configure(bg=fill, fg=fg)
            if cv._focused:
                ring = base if primary else tok['fg']
                ins = px(2)
                cv.create_rectangle(ins + 0.5, ins + 0.5, w - ins - 0.5,
                                    hp - ins - 0.5, outline=ring, width=1)

        paint(fill0, fg0)

        def enter(e):
            paint(mix(tok['fg'], base, 0.08) if primary else tok['fill_2'],
                  fg0 if primary else tok['fg'])

        def leave(e):
            paint(fill0, fg0)

        def press(e):
            paint(mix(tok['fg'], base, 0.14) if primary else tok['press'],
                  fg0 if primary else tok['fg'])

        for wd in (cv, lbl):
            wd.bind('<Enter>', enter, add='+')
            wd.bind('<Leave>', leave, add='+')
            wd.bind('<ButtonPress-1>', press, add='+')
            wd.bind('<ButtonRelease-1>', lambda e: command(), add='+')
        cv.bind('<Return>', lambda e: command(), add='+')
        cv.bind('<space>', lambda e: command(), add='+')

        def fin(e):
            cv._focused = True
            paint(fill0, fg0)

        def fout(e):
            cv._focused = False
            paint(fill0, fg0)

        cv.bind('<FocusIn>', fin, add='+')
        cv.bind('<FocusOut>', fout, add='+')
        return cv

    # ---- 标题栏：WBOX 字标 + 安装 + 版本 + 关闭，整行可拖动 ----
    bar = tk.Frame(root, bg=bg, height=px(36))
    bar.pack(fill='x')
    bar.pack_propagate(False)
    tb_left = tk.Frame(bar, bg=bg)
    tb_left.pack(side='left', padx=(PAD, 0))
    tk.Label(tb_left, text='WBOX', font=F_WORD, fg=tok['fg'],
             bg=bg).pack(side='left')
    tk.Label(tb_left, text='安装程序', font=F_SUB, fg=tok['t3'],
             bg=bg).pack(side='left', padx=(px(8), 0))
    tk.Label(tb_left, text='v%s' % VERSION, font=F_TB, fg=tok['t4'],
             bg=bg).pack(side='left', padx=(px(8), 0))

    ctrl = tk.Frame(bar, bg=bg)
    ctrl.pack(side='right', padx=(0, px(6)))
    cw, ch = px(34), px(26)
    close_cv = tk.Canvas(ctrl, width=cw, height=ch, bg=bg,
                         highlightthickness=0, bd=0, cursor='hand2')
    close_cv.pack(side='right')
    close_cov = sprite.cov_cached(('chip', cw, ch, px(6)), cw, ch,
                                  [(4, sprite.round_rect_pts(1, 1, cw - 1,
                                                             ch - 1, px(6)))])
    close_ic = sprite.cov_cached(('ic', 'close', cw, ch, round(HW, 2)), cw, ch,
                                 _icon_prims('close', cw, ch, HW))

    def close_paint(on):
        layers = ([(close_cov, tok['fill_2']), (close_ic, tok['fg'])] if on
                  else [(close_ic, tok['t3'])])
        close_cv.delete('all')
        close_cv._img = sprite.sprite(close_cv, cw, ch, layers, bg)
        close_cv.create_image(0, 0, anchor='nw', image=close_cv._img)

    close_paint(False)
    close_cv.bind('<Enter>', lambda e: close_paint(True), add='+')
    close_cv.bind('<Leave>', lambda e: close_paint(False), add='+')
    close_cv.bind('<ButtonRelease-1>', lambda e: on_close(), add='+')
    close_cv.bind('<Return>', lambda e: on_close(), add='+')
    close_cv.bind('<space>', lambda e: on_close(), add='+')

    drag = {}

    def drag_start(e):
        drag['x'] = e.x_root - root.winfo_x()
        drag['y'] = e.y_root - root.winfo_y()

    def drag_move(e):
        if 'x' in drag:
            root.geometry('+%d+%d' % (e.x_root - drag['x'],
                                      e.y_root - drag['y']))

    draggables = [bar, tb_left] + list(tb_left.winfo_children())
    for wd in draggables:
        wd.bind('<Button-1>', drag_start, add='+')
        wd.bind('<B1-Motion>', drag_move, add='+')

    # ---- 中部：四态各一帧，apply_state 切换 ----
    middle = tk.Frame(root, bg=bg, height=H - px(36))
    middle.pack(fill='both', expand=True)
    middle.pack_propagate(False)
    idle_f = tk.Frame(middle, bg=bg)
    busy_f = tk.Frame(middle, bg=bg)
    done_f = tk.Frame(middle, bg=bg)
    fail_f = tk.Frame(middle, bg=bg)

    dir_var = tk.StringVar(value=default_dir)
    auto_var = tk.BooleanVar(value=True)
    launch_var = tk.BooleanVar(value=False)
    log_lines = []
    state = {'cur': 'idle', 'target': os.path.normpath(default_dir)}
    events = deque()

    def say(msg):
        # _GuiLog 的落点：收集安装技术日志（含原始异常），供「查看详情」展开
        log_lines.append(str(msg))

    # ---- 首屏 ----
    head = tk.Frame(idle_f, bg=bg)
    head.pack(fill='x', padx=PAD, pady=(px(14), 0))
    tk.Label(head, text='安装 WBOX', font=F_TITLE, fg=tok['fg'], bg=bg,
             anchor='w').pack(fill='x')
    tk.Label(head, text='按需启用功能，随时可以卸载。装完不需要重启。',
             font=F_SUB, fg=tok['t2'], bg=bg, anchor='w').pack(
                 fill='x', pady=(px(4), 0))

    body = tk.Frame(idle_f, bg=bg)
    body.pack(fill='both', expand=True, padx=PAD, pady=(px(14), px(16)))
    tk.Label(body, text='安装位置', font=F_SUB, fg=tok['t3'], bg=bg,
             anchor='w').pack(fill='x', pady=(0, px(6)))

    in_h = px(40)
    in_cv = tk.Canvas(body, width=INNER, height=in_h, bg=bg,
                      highlightthickness=0, bd=0)
    in_cv.pack(fill='x')
    in_cv.create_rectangle(0.5, 0.5, INNER - 0.5, in_h - 0.5,
                           fill=tok['inset'], outline=tok['hair'], width=1,
                           tags='edge')
    f_ic = sprite.cov_cached(('ic', 'folder', px(16), px(16), round(HW, 2)),
                             px(16), px(16),
                             _icon_prims('folder', px(16), px(16), HW))
    in_cv._folder = sprite.sprite(in_cv, px(16), px(16), [(f_ic, tok['t3'])],
                                  tok['inset'])
    in_cv.create_image(px(12), in_h // 2, anchor='w', image=in_cv._folder)
    entry = tk.Entry(in_cv, textvariable=dir_var, font=F_INPUT,
                     fg=tok['fg'], bg=tok['inset'], insertbackground=tok['fg'],
                     relief='flat', bd=0, highlightthickness=0)
    in_cv.create_window(px(34), in_h // 2, anchor='w', window=entry,
                        width=INNER - px(34) - px(84))
    entry.bind('<FocusIn>', lambda e: in_cv.itemconfigure(
        'edge', outline=tok['fg']), add='+')
    entry.bind('<FocusOut>', lambda e: in_cv.itemconfigure(
        'edge', outline=tok['hair']), add='+')

    def browse():
        d = filedialog.askdirectory(
            parent=root, initialdir=os.path.dirname(dir_var.get()))
        if d:
            dir_var.set(os.path.normpath(d))

    br_cv = chip(in_cv, '更改', browse, primary=False, h=26, width=64,
                 bgc=tok['inset'])
    in_cv.create_window(INNER - px(10) - px(64) // 2, in_h // 2, window=br_cv)

    def option_row(text, sub, var):
        rowf = tk.Frame(body, bg=bg, height=px(40))
        rowf.pack(fill='x')
        rowf.pack_propagate(False)
        tk.Label(rowf, text=text, font=F_ROW, fg=tok['fg'],
                 bg=bg).pack(side='left')
        tk.Label(rowf, text=sub, font=F_SUB, fg=tok['t3'],
                 bg=bg).pack(side='left', padx=(px(8), 0))
        sw = Sw(rowf, var, bg)
        sw.pack(side='right')
        rowf.bind('<Button-1>', lambda e, s=sw: s.toggle(), add='+')
        for wd in rowf.winfo_children():
            if wd is not sw:
                wd.bind('<Button-1>', lambda e, s=sw: s.toggle(), add='+')
        tk.Frame(body, bg=tok['hair'], height=1).pack(fill='x')
        return sw

    tk.Frame(body, bg=tok['hair'], height=1).pack(fill='x', pady=(px(14), 0))
    sw_auto = option_row('开机自动运行', '登录后静默启动守护', auto_var)
    sw_launch = option_row('装完立即启动', '打开面板并开始守护', launch_var)

    # 按钮钉在窗底：中部留白落在选项与状态行之间，而不是按钮下面
    foot = tk.Frame(body, bg=bg)
    foot.pack(side='bottom', fill='x')
    tk.Label(body, text='准备就绪，点「安装」开始。', font=F_SUB,
             fg=tok['t3'], bg=bg, anchor='w').pack(
                 side='bottom', fill='x', pady=(0, px(10)))
    cancel_cv = chip(foot, '取消', lambda: on_close(), primary=False,
                     width=88)
    cancel_cv.pack(side='left')
    install_cv = chip(foot, '安装', lambda: do_install(), primary=True,
                      width=int(round((INNER - px(88) - px(12)) / scale)))
    install_cv.pack(side='left', padx=(px(12), 0))

    # ---- 安装中 ----
    busy_card = tk.Frame(busy_f, bg=bg)
    busy_card.place(relx=0.5, rely=0.45, anchor='center')
    tk.Label(busy_card, text='正在安装', font=F_BUSY, fg=tok['fg'],
             bg=bg).pack(pady=(0, px(14)))
    p_w, p_h = INNER, px(4)
    prog_cv = tk.Canvas(busy_card, width=p_w, height=p_h, bg=bg,
                        highlightthickness=0, bd=0)
    prog_cv.pack()
    busy_step = tk.Label(busy_card, text='', font=F_SUB, fg=tok['t2'], bg=bg)
    busy_step.pack(pady=(px(10), px(4)))
    busy_cap = tk.Label(busy_card, text='', font=F_SUB, fg=tok['t3'], bg=bg)
    busy_cap.pack()

    def set_progress(frac, text, detail=''):
        frac = max(0.0, min(1.0, frac))
        prog_cv.delete('all')
        prog_cv.create_rectangle(0, 0, p_w, p_h, fill=tok['fill_2'], outline='')
        fw = int(round(p_w * frac))
        if fw > 0:
            prog_cv.create_rectangle(0, 0, fw, p_h, fill=tok['fg'], outline='')
        busy_step.configure(text='%s · %d%%' % (text.rstrip('…'),
                                                int(round(frac * 100))))
        busy_cap.configure(text=state['target']
                           + ('\\ · ' + detail if detail else ''))

    # ---- 完成 ----
    done_card = tk.Frame(done_f, bg=bg)
    done_card.place(relx=0.5, rely=0.5, anchor='center')
    s = px(40)
    ic = tk.Canvas(done_card, width=s, height=s, bg=bg,
                   highlightthickness=0, bd=0)
    ic.pack(pady=(0, px(12)))
    d_cov = sprite.cov_cached(('dsc', s, s), s, s, [(1, s / 2.0, s / 2.0, s / 2.0)])
    c_cov = sprite.cov_cached(('ic', 'check', s, s, round(HW, 2)), s, s,
                              _icon_prims('check', s, s, HW))
    ic._img = sprite.sprite(ic, s, s, [(d_cov, tok['fg']), (c_cov, tok['on_knob'])], bg)
    ic.create_image(0, 0, anchor='nw', image=ic._img)
    tk.Label(done_card, text='安装完成', font=F_DONE, fg=tok['fg'],
             bg=bg).pack(pady=(0, px(6)))
    done_p = tk.Label(done_card, text='', font=F_SUB, fg=tok['t2'], bg=bg,
                      wraplength=INNER - px(40), justify='center')
    done_p.pack(pady=(0, px(16)))
    open_cv = chip(done_card, '打开设置', lambda: open_settings(),
                   primary=True, width=int(round(INNER / scale)))
    open_cv.pack()
    finish_cv = chip(done_card, '完成', lambda: on_close(), primary=False)
    finish_cv.pack(pady=(px(8), 0))

    # ---- 失败 ----
    fail_card = tk.Frame(fail_f, bg=bg)
    fail_card.place(relx=0.5, rely=0.5, anchor='center')
    tk.Label(fail_card, text='安装未完成', font=F_DONE, fg=tok['fg'],
             bg=bg).pack(pady=(0, px(6)))
    tk.Label(fail_card, text='安装没能完成，文件没有被改动。'
             '可以重试一次，或点「查看详情」看具体原因。',
             font=F_SUB, fg=tok['t2'], bg=bg, wraplength=INNER - px(40),
             justify='center').pack(pady=(0, px(16)))
    retry_cv = chip(fail_card, '重试', lambda: do_install(),
                    primary=True, width=int(round(INNER / scale)))
    retry_cv.pack()
    fail_row = tk.Frame(fail_card, bg=bg)
    fail_row.pack(pady=(px(8), 0))
    detail_cv = chip(fail_row, '查看详情', lambda: show_detail(),
                     primary=False)
    detail_cv.pack(side='left', padx=(0, px(8)))
    exit_cv = chip(fail_row, '退出', lambda: on_close(), primary=False)
    exit_cv.pack(side='left')
    detail_box = tk.Text(fail_card, height=6, width=40, font=F_LOG,
                         bg=tok['inset'], fg=tok['t2'], relief='flat', bd=0,
                         highlightthickness=1, highlightbackground=tok['hair'],
                         highlightcolor=tok['hair'], wrap='word',
                         state='disabled', padx=px(8), pady=px(6))
    detail_shown = {'v': False}

    def show_detail():
        if detail_shown['v']:
            detail_shown['v'] = False
            detail_box.pack_forget()
            detail_cv.lbl.configure(text='查看详情')
            return
        detail_shown['v'] = True
        detail_cv.lbl.configure(text='收起详情')
        detail_box.configure(state='normal')
        detail_box.delete('1.0', 'end')
        detail_box.insert('end', ('\n'.join(log_lines) or '暂无更多细节。')
                          + '\n')
        detail_box.see('end')
        detail_box.configure(state='disabled')
        detail_box.pack(fill='x', padx=PAD, pady=(px(12), 0))

    # ---- 状态机 / 安装线程 ----
    def open_settings():
        t = os.path.normpath(dir_var.get().strip() or DEFAULT_DIR)
        try:
            subprocess.Popen([os.path.join(t, APP_EXE)], cwd=t)
        except Exception:
            pass
        root.destroy()

    def on_close():
        if state['cur'] == 'busy':
            return          # 安装中不许关：半截安装比等几秒更糟
        root.destroy()

    root.protocol('WM_DELETE_WINDOW', on_close)
    root.bind('<Escape>', lambda e: on_close())

    def do_install():
        target = os.path.normpath(dir_var.get().strip() or DEFAULT_DIR)
        state['target'] = target
        del log_lines[:]
        detail_shown['v'] = False
        detail_box.pack_forget()
        detail_cv.lbl.configure(text='查看详情')
        apply_state('busy')
        set_progress(0.0, '正在检查安装环境…')

        def progress(frac, text, detail=''):
            events.append(('step', frac, text, detail))

        # Tk 变量只能在主线程读：先取值再进线程
        auto = bool(auto_var.get())
        launch = bool(launch_var.get())

        def worker():
            try:
                install(target, task, auto, launch, _GuiLog(say, log_path),
                        no_task=no_task, progress=progress)
                events.append(('done',))
            except Exception as e:
                say('安装失败：%r' % (e,))
                events.append(('fail',))

        threading.Thread(target=worker, daemon=True).start()
        pump()

    def pump():
        while events:
            ev = events.popleft()
            if ev[0] == 'step':
                set_progress(ev[1], ev[2], ev[3])
            elif ev[0] == 'done':
                set_progress(1.0, '安装完成')
                done_p.configure(text='已装到 %s。现在可以打开设置，'
                                 '按需启用功能。' % state['target'])
                if launch_var.get():
                    # 「装完立即启动」：守护已由 install() 拉起，这里再开面板
                    try:
                        subprocess.Popen(
                            [os.path.join(state['target'], APP_EXE)],
                            cwd=state['target'])
                    except Exception:
                        pass
                apply_state('done')
                return
            elif ev[0] == 'fail':
                apply_state('fail')
                return
        if state['cur'] == 'busy':
            root.after(80, pump)

    def set_chain(widgets):
        chain = [w for w in widgets if w is not None]

        def step(dir):
            if not chain:
                return 'break'
            try:
                cur = root.focus_get()
            except Exception:
                cur = None
            idx = chain.index(cur) if cur in chain else -1
            chain[(idx + dir) % len(chain)].focus_set()
            return 'break'

        root.bind('<Tab>', lambda e: step(1))
        root.bind('<Shift-Tab>', lambda e: step(-1))

    def apply_state(name):
        state['cur'] = name
        for f in (idle_f, busy_f, done_f, fail_f):
            f.pack_forget()
        {'idle': idle_f, 'busy': busy_f,
         'done': done_f, 'fail': fail_f}[name].pack(fill='both', expand=True)
        root.update_idletasks()
        if name == 'idle':
            set_chain([entry, br_cv, sw_auto, sw_launch,
                       cancel_cv, install_cv])
            entry.focus_set()
        elif name == 'done':
            set_chain([open_cv, finish_cv])
            open_cv.focus_set()
        elif name == 'fail':
            set_chain([retry_cv, detail_cv, exit_cv])
            retry_cv.focus_set()
        else:
            set_chain([])

    apply_state('idle')

    try:
        sw_, sh_ = root.winfo_screenwidth(), root.winfo_screenheight()
        x = max(0, (sw_ - W) // 2)
        y = max(0, (sh_ - H) // 3)
        root.geometry('%dx%d+%d+%d' % (W, H, x, y))
    except Exception:
        root.geometry('%dx%d' % (W, H))
    root.overrideredirect(True)
    root.update_idletasks()

    def apply_shell():
        """任务栏按钮（WS_EX_APPWINDOW）；极简档方窗，不做圆角裁剪。"""
        try:
            u = ctypes.windll.user32
            try:
                hwnd = int(root.wm_frame(), 16)
            except Exception:
                hwnd = root.winfo_id()
            u.GetWindowLongW.restype = ctypes.c_long
            ex = u.GetWindowLongW(ctypes.c_void_p(hwnd), -20)
            u.SetWindowLongW(ctypes.c_void_p(hwnd), -20,
                             ctypes.c_long(ex | 0x00040000))
        except Exception:
            pass

    apply_shell()
    # 测试钩子：GUITEST / 离线探针驱动四态，不必真装一遍
    root.setup_ctl = {'state': apply_state, 'install': do_install,
                      'close': on_close, 'detail': show_detail,
                      'get_state': lambda: state['cur'],
                      'dir': dir_var, 'auto': auto_var, 'launch': launch_var,
                      'sw_auto': sw_auto, 'sw_launch': sw_launch}

    size = {'w': 0, 'h': 0}

    def gtest_close():
        size['w'] = root.winfo_width()
        size['h'] = root.winfo_height()
        root.destroy()

    if selftest:
        root.after(900, gtest_close)
    root.mainloop()
    if selftest:
        print('GUI OK %dx%d (dpi=%d)' % (size['w'], size['h'], dpi))
    return 0


class _GuiLog(object):
    def __init__(self, fn, path=None):
        self.fn = fn
        self.path = path

    def write(self, msg):
        self.fn(msg)
        if self.path:
            try:
                with open(self.path, 'a', encoding='utf-8') as f:
                    f.write(msg + '\n')
            except OSError:
                pass


# ---------------- 入口 ----------------
def main():
    args = arg_map(sys.argv[1:])
    silent = flag(args, 'S') or flag(args, 'SILENT')
    target = resolve_target(args)
    task = resolve_task(args)

    log_path = os.path.join(tempfile.gettempdir(), 'wbox_setup.log')
    if not is_admin():
        # 非管理员：拉起提权副本，自己立即退出（否则会以无权限身份继续跑）
        if ensure_admin():
            return 0
        print('需要管理员权限，请右键“以管理员身份运行”')
        return 1

    if flag(args, 'UNINSTALL'):
        # 控制面板卸载时，卸载程序本身就在安装目录里，先搬到 %TEMP% 再干活
        if not flag(args, 'FROMTEMP') and _relaunch_from_temp(target, task):
            return 0
        log_line('=== 卸载 %s ===' % target, log_path)
        uninstall(target, task, log_path)
        if not silent:
            try:
                ctypes.windll.user32.MessageBoxW(None, '卸载完成：%s' % target,
                                                 'WBOX', 0x40)
            except Exception:
                pass
        return 0

    log_line('=== 安装 %s v%s ===' % (target, VERSION), log_path)
    log_line('安装目录：%s（来源：%s）' % (target, uninstall_core.TARGET_SOURCE), log_path)
    no_task = flag(args, 'NOTASK')
    if silent:
        install(target, task, flag(args, 'AUTO', True), flag(args, 'LAUNCH', False),
                log_path, no_task=no_task)
        return 0
    return gui(target, task, no_task, selftest=flag(args, 'GUITEST'),
               log_path=log_path)


if __name__ == '__main__':
    sys.exit(main() or 0)
