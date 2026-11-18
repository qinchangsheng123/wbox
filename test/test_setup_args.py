# -*- coding: utf-8 -*-
"""安装器 /DIR 参数解析离线测试（对应 P0-7：/DIR 引号与空值）。

只调 arg_map / resolve_target / flag 三个纯函数，不解包、不建任务、不写注册表：
installed_dir() 被替身成空字符串，避免读到本机已有安装痕迹影响断言。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'installer'))
import setup_abg as s
import uninstall_core as uc   # v2.6.0：resolve_target/TARGET_SOURCE 的单一来源

FAILED = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILED.append(msg)


# 本机若装过 ABG，HKLM 里有 InstallLocation = 跑出来的结果会依赖环境。
# 这里统一替身成"没有安装记录"，让 /DIR 为空时必须落到 DEFAULT_DIR。
s.installed_dir = uc.installed_dir = lambda: ''

print('[1] /DIR="C:\\Program Files\\ABG"（引号未剥，直接来自命令行）')
a = s.arg_map(['/S', '/DIR="C:\\Program Files\\ABG"'])
t = s.resolve_target(a)
check(t == r'C:\Program Files\ABG', 'target=%r' % t)
check(uc.TARGET_SOURCE == '/DIR', '来源标记为 /DIR：%s' % uc.TARGET_SOURCE)

print('[2] /DIR=C:\\Program Files\\ABG（CRT 已吃掉引号，argv 里没引号）')
a = s.arg_map(['/DIR=C:\\Program Files\\ABG'])
t = s.resolve_target(a)
check(t == r'C:\Program Files\ABG', 'target=%r' % t)

print('[3] /DIR= 空值 → 回落默认目录，且来源标记不是 /DIR')
a = s.arg_map(['/S', '/DIR='])
t = s.resolve_target(a)
check(t == os.path.normpath(s.DEFAULT_DIR), 'target=%r' % t)
check(not uc.TARGET_SOURCE.startswith('/DIR') and '默认目录' in uc.TARGET_SOURCE,
      '来源=%s（应提示为空并回落）' % uc.TARGET_SOURCE)

print('[4] 完全没有 /DIR → 同样回落默认目录')
a = s.arg_map(['/S', '/AUTO=1'])
t = s.resolve_target(a)
check(t == os.path.normpath(s.DEFAULT_DIR), 'target=%r' % t)
check(not uc.TARGET_SOURCE.startswith('/DIR'), '来源=%s' % uc.TARGET_SOURCE)

print('[5] 单引号 / 大小写 / 首尾空白')
a = s.arg_map(["/dir='D:\\ABG Test'"])
t = s.resolve_target(a)
check(t == r'D:\ABG Test', 'target=%r' % t)
a = s.arg_map(['/DIR=   "C:\\Spaced Dir"   '])
t = s.resolve_target(a)
check(t == r'C:\Spaced Dir', 'target=%r' % t)
a = s.arg_map(['/DiR=C:\\Mixed\\'])
t = s.resolve_target(a)
check(t == r'C:\Mixed', 'normpath 去掉尾部反斜杠：%r' % t)

print('[6] /DIR 只含引号（/DIR=""）等同于空值')
a = s.arg_map(['/DIR=""'])
t = s.resolve_target(a)
check(t == os.path.normpath(s.DEFAULT_DIR), 'target=%r' % t)
check(not uc.TARGET_SOURCE.startswith('/DIR'), '来源=%s' % uc.TARGET_SOURCE)

print('[7] /AUTO=0 必须是 False（字符串 "0" 不能当真值）')
a = s.arg_map(['/S', '/AUTO=0'])
check(s.flag(a, 'AUTO', True) is False, '/AUTO=0 -> False')
a = s.arg_map(['/S', '/AUTO=1'])
check(s.flag(a, 'AUTO', False) is True, '/AUTO=1 -> True')
a = s.arg_map(['/S'])
check(s.flag(a, 'AUTO', True) is True, '未指定时用默认值 True')
check(s.flag(a, 'NOTASK', False) is False, '未指定时用默认值 False')
a = s.arg_map(['/NOTASK'])
check(s.flag(a, 'NOTASK', False) is True, '裸开关 -> True')

print('[8] 带等号的路径只切第一个 =')
a = s.arg_map(['/DIR=C:\\A=B\\C'])
check(a.get('DIR') == r'C:\A=B\C', 'DIR=%r' % a.get('DIR'))

# ---------------- [9] 手写 GUID 解析的字节级等价性 ----------------
# v2.3.0 复核补丁（QA P2-高价值）：setup_abg 原来 `import uuid` 只为
# uuid.UUID(text) 解析
# 一个 16 字节结构，PyInstaller 顺着 uuid -> platform -> ctypes.util 和
# uuid._netbios_getnode() -> win32wnet/netbios 把 pywin32 三件整条链拖进安装器，
# Setup_WBOX.exe 白胖 ≈125 KB。现在改成手写十六进制解析。
# 这里用 uuid.UUID(...).bytes_le 当**独立裁判**：它用的就是 COM 要的内存布局
# （Data1 u32 LE / Data2 u16 LE / Data3 u16 LE / Data4 8 字节原序）。
# 测试文件里 import uuid 无妨 —— 测试不进安装包。
print('[9] _guid 手写解析 == uuid.UUID(...).bytes_le（字节级等价）')
import ctypes   # noqa: E402  （只为把 ctypes 结构体摊平成 16 字节）
import uuid     # noqa: E402


def guid_bytes(g):
    """_GUID 结构体 -> 16 字节内存映像（等价于 C 里 memcpy 出来的那个）。"""
    return ctypes.string_at(ctypes.byref(g), ctypes.sizeof(g))


GUID_CASES = (s._CLSID_SHELLLINK, s._IID_ISHELLLINKW, s._IID_IPERSISTFILE,
              '6B2E9C11-3B4A-4F0E-9C1D-8A7F5E2D4C33')   # 第 4 个是随机样本
for text in GUID_CASES:
    got = guid_bytes(s._guid(text))
    want = uuid.UUID(text).bytes_le
    check(got == want,
          '%s -> %s（uuid 参考 %s）' % (text, got.hex(), want.hex()))

check(guid_bytes(s._guid('{00021401-0000-0000-C000-000000000046}'))
      == guid_bytes(s._guid('00021401-0000-0000-c000-000000000046')),
      '带花括号 / 大写小写不影响结果')
check(guid_bytes(s._guid('00000000-0000-0000-0000-0000000000AB'))[15] == 0xAB,
      'Data4 的 8 个字节保持原序（尾字节落在第 16 字节）')
bad = []
for text in ('', '{123}', 'zzzzzzzz-0000-0000-0000-000000000000',
             '0002140100000000C0000000000000046'):
    try:
        s._guid(text)
        bad.append(text)
    except (ValueError, TypeError):
        pass
check(not bad, '非法 GUID 一律抛异常（漏放行：%r）' % (bad or '无'))

# 防回归：别再让 `import uuid` 回到安装器里（那 125 KB 就是这么来的）。
# 用 AST 判，不用字符串匹配 —— 本文件的注释与 _guid 的 docstring 里都写着
# "uuid" 这个词（是在讲历史），字符串匹配会把解释性文字当成代码。
import ast      # noqa: E402

with open(os.path.join(HERE, '..', 'installer', 'setup_abg.py'),
          encoding='utf-8') as _f:
    _SRC = _f.read()
_tree = ast.parse(_SRC)
_uuid_imports = [
    n for n in ast.walk(_tree)
    if (isinstance(n, ast.Import)
        and any(a.name.split('.')[0] == 'uuid' for a in n.names))
    or (isinstance(n, ast.ImportFrom)
        and (n.module or '').split('.')[0] == 'uuid')]
check(not _uuid_imports,
      'setup_abg.py 里没有 import uuid（否则安装器会拖进 pywin32 三件）')

print('')
print('ALL PASS' if not FAILED else 'FAILED: %d -> %s' % (len(FAILED), FAILED))
sys.exit(1 if FAILED else 0)
