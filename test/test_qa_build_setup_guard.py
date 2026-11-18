# -*- coding: utf-8 -*-
"""QA 独立验证 D/BLOCKER 回归门：安装包 payload 里必须有内置插件。

这一组测试就是为了**永久挡住 B-1 那个事故**：
`installer\\build_setup.py` 的 `ABG_APP` 默认指向 `<repo>\\app`（生产安装目录，
只有程序文件、没有 `plugins\\`），于是"顺手重打一次安装包"会产出一份 0 插件的
payload —— 装到用户机器上守护照跑、主页 0 行、三个功能全丢，且没有任何报错。
原来的脚本只打印一句警告就继续打包，谁也发现不了。

现在要求（缺一条就该在打包时失败，而不是等 QA 事后发现）：
  [1] ABG_APP 缺 `WBOX.exe` -> rc=1
  [2] ABG_APP 缺 `plugins\\`    -> rc=1
  [3] `plugins\\` 存在但没有 plugin.json -> rc=1，且删掉半成品 payload
  [4] 不传 ABG_APP（默认生产目录）-> rc=1（这条是当初的坑，必须永久失败）
  [5] 正式产物：payload.zip 含 5 个内置插件（10 个文件 / 5 个 plugin.json）/
      无 .pyc / 无 plugins_state.json；且与仓库源码逐字节一致
  [5b] payload 必含 docs 四件套（插件开发指南 HTML + 规范 Markdown + 示例模板），
      各出现一次、HTML 自包含、正文与安全警告都在（v2.3.0 复核补丁加的守门断言）
  [5c] 两份 spec（src\\abg.spec / installer\\setup_abg.spec）的五块 Tk 裁剪规则
      逐块一致：KEEP_ENC / DROP_DIR_PREFIX / DROP_FILES / EXCLUDES 比集合，
      trim_tk_data 比函数体（只允许 print 前缀 [spec] / [setup spec] 不同）。
      只改一份，app\\ 与 payload 的 Tk 裁剪就会分叉，这条断言会直接点名差项。
  [6] Setup exe 内嵌的 payload 与磁盘 payload.zip 逐字节相同（PyInstaller 会把
      CArchive 条目 zlib 压缩，所以**不能**用 grep 判断，必须解归档）

只读 + 只写临时目录，绝不写 installer\\ 正式产物、不跑 Setup_WBOX.exe。
用法：C:\\Python38\\python.exe test\\test_qa_build_setup_guard.py
"""
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD_SETUP = os.path.join(ROOT, 'installer', 'build_setup.py')
PAYLOAD = os.path.join(ROOT, 'installer', 'payload.zip')
SETUP_EXE = os.path.join(ROOT, 'installer', 'dist', 'Setup_WBOX.exe')
BUILTINS = ('afterburner', 'ace', 'popup', 'gamepp', 'desktop_hud')

# 两份 spec 必须完全一致的裁剪规则块（[5c] 用）
ABG_SPEC = os.path.join(ROOT, 'src', 'abg.spec')
SETUP_SPEC = os.path.join(ROOT, 'installer', 'setup_abg.spec')
RULE_BLOCKS = ('KEEP_ENC', 'DROP_DIR_PREFIX', 'DROP_FILES', 'EXCLUDES')

sys.path.insert(0, os.path.join(ROOT, 'installer'))
import setup_abg
import uninstall_core  # noqa: E402  [5] 交叉核对内置名单

PASSED = []
FAILED = []
TMP = tempfile.mkdtemp(prefix='abg_qa_build_guard_')


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


def run_payload_only(app_dir, payload_path):
    """跑 `build_setup.py --payload-only`，返回 (rc, stdout)。"""
    env = dict(os.environ)
    env['ABG_APP'] = app_dir
    env['ABG_PAYLOAD'] = payload_path
    p = subprocess.run([sys.executable, BUILD_SETUP, '--payload-only'],
                       cwd=ROOT, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=180)
    return p.returncode, (p.stdout or b'').decode('utf-8', 'replace')


