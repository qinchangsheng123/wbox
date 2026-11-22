# -*- coding: utf-8 -*-
"""QA 独立验证 B：配置向后兼容（用真实 app\\config.json 的副本）。

CEO 关注点：升级到 v2.0.0 后，用户原有的 30 键配置是否一个不丢、三个功能是否
仍然"开"，面板拨开关是否只动它自己的键。

  [1] 三段合并后 30 键原值不变（逐键对比）
  [2] 三个功能开关 True / True / True
  [3] ACE 四道闸门秒数与限速参数
  [4] 弹窗 watch / early_close_enabled / protect_names
  [5] afterburner_path 原样
  [6] 面板拨开关（gui_toggle + 写盘）：只改自己的键，其它键逐键不变
  [7] v1 老键名 ace_guard -> ace_cleanup 迁移，grace_sec 仍是 180
  [8] plugin_cfg 不泄漏别人的段 / 内核键

用法：C:\\Python38\\python.exe test\\test_qa_config_compat.py
"""
import copy
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

import core_defaults  # noqa: E402
import plugin_host  # noqa: E402

REAL_CFG = os.path.join(ROOT, 'app', 'config.json')
PLUGINS_DIR = _plugin_src.require('config_compat（真实插件默认值）')

PASSED = []
FAILED = []


def step(cond, msg):
    if cond:
        PASSED.append(msg)
        print('  ok   ' + msg)
    else:
        FAILED.append(msg)
        print('  FAIL ' + msg)


def make_host(base, raw):
    host = plugin_host.PluginHost(base, log_func=lambda m: None, cfg={},
                                  plugins_dir=PLUGINS_DIR)
    host.load_all(None, write_state=False)
    return host


def flat(cfg, prefix=''):
    """把嵌套 dict 拍平成 {路径: 值}，便于逐键对比。"""
    out = {}
    if isinstance(cfg, dict):
        for k, v in cfg.items():
            p = prefix + '.' + k if prefix else k
            if isinstance(v, dict):
                out.update(flat(v, p))
            else:
                out[p] = v
    return out


