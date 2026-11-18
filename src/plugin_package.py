# -*- coding: utf-8 -*-
"""WBOX 插件包规范 v1：统一 .zip 分发 / 校验 / 安装 / 卸载 / 打包。

为什么单独一个模块：
  - 面板要点「＋」选 zip 装插件、点「垃圾桶」卸插件，守护不需要这段逻辑；
  - 但校验规则必须只有一份（面板装进去的东西，守护下一轮就会 import），
    散在 settings_gui.py 里迟早会和安装器的插件保留逻辑分叉。

包格式（wbox-plugin-zip-v1）：
    <id>-vX.Y.Z.zip
      └─ <id>/plugin.json         必填，id 必须与目录名一致
         <id>/plugin.py           默认入口；manifest.entry 可改
         <id>/...                 只允许白名单扩展名的数据/代码文件

安全边界（插件是管理员进程内的 Python 代码 = 完全信任）：
  - 不接受散装文件、不接受多个顶层目录、不接受路径穿越/绝对路径/盘符/软链；
  - 不接受可执行文件与脚本（exe/dll/pyd/bat/cmd/ps1/vbs/lnk/reg/...）；
  - 限制文件数、单文件大小、解压总量，避免 zip bomb 把安装目录撑爆；
  - 所有校验通过后才落到 plugins\\<id>\\，失败不留下半成品目录。

信任边界（v2.11.3 起在安装框里明说，README/插件规范同步）：
  校验能挡住"包写坏了/塞了可执行文件/想逃出目录"，**挡不住插件代码本身的行为**。
  所以产品侧做两件事，让用户的判断有依据：
  1. 展示来源：manifest 的 publisher / homepage / permissions 原样呈现，官方包与
     第三方包分开措辞；未声明发布者的包显示「来源不明」。
  2. 校验完整性：zip 的 SHA-256 直接算给用户看；同目录若有 `<包名>.zip.sha256`
     侧车（sha256sum 格式），不一致直接拒绝安装——包被替换/传坏时能发现。
     sidecar 只能证明"和发布时同一份文件"，不能证明"发布者是谁"，界面文案不夸大。

安装事务（v2.11.3）：安装前写 `.plugin-install.json` 事务日志，异常即时回滚；
下次启动（守护 / 面板）调 recover() 收尾被打断的安装：目标目录缺失就搬回备份，
没有备份但有完整暂存目录就补完，接口不留半成品、也不留孤儿暂存目录。
"""
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile

import plugin_api
import plugin_host

FORMAT_ID = 'wbox-plugin-zip-v1'
MANIFEST = 'plugin.json'
RELOAD_FLAG = 'plugins_reload.flag'
ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]{1,31}$')

# 安装事务：日志落在 <base>\\，暂存目录同盘，保证 shutil.move 是原子的重命名。
JOURNAL = '.plugin-install.json'
STAGING_PREFIX = '.plugin-staging-'
STAGING_STALE_SEC = 6 * 3600.0     # 没被任何日志引用的暂存目录，超此时长才清
SIDECAR_SUFFIX = '.sha256'

# 官方发布者标识。只在 manifest 的 publisher 与内置 id 同时命中时才叫「官方」，
# 不靠"包里有 builtin: true"单独下结论（那个字段任何人都能写）。
OFFICIAL_PUBLISHER = 'WBOX'

# 声明式权限词表：**只是让用户看清插件要碰什么，内核不做任何限制**。
# 未登记的权限键原样展示（不拒绝），否则第三方插件写个新词就装不进去。
PERMISSION_LABELS = {
    'process.watch': '监视进程启动与退出',
    'process.kill': '结束进程',
    'process.priority': '调整进程优先级',
    'window.close': '关闭窗口/弹窗',
    'files.plugin': '读写自己的插件目录',
    'files.any': '读写任意文件',
    'registry.write': '修改注册表',
    'network': '联网收发数据',
    'shell.run': '启动程序或调用系统命令',
    'config.write': '修改 WBOX 配置',
    'ui.panel': '向面板添加界面元素',
}
MAX_PERMISSIONS = 16

