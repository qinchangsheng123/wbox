"""进程时间线采样器：每 2 秒记录一次相关进程，用于核对 WBOX 行为。
用法: python monitor.py <采样秒数> <输出文件>
"""
import ctypes
import sys
import time
from ctypes import wintypes

TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
k32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
k32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
k32.CloseHandle.argtypes = [wintypes.HANDLE]


def snapshot():
    h = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if h == INVALID_HANDLE_VALUE or not h:
        return {}
    out = {}
    e = PROCESSENTRY32()
    e.dwSize = ctypes.sizeof(PROCESSENTRY32)
    if k32.Process32First(h, ctypes.byref(e)):
        while True:
            name = e.szExeFile.decode("mbcs", "replace").lower()
            out.setdefault(name, []).append(e.th32ProcessID)
            if not k32.Process32Next(h, ctypes.byref(e)):
                break
    k32.CloseHandle(h)
    return out


AB = {"wbox.exe", "wingman.exe"}   # 迁移期旧名守护可能仍在跑
RTSS = {"rtss.exe", "rtsshooksloader64.exe", "rtsshooksloader.exe",
        "encoderserver.exe", "rtsshal64.exe", "rtsscmdpipeline.exe"}
ACE = {"sguard64.exe", "sguardsvc64.exe", "ace-service64.exe", "ace-tray.exe",
       "ace-helper.exe", "ace-base64.exe", "sguardupdate64.exe", "sguardupdate.exe"}
GAME = {"tgame.exe", "crossfire.exe", "vampire crawlers.exe", "cs2.exe",
        "witcher3.exe", "tombraider.exe", "deathstranding.exe",
        "hogwartslegacy.exe", "flotsam.exe", "pathofexile2.exe",
        "wardogsclient-win64-shipping.exe"}


def fmt(snap, keys):
    hits = []
    for k in sorted(keys):
        for pid in snap.get(k, []):
            hits.append("%s(%d)" % (k, pid))
    return " ".join(hits) if hits else "none"


def main():
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60
    out = sys.argv[2] if len(sys.argv) > 2 else r"C:\Windows\Temp\monitor.log"
    end = time.time() + dur
    with open(out, "w", encoding="utf-8") as f:
        while time.time() < end:
            s = snapshot()
            f.write("%s | AB[%s] | RTSS[%s] | ACE[%s] | GAME[%s]\n" % (
                time.strftime("%H:%M:%S"), fmt(s, AB), fmt(s, RTSS),
                fmt(s, ACE), fmt(s, GAME)))
            f.flush()
            time.sleep(2)


if __name__ == "__main__":
    main()
