# -*- coding: utf-8 -*-
"""内存长窗口探针（手工测量脚本，不是测试，不进套件）。

对外部进程按固定间隔采 WS / 私有字节 / 句柄 / 线程 / GDI / USER 对象，
首尾各dump一次模块列表做归因：tkinter / WMI / PDH 这类原生 DLL 的内存
tracemalloc 看不见，必须结合模块列表与工作集一起读（文章「性能检测体系」
一条的本机落地）。句柄/线程/GDI 单调增长是泄漏指纹，WS 缓增归因用。

默认 100 轮 x 3 s（铁律：短窗口读数一律不信）。Python 层分配归因请另在
进程内用 tracemalloc 跑目标模块，本脚本不替代它。

用法: C:\\Python38\\python.exe test\\perf_mem_probe.py <pid|进程名> [轮数] [间隔秒] [csv路径]
例:   C:\\Python38\\python.exe test\\perf_mem_probe.py WBOX 100 3 D:/06-Temp/mem.csv
退出码: 0 正常采完; 2 目标进程中途退出或打不开。
"""
import ctypes
import sys
import time
from ctypes import wintypes

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)
u32 = ctypes.WinDLL('user32', use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
LIST_MODULES_ALL = 0x03
TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPTHREAD = 0x00000004
GR_GDIOBJECTS = 0
GR_USEROBJECTS = 1


class PMC_EX(ctypes.Structure):
    _fields_ = [
        ('cb', wintypes.DWORD),
        ('PageFaultCount', wintypes.DWORD),
        ('PeakWorkingSetSize', ctypes.c_size_t),
        ('WorkingSetSize', ctypes.c_size_t),
        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPagedPoolUsage', ctypes.c_size_t),
        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
        ('PagefileUsage', ctypes.c_size_t),
        ('PeakPagefileUsage', ctypes.c_size_t),
        ('PrivateUsage', ctypes.c_size_t),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
        ('th32ModuleID', wintypes.DWORD),
        ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
        ('szExeFile', ctypes.c_wchar * 260),
    ]


class THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD),
        ('cntUsage', wintypes.DWORD),
        ('th32ThreadID', wintypes.DWORD),
        ('th32OwnerProcessID', wintypes.DWORD),
        ('tpBasePri', ctypes.c_long),
        ('tpDeltaPri', ctypes.c_long),
        ('dwFlags', wintypes.DWORD),
    ]


k32.OpenProcess.restype = wintypes.HANDLE
k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.GetProcessHandleCount.restype = wintypes.BOOL
k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Thread32First.restype = wintypes.BOOL
k32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
k32.Thread32Next.restype = wintypes.BOOL
k32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
u32.GetGuiResources.restype = wintypes.DWORD
u32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC_EX), wintypes.DWORD]
psapi.EnumProcessModulesEx.restype = wintypes.BOOL
psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
psapi.GetModuleBaseNameW.restype = wintypes.DWORD
psapi.GetModuleBaseNameW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD]


def find_pid(name):
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.c_void_p(-1).value:
        return None
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == name.lower() or e.szExeFile.lower() == name.lower() + '.exe':
                return e.th32ProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return None


def modules(h):
    arr = (ctypes.c_void_p * 2048)()
    need = wintypes.DWORD()
    if not psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(need), LIST_MODULES_ALL):
        return []
    n = need.value // ctypes.sizeof(ctypes.c_void_p)
    out = []
    buf = ctypes.create_unicode_buffer(260)
    for i in range(min(n, 2048)):
        if psapi.GetModuleBaseNameW(h, arr[i], buf, 260):
            out.append(buf.value)
    return sorted(out)


def sample(h, pid):
    c = PMC_EX()
    c.cb = ctypes.sizeof(c)
    if not psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
        return None
    hc = wintypes.DWORD()
    handles = hc.value if k32.GetProcessHandleCount(h, ctypes.byref(hc)) else -1
    threads = -1
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap != ctypes.c_void_p(-1).value:
        try:
            te = THREADENTRY32()
            te.dwSize = ctypes.sizeof(te)
            n = 0
            ok = k32.Thread32First(snap, ctypes.byref(te))
            while ok:
                if te.th32OwnerProcessID == pid:
                    n += 1
                ok = k32.Thread32Next(snap, ctypes.byref(te))
            threads = n
        finally:
            k32.CloseHandle(snap)
    gdi = u32.GetGuiResources(h, GR_GDIOBJECTS)
    user = u32.GetGuiResources(h, GR_USEROBJECTS)
    return c.WorkingSetSize, c.PrivateUsage, handles, threads, gdi, user


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else 3.0
    csv_path = sys.argv[4] if len(sys.argv) > 4 else None
    pid = int(target) if target.isdigit() else find_pid(target)
    if not pid:
        print('找不到进程: %s' % target)
        return 2
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        print('OpenProcess 失败 pid=%d err=%d' % (pid, ctypes.get_last_error()))
        return 2
    rows = []
    csv_f = open(csv_path, 'w', newline='') if csv_path else None
    if csv_f:
        csv_f.write('t,ws_bytes,private_bytes,handles,threads,gdi,user\n')
    mods0 = modules(h)
    print('pid=%d rounds=%d interval=%.1fs 模块=%d' % (pid, rounds, interval, len(mods0)))
    try:
        for i in range(rounds):
            s = sample(h, pid)
            if s is None:
                print('第 %d 轮采样失败（进程退出？）' % (i + 1))
                return 2
            ws, priv, handles, threads, gdi, user = s
            rows.append(s)
            if csv_f:
                csv_f.write('%.1f,%d,%d,%d,%d,%d,%d\n'
                            % (i * interval, ws, priv, handles, threads, gdi, user))
            if (i + 1) % 10 == 0 or i == 0:
                print('[%3d/%d] WS=%6.2f MB Priv=%6.2f MB H=%d T=%d GDI=%d USER=%d'
                      % (i + 1, rounds, ws / 1048576.0, priv / 1048576.0,
                         handles, threads, gdi, user))
            if i + 1 < rounds:
                time.sleep(interval)
    finally:
        if csv_f:
            csv_f.close()
        mods1 = modules(h)
        k32.CloseHandle(h)
    mb = 1048576.0
    print('--- 汇总（%d 轮 x %.1fs）---' % (len(rows), interval))
    names = ('WS(MB)', 'Priv(MB)', 'Handles', 'Threads', 'GDI', 'USER')
    for col in range(6):
        vals = [r[col] for r in rows]
        div = mb if col < 2 else 1.0
        print('%-8s min/avg/max = %.2f / %.2f / %.2f  首末差 %+.2f'
              % (names[col], min(vals) / div, sum(vals) / len(vals) / div,
                 max(vals) / div, (vals[-1] - vals[0]) / div))
    added = sorted(set(mods1) - set(mods0))
    gone = sorted(set(mods0) - set(mods1))
    if added:
        print('窗口内新增模块: %s' % ', '.join(added))
    if gone:
        print('窗口内卸载模块: %s' % ', '.join(gone))
    for flag in ('_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll', 'wbemprox.dll', 'pdh.dll'):
        print('  %-14s %s' % (flag, '在' if flag in mods1 else '不在'))
    return 0


if __name__ == '__main__':
    sys.exit(main())