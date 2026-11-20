# -*- coding: utf-8 -*-
"""WBOX - 配置校验（v2.13.0，审查项 5）。

配置有四个来源：面板、旧版 config.json、手工编辑、插件运行期状态文件。
以前每个消费点各写各的 `.get(..., 默认)`：类型不对（字符串 "6" 当秒数）、
超范围（poll_interval_sec=0 把守护变成忙等）、枚举写错（position="居中"）
都会安静地跑出怪行为，日志里只有结果没有原因。

这里把「类型 / 范围 / 枚举 / 列表元素」收成一份声明式规则，加载配置后统一
过一遍：
  - 能安全修正的（"6" -> 6.0、-1 -> 下限）就地修正；
  - 修正不了的（"abc"、枚举不认识）回退到默认值；
  - 每一项都记 {path, got, fallback, why}，由守护写进日志与
    `config_issues.json`，自检与诊断包直接可读。

原则：**只修正值，不删键**。未知键原样保留（插件可能自己读），这里不扮演
schema 警察；校验失败也绝不让守护起不来。
"""
import copy

import core_defaults
import plugin_api


def _num(v):
    """把 "6"、"6.0" 这类字符串转成数字；其余返回 None。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


# path -> (kind, min, max, enum, fallback)
# kind: int / float / bool / list / enum / str
# fallback 为 None 时用 core_defaults.DEFAULTS 里同一路径的值。
CORE_SPEC = {
    'poll_interval_sec': ('float', 0.2, 60.0, None, None),
    'poll_fast_sec': ('float', 0.1, 10.0, None, None),
    'exit_grace_sec': ('int', 0, 300, None, None),
    'close_wait_sec': ('int', 0, 120, None, None),
    'rescan_roots_sec': ('int', 30, 86400, None, None),
    'log_max_kb': ('int', 64, 8192, None, None),
    'min_game_exe_mb': ('float', 0.0, 10240.0, None, None),
    'max_scan_depth': ('int', 1, 32, None, None),
    'afterburner_path': ('str', None, None, None, ''),
    'game_processes': ('list', None, None, None, None),
    'game_roots': ('list', None, None, None, None),
    'exclude_processes': ('list', None, None, None, None),
    'exclude_dir_keywords': ('list', None, None, None, None),
    'exclude_name_keywords': ('list', None, None, None, None),
    'rtss_processes': ('list', None, None, None, None),
    'force_close': ('bool', None, None, None, None),
    'manage_rtss': ('bool', None, None, None, None),
    'afterburner_enabled': ('bool', None, None, None, None),
    'desktop_hud_enabled': ('bool', None, None, None, None),
    'game_lib.enabled': ('bool', None, None, None, None),
    'game_lib.rescan_hours': ('int', 1, 168, None, None),
    'game_lib.steam': ('bool', None, None, None, None),
    'game_lib.epic': ('bool', None, None, None, None),
    'game_lib.wegame': ('bool', None, None, None, None),
    'game_lib.gog': ('bool', None, None, None, None),
    'game_lib.ubisoft': ('bool', None, None, None, None),
    'ace_cleanup.enabled': ('bool', None, None, None, None),
    'ace_cleanup.grace_sec': ('int', 0, 7200, None, None),
    'ace_cleanup.min_ace_age_sec': ('int', 0, 7200, None, None),
    'ace_cleanup.quiet_after_update_sec': ('int', 0, 600, None, None),
    'ace_cleanup.cooldown_sec': ('int', 0, 86400, None, None),
    'ace_cleanup.eval_interval_sec': ('int', 5, 3600, None, None),
    'ace_cleanup.max_idle_sec': ('int', 60, 86400, None, None),
    'ace_cleanup.dry_run': ('bool', None, None, None, None),
    'ace_cleanup.ace_processes': ('list', None, None, None, None),
    'ace_cleanup.ace_services': ('list', None, None, None, None),
    'ace_cleanup.blockers': ('list', None, None, None, None),
    'ace_throttle.enabled': ('bool', None, None, None, None),
    'ace_throttle.delay_sec': ('int', 0, 600, None, None),
    'ace_throttle.io_priority': ('int', 0, 2, None, None),
    'ace_throttle.affinity_last_core': ('bool', None, None, None, None),
    'ace_throttle.processes': ('list', None, None, None, None),
    'ace_throttle.update_processes': ('list', None, None, None, None),
    'popup_guard.enabled': ('bool', None, None, None, None),
    'popup_guard.delay_sec': ('int', 0, 600, None, None),
    'popup_guard.watch_sec': ('int', 0, 3600, None, None),
    'popup_guard.scan_interval_sec': ('int', 0, 600, None, None),
    'popup_guard.early_close_enabled': ('bool', None, None, None, None),
    'popup_guard.early_max_tries': ('int', 0, 100, None, None),
    'popup_guard.kill_tree': ('bool', None, None, None, None),
    'popup_guard.log_all_new': ('bool', None, None, None, None),
    'popup_guard.observe_only': ('bool', None, None, None, None),
    'popup_guard.process_names': ('list', None, None, None, None),
    'popup_guard.path_hints': ('list', None, None, None, None),
    'popup_guard.title_keywords': ('list', None, None, None, None),
    'popup_guard.path_keywords': ('list', None, None, None, None),
    'popup_guard.protect_names': ('list', None, None, None, None),
}

# 插件段的额外规则（manifest.settings 之外的运行期参数）
PLUGIN_SPEC = {
    'close_delay_sec': ('int', 0, 60, None, 0),
    'gamepp.close_delay_sec': ('int', 0, 60, None, 0),
    'gamepp.close_wait_sec': ('int', 0, 120, None, 6),
    'gamepp.enabled': ('bool', None, None, None, True),
    'gamepp.path': ('str', None, None, None, ''),
    'gamepp.processes': ('list', None, None, None, None),
    'desktop_hud.refresh_ms': ('int', 200, 10000, None, None),
    'desktop_hud.opacity': ('int', 40, 255, None, None),
    'desktop_hud.font_size': ('int', 9, 32, None, None),
    'desktop_hud.pct_decimals': ('int', 0, 1, None, None),
    'desktop_hud.warn_percent': ('int', 0, 100, None, None),
    'desktop_hud.high_percent': ('int', 0, 100, None, None),
    'desktop_hud.position': ('enum', None, None,
                             ('top-right', 'top-left', 'bottom-right',
                              'bottom-left', 'top-center', 'bottom-center',
                              'left-center', 'right-center', 'custom'), None),
    'desktop_hud.gpu_select': ('enum', None, None, ('auto', 'load', 'luid'), None),
    'desktop_hud.gpu_luid': ('str', None, None, None, ''),
    'desktop_hud.x': ('int', -100000, 100000, None, None),
    'desktop_hud.y': ('int', -100000, 100000, None, None),
}

_ALL_SPEC = dict(CORE_SPEC)
_ALL_SPEC.update(PLUGIN_SPEC)

# popup 保护名单的硬底线：这些常驻进程绝不能被用户配置删掉（删了会掉线）
PROTECT_MUST_HAVE = ('explorer.exe',)


def _default_for(path, defaults):
    v = plugin_api.cfg_get_path(defaults, path, None)
    if v is not None:
        return v
    v = plugin_api.cfg_get_path(core_defaults.DEFAULTS, path, None)
    return v


def _coerce(path, value, rule, defaults):
    """返回 (新值, 说明或 None)。说明非空表示这条值被修正/回退过。"""
    kind, lo, hi, enum, fallback = rule
    if kind == 'bool':
        if isinstance(value, bool):
            return value, None
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value), '数值 %r 转布尔' % (value,)
        if isinstance(value, str) and value.strip().lower() in (
                'true', '1', 'yes', 'on', '开'):
            return True, '字符串 %r 转布尔' % (value,)
        if isinstance(value, str) and value.strip().lower() in (
                'false', '0', 'no', 'off', '关', ''):
            return False, '字符串 %r 转布尔' % (value,)
    elif kind in ('int', 'float'):
        n = _num(value)
        if n is None:
            return None, '不是数字：%r' % (value,)
        coerced = None
        if kind == 'int':
            n = int(round(n))
            if isinstance(value, str):
                coerced = '字符串 %r 转整数' % (value,)
            elif isinstance(value, float) and abs(value - n) > 1e-9:
                coerced = '小数 %r 取整' % (value,)
        else:
            n = float(n)
            if isinstance(value, str):
                coerced = '字符串 %r 转数字' % (value,)
        if lo is not None and n < lo:
            return lo, '小于下限 %s，已提到下限' % lo
        if hi is not None and n > hi:
            return hi, '大于上限 %s，已压到上限' % hi
        return n, coerced
    elif kind == 'enum':
        if value in enum:
            return value, None
        return None, '不在可选值 %r 内' % (list(enum),)
    elif kind == 'list':
        if not isinstance(value, list):
            return None, '不是列表'
        out, dropped = [], 0
        for item in value:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    out.append(s)
                    continue
            dropped += 1
        if dropped:
            return out, '丢弃 %d 个非字符串/空元素' % dropped
        return out, None
    elif kind == 'str':
        if isinstance(value, str):
            return value, None
        if value is None:
            return '', '空值转空字符串'
        return str(value), '非字符串 %r 转字符串' % (value,)
    return value, None


def validate(cfg, defaults=None, entries=None):
    """校验整份合并后的配置，返回 (新配置, 问题列表)。

    - 纯函数：不修改入参（deepcopy 后处理），调用方拿到的是新 dict。
    - 问题列表元素：{'path','got','value','why'}，`value` 是最终采用的值。
    - 只处理声明过的路径；未知键原样保留。
    """
    defaults = cfg if defaults is None else defaults
    out = copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
    issues = []

    for path, rule in _ALL_SPEC.items():
        head = path.split('.', 1)[0]
        if head not in out:
            continue                    # 整段不存在：保持"未配置"，不凭空造段
        if '.' in path and not isinstance(out.get(head), dict):
            issues.append({'path': path, 'got': out.get(head), 'value': None,
                           'why': '段不是对象，整段被忽略'})
            continue
        sentinel = object()
        got = plugin_api.cfg_get_path(out, path, sentinel)
        if got is sentinel:
            continue
        new, why = _coerce(path, got, rule, defaults)
        if why is None:
            continue
        if new is None:
            new = rule[4] if rule[4] is not None else _default_for(path, defaults)
        plugin_api.cfg_set_path(out, path, new)
        issues.append({'path': path, 'got': got, 'value': new, 'why': why})

    # manifest.settings 声明的插件设置项：类型/范围/选项以插件自己的声明为准
    for entry in entries or []:
        for spec in getattr(entry, 'settings', None) or []:
            path = spec.get('key')
            if not path or path in _ALL_SPEC:
                continue
            head = path.split('.', 1)[0]
            if head not in out:
                continue
            got = plugin_api.cfg_get_path(out, path, None)
            if got is None:
                continue
            new, why = _coerce_setting(spec, got)
            if why is None:
                continue
            if new is None:
                new = plugin_api.cfg_get_path(defaults, path, None)
                if new is None:
                    # 没有可用默认值：保留原值但把问题记下来（插件自己会看到
                    # value 为 None，表示"这条没被修正"）。
                    issues.append({'path': path, 'got': got, 'value': None,
                                   'why': why + '（无可回退默认值）'})
                    continue
            plugin_api.cfg_set_path(out, path, new)
            issues.append({'path': path, 'got': got, 'value': new, 'why': why})

    # 保护名单底线：无论谁改配置，explorer.exe 等必须留在名单里
    names = plugin_api.cfg_get_path(out, 'popup_guard.protect_names', None)
    if isinstance(names, list):
        low = set(n.lower() for n in names)
        missing = [n for n in PROTECT_MUST_HAVE if n not in low]
        if missing:
            plugin_api.cfg_set_path(out, 'popup_guard.protect_names',
                                    list(names) + missing)
            issues.append({'path': 'popup_guard.protect_names', 'got': names,
                           'value': list(names) + missing,
                           'why': '补齐必需的保护项 %r' % (missing,)})
    return out, issues


def _coerce_setting(spec, got):
    """按 manifest.settings 的单条声明校验一个值。"""
    typ = spec.get('type')
    if typ == 'bool':
        if isinstance(got, bool):
            return got, None
        return None, '需要布尔值'
    if typ == 'int':
        n = _num(got)
        if n is None:
            return None, '不是数字：%r' % (got,)
        n = int(round(n))
        lo, hi = spec.get('min'), spec.get('max')
        if lo is not None and n < lo:
            return lo, '小于下限 %s' % lo
        if hi is not None and n > hi:
            return hi, '大于上限 %s' % hi
        return n, None if n == got else '规整为整数 %d' % n
    if typ == 'choice':
        values = [o[0] for o in (spec.get('options') or [])]
        if got in values:
            return got, None
        return None, '不在可选值 %r 内' % (values,)
    if typ == 'path':
        if isinstance(got, str):
            return got, None
        return '', '路径不是字符串'
    return got, None


def describe(issues, limit=6):
    """把问题列表压成一行可读文案（日志用）。"""
    if not issues:
        return ''
    parts = ['%s=%r（%s -> %r）' % (i['path'], i.get('got'), i.get('why'),
                                    i.get('value')) for i in issues[:limit]]
    more = '' if len(issues) <= limit else ' 等 %d 项' % len(issues)
    return '；'.join(parts) + more


def signature(issues):
    """用于日志去重：同一批问题只记一次。"""
    return '|'.join('%s:%s' % (i.get('path'), i.get('why')) for i in issues)
