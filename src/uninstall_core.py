# -*- coding: utf-8 -*-
"""卸载与安装共用的系统动作（单一来源，纯标准库）。

v2.6.0 从 installer/setup_abg.py 抽出：卸载入口并进主 exe
（WBOX.exe /uninstall），不再往安装目录放 Uninstall_Wingman.exe
全量副本（旧做法是 Setup 自拷贝，+5.81 MB）。setup_abg 与 abg_main
都 import 本模块，卸载逻辑只此一份。

只依赖标准库：守护 exe 与 Setup 都要冻结它，绝不能拖 tkinter / wmi /
uuid 这类包（见 L2 铁律 6）。winreg 一律函数内惰性 import。
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time

from core_defaults import VERSION

APP_EXE = 'WBOX.exe'
DEFAULT_TASK = 'WBOX'
UNINST_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\WBOX'
DEFAULT_DIR = r'C:\Program Files\WBOX'

# v2.11.0 全量改名前的旧标识：只用于安装器迁移与卸载清残留，不再新建。
LEGACY_EXE = 'Wingman.exe'
LEGACY_TASK = 'Wingman'
LEGACY_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Wingman'
LEGACY_DIR = r'C:\Program Files\Wingman'

# 安装包自带的内置插件（plugins\<id>\）。这个名单只用来区分"我们要覆盖的"
# 与"用户自己装的"：不在名单里的插件目录在升级/卸载时必须保住。
#
# v2.11.3 起本体不再附带插件（插件在独立的 wbox-plugins 仓库分发），所以这里
# 是**空集**：安装器不再覆盖任何插件目录，升级 / 卸载时所有插件都按"用户的"
# 对待 —— 暂存后原样还原、卸载时整份备份。名单保留是接口兼容，不是遗留。
BUILTIN_PLUGINS = ()

# 安装器是提权运行的：外部命令一律用 SystemRoot\System32 下的绝对路径，
# 避免 PATH / 当前目录里的同名 exe 被优先执行。
SYS32 = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32')
TASKLIST = os.path.join(SYS32, 'tasklist.exe')
TASKKILL = os.path.join(SYS32, 'taskkill.exe')
SCHTASKS = os.path.join(SYS32, 'schtasks.exe')
POWERSHELL = os.path.join(SYS32, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
CMD = os.path.join(SYS32, 'cmd.exe')

# resolve_target 的解析来源：'/DIR' / '已安装记录（/DIR 为空）' / '默认目录（/DIR 为空）'。
# 安装开头会把它打进日志，出问题时能一眼看出是参数没传进来还是参数空了。
TARGET_SOURCE = '默认目录'


# ---------------- 基础 ----------------
def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_admin():
    """没有管理员权限就自我提权重开。"""
    if is_admin():
        return True
    try:
        params = ' '.join('"%s"' % a for a in sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', sys.executable, params or None, None, 1)
        return rc and rc > 32
    except Exception:
        return False


def run(args, timeout=120, check=False):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=0x08000000, timeout=timeout)
        out = (p.stdout or b'').decode('mbcs', 'replace').strip()
        return p.returncode, out
    except Exception as e:
        return -1, repr(e)


def ps(script, timeout=60):
    return run([POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
               timeout=timeout)


def log_line(msg, path):
    line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line)
    # 静默安装传文件路径；GUI 传内存 sink（_GuiLog，供「查看详情」）。
    # 只认路径时 GUI 真安装会在第一行日志就 TypeError 进失败态。
    if callable(path):
        try:
            if hasattr(path, 'write'):
                path.write(line)
            else:
                path(line)
        except Exception:
            pass
        return
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except OSError:
        pass


def flag(args, key, default=False):
    """把 /AUTO=0、/AUTO=true 这类参数解析成布尔（字符串 "0" 不能当真值）。"""
    v = args.get(key)
    if v is None:
        return default
    if v is True:
        return True
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


# ---------------- 目标判定 ----------------
def arg_map(argv):
    out = {}
    for a in argv:
        if a.startswith('/') or a.startswith('-'):
            body = a[1:]
            if '=' in body:
                k, v = body.split('=', 1)
                out[k.upper()] = v
            else:
                out[body.upper()] = True
    return out


def installed_dir():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, UNINST_KEY) as k:
            return winreg.QueryValueEx(k, 'InstallLocation')[0]
    except Exception:
        pass
    # v2.11.0 迁移：新键还没有时读旧 Wingman 键；旧默认目录映射到新默认目录，
    # 自定义目录尊重用户当初的选择（标识仍换 exe/任务/键，目录名不动）。
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, LEGACY_KEY) as k:
            old = winreg.QueryValueEx(k, 'InstallLocation')[0]
        if os.path.normcase(old) == os.path.normcase(LEGACY_DIR):
            return DEFAULT_DIR
        return old
    except Exception:
        return ''


def resolve_target(args):
    """解析安装目录。

    /DIR 的值可能带引号（/DIR="C:\\Program Files\\ABG"），也可能为空（/DIR=）。
    处理规则：先去掉首尾的引号与空白；空值回落到"已安装记录 → 默认目录"，
    最后统一 normpath，保证日志与后续 os.makedirs 用的是同一个规范路径。
    """
    global TARGET_SOURCE
    raw = args.get('DIR')
    d = raw if isinstance(raw, str) else ''
    # 引号可能出现在任意一侧（/DIR="C:\A"、/DIR='C:\A'、/DIR=C:\A"）
    for _ in range(3):
        before = d
        d = d.strip().strip('"').strip("'").strip()
        if d == before:
            break
        if not d:
            break
    if d:
        TARGET_SOURCE = '/DIR'
    else:
        inst = installed_dir()
        if inst:
            d, TARGET_SOURCE = inst, '已安装记录（/DIR 为空）'
        else:
            d, TARGET_SOURCE = DEFAULT_DIR, '默认目录（/DIR 为空）'
        print('/DIR 未指定或为空，回落到%s：%s' % (TARGET_SOURCE, d))
    d = os.path.normpath(d)
    return d


def resolve_task(args):
    t = args.get('TASK')
    return t if isinstance(t, str) and t else DEFAULT_TASK


# ---------------- 系统动作 ----------------
def guard_pids(target, exe=None):
    """只找目标目录里的本程序进程（别动别的安装，多版本能共存）。

    exe 可传 LEGACY_EXE：迁移旧 Wingman 安装时旧进程名与新名不同。"""
    t = target.replace('"', '`"')
    proc = os.path.splitext(exe or APP_EXE)[0]
    rc, out = ps('Get-Process %s -ErrorAction SilentlyContinue | '
                 'Where-Object { $_.Path -and $_.Path.StartsWith("%s",'
                 '[StringComparison]::OrdinalIgnoreCase) } | '
                 'ForEach-Object { $_.Id }' % (proc, t), timeout=30)
    return [int(x) for x in (out or '').split() if x.strip().isdigit()]


def close_panel_windows(target, exe=None):
    """请面板自己退出：给它的 Tk 顶层窗口发 WM_CLOSE。

    v2.14.1：stop_guard 以前只写 stop.flag（只有守护认），面板一直活到
    8 秒后被 taskkill /F。强杀不会走任何 finally，托盘图标就留在通知区——
    鼠标不扫过去不掉，用户看到的就是「托盘残余」。Tk 没绑 WM_DELETE_WINDOW，
    收到 WM_CLOSE 会销毁根窗口 → 面板走 <Destroy> → 正常摘图标后退出。
    只发 CLASS=TkTopLevel 的窗口，别碰托盘消息窗（那会把图标先摘了）。
    """
    pids = set(guard_pids(target, exe))
    if not pids:
        return 0
    u = ctypes.WinDLL('user32', use_last_error=True)
    from ctypes import wintypes
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                           ctypes.POINTER(wintypes.DWORD)]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, lp):
        pid = wintypes.DWORD(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        buf = ctypes.create_unicode_buffer(128)
        u.GetClassNameW(hwnd, buf, 128)
        if buf.value.startswith('TkTopLevel'):
            u.PostMessageW(hwnd, 0x0010, 0, 0)       # WM_CLOSE
            found.append(hwnd)
        return True

    u.EnumWindows(_cb, 0)
    return len(found)


def stop_guard(target, exe=None):
    # v2.6.0：卸载入口并进主 exe 后，卸载进程自己也是目标目录里的
    # 主 exe，会被 guard_pids 命中；不排除自身就会在卸载中途自杀。
    me = os.getpid()
    flag_path = os.path.join(target, 'stop.flag')
    try:
        os.makedirs(target, exist_ok=True)
        with open(flag_path, 'w', encoding='ascii') as f:
            f.write('stop')
    except OSError:
        pass
    # 守护认 stop.flag，面板不认：先请面板自己退（正常退出才会摘托盘图标），
    # 免得 8 秒后连同它一起 taskkill /F。
    asked = 0
    for rnd in range(4):
        if rnd:
            time.sleep(1.6)      # 面板可能刚起来还没建窗口，隔一会儿再请一次
        try:
            n = close_panel_windows(target, exe)
        except Exception as e:
            print('[stop] 请求面板退出失败：%r' % (e,))
            n = 0
        asked += n
        if n:
            print('[stop] 已请求 %d 个面板窗口退出（第 %d 轮，累计 %d）'
                  % (n, rnd + 1, asked))
    stopped = False
    for _ in range(8):
        time.sleep(0.4)
        if not [p for p in guard_pids(target, exe) if p != me]:
            stopped = True
            break
    if not stopped:
        for pid in guard_pids(target, exe):
            if pid != me:
                run([TASKKILL, '/F', '/PID', str(pid)])
        time.sleep(1)
        stopped = not [p for p in guard_pids(target, exe) if p != me]
    # 兜底：只要有一个进程没走到正常退出（面板异常死亡 / 最终仍被 taskkill
    # /F），通知区就会留一枚死图标，用户不把鼠标移过去就不掉。装包/卸载结束
    # 时替他扫一遍；nudge 自己会检查「用户 1.2 秒没动键鼠」，不抢指针。
    try:
        import tray_clean
        tray_clean.nudge()
    except Exception:
        pass
    return stopped


def delete_task(task):
    rc, out = run([SCHTASKS, '/Delete', '/TN', task, '/F'])
    if rc == 0:
        return True
    rc2, out2 = ps("Unregister-ScheduledTask -TaskName '%s' -Confirm:$false"
                   % task.replace("'", "''"), timeout=60)
    return rc2 == 0


def user_plugin_names(target):
    """目标目录里**非内置**的插件目录名（即用户自己放进去的插件）。

    只看目录、不看文件：插件必须是 plugins\\<id>\\plugin.json 这种结构，
    散落在 plugins\\ 根上的零散文件不属于任何插件，不参与保留/备份。
    """
    root = os.path.join(target, 'plugins')
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    out = []
    for name in names:
        if name in BUILTIN_PLUGINS:
            continue
        if os.path.isdir(os.path.join(root, name)):
            out.append(name)
    return out


def backup_root():
    """用户插件备份根：%LOCALAPPDATA%\\WBOX（v2.11.0 改名，旧 Wingman 由安装器归并）。"""
    local = os.environ.get('LOCALAPPDATA') or os.path.join(
        os.environ.get('USERPROFILE', ''), 'AppData', 'Local')
    return os.path.join(local, 'WBOX')


def backup_user_plugins(target, log):
    """卸载前把用户自装插件备份到 %LOCALAPPDATA%\\WBOX\\removed-plugins-<时间戳>\\。

    卸载会把整个安装目录删掉，用户自己放进 plugins\\ 的插件不该跟着消失。
    备份失败不阻止卸载（用户点卸载时，删干净比保插件更要紧），但要留日志：
    返回备份目录，没有可备份的内容或失败时返回 ''。
    """
    names = user_plugin_names(target)
    if not names:
        return ''
    dst_root = os.path.join(backup_root(),
                            'removed-plugins-%s' % time.strftime('%Y%m%d-%H%M%S'))
    try:
        os.makedirs(dst_root, exist_ok=True)
    except OSError as e:
        log_line('用户插件备份目录创建失败：%r（继续卸载）' % (e,), log)
        return ''
    saved = []
    for name in names:
        try:
            shutil.copytree(os.path.join(target, 'plugins', name),
                            os.path.join(dst_root, name))
            saved.append(name)
        except Exception as e:
            log_line('用户插件 %s 备份失败：%r（继续卸载）' % (name, e), log)
    if not saved:
        shutil.rmtree(dst_root, ignore_errors=True)
        log_line('没有可备份的用户插件，继续卸载', log)
        return ''
    log_line('已备份 %d 个用户自装插件到：%s' % (len(saved), dst_root), log)
    return dst_root


def write_uninstall_key(target):
    # v2.6.0：UninstallString 指向主 exe 的 /uninstall（卸载逻辑在
    # uninstall_core，随主 exe 冻结）。旧版指向 Uninstall_Wingman.exe
    # 全量副本，那份 +5.81 MB 的拷贝不再放置。
    exe = os.path.join(target, APP_EXE)
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, UNINST_KEY) as k:
            winreg.SetValueEx(k, 'DisplayName', 0, winreg.REG_SZ, 'WBOX')
            winreg.SetValueEx(k, 'DisplayVersion', 0, winreg.REG_SZ, VERSION)
            winreg.SetValueEx(k, 'Publisher', 0, winreg.REG_SZ, 'local')
            winreg.SetValueEx(k, 'InstallLocation', 0, winreg.REG_SZ, target)
            winreg.SetValueEx(k, 'DisplayIcon', 0, winreg.REG_SZ, exe)
            winreg.SetValueEx(k, 'UninstallString', 0, winreg.REG_SZ,
                              '"%s" /uninstall' % exe)
            winreg.SetValueEx(k, 'QuietUninstallString', 0, winreg.REG_SZ,
                              '"%s" /S /uninstall' % exe)
            winreg.SetValueEx(k, 'NoModify', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, 'NoRepair', 0, winreg.REG_DWORD, 1)
        return True
    except Exception:
        return False


def drop_uninstall_key():
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, UNINST_KEY)
        return True
    except Exception:
        return False


def _spawn(args):
    """脱离父进程启动，父进程退出后子进程继续干活。"""
    try:
        subprocess.Popen(args, creationflags=0x00000008)   # DETACHED_PROCESS
        return True
    except Exception:
        return False


def _delete_on_reboot(path):
    """兜底：让系统在下次重启时删掉这个文件（删不掉自己时的最后手段）。"""
    try:
        f = ctypes.windll.kernel32.MoveFileExW
        f.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        f.restype = ctypes.c_int
        return bool(f(path, None, 4))       # MOVEFILE_DELAY_UNTIL_REBOOT
    except Exception:
        return False


def _detached_cmd(script):
    """脱离父进程跑一段 cmd 脚本（父进程退出后继续执行）。

    两个实测坑（2026-11-16，v2.6.0 卸载链验证时定位）：
    1) Popen 列表形式会被 list2cmdline 把内嵌引号转义成 \\"，cmd 不认，
       整条脚本静默失败——必须传整条命令行字符串。
    2) cmd /c 遇到"多引号+特殊字符"会剥掉首尾引号导致语法破碎，
       要再包一层外引号并用 /S 让 cmd 原样执行内层。
    延时载体用 ping 不用 timeout：timeout 在无控制台进程里立即报错退出，
    延时归零，rmdir 会撞上还活着的卸载进程。
    """
    return subprocess.Popen('%s /s /c "%s"' % (CMD, script),
                            creationflags=0x00000008)   # DETACHED_PROCESS


def uninstall(target, task, log):
    # v2.13.0：卸载也走事务状态。卸载比安装更容易被打断（目录里还有正在
    # 退出的守护、杀软锁着文件），下次启动安装器/守护时能识别"删了一半"。
    import install_txn
    prev = install_txn.recover(target, log=lambda m: log_line(m, log))
    if prev.get('action'):
        log_line('上次卸载未正常结束：%s' % prev.get('text'), log)
    txn = install_txn.begin('uninstall', target, task)
    log_line('停止守护', log)
    stop_guard(target)
    # 用户自装插件先备份：下一步会把整个安装目录删掉，插件作者的东西不该跟着没。
    backup = backup_user_plugins(target, log)
    if backup:
        log_line('用户插件备份位置：%s' % backup, log)
    install_txn.step(txn, 'backup_plugins', 'task')
    log_line('删除计划任务 %s' % task, log)
    delete_task(task)
    install_txn.step(txn, 'task_deleted', 'shortcuts')
    sm_root = os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'),
                           'Microsoft', 'Windows', 'Start Menu', 'Programs')
    desktop = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop')
    # v2.10.0 起入口叫 WBOX；v2.11.0 系统标识也全量改名，
    # 旧 Wingman 的开始菜单目录 / 桌面 lnk / 任务 / 注册表键一并清残留。
    for p in (os.path.join(sm_root, 'WBOX'), os.path.join(sm_root, 'Wingman'),
              os.path.join(desktop, 'WBOX 设置.lnk'),
              os.path.join(desktop, '僚机 Wingman 设置.lnk')):
        try:
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            elif os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass
    delete_task(LEGACY_TASK)
    try:
        import winreg
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, LEGACY_KEY)
    except Exception:
        pass
    drop_uninstall_key()
    log_line('已清理快捷方式与注册表项', log)
    install_txn.step(txn, 'registry_dropped', 'files')

    me = os.path.abspath(sys.executable)
    inside = os.path.normcase(me).startswith(os.path.normcase(target) + os.sep)
    if inside:
        # 自己就在目标目录里（WBOX.exe /uninstall 主路径）：onedir 的 exe
        # 不能像 onefile 那样自拷贝到 %TEMP% 再跑（缺兄弟 dll 起不来），
        # 交给脱离的 cmd 延时删：本进程退出后 exe 解锁，rmdir 才能成功。
        script = '"%s" -n 3 127.0.0.1 >nul & rmdir /s /q "%s"' % (
            os.path.join(SYS32, 'ping.exe'), target)
        _detached_cmd(script)
        log_line('目录将在 2 秒后删除：%s' % target, log)
        # 延时删除是另一条进程在跑：事务日志**故意不清理**。下次运行如果
        # 目录还在，recover() 会报"卸载未删干净"，而不是假装成功。
        install_txn.step(txn, 'files_deferred', 'deferred')
    else:
        shutil.rmtree(target, ignore_errors=True)
        log_line('已删除目录：%s' % target, log)
        install_txn.step(txn, 'files_deleted', 'done')
        install_txn.finish(txn)
        # 自己是从 %TEMP% 跑起来的临时副本（Setup onefile 路径），退出后把自个儿删掉
        if os.path.normcase(me).startswith(os.path.normcase(tempfile.gettempdir())):
            _detached_cmd('"%s" -n 3 127.0.0.1 >nul & del /f /q "%s"'
                          % (os.path.join(SYS32, 'ping.exe'), me))
            _delete_on_reboot(me)
    return True


def main(argv=None):
    """WBOX.exe /uninstall 入口（v2.6.0，注册表 UninstallString 指向这里）。"""
    args = arg_map(sys.argv[1:] if argv is None else argv)
    if not ensure_admin():
        return 1
    target = resolve_target(args)
    task = resolve_task(args)
    log = os.path.join(tempfile.gettempdir(), 'wbox_setup.log')
    silent = flag(args, 'S')
    if not silent:
        rc = ctypes.windll.user32.MessageBoxW(
            None,
            '确定卸载 WBOX？\n\n安装目录：%s\n用户自装插件会先备份到'
            ' %%LOCALAPPDATA%%\\WBOX。' % target,
            'WBOX 卸载', 0x24)      # MB_YESNO | MB_ICONQUESTION
        if rc != 6:                          # IDYES
            return 0
    log_line('卸载开始 target=%s task=%s（来源：%s）' % (target, task, TARGET_SOURCE), log)
    ok = uninstall(target, task, log)
    log_line('卸载完成' if ok else '卸载未完成', log)
    if not silent:
        ctypes.windll.user32.MessageBoxW(None, '卸载完成。', 'WBOX', 0x40)
    return 0 if ok else 1