def make_app(name, with_exe=True, with_plugins='full'):
    """with_plugins: 'full' | 'empty' | 'none'。"""
    d = os.path.join(TMP, name)
    os.makedirs(d, exist_ok=True)
    if with_exe:
        with open(os.path.join(d, 'WBOX.exe'), 'wb') as f:
            f.write(b'MZ fake exe')
    with open(os.path.join(d, 'config.json'), 'w', encoding='utf-8') as f:
        json.dump({'ui_theme': 'light'}, f)
    # 共享运行时占位：payload 的 `.wbox-shared-runtime` 清单不能为空，否则
    # 打包器会判定"装出来的程序缺 dll"直接退出。真实构建里这些来自 PyInstaller。
    with open(os.path.join(d, 'python38.dll'), 'wb') as f:
        f.write(b'MZ shared')
    if with_plugins == 'full':
        for pid in BUILTINS:
            pdir = os.path.join(d, 'plugins', pid)
            os.makedirs(pdir, exist_ok=True)
            with open(os.path.join(pdir, 'plugin.json'), 'w', encoding='utf-8') as f:
                json.dump({'id': pid, 'name': pid, 'version': '2.0.0',
                           'api': 1}, f)
            with open(os.path.join(pdir, 'plugin.py'), 'w', encoding='utf-8') as f:
                f.write('# %s\n' % pid)
    elif with_plugins == 'empty':
        os.makedirs(os.path.join(d, 'plugins'), exist_ok=True)
    return d