def main():
    with open(REAL_CFG, 'r', encoding='utf-8-sig') as f:
        raw_real = json.load(f)

    tmp = tempfile.mkdtemp(prefix='abg_qa_cfg_')
    try:
        shutil.copy(REAL_CFG, os.path.join(tmp, 'config.json'))
        host = make_host(tmp, raw_real)

        raw = copy.deepcopy(raw_real)
        ids = [e.id for e in host.registry]
        # 前五个的顺序由 (order, id) 固定；shutdown_bin 是后加的内置插件，
        # 断言用「顺序前缀 + 包含」而不是全等，新增内置插件不该让这条挂掉。
        step(ids[:5] == ['afterburner', 'gamepp', 'ace', 'popup',
                         'desktop_hud'] and 'shutdown_bin' in ids,
             '注册表按 (order, id) 加载出内置插件：%r' % (ids,))

        # ---------------- [1] 合并后原值不变 ----------------
        merged = plugin_host.merge_defaults(core_defaults.DEFAULTS,
                                           host.registry, raw)
        lost, changed = [], []
        for path, val in flat(raw_real).items():
            if path.startswith('_'):
                continue
            got = plugin_host.plugin_api.cfg_get_path(merged, path, '<missing>')
            if got == '<missing>':
                lost.append(path)
            elif got != val:
                # 安全并集键（plugin_host.SAFE_UNION_PATHS）允许"用户值 + 默认值"的
                # 超集：protect_names 只该增加不该被覆盖，用户项一个不丢（下面单独断言）。
                if path in plugin_host.SAFE_UNION_PATHS:
                    continue
                changed.append('%s: %r -> %r' % (path, val, got))
        step(not lost, '真实配置的每个键在合并结果里都还在（丢键：%r）' % (lost or '无'))
        step(not changed, '真实配置的每个键值都没被默认值改掉（变了：%r）'
             % (changed[:5] or '无'))
        # 并集键：默认项在前、用户额外项追加（plugin_api.merge_name_list 的顺序）。
        # 断言只要求"用户项一个不丢"，不锁死顺序——顺序不是产品语义，锁了会误伤重构。
        for path in plugin_host.SAFE_UNION_PATHS:
            user_vals = plugin_host.plugin_api.cfg_get_path(raw_real, path, []) or []
            got_vals = plugin_host.plugin_api.cfg_get_path(merged, path, []) or []
            step(all(v in got_vals for v in user_vals),
                 '安全并集键 %s 保留了用户全部 %d 项：%r'
                 % (path, len(user_vals), got_vals))
        step(merged.get('_说明') == raw_real['_说明']
             and merged.get('_弹窗') == raw_real['_弹窗'],
             '中文说明键（_说明 / _弹窗）原样保留')

        # ---------------- [2] 三个功能开关 ----------------
        ab = plugin_host.plugin_api.cfg_get_path(merged, 'afterburner_enabled')
        ace_c = plugin_host.plugin_api.cfg_get_path(merged, 'ace_cleanup.enabled')
        ace_t = plugin_host.plugin_api.cfg_get_path(merged, 'ace_throttle.enabled')
        pop = plugin_host.plugin_api.cfg_get_path(merged, 'popup_guard.enabled')
        step(ab is True, '小飞机开关 afterburner_enabled = %r（期望 True）' % (ab,))
        step(ace_c is True and ace_t is True,
             'ACE 开关 ace_cleanup/ace_throttle.enabled = %r/%r（期望 True/True）'
             % (ace_c, ace_t))
        step(pop is True, '弹窗开关 popup_guard.enabled = %r（期望 True）' % (pop,))

        # ---------------- [3] ACE 闸门 + 限速 ----------------
        gate = dict((k, plugin_host.plugin_api.cfg_get_path(merged, 'ace_cleanup.' + k))
                    for k in ('grace_sec', 'min_ace_age_sec',
                              'quiet_after_update_sec', 'cooldown_sec'))
        step(gate == {'grace_sec': 180, 'min_ace_age_sec': 300,
                      'quiet_after_update_sec': 30, 'cooldown_sec': 600},
             'ACE 四道闸门 = grace180 / age300 / quiet30 / cooldown600：%r' % (gate,))
        th = dict((k, plugin_host.plugin_api.cfg_get_path(merged, 'ace_throttle.' + k))
                  for k in ('delay_sec', 'io_priority'))
        step(th == {'delay_sec': 20, 'io_priority': 0},
             'ACE 限速 = delay20 / io_priority0：%r' % (th,))

        # ---------------- [4] 弹窗 ----------------
        pg = merged.get('popup_guard') or {}
        step(pg.get('watch_sec') == 90, '弹窗 watch_sec = %r（期望 90）'
             % (pg.get('watch_sec'),))
        step(pg.get('early_close_enabled') is True,
             '弹窗 early_close_enabled = %r（期望 True）'
             % (pg.get('early_close_enabled'),))
        prot = [str(n).lower() for n in (pg.get('protect_names') or [])]
        step('wegame.exe' in prot, 'protect_names 含 wegame.exe：%r' % (prot,))
        if 'qq.exe' in prot:
            step(True, 'protect_names 含 qq.exe')
        else:
            step(False,
                 'protect_names 缺 qq.exe（合并结果 = %d 项：%r）；'
                 'v2.0.0 起 plugin_host.SAFE_UNION_PATHS 会对该键做安全并集'
                 '（默认 10 项 + 用户项），这里不该再出现缺项'
                 % (len(prot), prot))

        # ---------------- [5] afterburner_path ----------------
        step(merged.get('afterburner_path') == raw_real.get('afterburner_path'),
             'afterburner_path 原样：%r' % (merged.get('afterburner_path'),))

        # ---------------- [6] 面板拨开关 ----------------
        for pid, mine in (('afterburner', ['afterburner_enabled']),
                          ('ace', ['ace_cleanup.enabled', 'ace_throttle.enabled']),
                          ('popup', ['popup_guard.enabled'])):
            entry = host.entry_by_id(pid)
            if entry is None:
                step(False, '%s 插件不可用，无法测试拨开关' % pid)
                continue
            before = copy.deepcopy(raw)
            on_before = entry.plugin.gui_state(before)
            wrote = entry.plugin.gui_toggle(before, False)
            # 按面板的做法写盘（tmp + replace），再读回来
            path = os.path.join(tmp, 'config.json')
            with open(path + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(before, f, ensure_ascii=False, indent=2)
            os.replace(path + '.tmp', path)
            with open(path, 'r', encoding='utf-8-sig') as f:
                back = json.load(f)

            diff = []
            for k in set(flat(raw)) | set(flat(back)):
                if flat(raw).get(k, '<missing>') != flat(back).get(k, '<missing>'):
                    diff.append(k)
            step(wrote is True and on_before is True,
                 '%s：gui_state=True、gui_toggle 返回 True 且%s'
                 % (pid, '' if wrote and on_before else '（异常！）'))
            step(sorted(diff) == sorted(mine),
                 '%s 拨到关：只改了自己的键 %r（实际改动 %r）'
                 % (pid, mine, sorted(diff)))
            step(len(back.keys()) == len(raw.keys()),
                 '%s 拨开关后 config.json 顶层键数不变（%d -> %d）'
                 % (pid, len(raw.keys()), len(back.keys())))
            raw = back   # 下一轮从"已关"状态继续
            merged = plugin_host.merge_defaults(core_defaults.DEFAULTS,
                                               host.registry, raw)
            step(entry.plugin.gui_state(merged) is False,
                 '%s：拨到关后重新合并，gui_state 确实读到 False' % pid)
            entry.plugin.gui_toggle(raw, True)   # 还原成开，避免影响后续断言

        # ---------------- [7] ace_guard 老键迁移 ----------------
        legacy = copy.deepcopy(raw_real)
        for k in ('ace_cleanup', 'ace_throttle'):
            legacy.pop(k, None)
        legacy['ace_guard'] = {'enabled': True, 'max_idle_sec': 900,
                               'ace_processes': ['SGuard64.exe']}
        host2 = make_host(tmp, legacy)
        host2.dispatch('migrate', legacy)
        step(isinstance(legacy.get('ace_cleanup'), dict),
             'migrate 后 raw 里出现 ace_cleanup 段')
        step(legacy.get('ace_guard') is not None,
             'migrate 不动旧段（仍可回滚，v1 行为一致）')
        merged2 = plugin_host.merge_defaults(core_defaults.DEFAULTS,
                                            host2.registry, legacy)
        step(merged2['ace_cleanup']['grace_sec'] == 180,
             '迁移后 grace_sec 仍是 180：%r' % (merged2['ace_cleanup']['grace_sec'],))
        step(merged2['ace_cleanup']['max_idle_sec'] == 900,
             '迁移后 max_idle_sec 取旧值 900：%r'
             % (merged2['ace_cleanup']['max_idle_sec'],))
        step(merged2['ace_cleanup']['ace_processes'] == ['SGuard64.exe'],
             '迁移后 ace_processes 取旧值')
        step(merged2['ace_cleanup']['min_ace_age_sec'] == 300,
             '迁移后未指定的闸门仍取默认 300')

        # ---------------- [8] plugin_cfg 隔离 ----------------
        entry_popup = host.entry_by_id('popup')
        own = plugin_host.plugin_cfg(merged, entry_popup)
        step('popup_guard' in own, 'popup 的 plugin_cfg 里有自己那段')
        step('game_processes' not in own and 'ui_theme' not in own
             and 'ace_cleanup' not in own,
             'popup 的 plugin_cfg 不含内核键与别人的段：%r' % (sorted(own),))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print('\nPASS %d / FAIL %d' % (len(PASSED), len(FAILED)))
    if FAILED:
        print('FAILED:')
        for m in FAILED:
            print('  - ' + m)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
