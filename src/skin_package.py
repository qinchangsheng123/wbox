# -*- coding: utf-8 -*-
"""`.wbskin` 皮肤的校验与安装。

与插件包共用 `plugin_package._parts()` 的路径穿越检查——两份各写一遍迟早分叉。
与插件包的三处差异：

* zip 根目录直接是 `skin.json`，不套一层 id 目录（皮肤是一个「外观」，不是程序）。
* 后缀白名单只有 `.json .png .md`：出现 `.py/.dll/.exe` 直接拒，这是皮肤不带
  代码这条规则的**执行点**，不是建议。
* 装到 `<base>\\skins\\<id>\\`，与 `plugins\\` 并列。卸载 = 删目录。
"""
import json
import os
import shutil
import stat
import zipfile

import plugin_package
import skins

MANIFEST = 'skin.json'
ALLOWED_EXT = frozenset(('.json', '.png', '.md'))
MAX_FILES = 32
MAX_MEMBER_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024


class SkinPackageError(plugin_package.PackageError):
    """皮肤包不合格。继承插件的 PackageError，面板可以用同一段 except 处理。

    reason 是机器可读的拒绝原因，落 guard.log 的 `skin.reject reason=…`
    （SKIN-SPEC 硬要求）：executable / path / encrypted / symlink / size /
    manifest / api / duplicate / invalid。
    """

    def __init__(self, msg, reason='invalid'):
        plugin_package.PackageError.__init__(self, msg)
        self.reason = reason


def _check_ext(rel):
    ext = os.path.splitext(rel)[1].lower()
    if not ext:
        raise SkinPackageError('皮肤包不允许无扩展名文件：%s' % rel)
    if ext not in ALLOWED_EXT:
        raise SkinPackageError(
            '皮肤包只允许 .json / .png / .md，发现 %s（%s）——皮肤不许带可执行代码'
            % (ext, rel), reason='executable')