# 插件里允许出现的东西：源码、清单、说明、小型静态资源。
# 白名单而不是黑名单：新增可执行格式时默认拒之门外，不会漏判。
ALLOWED_EXT = frozenset((
    '.py', '.json', '.md', '.txt', '.cfg', '.ini', '.yaml', '.yml',
    '.csv', '.xml', '.png', '.ico',
))
# 打包时从插件工作目录里排除（运行期产物 / 私有状态）。
PACK_SKIP_DIRS = frozenset(('__pycache__', 'data', '.git', '.idea'))
PACK_SKIP_SUFFIX = ('.pyc', '.pyo', '.bak', '.tmp', '.log', '.zip', '.flag')
PACK_SKIP_NAMES = frozenset(('Thumbs.db', 'desktop.ini'))

MAX_FILES = 64
MAX_MEMBER_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024

BUILTIN_IDS = frozenset(('afterburner', 'ace', 'popup', 'gamepp'))
# 随安装包分发的全部插件 id（desktop_hud 不在内核硬编码名单里，但同源发布）。
# 只用于安装框的措辞，不参与任何加载/删除判定。
OFFICIAL_IDS = BUILTIN_IDS | frozenset(('desktop_hud',))


class PackageError(Exception):
    """插件包不合格 / 安装卸载失败，消息可直接展示给用户。"""


def _parts(name):
    """zip 条目名 -> 安全的相对路径分段；任何可疑写法一律拒绝。"""
    if not isinstance(name, str) or not name or '\x00' in name:
        raise PackageError('压缩包里有非法条目名')
    norm = name.replace('\\', '/')
    if norm.startswith('/') or re.match(r'^[A-Za-z]:', norm):
        raise PackageError('压缩包里有绝对路径条目：%s' % name)
    parts = [p for p in norm.split('/') if p not in ('', '.')]
    if not parts:
        raise PackageError('压缩包里有空条目：%s' % name)
    if any(p == '..' for p in parts):
        raise PackageError('压缩包里有路径穿越条目：%s' % name)
    if any(p.endswith('.') or p.endswith(' ') for p in parts):
        # Windows 会静默去掉尾点/尾空格，可能导致落地文件与清单不一致
        raise PackageError('压缩包里有 Windows 非法名称：%s' % name)
    return parts


def _check_ext(rel):
    ext = os.path.splitext(rel)[1].lower()
    if ext not in ALLOWED_EXT:
        raise PackageError('插件包不允许的文件类型 %r（%s）' % (ext or '无扩展名', rel))
    return ext


