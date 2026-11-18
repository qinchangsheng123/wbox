# -*- coding: utf-8 -*-
"""弹窗插件「保护名单并集」回归（QA FAIL：protect_names 只剩 5 项）。

现象（QA 独立复现）：用户 config.json 按 v1 模板只写了 5 项 protect_names，
配置三段合并用的是 `plugin_api.deep_merge`，它对 list 是**整体替换**，
于是内核默认的 10 项被覆盖成 5 项 —— qq.exe / wechat.exe / weixin.exe /
tim.exe / qqprotect.exe 从保护名单里消失。游戏退出后清宿主进程树时会把
常驻 QQ/微信一起结束，用户直接掉线。

断言：
  [1] merge_protect_names：默认 10 + 用户 5 -> 10 项，含 qq.exe，原 5 项一个不少
  [2] 去重（大小写 / 首尾空白）、非字符串项剔除、None/空 安全
  [3] 真实三段合并再现"只剩 5 项"，经插件 on_config 后 PopupGuard 拿到 10 项
  [4] 合并只写回给 PopupGuard 的副本：传进去的 cfg 与 config.json 都未被改
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_src  # noqa: E402  插件源码在独立仓库 wbox-plugins

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'src'))
import core_defaults          # noqa: E402
import plugin_api             # noqa: E402
import plugin_host            # noqa: E402

FAILS = []
LOGS = []
TMP = tempfile.mkdtemp(prefix='abg_popup_cfg_')

# 用户配置里的 5 项（QA 报的那一份，与 v1 config.json 完全一致）
USER_5 = ['explorer.exe', 'wegame.exe', 'wegame_env.exe',
          'tcls_core.exe', 'tgame.exe']
DEFAULTS_10 = core_defaults.DEFAULTS['popup_guard']['protect_names']


def check(cond, msg):
    print(('  ok   ' if cond else '  FAIL ') + msg)
    if not cond:
        FAILS.append(msg)


def load_plugin_module():
    """按文件路径加载插件模块（不进 sys.path、不参与插件合成包命名规则）。"""
    path = os.path.join(_plugin_src.require('popup'), 'popup', 'plugin.py')
    spec = importlib.util.spec_from_file_location('popup_plugin_ut', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeCtx(object):
    """够用就行的内核上下文：只实现插件真正用到的四个成员。"""

    def __init__(self, cfg):
        self._cfg = cfg
        self.providers = {}

    def import_core(self, name):
        return importlib.import_module(name)

    def log(self, msg):
        LOGS.append(msg)

    def games_present(self):
        return False

    def cfg(self, key, default=None):
        return self._cfg.get(key, default)


def main():
    mod = load_plugin_module()

    # ---------------- [1] 并集本身 ----------------
    print('[1] merge_protect_names：默认 10 项 + 用户 5 项 -> 10 项，一项不少')
    check(len(DEFAULTS_10) == 10, '内核默认保护名单是 10 项（前置条件）')
    merged = mod.merge_protect_names(USER_5, DEFAULTS_10)
    check(len(merged) == 10, '并集后 10 项：%r' % (merged,))
    check('qq.exe' in merged, 'qq.exe 回到保护名单（QA 复现的那一项）')
    missing = [n for n in USER_5 if n not in merged]
    check(not missing, '用户原来那 5 项一个不少：缺 %r' % (missing,))
    missing = [n for n in DEFAULTS_10 if n not in merged]
    check(not missing, '内核默认 10 项全部在内：缺 %r' % (missing,))
    check(merged[:len(DEFAULTS_10)] == DEFAULTS_10,
          '顺序：默认项在前，用户额外项追加在后（PopupGuard 按原顺序遍历）')

    # ---------------- [2] 去重与脏数据 ----------------
    print('[2] 去重（大小写 / 空白）与脏数据')
    dup = mod.merge_protect_names([' QQ.EXE ', 'qq.exe', 'WeChat.exe', '', None, 7],
                                  ['qq.exe', 'wechat.exe'])
    check(dup == ['qq.exe', 'wechat.exe'], '大小写/空白去重且保留首次出现顺序：%r' % (dup,))
    check(mod.merge_protect_names(None, DEFAULTS_10) == DEFAULTS_10,
          '用户没配（None）时等于默认 10 项')
    check(mod.merge_protect_names([], None) == [], '两边都空 -> 空列表，不抛异常')
    check(mod.merge_protect_names(['notepad.exe'], None) == ['notepad.exe'],
          '读不到内核默认值时保住用户的项（退化但不丢配置）')

    # ---------------- [3] 真实三段合并 + 插件 on_config ----------------
    print('[3] 真实的三段合并（内核并集）+ 插件层（插件子集/老内核）两道防线')
    cfg_path = os.path.join(TMP, 'config.json')
    user_cfg = {
        'ui_theme': 'light',
        'popup_guard': {'enabled': True, 'watch_sec': 90,
                        'protect_names': list(USER_5)},
    }
    with open(cfg_path, 'w', encoding='utf-8') as f:
        json.dump(user_cfg, f, ensure_ascii=False, indent=2)
    before = open(cfg_path, 'rb').read()

    plugin_dir = os.path.join(_plugin_src.require('popup'), 'popup')
    entry = plugin_host.parse_manifest(plugin_dir, api=plugin_api.API_VERSION)
    check(entry.status == plugin_host.STATUS_OK, '弹窗插件 manifest 合法')
    with open(cfg_path, 'r', encoding='utf-8') as f:
        raw_cfg = json.load(f)

    # 内核层：merge_defaults 对 SAFE_UNION_PATHS 做并集，面板/守护/自检口径一致
    check('popup_guard.protect_names' in plugin_host.SAFE_UNION_PATHS,
          'protect_names 登记在内核 SAFE_UNION_PATHS 里')
    cfg = plugin_host.merge_defaults(core_defaults.DEFAULTS, [entry], raw_cfg)
    got_cfg = cfg['popup_guard']['protect_names']
    check(len(got_cfg) == 10 and 'qq.exe' in got_cfg,
          'merge_defaults 的结果已是 10 项且含 qq.exe：%r' % (got_cfg,))
    check(all(n in got_cfg for n in USER_5), '内核并集没有丢掉用户那 5 项')

    plugin = mod.Plugin(FakeCtx(cfg))
    plugin.on_config(cfg)
    got = plugin.guard.cfg.get('protect_names') or []
    check(len(got) == 10, 'PopupGuard 实际拿到 10 项保护名单：%r' % (got,))
    check('qq.exe' in got, 'qq.exe 在 PopupGuard 的保护名单里')
    check(plugin.protect == got, '插件自己记下的保护名单与实际生效的一致（self-test 可查）')

    # 插件层：即使内核没做并集（老内核 / plugin_cfg 只给 5 项的配置子集），
    # 插件也必须自己补 —— 这道防线不依赖内核版本。
    seg5 = {'popup_guard': {'enabled': True, 'watch_sec': 90,
                            'protect_names': list(USER_5)}}
    plugin2 = mod.Plugin(FakeCtx(seg5))
    plugin2.on_config(seg5)
    got2 = plugin2.guard.cfg['protect_names']
    check(len(got2) == 10 and 'qq.exe' in got2,
          '只给 5 项配置时插件自己补齐到 10 项（老内核/配置子集也安全）')
    check(seg5['popup_guard']['protect_names'] == USER_5,
          '插件层并集只改副本，传进去的段未被就地修改')

    # ---------------- [4] 不回写 ----------------
    print('[4] 只改合并结果/传给 PopupGuard 的副本：入参与 config.json 都不动')
    check(raw_cfg['popup_guard']['protect_names'] == USER_5,
          '传进 merge_defaults 的原始配置未被就地修改（仍是 5 项，纯函数）')
    check(open(cfg_path, 'rb').read() == before,
          'config.json 逐字节未变（并集不回写磁盘）')

    # 配置里本来就有 10 项时，日志不该刷"补齐"
    quiet_logs = len(LOGS)
    cfg2 = json.loads(json.dumps(cfg))
    cfg2['popup_guard']['protect_names'] = list(DEFAULTS_10)
    plugin.on_config(cfg2)
    new_logs = [m for m in LOGS[quiet_logs:] if '保护名单已补齐' in m]
    check(not new_logs, '10 项齐全时不打「已补齐」日志（避免每轮刷屏）')
    check(len(plugin.guard.cfg['protect_names']) == 10, '10 项配置仍然是 10 项')

    # 用户额外加一项自定义保护：必须保留
    cfg3 = json.loads(json.dumps(cfg))
    cfg3['popup_guard']['protect_names'] = ['my_launcher.exe', 'qq.exe']
    plugin.on_config(cfg3)
    got3 = plugin.guard.cfg['protect_names']
    check('my_launcher.exe' in got3 and len(got3) == 11,
          '用户自定义项保留且去重后 11 项：%r' % (got3,))


try:
    main()
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print('')
if FAILS:
    print('FAILED: %d -> %s' % (len(FAILS), FAILS))
    sys.exit(1)
print('ALL PASS')
sys.exit(0)
