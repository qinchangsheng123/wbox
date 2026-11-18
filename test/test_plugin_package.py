# -*- coding: utf-8 -*-
"""离线验证插件包规范 v1：打包 / 只读校验 / 安装 / 覆盖备份 / 卸载备份 / 拒绝清单。

全部在 tempfile 里跑，不碰 app\\ 与生产插件目录；LOCALAPPDATA 也改指临时目录，
备份不会落进用户真实的 %LOCALAPPDATA%\\WBOX。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))
import plugin_host
import plugin_package
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_src  # noqa: E402  插件源码在独立仓库 wbox-plugins

FAILS = []


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def manifest(pid='demo_tool'):
    return {
        'id': pid, 'name': '演示插件', 'version': '1.2.3', 'api': 1,
        'icon': 'dot', 'order': 90, 'home': True,
        'toggle': [pid + '.enabled'], 'entry': 'plugin.py:Plugin',
        'tip': '演示', 'config': {'keys': [pid]},
    }


def body():
    return ('# -*- coding: utf-8 -*-\n'
            'from plugin_api import WBOXPlugin\n'
            'class Plugin(WBOXPlugin):\n    pass\n')


def good_entries(pid='demo_tool', extra=None, over=None):
    m = manifest(pid)
    if over:
        m.update(over)
    e = {
        pid + '/plugin.json': json.dumps(m, ensure_ascii=False),
        pid + '/plugin.py': body(),
    }
    if extra:
        e.update(extra)
    return e


def write_zip(path, entries):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data.encode('utf-8') if isinstance(data, str) else data)
    return path


def must_reject(tmp, label, entries, raw=None):
    p = os.path.join(tmp, label + '.zip')
    if raw is None:
        write_zip(p, entries)
    else:
        with open(p, 'wb') as f:
            f.write(raw)
    try:
        plugin_package.inspect_zip(p)
    except plugin_package.PackageError as e:
        check(True, '拒绝 %s：%s' % (label, str(e)[:60]))
        return
    check(False, '本该拒绝却放行了：%s' % label)


def write_journal(base, **kw):
    """模拟"上一次安装的记录还在、进程已经不在了"（事务日志是 module 常量）。"""
    data = {'id': 'demo_tool', 'target': '', 'stage': '', 'backup': '',
            'version': '1.0.0', 'ts': 0, 'state': 'staged'}
    data.update(kw)
    with open(os.path.join(base, plugin_package.JOURNAL), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    return data


def main():
    tmp = tempfile.mkdtemp(prefix='abg_pkg_')
    old_lad = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = os.path.join(tmp, 'localappdata')
    base = os.path.join(tmp, 'app')
    plugins = os.path.join(base, 'plugins')
    os.makedirs(plugins)
    try:
        print('[1] 真实插件能用 pack_plugin 打出规范包，且自检通过')
        out_dir = os.path.join(tmp, 'dist')
        src_root = _plugin_src.plugins_root()
        zp = ''
        if not src_root:
            print('  skip 未找到插件源码（../wbox-plugins 或 WBOX_PLUGINS_SRC）')
        else:
            for pid in ('afterburner', 'ace', 'popup', 'gamepp'):
                zp = plugin_package.pack_plugin(os.path.join(src_root, pid), out_dir)
                info = plugin_package.inspect_zip(zp)
                check(info['id'] == pid and info['members'] and info['builtin'],
                      '%s-v.zip 通过校验（%d 文件 / %d B）'
                      % (pid, len(info['members']), info['total_bytes']))
            check(os.path.isfile(zp + '.sha256'), '打包同时产出 .sha256 侧车')

        print('[2] 安装 -> 宿主发现 -> 内容逐字节一致')
        zp = write_zip(os.path.join(tmp, 'demo.zip'), good_entries())
        info = plugin_package.inspect_zip(zp)
        check(info['format'] == 'wbox-plugin-zip-v1' and info['name'] == '演示插件',
              '只读校验返回格式 / 名称 / 版本 v%s' % info['version'])
        res = plugin_package.install_zip(zp, plugins, base=base)
        target = os.path.join(plugins, 'demo_tool')
        check(os.path.isfile(os.path.join(target, 'plugin.json')), '落地 plugins\\demo_tool\\')
        entries = plugin_host.discover(base, plugins_dir=plugins)
        check([e.id for e in entries if e.status == 'ok'] == ['demo_tool'],
              '宿主 discover 能加载，status=ok')
        with open(os.path.join(target, 'plugin.py'), 'rb') as f:
            check(f.read() == body().encode('utf-8'), '插件源码落地后逐字节一致')
        check(not [d for d in os.listdir(base) if d.startswith('.plugin-staging-')],
              '安装结束没有留下暂存目录')

        print('[3] 重复安装默认拒绝；确认覆盖后旧目录进备份区')
        try:
            plugin_package.install_zip(zp, plugins, base=base)
            check(False, '覆盖前必须显式确认')
        except plugin_package.PackageError:
            check(True, '已存在且 overwrite=False -> PackageError')
        zp2 = write_zip(os.path.join(tmp, 'demo2.zip'),
                        good_entries(extra={'demo_tool/plugin.py': body() + '# v2\n'}))
        res2 = plugin_package.install_zip(zp2, plugins, base=base, overwrite=True)
        check(res2['replaced'] and os.path.isdir(res2['backup']),
              '覆盖前备份了旧目录：%s' % os.path.basename(os.path.dirname(res2['backup'])))
        with open(os.path.join(target, 'plugin.py'), 'r', encoding='utf-8') as f:
            check('# v2' in f.read(), '新版本已生效')

        print('[4] 卸载：目录移走、备份存在、宿主不再发现')
        backup = plugin_package.remove_plugin('demo_tool', plugins, base=base)
        check(not os.path.exists(target) and os.path.isdir(backup),
              '目录从 plugins\\ 移出并落在备份区')
        check(plugin_host.discover(base, plugins_dir=plugins) == [],
              '宿主 discover 返回空（零插件可跑）')

        print('[5] 热重载标记')
        flag = plugin_package.notify_reload(base)
        with open(flag, 'r', encoding='ascii') as f:
            check(f.read() == plugin_package.FORMAT_ID,
                  'plugins_reload.flag 写了格式标识（守护按它重载）')
        os.remove(flag)

        print('[6] 不合格的包一律拒绝，且不留半成品')
        must_reject(tmp, 'path_traversal', good_entries(
            extra={'demo_tool/../../evil.py': body()}))
        must_reject(tmp, 'absolute', good_entries(
            extra={'C:/evil.py': body()}))
        must_reject(tmp, 'two_roots', good_entries(
            extra={'other/plugin.py': body()}))
        must_reject(tmp, 'exe', good_entries(
            extra={'demo_tool/evil.exe': b'MZ'}))
        must_reject(tmp, 'id_mismatch', good_entries(over={'id': 'other_id'}))
        must_reject(tmp, 'no_manifest', {'demo_tool/plugin.py': body()})
        must_reject(tmp, 'api_too_new', good_entries(over={'api': 99}))
        must_reject(tmp, 'bad_json', {'demo_tool/plugin.json': '{oops',
                                      'demo_tool/plugin.py': body()})
        must_reject(tmp, 'no_entry', good_entries(over={'entry': 'missing.py:Plugin'}))
        must_reject(tmp, 'bad_id', good_entries('Bad-ID'))
        must_reject(tmp, 'not_zip', {}, raw=b'this is not a zip')
        check(os.listdir(plugins) == [],
              '全部拒绝后 plugins\\ 仍是空的（失败不留半成品）')

        print('[7] 写权限探针')
        check(plugin_package.plugins_writable(plugins) is True,
              '可写目录返回 True')

        print('[8] 信任边界：发布者 / 权限 / 来源 / 完整性都摆给用户看')
        zp8 = write_zip(os.path.join(tmp, 'trust.zip'), good_entries(over={
            'publisher': 'ACME 工作室', 'homepage': 'https://example.com/acme',
            'permissions': ['process.kill', 'files.any', 'acme.custom']}))
        info8 = plugin_package.inspect_zip(zp8)
        check(info8['publisher'] == 'ACME 工作室'
              and info8['homepage'] == 'https://example.com/acme',
              '发布者与来源从 manifest 读出来了')
        check(info8['known_permissions'] == ['process.kill', 'files.any']
              and info8['unknown_permissions'] == ['acme.custom'],
              '权限分「已登记 / 未登记」两栏（未登记不拒绝，原样展示）')
        check(info8['trust'] == 'third_party', '非官方 id -> 按第三方措辞')
        text8 = plugin_package.describe_install(info8)
        check('ACME 工作室' in text8 and '第三方' in text8,
              '安装框展示发布者：%s' % text8.splitlines()[0])
        check('结束进程' in text8 and '读写任意文件' in text8,
              '安装框把权限键翻成人话')
        check('完全信任' in text8 and '自己声明' in text8,
              '安装框点明信任边界（权限是声明式的）')
        check('SHA-256' in text8 and info8['sha256'][:24] in text8,
              '安装框给出整包 SHA-256')
        check(info8['integrity'] == 'sidecar_missing',
              '没有 .sha256 侧车 -> 如实标"无法核对"')

        # 侧车一致 -> 认可；侧车不一致 -> 拒绝（包被替换/传坏）
        with open(zp8 + '.sha256', 'w', encoding='ascii') as f:
            f.write(info8['sha256'] + '  trust.zip\n')
        check(plugin_package.inspect_zip(zp8)['integrity'] == 'sidecar_ok',
              '侧车与本包一致 -> 标「已核对」')
        with open(zp8 + '.sha256', 'w', encoding='ascii') as f:
            f.write('0' * 64 + '  trust.zip\n')
        try:
            plugin_package.inspect_zip(zp8)
            check(False, '侧车不一致必须拒绝安装')
        except plugin_package.PackageError as e:
            check('sha256' in str(e).lower(), '侧车不一致 -> 拒绝：%s' % str(e)[:40])
        os.remove(zp8 + '.sha256')
        # 官方包：内置 id + 官方发布者 -> 官方的措辞；只写 builtin 不算数
        fake_official = write_zip(os.path.join(tmp, 'fake_off.zip'),
                                  good_entries('afterburner', over={
                                      'publisher': 'WBOX', 'builtin': True}))
        check(plugin_package.inspect_zip(fake_official)['trust'] == 'official',
              '内置 id + 官方发布者 -> 官方')
        fake_third = write_zip(os.path.join(tmp, 'third.zip'),
                               good_entries('afterburner', over={'builtin': True}))
        check(plugin_package.inspect_zip(fake_third)['trust'] == 'unknown',
              '没写发布者就是"来源不明"，builtin:true 不能当担保')

        print('[9] 安装被打断：下次启动/下次安装自动收尾')
        zp9 = write_zip(os.path.join(tmp, 'demo9.zip'), good_entries())
        plugin_package.install_zip(zp9, plugins, base=base)
        target = os.path.join(plugins, 'demo_tool')
        check(os.path.isdir(target), '先装一份作为"旧版"')

        # (a) 旧版已移走、新版还没换入（断电点）-> 备份搬回
        backup = os.path.join(tmp, 'bk', 'demo_tool')
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        shutil.move(target, backup)
        write_journal(base, target=target, backup=backup, state='backing_up')
        rec = plugin_package.recover(plugins, base=base)
        check(rec['action'] == 'busy' and not os.path.isdir(target),
              '刚写下的安装记录 -> 本轮不动（防与另一个进程抢同一份目录）')
        rec = plugin_package.recover(plugins, base=base, force=True)
        check(rec['action'] == 'restored' and os.path.isdir(target),
              '目标缺失 + 有备份 -> 回滚旧版（%s）' % rec['action'])
        check(not os.path.exists(os.path.join(base, plugin_package.JOURNAL)),
              '恢复后事务日志被清掉')

        # (b) 首发安装被打断：没有备份，暂存目录完整 -> 补完
        keep = os.path.join(tmp, 'keep_demo')
        shutil.move(target, keep)
        stage = tempfile.mkdtemp(prefix=plugin_package.STAGING_PREFIX, dir=base)
        shutil.copytree(keep, os.path.join(stage, 'demo_tool'))
        write_journal(base, target=target, backup='', stage=stage, state='staged')
        rec = plugin_package.recover(plugins, base=base, force=True)
        check(rec['action'] == 'completed' and os.path.isdir(target),
              '无备份 + 暂存完整 -> 补完安装（%s）' % rec['action'])
        check(not os.path.isdir(stage), '补完后暂存目录已删')

        # (c) 目标已就位、日志残留 -> 按已完成处理，不清插件
        write_journal(base, target=target, stage='', state='installed')
        rec = plugin_package.recover(plugins, base=base, force=True)
        check(rec['action'] == 'kept' and os.path.isdir(target),
              '目标已就位 -> 只清日志，不动插件')

        # (d) 孤儿暂存目录（超过 6 小时没人认领）-> 清掉
        stale = tempfile.mkdtemp(prefix=plugin_package.STAGING_PREFIX, dir=base)
        old = time.time() - 7 * 3600
        os.utime(stale, (old, old))
        plugin_package.recover(plugins, base=base, force=True)
        check(not os.path.isdir(stale), '陈旧暂存目录被清理')

        # (e) 日志写坏 -> 不抛异常，也不留垃圾
        with open(os.path.join(base, plugin_package.JOURNAL), 'w',
                  encoding='utf-8') as f:
            f.write('{oops')
        rec = plugin_package.recover(plugins, base=base, force=True)
        check(rec['action'] == '' and os.path.isdir(target),
              '坏日志不抛异常、不误删插件')
        check(not os.path.exists(os.path.join(base, plugin_package.JOURNAL)),
              '坏日志被清掉')
    finally:
        if old_lad is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = old_lad
        shutil.rmtree(tmp, ignore_errors=True)

    print('')
    if FAILS:
        print('FAILED %d 项：%s' % (len(FAILS), FAILS))
        sys.exit(1)
    print('ALL PASS')


if __name__ == '__main__':
    main()