def archive_sha256(path, chunk=262144):
    """整包 SHA-256（十六进制小写）。分块读，8 MB 上限下开销可忽略。"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _sidecar_path(path):
    return path + SIDECAR_SUFFIX


def read_sidecar(path):
    """读 `<zip>.sha256` 侧车，返回 (hash 或 None, 错误说明 或 '')。

    接受 sha256sum 的两种写法：`<64 hex>` 或 `<64 hex>  <文件名>`。
    文件不存在 -> (None, '')；文件存在但内容不是合法 hash -> (None, 原因)。
    """
    sp = _sidecar_path(path)
    if not os.path.isfile(sp):
        return None, ''
    try:
        with open(sp, 'r', encoding='utf-8-sig') as f:
            text = f.read(4096)
    except OSError as e:
        return None, '读不到 %s：%r' % (os.path.basename(sp), e)
    token = text.strip().split()[0] if text.strip() else ''
    token = token.lower()
    if len(token) != 64 or any(c not in '0123456789abcdef' for c in token):
        return None, '%s 不是合法的 SHA-256（应为 64 位十六进制）' % os.path.basename(sp)
    return token, ''


def _manifest_permissions(raw):
    """manifest.permissions -> (已登记权限, 未登记权限, 是否写坏了)。

    写坏的（不是字符串数组）按"未声明"处理并标 invalid，界面据此提示作者写错，
    但不因此拒绝安装：权限本来就是声明式，内核没有任何强制点。
    """
    val = raw.get('permissions')
    if val is None:
        return [], [], False
    if not isinstance(val, list):
        return [], [], True
    known, unknown = [], []
    for item in val:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        if not key or key in known or key in unknown:
            continue
        if key in PERMISSION_LABELS:
            known.append(key)
        else:
            unknown.append(key[:32])
        if len(known) + len(unknown) >= MAX_PERMISSIONS:
            break
    return known, unknown, False


def trust_level(pid, publisher):
    """'official' / 'third_party' / 'unknown'。

    官方 = 内置 id + 官方发布者标识。缺 publisher 一律 'unknown'（来源不明），
    界面要按这个措辞提示，不能因为"名字眼熟"就算官方。
    """
    pub = (publisher or '').strip()
    if not pub:
        return 'unknown'
    if pid in OFFICIAL_IDS and pub.lower() == OFFICIAL_PUBLISHER.lower():
        return 'official'
    return 'third_party'


def describe_install(info):
    """安装确认框正文：发布者 / 来源 / 权限 / 完整性 + 一句信任边界说明。

    只陈述包与 manifest 里的事实，不替用户判断"安全"。字符串给弹框直接用。
    """
    trust = info.get('trust') or trust_level(info.get('id'), info.get('publisher'))
    pub = (info.get('publisher') or '').strip()
    if not pub:
        pub_line = '发布者：未声明（来源不明）'
    elif trust == 'official':
        pub_line = '发布者：%s（随安装包分发）' % pub
    else:
        pub_line = '发布者：%s（第三方提供）' % pub

    lines = [pub_line]
    if info.get('homepage'):
        lines.append('来源：%s' % info['homepage'])
    lines.append('版本：%s · %d 个文件 · %.1f KB'
                 % (info.get('version') or '-', len(info.get('members') or ()),
                    (info.get('total_bytes') or 0) / 1024.0))

    known = list(info.get('known_permissions') or ())
    unknown = list(info.get('unknown_permissions') or ())
    if info.get('permissions_invalid'):
        lines.append('声明权限：字段格式不对（应为字符串数组），按未声明处理')
    elif known or unknown:
        parts = [PERMISSION_LABELS[k] for k in known]
        parts += ['%s（未登记）' % k for k in unknown]
        lines.append('声明权限：%s' % '、'.join(parts))
    else:
        lines.append('声明权限：未声明')

    integrity = info.get('integrity')
    if integrity == 'sidecar_ok':
        lines.append('包完整性：与同目录 .sha256 一致')
    elif integrity == 'sidecar_bad':
        lines.append('包完整性：同目录 .sha256 无法解析，本次未做核对')
    else:
        lines.append('包完整性：包未附带 .sha256，无法核对是否被替换')
    sha = info.get('sha256') or ''
    if sha:
        lines.append('SHA-256：%s…%s' % (sha[:24], sha[-8:]))

    lines.append('')
    lines.append('插件代码以管理员权限在内核进程里运行，安装即等于完全信任它的作者。')
    lines.append('请只安装你了解来源的插件；发布者与权限是插件自己声明的，不能当成担保。')
    return '\n'.join(lines)


def inspect_zip(path):
    """只读校验一个插件 zip，返回包信息（不解压、不写盘）。

    返回 dict: {id, name, version, root, entry, members, total_bytes, builtin}
    任何不合格都抛 PackageError，消息是中文、可直接弹框。
    """
    if not os.path.isfile(path):
        raise PackageError('找不到插件包：%s' % path)
    if os.path.getsize(path) > MAX_ARCHIVE_BYTES:
        raise PackageError('插件包超过 %d MB，拒绝安装' % (MAX_ARCHIVE_BYTES // 1048576))
    if not zipfile.is_zipfile(path):
        raise PackageError('这不是有效的 zip 插件包')
    try:
        zf = zipfile.ZipFile(path, 'r')
    except (OSError, zipfile.BadZipFile) as e:
        raise PackageError('打不开插件包：%r' % (e,))

    members = []
    total = 0
    roots = set()
    try:
        for info in zf.infolist():
            if info.flag_bits & 0x1:
                raise PackageError('插件包含加密条目，拒绝安装')
            mode = info.external_attr >> 16
            if mode and stat.S_ISLNK(mode):
                raise PackageError('插件包含符号链接，拒绝安装')
            parts = _parts(info.filename)
            if info.is_dir():
                roots.add(parts[0])
                continue
            roots.add(parts[0])
            rel = '/'.join(parts)
            _check_ext(rel)
            if info.file_size > MAX_MEMBER_BYTES:
                raise PackageError('单个文件超过 1 MB：%s' % rel)
            total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise PackageError('解压后总量超过 2 MB，拒绝安装')
            members.append((rel, info.file_size))
        if len(members) > MAX_FILES:
            raise PackageError('文件数超过 %d 个，拒绝安装' % MAX_FILES)
        if len(roots) != 1:
            raise PackageError('插件包必须只有一个顶层目录（当前 %d 个）' % len(roots))
        root = next(iter(roots))
        if not ID_PATTERN.match(root):
            raise PackageError('顶层目录名不是合法插件 id（%s）' % root)
        manifest_rel = root + '/' + MANIFEST
        if manifest_rel not in [m[0] for m in members]:
            raise PackageError('插件包缺少 <%s>/%s' % (root, MANIFEST))
        try:
            raw = json.loads(zf.read(manifest_rel).decode('utf-8-sig'))
        except (ValueError, UnicodeDecodeError, KeyError) as e:
            raise PackageError('plugin.json 不是有效 UTF-8 JSON：%r' % (e,))
        if not isinstance(raw, dict):
            raise PackageError('plugin.json 顶层必须是对象')
        for key in ('id', 'name', 'version', 'api'):
            if key not in raw:
                raise PackageError('plugin.json 缺少必填字段 %s' % key)
        pid = raw.get('id')
        if not isinstance(pid, str) or not ID_PATTERN.match(pid):
            raise PackageError('plugin.json 的 id 非法：%r' % (pid,))
        if pid != root:
            raise PackageError('plugin.json 的 id (%s) 与顶层目录 (%s) 不一致' % (pid, root))
        api = raw.get('api')
        if isinstance(api, bool) or not isinstance(api, int) or api <= 0:
            raise PackageError('plugin.json 的 api 必须是正整数')
        if api > plugin_api.API_VERSION:
            raise PackageError('插件要求 api=%d，当前内核只支持 api=%d' %
                               (api, plugin_api.API_VERSION))
        spec = raw.get('entry') or plugin_host.DEFAULT_ENTRY
        if not isinstance(spec, str):
            raise PackageError('plugin.json 的 entry 必须是字符串')
        entry_file = spec.split(':', 1)[0].strip().replace('\\', '/')
        entry_rel = root + '/' + entry_file.lstrip('/')
        if entry_rel not in [m[0] for m in members]:
            raise PackageError('插件包缺少入口文件 <%s>/%s' % (root, entry_file))

        # --- 来源与权限（可选字段；写坏了要拒绝，缺省则如实标"未声明"）---
        pub = raw.get('publisher')
        if pub is not None and not isinstance(pub, str):
            raise PackageError('plugin.json 的 publisher 必须是字符串')
        publisher = (pub or '').strip()[:64]
        home = raw.get('homepage')
        if home is not None and not isinstance(home, str):
            raise PackageError('plugin.json 的 homepage 必须是字符串')
        homepage = (home or '').strip()[:200]
        if homepage and not re.match(r'^https?://', homepage, re.I):
            raise PackageError('plugin.json 的 homepage 只接受 http(s) 链接：%r'
                               % (homepage,))
        known_perms, unknown_perms, perms_invalid = _manifest_permissions(raw)

        # --- 完整性：整包 SHA-256 + 可选侧车比对 ---
        sha = archive_sha256(path)
        side, side_err = read_sidecar(path)
        if side_err:
            integrity = 'sidecar_bad'
        elif side:
            if side != sha:
                raise PackageError(
                    '插件包与同目录的 .sha256 不一致：文件可能被替换或传输损坏，'
                    '请重新获取原包')
            integrity = 'sidecar_ok'
        else:
            integrity = 'sidecar_missing'

        return {
            'id': pid,
            'name': str(raw.get('name') or pid),
            'version': str(raw.get('version')),
            'root': root,
            'entry': entry_file,
            'members': members,
            'total_bytes': total,
            'builtin': pid in BUILTIN_IDS,
            'format': FORMAT_ID,
            'publisher': publisher,
            'homepage': homepage,
            'known_permissions': known_perms,
            'unknown_permissions': unknown_perms,
            'permissions_invalid': perms_invalid,
            'permissions': known_perms + unknown_perms,
            'trust': trust_level(pid, publisher),
            'sha256': sha,
            'integrity': integrity,
        }
    finally:
        zf.close()


def _backup_root(base):
    """备份落点：%LOCALAPPDATA%\\WBOX\\<用途>-<时间戳>\\，失败回退安装目录旁。"""
    local = os.environ.get('LOCALAPPDATA') or ''
    parent = os.path.join(local, 'WBOX') if local else os.path.join(base, '.wbox-backups')
    return parent


def _backup_dir(base, purpose):
    """每个插件一个子目录，用 shutil.move 而不是删除：删错还能捡回来。"""
    root = os.path.join(_backup_root(base), '%s-%s' %
                        (purpose, time.strftime('%Y%m%d-%H%M%S')))
    os.makedirs(root, exist_ok=True)
    return root


def _remove_tree(path):
    """只删自己刚建的临时目录，逐个文件删（本机策略会拦 Remove-Item -Include）。"""
    if not os.path.isdir(path):
        return
    for dp, dns, fns in os.walk(path, topdown=False):
        for fn in fns:
            try:
                os.remove(os.path.join(dp, fn))
            except OSError:
                pass
        for dn in dns:
            try:
                os.rmdir(os.path.join(dp, dn))
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def _check_id(pid):
    if not isinstance(pid, str) or not ID_PATTERN.match(pid):
        raise PackageError('非法插件 id：%r' % (pid,))
    return pid


# ---------------------------------------------------------------- 安装事务
def _journal_path(base):
    return os.path.join(base, JOURNAL)


def _write_journal(base, data):
    """原子写事务日志。写不进去不阻断安装：最坏情况是崩溃时少数一条回滚线索。"""
    path = _journal_path(base)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _read_journal(base):
    try:
        with open(_journal_path(base), 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clear_journal(base):
    for path in (_journal_path(base), _journal_path(base) + '.tmp'):
        try:
            os.remove(path)
        except OSError:
            pass


def _purge_staging(base, keep=(), note=None):
    """清掉没人认领的暂存目录（只清超过 6 小时的，避免误删正在进行的安装）。"""
    removed = []
    now = time.time()
    try:
        names = os.listdir(base)
    except OSError:
        return removed
    keep_real = set(os.path.abspath(k) for k in keep if k)
    for name in names:
        if not name.startswith(STAGING_PREFIX):
            continue
        path = os.path.join(base, name)
        if os.path.abspath(path) in keep_real or not os.path.isdir(path):
            continue
        try:
            if now - os.path.getmtime(path) < STAGING_STALE_SEC:
                continue
        except OSError:
            continue
        _remove_tree(path)
        removed.append(path)
    if removed and callable(note):
        note('[plugin-package] 清理 %d 个陈旧暂存目录' % len(removed))
    return removed


def _looks_installed(path, pid):
    """暂存目录里是否有一份能直接换入的完整插件（崩溃在换入前时用它补完）。"""
    root = os.path.join(path, pid)
    return (os.path.isfile(os.path.join(root, MANIFEST))
            and os.path.isfile(os.path.join(root, 'plugin.py')))


def recover(plugins_dir, base=None, log=None, fresh_sec=90.0, force=False):
    """收尾上一次被打断的安装。返回一行 dict（无动作时 action 为空）。

    判定只看文件系统现状，不信日志里的 state（崩溃点可能恰好落在两次写日志之间）：
      - 目标目录在 -> 安装已完成（或旧版还在），清日志、清暂存；
      - 目标缺失、备份在 -> 把备份搬回目标（回滚到已知可用的旧版）；
      - 目标缺失、没备份 -> 暂存目录完整就补完（首发安装被打断），否则只能报丢失。
    任何异常都不外抛：恢复是"尽量挽回"，不能因此让守护 / 面板起不来。

    fresh_sec / force：守护刚启动时可能正撞上另一个进程在装插件（面板安装
    < 1 秒，但这个窗口真实存在）。日志太新时**本轮什么都不动**（action='busy'），
    免得一边回滚一边被另一方换入。install_zip 自己调用时传 force=True——
    它是在同一线程里、写新日志之前先收尾，不存在并发。
    """
    note = log if callable(log) else (lambda m: None)
    base = base or os.path.dirname(os.path.abspath(plugins_dir))
    result = {'action': '', 'id': '', 'detail': ''}
    data = _read_journal(base)
    if not isinstance(data, dict):
        _clear_journal(base)           # 日志写坏 = 没有线索，别留着误导下一次
        _purge_staging(base, note=note)
        return result

    if not force:
        try:
            age = time.time() - os.path.getmtime(_journal_path(base))
        except OSError:
            age = None
        if age is not None and age < fresh_sec:
            result['action'] = 'busy'
            result['detail'] = ('%0.f 秒前有一次安装记录，可能仍在进行，'
                               '本轮不动' % age)
            return result

    pid = str(data.get('id') or '')
    target = str(data.get('target') or (os.path.join(plugins_dir, pid) if pid else ''))
    backup = str(data.get('backup') or '')
    stage = str(data.get('stage') or '')
    result['id'] = pid
    try:
        if not pid or not target:
            result['action'] = 'dropped'
            result['detail'] = '事务日志内容不完整'
        elif os.path.isdir(target):
            result['action'] = 'kept'
            result['detail'] = '目标目录已就位，按已完成处理'
        elif backup and os.path.isdir(backup):
            os.makedirs(os.path.dirname(os.path.abspath(target)) or '.', exist_ok=True)
            shutil.move(backup, target)
            result['action'] = 'restored'
            result['detail'] = '目标缺失，已把备份搬回（%s）' % pid
        elif stage and _looks_installed(stage, pid):
            os.makedirs(os.path.dirname(os.path.abspath(target)) or '.', exist_ok=True)
            shutil.move(os.path.join(stage, pid), target)
            result['action'] = 'completed'
            result['detail'] = '目标缺失，已用暂存目录补完安装（%s）' % pid
        else:
            result['action'] = 'lost'
            result['detail'] = '目标与备份都不在了，插件 %s 需要重新安装' % (pid or '?')
    except OSError as e:
        result['action'] = 'failed'
        result['detail'] = '恢复失败：%r' % (e,)
    finally:
        _clear_journal(base)
        if stage:
            _remove_tree(stage)
        _purge_staging(base, note=note)
    if result['action'] and result['action'] != 'kept':
        note('[plugin-package] 安装恢复：%s（%s）'
             % (result['detail'], result['action']))
    return result


def install_zip(path, plugins_dir, base=None, overwrite=False):
    """校验并安装一个插件 zip，返回包信息 + 是否覆盖 + 备份路径。

    全部校验通过后才动 plugins\\ 里的东西：先解压到安装目录旁的隐藏暂存区，
    再用同盘 shutil.move 一次性换入。失败时 plugins\\ 保持原样，异常退出时
    就地回滚，断电/被杀则留给下一次启动的 recover() 收尾。
    """
    info = inspect_zip(path)
    base = base or os.path.dirname(os.path.abspath(plugins_dir))
    pid = info['id']
    target = os.path.join(plugins_dir, pid)
    if os.path.exists(target) and not overwrite:
        raise PackageError('插件 %s 已存在；要升级请先在弹框里确认覆盖' % pid)
    # 上一次安装若被打断，先把现场收干净再动新包（同线程内没有并发，force 收尾）。
    recover(plugins_dir, base=base, force=True)
    os.makedirs(plugins_dir, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=base)
    backup = ''
    journal = {'id': pid, 'target': target, 'stage': stage, 'backup': '',
               'version': info['version'], 'ts': round(time.time(), 3),
               'state': 'staged'}
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            for rel, _size in info['members']:
                dst = os.path.join(stage, *rel.split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with zf.open(rel) as src, open(dst, 'wb') as out:
                    shutil.copyfileobj(src, out)
        staged_root = os.path.join(stage, pid)
        # 落盘后再走一遍宿主原本的 manifest 校验（目录名/entry/api/toggle 等），
        # 面板与守护用的是同一份规则，不会出现「面板装得上、守护加载失败」。
        entry = plugin_host.parse_manifest(staged_root, api=plugin_api.API_VERSION)
        if entry.status != plugin_host.STATUS_OK:
            raise PackageError('plugin.json 校验未通过：%s' % (entry.detail or entry.status))
        entry_file = entry.entry_file.replace('\\', '/')
        if not os.path.isfile(os.path.join(staged_root, *entry_file.split('/'))):
            raise PackageError('入口文件不存在：%s' % entry_file)
        _write_journal(base, journal)
        if os.path.exists(target):
            backup = os.path.join(_backup_dir(base, 'replaced-plugins'), pid)
            journal.update({'backup': backup, 'state': 'backing_up'})
            _write_journal(base, journal)
            shutil.move(target, backup)
        journal['state'] = 'swapping_in'
        _write_journal(base, journal)
        shutil.move(staged_root, target)
        _clear_journal(base)
    except PackageError:
        _rollback_install(base, target, backup)
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        _rollback_install(base, target, backup)
        raise PackageError('安装失败：%r' % (e,))
    finally:
        _remove_tree(stage)
    info.update({'replaced': bool(backup), 'backup': backup, 'dir': target})
    return info


def _rollback_install(base, target, backup):
    """换入失败后的就地回滚：旧版搬回原位，事务日志清掉。"""
    try:
        if backup and os.path.isdir(backup) and not os.path.exists(target):
            shutil.move(backup, target)
    except OSError:
        pass
    _clear_journal(base)


def remove_plugin(pid, plugins_dir, base=None):
    """卸载插件：整目录移进备份区（不是硬删），返回备份路径。

    内置插件也允许删（用户点了垃圾桶就该生效），但备份保留，随时可以再从
    安装包或插件 zip 装回来。
    """
    _check_id(pid)
    base = base or os.path.dirname(os.path.abspath(plugins_dir))
    target = os.path.join(plugins_dir, pid)
    if not os.path.isdir(target):
        raise PackageError('插件目录不存在：%s' % target)
    backup = os.path.join(_backup_dir(base, 'removed-plugins'), pid)
    try:
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.move(target, backup)
    except OSError as e:
        raise PackageError('卸载失败：%r' % (e,))
    return backup


def notify_reload(base):
    """写一个标记，让守护下一轮重建插件宿主（最多 2 秒延迟）。

    不直接杀守护：守护可能正在游戏会话里，重启会让 ACE 限速/弹窗监视整套
    状态丢失。热重载只换插件注册表，会话状态机保持不动。
    """
    flag = os.path.join(base, RELOAD_FLAG)
    with open(flag, 'w', encoding='ascii') as f:
        f.write(FORMAT_ID)
    return flag


def plugins_writable(plugins_dir):
    """面板据此把 ＋ / 垃圾桶置灰（安装目录在 Program Files 且没提权时为 False）。

    目录还没建出来时看父目录：全新安装第一次点「＋」也该能装，不能因为
    plugins\\ 不存在就把按钮永久置灰。
    """
    probe_dir = plugins_dir
    if not os.path.isdir(probe_dir):
        probe_dir = os.path.dirname(os.path.abspath(plugins_dir)) or plugins_dir
    try:
        probe = os.path.join(probe_dir, '.wbox-write-test')
        with open(probe, 'w', encoding='ascii') as f:
            f.write('1')
        os.remove(probe)
        return True
    except OSError:
        return False


def pack_plugin(plugin_dir, out_dir=None):
    """把插件工作目录打成规范 zip，返回生成的文件路径。

    用于维护内置插件的分发包，也供第三方作者打包：产出的包会立刻用
    inspect_zip() 自检一遍，保证「能被打出来的就一定装得进去」。
    """
    plugin_dir = os.path.abspath(plugin_dir)
    manifest_path = os.path.join(plugin_dir, MANIFEST)
    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise PackageError('读不到 %s：%r' % (manifest_path, e))
    pid = raw.get('id')
    _check_id(pid)
    if os.path.basename(plugin_dir) != pid:
        raise PackageError('目录名 %s 与 manifest.id %s 不一致' %
                           (os.path.basename(plugin_dir), pid))
    version = str(raw.get('version') or '0.0.0')
    out_dir = os.path.abspath(out_dir or os.path.join(os.path.dirname(plugin_dir),
                                                      '..', 'plugins-dist'))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, '%s-v%s.zip' % (pid, version))
    count = 0
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for dp, dns, fns in os.walk(plugin_dir):
            dns[:] = sorted(d for d in dns if d not in PACK_SKIP_DIRS)
            for fn in sorted(fns):
                if fn in PACK_SKIP_NAMES or fn.lower().endswith(PACK_SKIP_SUFFIX):
                    continue
                full = os.path.join(dp, fn)
                rel = os.path.relpath(full, plugin_dir).replace('\\', '/')
                _check_ext(rel)
                z.write(full, pid + '/' + rel)
                count += 1
    if not count:
        try:
            os.remove(out)
        except OSError:
            pass
        raise PackageError('插件目录里没有可打包的文件')
    # 侧车 `.sha256` 与包一起分发：用户拿到包后若被替换，安装框里的完整性
    # 核对会直接失败。它不是签名，只证明"和发布时同一份文件"。
    sha = archive_sha256(out)
    with open(out + SIDECAR_SUFFIX, 'w', encoding='ascii') as f:
        f.write(sha + '  ' + os.path.basename(out) + '\n')
    inspect_zip(out)           # 产出的包必须自己能通过校验（含侧车比对）
    return out