def _consts(node):
    """把 spec 里的 list / tuple / set 字面量摊平成字符串列表。

    保序、**保留重复项** —— 重复本身也要能查出来（EXCLUDES 曾经多写了 bz2/lzma）。
    兼容 `set([...])` / `frozenset((...))` 这种套一层的写法。
    """
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [e.value for e in node.elts
                if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    if isinstance(node, ast.Call):
        out = []
        for arg in node.args:
            out.extend(_consts(arg))
        return out
    return []


def spec_rules(path):
    """抽取一份 PyInstaller spec 里必须与另一份完全一致的裁剪规则。

    只取五块内容，**不比整文件字节**：两份 spec 的入口脚本、datas、hiddenimports
    与 print 前缀本来就不同，整文件比只会假红。注释与格式差异也不参与比较
    （走 AST，注释根本不在语法树里）。
    """
    with io.open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    rules = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in RULE_BLOCKS:
                rules[target.id] = _consts(node.value)
        elif isinstance(node, ast.FunctionDef) and node.name == 'trim_tk_data':
            # 两份只允许 print 前缀不同（[spec] vs [setup spec]），先归一化再比
            rules['trim_tk_data'] = ast.dump(node).replace(
                "'[setup spec]", "'[spec]")
    return rules


def main():
    try:
        # ---------------- [1]-[4] 负向路径 ----------------
        print('[1] ABG_APP 缺 WBOX.exe -> 打包失败')
        app = make_app('no_exe', with_exe=False)
        p = os.path.join(TMP, 'p1.zip')
        rc, out = run_payload_only(app, p)
        step(rc == 1, '返回码 = %d（期望 1）' % rc)
        step('WBOX.exe' in out, '错误信息点明了缺 WBOX.exe')
        step(not os.path.exists(p), '没有产出半成品 payload')

        print('[2] ABG_APP 没有 plugins\\ -> v2.11.3 起正常打包，且包内 0 个插件')
        app = make_app('no_plugins', with_plugins='none')
        p = os.path.join(TMP, 'p2.zip')
        rc, out = run_payload_only(app, p)
        step(rc == 0, '返回码 = %d（期望 0：本体不带插件）' % rc)
        step(os.path.isfile(p), '产出了 payload')
        if os.path.isfile(p):
            with zipfile.ZipFile(p) as z:
                names = [n.replace('\\', '/') for n in z.namelist()]
            step(not [n for n in names if n.startswith('plugins/')],
                 'payload 里没有任何 plugins/ 条目')

        print('[3] ABG_APP 里有用户插件 -> 打包成功，但 plugins\\ 整目录被排除')
        app = make_app('with_plugins', with_plugins='full')
        p = os.path.join(TMP, 'p3.zip')
        rc, out = run_payload_only(app, p)
        step(rc == 0, '返回码 = %d（期望 0：插件是用户数据，不阻止打包）' % rc)
        step(os.path.isfile(p), '产出了 payload')
        if os.path.isfile(p):
            with zipfile.ZipFile(p) as z:
                names = [n.replace('\\', '/') for n in z.namelist()]
            step(not [n for n in names if n.startswith('plugins/')],
                 'payload 里剔除了源目录的插件（实际 %r）'
                 % ([n for n in names if n.startswith('plugins/')][:3],))
        # 源目录里的插件不能被构建删掉：那是用户装在现网目录里的东西
        step(os.path.isdir(os.path.join(app, 'plugins', 'ace')),
             '构建没有删掉源目录里的 plugins\\（用户插件保住）')

        print('[4] 不传 ABG_APP（默认 = <repo>\\app，生产目录）-> 视其是否有效构建目录')
        prod_app = os.path.join(ROOT, 'app')
        # v2.11.0：有效构建目录 = plugins\ + WBOX.exe。旧名生产目录（Wingman.exe）
        # 不是合法 payload 源，默认命令必须拒绝，而不是打个缺主程序的包。
        # v2.11.3：有效构建目录 = 有 WBOX.exe 且**没有** plugins\（本体不带插件）。
        has_plugins = (os.path.isdir(os.path.join(prod_app, 'plugins'))
                       and os.path.isfile(os.path.join(prod_app, 'WBOX.exe')))
        env = dict(os.environ)
        env.pop('ABG_APP', None)
        env['ABG_PAYLOAD'] = os.path.join(TMP, 'p4.zip')   # 不碰正式 payload
        proc = subprocess.run([sys.executable, BUILD_SETUP, '--payload-only'],
                              cwd=ROOT, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=180)
        out4 = (proc.stdout or b'').decode('utf-8', 'replace')
        # v2.11.3：默认 ABG_APP 是现网目录，里面通常装着用户插件 —— 这是正常的，
        # 打包只需把它排除，不需要（也不允许）删除。
        step(proc.returncode == 0,
             '生产 app\\ 可用（有 WBOX.exe；plugins\\ %s）-> 默认命令打包成功（rc=%d）'
             % ('存在，已排除' if has_plugins else '不存在', proc.returncode))
        if os.path.exists(os.path.join(TMP, 'p4.zip')):
            with zipfile.ZipFile(os.path.join(TMP, 'p4.zip')) as z:
                n4 = [n.replace('\\', '/') for n in z.namelist()]
            step(not [n for n in n4 if n.startswith('plugins/')],
                 '默认命令产出的 payload 里也没有插件条目')

        # ---------------- [5] 正式 payload 的内容 ----------------
        print('[5] 正式 installer\\payload.zip 必须**不含**任何插件')
        if not os.path.isfile(PAYLOAD):
            step(False, 'installer\\payload.zip 不存在（还没打过包？）')
        else:
            z = zipfile.ZipFile(PAYLOAD)
            names = [n.replace('\\', '/') for n in z.namelist()]
            plug = [n for n in names if n.startswith('plugins/')]
            step(not [n for n in plug if n.endswith('plugin.json')],
                 'payload 里 0 个 plugin.json（实际 %d 个）'
                 % len([n for n in plug if n.endswith('plugin.json')]))
            step(not plug, 'payload 里没有 plugins/ 条目（实际 %r）' % (plug[:5],))
            # v2.11.3：安装器侧的内置名单也必须是空集 —— 非空就说明还有代码
            # 认为"某些插件是我带进来的"，升级/卸载会把用户插件当内置覆盖。
            step(uninstall_core.BUILTIN_PLUGINS == (),
                 'uninstall_core.BUILTIN_PLUGINS 为空集（%r）'
                 % (uninstall_core.BUILTIN_PLUGINS,))
            # v2.11.1：payload 的 config.json 是安装合并模板，必须是出厂生成件。
            # v2.11.0 实测混入过生产配置（用户机器路径随包分发），这里永久设防。
            import factory_config
            fac = factory_config.factory_bytes()
            step(z.read('config.json') == fac,
                 'payload config.json = 出厂模板（%d B，不含本机运行数据）' % len(fac))
            fc = json.loads(fac)
            step(fc.get('afterburner_path') == '' and fc.get('game_roots') == [],
                 '出厂模板无机器路径 / 游戏库根（新机器由安装器探测写入）')
            # v2.11.2：merge_config 跳过用户旧注释键、只认模板文案，模板缺注释键
            # = 部署删键（v2.11.1 实测删光生产 7 个注释键）。正向设防。
            step(set(k for k in fc if k.startswith('_'))
                 == set(factory_config.COMMENTS),
                 '出厂模板含全部 %d 个注释键（模板缺键 = 部署删键）'
                 % len(factory_config.COMMENTS))
            runtime_names = [n for n in names
                             if n.split('/')[-1] in (
                                 'guard.pid', 'games_cache.json',
                                 'plugins_state.json', 'ace_state.json',
                                 'stop.flag', 'plugins_reload.flag',
                                 'config.json.bak', 'config.json.bak.1')
                             or n.endswith('.log')
                             or n.split('/')[-1].startswith('guard.log')]
            step(not runtime_names, 'payload 无运行状态文件（%r）' % (runtime_names,))
            step(not [n for n in names if '__pycache__' in n or n.endswith('.pyc')],
                 'payload 里没有 __pycache__ / .pyc')
            step(not [n for n in names if 'plugins_state' in n],
                 'payload 里没有 plugins_state.json（运行期状态不随安装包走）')
            step('WBOX.exe' in names, 'payload 里有 WBOX.exe')

            # ---------------- [5b] 插件开发指南必须随包 ----------------
            # v2.3.0 复核补丁（QA P1-2）：这一组以前完全没人守 —— payload 里少一份
            # HTML、Setup 里没打进 payload，28 个测试照样全绿。这和 v2.0.0
            # "装完三个功能全丢"是同一类风险（给插件加了硬断言，文档这一半漏了）。
            print('[5b] 插件开发指南必须随 package 分发')
            GUIDE_FILES = ('docs/插件开发指南.html', 'docs/PLUGIN-SPEC.md',
                           'docs/plugin-template/plugin.json',
                           'docs/plugin-template/plugin.py')
            missing_doc = [n for n in GUIDE_FILES if n not in names]
            step(not missing_doc, 'docs 四件套都在 payload 里（缺 %r）' % (missing_doc,))
            dup = [n for n in GUIDE_FILES if names.count(n) != 1]
            step(not dup, 'docs 每个条目只出现一次（重复 %r）' % (dup,))
            step(not [n for n in names if n.startswith('docs/')
                      and n.endswith(('.pyc', '.pyo'))],
                 'docs 里没有 .pyc（示例模板的字节码不该出厂）')
            # v2.3.0 复核补丁 R2（QA）：HTML 缺失时 z.read 会抛 KeyError，把后面
            # [6] 整段测试直接打断（报告里只剩 3 条 FAIL 和一个 traceback）。
            # 内容检查必须先判存在；缺失本身已由上面 missing_doc 断言报过。
            if 'docs/插件开发指南.html' not in names:
                print('  skip HTML 内容检查：文件不在 payload 里（上面已 FAIL）')
            else:
                html_raw = z.read('docs/插件开发指南.html')
                try:
                    html = html_raw.decode('utf-8')      # 严格解码：乱码/GBK 直接抛
                    utf8_ok = True
                except UnicodeDecodeError:
                    html = html_raw.decode('utf-8', 'replace')
                    utf8_ok = False
                step(utf8_ok, 'HTML 是合法 UTF-8（不是 GBK / 半截字节）')
                step(len(html_raw) > 20000,
                     'HTML 不是空壳：%d B（规范正文编译后的量级）' % len(html_raw))
                step('插件开发指南' in html and '插件不是配置项，是代码' in html,
                     'HTML 里有正文与前置安全警告')
                hanzi = sum(1 for ch in html if u'\u4e00' <= ch <= u'\u9fff')
                step(hanzi > 2000, 'HTML 里中文正文够多（%d 个汉字）' % hanzi)
                step(not re.search(r'(?:src|href)=["\']https?://', html)
                     and '@import' not in html,
                     'HTML 自包含：没有外链资源（离线双击可读）')

        # ---------------- [5c] 两份 spec 的 Tk 裁剪规则必须一致 ----------------
        # v2.3.0 复核补丁 R3（QA 遗留洞）：L2 铁律写着「两份 spec 的 Tcl 裁剪必须
        # 一致」，但以前只写在注释里、零自动守卫：谁只改一份，app\ 与 payload 的
        # Tk 裁剪就悄悄分叉（构建后 verify_shared_runtime 只会报 md5 不同，指不出
        # 是哪条规则差了）。这里逐块比，失败信息直接点名「哪一块、差哪一项」。
        print('[5c] 两份 spec 的 Tk 裁剪规则一致（不是整文件比字节）')
        rules_a = spec_rules(ABG_SPEC)
        rules_b = spec_rules(SETUP_SPEC)
        for block in RULE_BLOCKS:
            ra, rb = rules_a.get(block), rules_b.get(block)
            if ra is None or rb is None:
                step(False, '%s 两份 spec 里都要有（abg 有=%r / setup 有=%r）'
                     % (block, ra is not None, rb is not None))
                continue
            extra = sorted(set(ra) - set(rb))
            miss = sorted(set(rb) - set(ra))
            step(not extra and not miss,
                 '%s 集合一致：共 %d 项（abg 多 %r / setup 多 %r）'
                 % (block, len(set(ra)), extra, miss))
            dups_a = sorted(set(x for x in ra if ra.count(x) > 1))
            dups_b = sorted(set(x for x in rb if rb.count(x) > 1))
            step(not dups_a and not dups_b,
                 '%s 两份都没有重复项（abg %d 项 / setup %d 项，重复 %r / %r）'
                 % (block, len(ra), len(rb), dups_a, dups_b))
        # trim_tk_data 比的是 lower() 之后的相对路径：清单里写 'tk/optMenu.tcl'
        # 是永远匹配不上的死条目（v2.2.0 真踩过），所以强制全小写。
        upper = sorted(x for x in rules_a.get('DROP_FILES', ()) if x != x.lower())
        step(not upper, 'DROP_FILES 全小写（大小写混写=死条目）：%r' % (upper,))
        body_a = rules_a.get('trim_tk_data')
        body_b = rules_b.get('trim_tk_data')
        step(bool(body_a) and body_a == body_b,
             'trim_tk_data 函数体一致（只允许 print 前缀 [spec] / [setup spec] 不同）')

        # ---------------- [6] Setup exe 内嵌 payload ----------------
        print('[6] Setup_WBOX.exe 内嵌的 payload 与磁盘件一致')
        if not os.path.isfile(SETUP_EXE):
            print('  skip Setup_WBOX.exe 不存在（只打了 payload）')
        else:
            try:
                from PyInstaller.archive.readers import CArchiveReader
            except ImportError:
                print('  skip 当前解释器没有 PyInstaller，无法解 CArchive')
                CArchiveReader = None
            if CArchiveReader is not None:
                blob = CArchiveReader(SETUP_EXE).extract('payload.zip')
                with open(PAYLOAD, 'rb') as f:
                    disk = f.read()
                step(blob == disk,
                     '内嵌 payload 与 installer\\payload.zip 逐字节相同（%d B）'
                     % len(blob))
                z2 = zipfile.ZipFile(io.BytesIO(blob))
                names2 = [n.replace('\\', '/') for n in z2.namelist()]
                step(not [n for n in names2 if n.startswith('plugins/')],
                     '内嵌 payload 里 0 个插件条目（本体不带插件）')
                # 安装目录里的 docs 也必须是内嵌那份解出来的，不能只存在磁盘 payload
                step('docs/插件开发指南.html' in names2
                     and 'docs/plugin-template/plugin.py' in names2,
                     '内嵌 payload 里也有 docs 四件套（装完能找到指南）')
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print('\nPASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
    if FAILED:
        for m in FAILED:
            print('  - ' + m)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