def inspect(path):
    """只读校验，返回 {id, name, version, data, members, total_bytes}。"""
    if not os.path.isfile(path):
        raise SkinPackageError('找不到皮肤包：%s' % path)
    if os.path.getsize(path) > MAX_ARCHIVE_BYTES:
        raise SkinPackageError('皮肤包超过 %d MB，拒绝安装' % (MAX_ARCHIVE_BYTES // 1048576))
    if not zipfile.is_zipfile(path):
        raise SkinPackageError('这不是有效的 .wbskin（其实是 zip）')
    try:
        zf = zipfile.ZipFile(path, 'r')
    except (OSError, zipfile.BadZipFile) as e:
        raise SkinPackageError('打不开皮肤包：%r' % (e,))

    members = []
    total = 0
    try:
        try:
            return _inspect_members(zf, path, members)
        except SkinPackageError:
            raise
        except plugin_package.PackageError as e:
            # _parts() 抛的是插件的 PackageError（父类）。统一成 SkinPackageError，
            # 面板只 catch 本类就够，不会漏掉路径穿越这一路。
            raise SkinPackageError(str(e), reason='path')
    finally:
        zf.close()


def _inspect_members(zf, path, members):
    total = 0
    for info in zf.infolist():
        if info.flag_bits & 0x1:
            raise SkinPackageError('皮肤包含加密条目，拒绝安装', reason='encrypted')
        mode = info.external_attr >> 16
        if mode and stat.S_ISLNK(mode):
            raise SkinPackageError('皮肤包含符号链接，拒绝安装', reason='symlink')
        parts = plugin_package._parts(info.filename)
        if info.is_dir():
            continue
        rel = '/'.join(parts)
        _check_ext(rel)
        if info.file_size > MAX_MEMBER_BYTES:
            raise SkinPackageError('单个文件超过 512 KB：%s' % rel, reason='size')
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise SkinPackageError('解压后总量超过 4 MB，拒绝安装', reason='size')
        members.append(rel)
    if not members:
        raise SkinPackageError('皮肤包是空的', reason='manifest')
    if len(members) > MAX_FILES:
        raise SkinPackageError('文件数超过 %d 个，拒绝安装' % MAX_FILES,
                               reason='size')
    if MANIFEST not in members:
        raise SkinPackageError('皮肤包根目录缺少 skin.json（不允许再套一层文件夹）',
                               reason='manifest')
    if any('/' not in m and m not in (MANIFEST, 'preview.png', 'README.md')
           for m in members):
        raise SkinPackageError('根目录只允许 skin.json / preview.png / README.md',
                               reason='manifest')
    try:
        raw = json.loads(zf.read(MANIFEST).decode('utf-8-sig'))
    except (ValueError, UnicodeDecodeError, KeyError) as e:
        raise SkinPackageError('skin.json 不是有效 UTF-8 JSON：%r' % (e,),
                               reason='manifest')
    api = raw.get('api') if isinstance(raw, dict) else None
    if isinstance(api, int) and not isinstance(api, bool) \
            and api > skins.API_VERSION:
        raise SkinPackageError(
            '皮肤要求 api=%d，当前面板只支持 api=%d，更新面板后再装'
            % (api, skins.API_VERSION), reason='api')
    try:
        skins.validate(raw, origin='skin.json')
    except skins.SkinError as e:
        # 统一成 SkinPackageError：调用方只 catch 一种异常，少一处漏网
        raise SkinPackageError(str(e), reason='manifest')

    # assets 引用的文件必须真的在包里，否则加载时才炸
    for key, rel in (raw.get('assets') or {}).items():
        if not isinstance(rel, str):
            raise SkinPackageError('assets.%s 必须是路径字符串' % key)
        norm = '/'.join(plugin_package._parts(rel))
        if norm not in members:
            raise SkinPackageError('assets.%s 指向的文件不存在：%s' % (key, rel))
    return {
        'id': raw['id'], 'name': raw['name'], 'version': raw['version'],
        'data': raw, 'members': members, 'total_bytes': total, 'path': path,
    }


def install_zip(path, base, overwrite=False, log=None):
    """校验通过后解压到 `<base>\\skins\\<id>\\`，返回 Skin。

    任何拒绝都记一行 `skin.reject reason=…`（SKIN-SPEC 硬要求：拒绝必须
    留痕，QA 拿日志对账，不能只弹一个框就完事）。
    """
    try:
        info = inspect(path)
    except SkinPackageError as e:
        if log:
            log('skin.reject reason=%s %s' % (e.reason, e))
        raise
    sid = info['id']
    dest_root = skins.skins_dir(base)
    dest = os.path.join(dest_root, sid)
    if os.path.isdir(dest):
        if not overwrite:
            e = SkinPackageError('已经装过同名皮肤「%s」，先卸载再装' % info['name'],
                                 reason='duplicate')
            if log:
                log('skin.reject reason=%s %s' % (e.reason, e))
            raise e
        shutil.rmtree(dest, ignore_errors=True)
    tmp = dest + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        with zipfile.ZipFile(path, 'r') as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                parts = plugin_package._parts(member.filename)
                target = os.path.join(tmp, *parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, 'wb') as out:
                    shutil.copyfileobj(src, out)
        os.replace(tmp, dest)               # 同盘 rename，失败会抛，不留半成品
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    skin = skins.load_dir(dest, expect_id=sid)
    if log:
        log('皮肤已安装：%s v%s' % (skin.name, skin.version))
    return skin


def remove_skin(skin_id, base):
    """删掉用户皮肤目录。内置皮肤不许删（它们根本不在磁盘上）。"""
    if skins.get(skin_id) is not None and skins.get(skin_id).builtin:
        raise SkinPackageError('内置皮肤不能卸载')
    dest = os.path.join(skins.skins_dir(base), skin_id)
    if not os.path.isdir(dest):
        raise SkinPackageError('没有找到皮肤 %s' % skin_id)
    shutil.rmtree(dest)


def pack(src_dir, out_path, log=None):
    """把皮肤目录打成 `.wbskin`，打完立刻 inspect() 自检一遍（v2.15.0）。

    之前只有 inspect/install 没有 pack，第三方作者得自己压 zip——而 zip 的坑
    恰好全在细节里：根目录多套一层、skin.json 不在第一位、Windows 反斜杠、
    BOM、目录名和 id 不一致。这里把这些一次做对，打完直接跑校验，
    不合格当场抛，不留一个"看起来像皮肤"的坏包。

    返回 inspect() 的结果（id / name / version / members / total_bytes）。
    """
    src_dir = os.path.abspath(src_dir)
    manifest = os.path.join(src_dir, MANIFEST)
    if not os.path.isfile(manifest):
        raise SkinPackageError('源目录里没有 skin.json：%s' % src_dir,
                               reason='manifest')
    with open(manifest, 'rb') as f:
        try:
            raw = json.loads(f.read().decode('utf-8-sig'))
        except (ValueError, UnicodeDecodeError) as e:
            raise SkinPackageError('skin.json 不是有效 UTF-8 JSON：%r' % (e,),
                                   reason='manifest')
    skin_id = raw.get('id') if isinstance(raw, dict) else None
    if not isinstance(skin_id, str) or not skin_id:
        raise SkinPackageError('skin.json 缺少 id', reason='manifest')
    # 目录名 = id：装完的落点是 skins\<id>\，不一致时 set_skin(id) 找不到目录，
    # 卡片能列出、点下去没反应——这类问题排查成本远高于打包时报错。
    if os.path.basename(src_dir) != skin_id:
        raise SkinPackageError(
            '目录名必须是 id（%s），当前是 %s' % (skin_id,
                                              os.path.basename(src_dir)),
            reason='manifest')
    try:
        skins.load_dir(src_dir)
    except skins.SkinError as e:
        raise SkinPackageError(str(e), reason='manifest')

    # 固定顺序：skin.json 打头（人肉解包、外部工具都先看它），
    # 其后 preview.png / README.md / assets 按名字排——同内容出同字节序，
    # 便于 diff 与"这一版到底改了什么"。
    names = [MANIFEST]
    for name in ('preview.png', 'README.md'):
        if os.path.isfile(os.path.join(src_dir, name)):
            names.append(name)
    assets_dir = os.path.join(src_dir, 'assets')
    if os.path.isdir(assets_dir):
        for fn in sorted(os.listdir(assets_dir)):
            if os.path.isfile(os.path.join(assets_dir, fn)):
                names.append('assets/' + fn)

    out_path = os.path.abspath(out_path)
    out_dir = os.path.dirname(out_path)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    tmp = out_path + '.tmp'
    try:
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
            for rel in names:
                zf.write(os.path.join(src_dir, rel.replace('/', os.sep)), rel)
        os.replace(tmp, out_path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    if log:
        log('skin.pack id=%s version=%s bytes=%d'
            % (skin_id, raw.get('version'), os.path.getsize(out_path)))
    return inspect(out_path)
