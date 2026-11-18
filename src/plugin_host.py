# -*- coding: utf-8 -*-
"""WBOX - 插件宿主（发现 / 校验 / 加载 / 隔离 / 分发 / 状态落盘）。

本模块的三条硬约束（来自 design\\plugin-arch.md 第 1、3、9 节）：
  1. **绝不把插件目录加入 sys.path**：`plugins\\x\\json.py` 会污染整个进程的
     标准库导入，属可被利用的劫持面。插件用合成包名 `wbox_plugin_<id>` 注册，
     `submodule_search_locations` 指向插件目录，于是 `from . import helper` 仍可用。
  2. **单插件故障不影响守护**：所有钩子调用都过 `_invoke`，`try/except BaseException`
     （含 SystemExit——插件调 sys.exit() 会带走整个守护，必须拦）。
  3. **零插件也能跑**：plugins 目录不存在 / 为空时 registry 为 []，所有 dispatch
     都是空循环，不抛异常。

本模块不 import Win32 / tkinter，可被面板与离线测试直接使用。
"""
import importlib
import importlib.util
import json
import os
import re
import sys
import time
import types

import plugin_api

# ---------------------------------------------------------------- 常量

# 插件 id 规则：小写字母开头，只含小写字母/数字/下划线，长度 2..32
ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]{1,31}$')

DEFAULT_ENTRY = 'plugin.py:Plugin'
DEFAULT_ICON = 'dot'
DEFAULT_ORDER = 100
DEFAULT_API = plugin_api.API_VERSION

# 单次钩子耗时超过这个秒数就记一条警告。Python 无法安全中断同步调用，
# 所以只告警不终止（文档 3.3）。
HOOK_WARN_SEC = 5.0

# 同类异常 60 秒内只记一条日志，避免每轮 tick 刷爆 guard.log
ERR_LOG_WINDOW_SEC = 60.0

# on_tick 连续失败到这个次数后降频为每 TICK_SKIP_ROUNDS 轮一次，成功一次立即恢复
TICK_FAIL_LIMIT = 10
TICK_SKIP_ROUNDS = 30

# plugins_state.json 的心跳：状态没变也最多每 30 秒落一次盘
STATE_HEARTBEAT_SEC = 30.0

STATUS_OK = 'ok'
STATUS_DEGRADED = 'degraded'
STATUS_BAD_MANIFEST = 'bad_manifest'
STATUS_TOO_NEW = 'too_new'
STATUS_CONFLICT = 'conflict'
STATUS_LOAD_FAILED = 'load_failed'

# 只有这两种状态的插件会被分发钩子
USABLE_STATUS = frozenset((STATUS_OK, STATUS_DEGRADED))

# 插件允许通过 ctx.import_core 取到的内核低层库（白名单，未知名字抛 KeyError）。
# guard_core 在名单里是为了那批 Win32 优先级/启动辅助函数（throttle_process /
# restore_process / run_hidden / TASKKILL…）：它们是 v1 就在内核里冻结的实现，
# 不能复制一份到插件（复制就会出现"插件自己那份逻辑绕过闸门"的口子）。
# 插件只在守护进程的钩子里懒加载它，面板进程走 gui_* 时不会把 Win32 拉进来。
CORE_MODULES = frozenset(('ace_native', 'popup_guard', 'game_lib',
                          'core_defaults', 'plugin_api', 'guard_core'))

# 插件不得声明/写入的内核保留键前缀（文档 4.4）
_RESERVED_PREFIXES = ('poll_', 'exclude_', '_')
_RESERVED_KEYS = frozenset(('game_processes', 'game_roots', 'exit_grace_sec',
                            'log_max_kb', 'ui_theme'))


def _noop_log(msg):
    """默认日志：宿主被单独使用时（如测试）不写到任何地方。"""


def _is_reserved_key(path):
    """判断配置路径是否是内核保留键（插件只有读权、没有写权）。"""
    root = str(path).split('.')[0]
    if root in _RESERVED_KEYS:
        return True
    for prefix in _RESERVED_PREFIXES:
        if root.startswith(prefix):
            return True
    return False


def _atomic_write_json(path, obj):
    """原子写 JSON：先写 .tmp 再 os.replace，避免面板读到半截文件。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 目录解析
def resolve_plugins_dir(base):
    """解析插件目录：`<base>\\plugins`，源码调试时回退到上一层的 `plugins\\`。

    为什么要有公共函数：守护（guard_core）和面板（settings_gui）必须指向同一个
    目录，否则会出现"面板能看到插件、守护看不到"这种最难查的分裂状态。规则只能
    写在这一处（文档 10 节 S6 的硬要求）。

    生产环境（app\\WBOX.exe）：BASE=app\\，插件在 app\\plugins\\ → 第一条命中。
    源码调试：BASE 常常是 src\\，而插件按设计放在仓库根的 plugins\\ → 往上一层。
    两条都不存在时返回 `<base>\\plugins`（规范位置），调用方 makedirs 兜底。
    """
    primary = os.path.join(base, 'plugins')
    if os.path.isdir(primary):
        return primary
    fallback = os.path.join(os.path.dirname(os.path.abspath(base)), 'plugins')
    if os.path.isdir(fallback):
        return fallback
    return primary


# ---------------------------------------------------------------- 注册表条目
class RegistryEntry(object):
    """一个插件目录的全部已知信息 + 运行状态。

    `plugin` 为 None 表示没有实例（manifest 非法 / id 冲突 / 加载失败），
    但条目本身仍然保留：面板要展示「加载失败」这一行，不能静默丢掉。
    """

    def __init__(self, dir=''):
        self.id = ''
        self.name = ''
        self.version = ''
        self.api = 0
        self.icon = DEFAULT_ICON
        self.tip = ''
        self.order = DEFAULT_ORDER
        self.home = True
        self.toggle = []
        self.config_keys = []
        self.requires = []
        self.builtin = False
        # manifest 声明的运行前提：需要管理员权限（面板在未提权时提示用户）。
        # 缺省 False —— 老插件不写这个字段就该当"不需要"，不能因此判定 manifest 非法。
        self.requires_admin = False
        # manifest.settings 解析结果：面板「每插件齿轮」据此生成设置弹框。
        # 空列表 = 该插件没有可调项，面板不显示齿轮（老插件天然兼容）。
        self.settings = []
        # 来源信息（v2.11.3）：publisher / homepage / permissions 只做展示，
        # 宿主不据此授予任何权限——插件本来就是完全信任的代码（见插件规范 §0）。
        # 缺省为空字符串 / 空列表，老插件不写这些字段照样能加载。
        self.publisher = ''
        self.homepage = ''
        self.permissions = []
        self.manifest = {}
        # entry 拆分结果（文件名 / 类名）
        self.entry_file = 'plugin.py'
        self.entry_class = 'Plugin'
        # 运行状态
        self.status = STATUS_OK
        self.detail = ''
        self.error_count = 0
        self.last_error = ''
        self.last_error_at = 0.0
        self.ticks = 0
        self.tick_fails = 0      # on_tick 连续失败次数（降频判定）
        self.tick_skip = 0       # 降频后跳过的轮次计数
        # 最近一次 on_tick 真正跑完的时刻（心跳管道发给面板用；内存字段，
        # 不进 plugins_state.json —— 那份文件的口径见文档 6.4，别混）。
        self.last_tick_at = 0.0
        self.state_text = ''     # ctx.set_status() 写的一行摘要
        self.plugin = None
        self.dir = dir

    @property
    def usable(self):
        return self.plugin is not None and self.status in USABLE_STATUS

    def to_state_dict(self):
        """plugins_state.json 里的单条记录（字段见文档 6.4）。"""
        return {
            'id': self.id,
            'version': self.version,
            'status': self.status,
            'detail': self.detail,
            'error_count': self.error_count,
            'last_error': self.last_error,
            'last_error_at': round(self.last_error_at, 3),
            'ticks': self.ticks,
        }

    def __repr__(self):
        return 'RegistryEntry(id=%r, version=%r, order=%r, status=%r)' % (
            self.id, self.version, self.order, self.status)


# ---------------------------------------------------------------- manifest
def _read_manifest_json(plugin_dir):
    """读 plugin.json（utf-8-sig，允许 BOM，与 config.json 一致）。

    返回 (raw, error_text)：两者恰好有一个为 None。
    """
    path = os.path.join(plugin_dir, 'plugin.json')
    if not os.path.isfile(path):
        return None, '缺少 plugin.json'
    try:
        with open(path, 'r', encoding='utf-8-sig') as f:
            raw = json.load(f)
    except (OSError, ValueError) as ex:
        return None, 'plugin.json 读取/解析失败：%r' % (ex,)
    if not isinstance(raw, dict):
        return None, 'plugin.json 根节点必须是对象'
    return raw, None


def _as_bool(value, default=False):
    """manifest 里的布尔字段：只认真正的 bool，其它类型回落默认值。"""
    return value if isinstance(value, bool) else default


def _as_int(value, default):
    """manifest 里的整数字段：bool 是 int 的子类，必须显式排除。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _as_str(value, default=''):
    return value if isinstance(value, str) else default


def _as_path_list(value, reserved_filter=True):
    """把 manifest 的字符串数组洗成干净的路径列表（去空、去非字符串、去保留键）。"""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, str):
            continue
        item = item.strip()
        if not item:
            continue
        if reserved_filter and _is_reserved_key(item):
            continue
        out.append(item)
    return out


# manifest.settings 支持的控件类型。新增类型必须同时改两处：
# 面板渲染（settings_gui.build_plugin_settings 的 if 分支）与 docs/PLUGIN-SPEC.md。
SETTING_TYPES = ('bool', 'int', 'choice', 'path')
# 设置项的 key 是"点路径"（ace_throttle.enabled），只允许字母数字下划线与点。
SETTING_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$')


def _parse_settings(value):
    """manifest.settings -> 干净的设置项列表。

    **非法项一律丢弃、不判 manifest 失败**：这是展示层字段，写坏一项不该让整个
    插件变成 bad_manifest（那会让一个本来能跑的插件彻底不工作）。丢弃的同时
    不打日志——parse_manifest 在面板里会被频繁调用，噪声会淹掉真问题。
    """
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key = item.get('key')
        typ = item.get('type')
        label = item.get('label')
        if not isinstance(key, str) or not SETTING_KEY.match(key.strip()):
            continue
        if typ not in SETTING_TYPES:
            continue
        if not isinstance(label, str) or not label.strip():
            continue
        spec = {'key': key.strip(), 'type': typ, 'label': label.strip(),
                'hint': _as_str(item.get('hint'), '').strip()}
        if typ == 'int':
            lo = _as_int(item.get('min'), 0)
            hi = _as_int(item.get('max'), 100)
            if hi < lo:
                lo, hi = hi, lo
            spec['min'] = lo
            spec['max'] = hi
            spec['step'] = max(1, _as_int(item.get('step'), 1))
            spec['unit'] = _as_str(item.get('unit'), '').strip()
            # 小数参数（如 refresh_sec）不在这里开：面板是整数步进器，
            # 需要小数的插件自己用代码默认值，别塞进设置面板。
        elif typ == 'choice':
            opts = []
            for o in (item.get('options') or []):
                if isinstance(o, dict):
                    v, lab = o.get('value'), o.get('label')
                elif isinstance(o, (list, tuple)) and len(o) == 2:
                    v, lab = o[0], o[1]
                else:
                    continue
                if isinstance(v, str) and isinstance(lab, str) and v.strip() and lab.strip():
                    opts.append((v.strip(), lab.strip()))
            if not opts:
                continue        # 没有选项的 choice 渲染出来是空壳，直接丢掉
            spec['options'] = opts
        out.append(spec)
    return out


def parse_manifest(plugin_dir, api=DEFAULT_API, log_func=None):
    """把 `<plugin_dir>\\plugin.json` 解析成 RegistryEntry（只校验，不 import 代码）。

    校验顺序刻意如此：先把所有 manifest 层面的问题判完，再看 api 版本，
    最后才决定要不要 import——manifest 非法时**绝不 import 插件代码**，
    这样用户写坏一个 json 不会导致插件里的半成品代码被执行。
    """
    log = log_func or _noop_log
    entry = RegistryEntry(dir=plugin_dir)
    dir_name = os.path.basename(plugin_dir.rstrip('\\/')) or plugin_dir
    entry.name = dir_name

    raw, err = _read_manifest_json(plugin_dir)
    if err:
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = err
        return entry
    entry.manifest = raw

    # --- id ---
    pid = raw.get('id')
    if not isinstance(pid, str) or not pid:
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'manifest 缺少 id'
        return entry
    if not ID_PATTERN.match(pid):
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'id 非法（需匹配 ^[a-z][a-z0-9_]{1,31}$）：%r' % (pid,)
        return entry
    entry.id = pid

    # --- name / version / api（必填；缺任一即整条拒绝）---
    # 取舍：文档第 2 节说 name 缺失可"用 id 兜底 + 告警"，但 3.3 与离线测试要求
    # "必填缺失 → bad_manifest"。这里按后者实现：宁可让作者一眼看到 manifest 有错，
    # 也不要出现一个名字等于 id 的怪异行（面板与日志都会跟着乱）。
    name = raw.get('name')
    if not isinstance(name, str) or not name.strip():
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'manifest 缺少 name'
        return entry
    entry.name = name.strip()

    version = raw.get('version')
    if not isinstance(version, str) or not version.strip():
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'manifest 缺少 version'
        return entry
    entry.version = version.strip()

    if 'api' not in raw:
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'manifest 缺少 api'
        return entry
    api_val = raw.get('api')
    if isinstance(api_val, bool) or not isinstance(api_val, int):
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'api 必须是整数：%r' % (api_val,)
        return entry
    entry.api = api_val

    # --- entry（文件:类名）---
    spec = raw.get('entry', DEFAULT_ENTRY)
    if not isinstance(spec, str) or not spec.strip():
        spec = DEFAULT_ENTRY
    spec = spec.strip()
    if ':' in spec:
        file_part, cls_part = spec.split(':', 1)
    else:
        file_part, cls_part = spec, 'Plugin'
    file_part = file_part.strip().replace('/', '\\').replace('\\', os.sep)
    cls_part = cls_part.strip() or 'Plugin'
    if (not file_part or os.path.isabs(file_part) or '..' in file_part.split(os.sep)
            or not cls_part.isidentifier()):
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'entry 非法（应形如 plugin.py:Plugin）：%r' % (spec,)
        return entry
    entry.entry_file = file_part
    entry.entry_class = cls_part

    # --- 展示字段 ---
    icon = _as_str(raw.get('icon'), DEFAULT_ICON).strip()
    if icon not in plugin_api.ICON_WHITELIST:
        if icon:
            log('[plugin] %s 的 icon %r 不在白名单，回落 %s' % (entry.id, icon, DEFAULT_ICON))
        icon = DEFAULT_ICON
    entry.icon = icon
    entry.tip = _as_str(raw.get('tip'), '')
    entry.order = _as_int(raw.get('order'), DEFAULT_ORDER)
    entry.builtin = _as_bool(raw.get('builtin'), False)
    entry.requires_admin = _as_bool(raw.get('requires_admin'), False)

    # --- toggle / home ---
    # toggle 缺省 = ['<id>_enabled']；显式空数组 = 该插件无开关（强制 home=False）
    if 'toggle' in raw:
        toggle = _as_path_list(raw.get('toggle'))
    else:
        toggle = [pid + '_enabled']
    entry.toggle = toggle
    home = _as_bool(raw.get('home'), True)
    if not toggle:
        home = False
    entry.home = home

    cfg_spec = raw.get('config')
    if isinstance(cfg_spec, dict):
        entry.config_keys = _as_path_list(cfg_spec.get('keys'))
    entry.settings = _parse_settings(raw.get('settings'))
    entry.requires = _as_path_list(raw.get('requires'), reserved_filter=False)

    # --- 来源信息（可选，仅展示）---
    entry.publisher = _as_str(raw.get('publisher'), '').strip()[:64]
    entry.homepage = _as_str(raw.get('homepage'), '').strip()[:200]
    perms = raw.get('permissions')
    if isinstance(perms, list):
        entry.permissions = [p.strip().lower()[:32] for p in perms
                             if isinstance(p, str) and p.strip()][:16]

    # --- api 版本闸门 ---
    if entry.api <= 0:
        entry.status = STATUS_BAD_MANIFEST
        entry.detail = 'api 必须 >= 1：%r' % (entry.api,)
        return entry
    if entry.api > api:
        entry.status = STATUS_TOO_NEW
        entry.detail = '插件要求 api=%d，当前内核 api=%d，需要更新的内核' % (
            entry.api, api)
        return entry
    if entry.api < api:
        log('[plugin] 插件 %s 按 api=%d 兼容模式运行（内核 api=%d）'
            % (entry.id, entry.api, api))

    if dir_name != entry.id:
        # 目录名 ≠ id 只告警不拒绝：中文目录名场景不该被误伤（文档 4.2）
        log('[plugin] 目录名 %r 与 manifest.id %r 不一致，以 id 为准'
            % (dir_name, entry.id))
    return entry


# ---------------------------------------------------------------- 发现
def discover(base, api=DEFAULT_API, log_func=None, plugins_dir=None):
    """扫描 `<base>\\plugins\\*`，返回按 (order, id) 排序的 RegistryEntry 列表。

    只做 manifest 校验与 id 冲突判定，**不 import 任何插件代码**。
    plugins 目录不存在时返回 []（零插件可跑是硬约束）。

    plugins_dir 可显式指定（源码调试时 BASE=src\\，插件却在仓库根的 plugins\\，
    由 guard_core 传进来），默认仍是 `<base>\\plugins`。
    """
    log = log_func or _noop_log
    root = plugins_dir or os.path.join(base, 'plugins')
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []

    entries = []
    seen = {}          # id -> 目录名（先到先得）
    for name in names:
        plugin_dir = os.path.join(root, name)
        if not os.path.isdir(plugin_dir):
            continue
        entry = parse_manifest(plugin_dir, api=api, log_func=log)
        if entry.status == STATUS_OK:
            if entry.id in seen:
                entry.status = STATUS_CONFLICT
                entry.detail = 'id 与 plugins\\%s 冲突' % seen[entry.id]
                log('[plugin] 插件 id %r 重复：plugins\\%s 已占用，plugins\\%s 不加载'
                    % (entry.id, seen[entry.id], name))
            else:
                seen[entry.id] = name
        entries.append(entry)

    # 目录枚举顺序在 NTFS 上不保证稳定，所以排序键必须是显式的 (order, id)。
    entries.sort(key=lambda e: (e.order, e.id, e.dir))

    # requires：被依赖的插件没加载成功就一起不加载（v2 内置插件都不用）
    loaded_ids = set(e.id for e in entries if e.status == STATUS_OK)
    for entry in entries:
        if entry.status != STATUS_OK or not entry.requires:
            continue
        missing = [r for r in entry.requires if r not in loaded_ids]
        if missing:
            entry.status = STATUS_LOAD_FAILED
            entry.detail = '依赖插件未加载：%s' % '、'.join(missing)
            log('[plugin] 插件 %s 跳过：%s' % (entry.id, entry.detail))
    return entries


# ---------------------------------------------------------------- 加载
def _module_names(entry):
    """合成包名 / 模块名。

    用 `wbox_plugin_<id>` 作为合成包，`<id>` 的合法性已在 manifest 校验过，
    所以名字一定可作为 Python 标识符使用。绝不把插件目录塞进 sys.path。
    """
    pkg = 'wbox_plugin_%s' % entry.id
    stem = os.path.splitext(os.path.basename(entry.entry_file))[0]
    return pkg, pkg + '.' + stem


def _ensure_package(pkg_name, plugin_dir):
    """注册/复用合成父包，让插件里的 `from . import helper` 能解析。"""
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [plugin_dir]
        pkg.__package__ = pkg_name
        sys.modules[pkg_name] = pkg
    else:
        pkg.__path__ = [plugin_dir]
    return pkg


def _load_one(entry, ctx, log_func=None):
    """import 一个插件的 entry 文件并实例化插件类。

    成功：entry.plugin = 实例，status 保持 ok。
    失败：status = load_failed，detail 写异常摘要；绝不抛异常出去。
    """
    log = log_func or _noop_log
    path = os.path.join(entry.dir, entry.entry_file)
    if not os.path.isfile(path):
        entry.status = STATUS_LOAD_FAILED
        entry.detail = 'entry 文件不存在：%s' % entry.entry_file
        return entry

    pkg_name, mod_name = _module_names(entry)
    _ensure_package(pkg_name, entry.dir)
    module = None
    try:
        spec = importlib.util.spec_from_file_location(
            mod_name, path, submodule_search_locations=[entry.dir])
        if spec is None or spec.loader is None:
            raise ImportError('无法为该文件创建 import spec')
        module = importlib.util.module_from_spec(spec)
        # 先注册再 exec：插件内部若有自引用（import 自己的模块名）才能成功
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    except BaseException as ex:      # 插件顶层代码可以抛任何东西，包括 SystemExit
        _purge_module(mod_name)
        entry.status = STATUS_LOAD_FAILED
        entry.detail = '导入失败：%s: %s' % (type(ex).__name__, str(ex) or '<无消息>')
        log('[plugin] 插件 %s 加载失败：%s' % (entry.id, entry.detail))
        return entry

    cls = getattr(module, entry.entry_class, None)
    if cls is None:
        _purge_module(mod_name)
        entry.status = STATUS_LOAD_FAILED
        entry.detail = '类 %s 不存在（%s）' % (entry.entry_class, entry.entry_file)
        log('[plugin] 插件 %s 加载失败：%s' % (entry.id, entry.detail))
        return entry
    try:
        inst = cls(ctx)
    except BaseException as ex:
        _purge_module(mod_name)
        entry.status = STATUS_LOAD_FAILED
        entry.detail = '构造失败：%s: %s' % (type(ex).__name__, str(ex) or '<无消息>')
        log('[plugin] 插件 %s 加载失败：%s' % (entry.id, entry.detail))
        return entry
    if inst is None:
        _purge_module(mod_name)
        entry.status = STATUS_LOAD_FAILED
        entry.detail = '构造返回了 None'
        return entry

    entry.plugin = inst
    if not isinstance(inst, plugin_api.WBOXPlugin):
        # 鸭子类型也放行，但提醒作者继承基类（否则 gui_* 默认实现全都没有）
        log('[plugin] 插件 %s 的类没有继承 WBOXPlugin，按鸭子类型继续'
            % entry.id)
    else:
        api_decl = getattr(inst, 'API', plugin_api.API_VERSION)
        if isinstance(api_decl, int) and api_decl != entry.api:
            log('[plugin] 插件 %s 的 API=%r 与 manifest.api=%r 不一致，以 manifest 为准'
                % (entry.id, api_decl, entry.api))
    return entry


def _purge_module(mod_name):
    """加载失败时把半成品模块从 sys.modules 摘掉，避免下次加载拿到脏模块。"""
    try:
        sys.modules.pop(mod_name, None)
    except Exception:
        pass


# ---------------------------------------------------------------- 配置合并
def plugin_defaults(entry):
    """取一个插件的默认配置：权威来源是插件类的 DEFAULT_CONFIG。

    manifest 的 `config.defaults` 只在插件类没有 DEFAULT_CONFIG 时兜底
    （文档 4.3 第 3 条），因为 Python 里的默认值可被测试断言，json 里的不能。
    """
    cfg = {}
    plugin = entry.plugin
    if plugin is not None:
        dc = getattr(plugin, 'DEFAULT_CONFIG', None)
        if isinstance(dc, dict) and dc:
            return dict(dc)
    raw = entry.manifest.get('config') if isinstance(entry.manifest, dict) else None
    if isinstance(raw, dict):
        dc = raw.get('defaults')
        if isinstance(dc, dict) and dc:
            cfg = dict(dc)
    return cfg


# ---- 安全并集键 -----------------------------------------------------------
# deep_merge 对 list 是整体替换，这对绝大多数名单是对的（用户要能整体换成自己的）。
# 但这个路径上的名单是**保护性**的：用户从 v1 模板里只抄了 5 项 protect_names，
# 合并后默认的 10 项就没了 —— qq.exe / wechat.exe / weixin.exe 从保护名单消失，
# 游戏退出后清宿主进程树时会把常驻 QQ/微信一起结束（用户直接掉线）。
# 所以这一类键在合并完成后单独并一次默认值：只增不减、默认项在前、去重。
# deep_merge 的通用语义一行没动，config.json 也不会被回写（merge_defaults 是纯函数）。
SAFE_UNION_PATHS = ('popup_guard.protect_names',)


def apply_safe_unions(merged, core_defaults):
    """把 SAFE_UNION_PATHS 上的用户值与内核默认值并起来（原地改 merged）。"""
    for path in SAFE_UNION_PATHS:
        head = path.split('.', 1)[0]
        if not isinstance(merged.get(head), dict):
            # 该段不存在（调用方给了一份不含 popup_guard 的精简默认值）：
            # 不凭空造一段出来，否则零配置场景会凭空长出 10 项名单。
            continue
        base = plugin_api.cfg_get_path(core_defaults, path, None)
        got = plugin_api.cfg_get_path(merged, path, None)
        if not isinstance(base, list) and not isinstance(got, list):
            continue
        names = plugin_api.merge_name_list(got, base)
        if names and names != got:
            plugin_api.cfg_set_path(merged, path, names)
    return merged


def merge_defaults(core_defaults, entries, user_cfg):
    """三段合并：内核默认值 -> 各插件 DEFAULT_CONFIG -> 用户 JSON（用户值优先）。

    纯函数：不修改任何入参（内部一律 deep_merge 出新的 dict），
    否则调用方手里的 DEFAULTS 会被插件默认值悄悄污染，热加载几次就串味了。

    最后一步对 SAFE_UNION_PATHS（保护性名单）做并集补齐：这是产品语义要求
    「安全名单只该增加、不该被覆盖」，与 deep_merge 的 list 语义无关。
    """
    merged = plugin_api.deep_merge(core_defaults, {})
    for entry in entries or []:
        dc = plugin_defaults(entry)
        if dc:
            merged = plugin_api.deep_merge(merged, dc)
    merged = plugin_api.deep_merge(merged, user_cfg)
    return apply_safe_unions(merged, core_defaults)


def plugin_cfg(cfg, entry):
    """取某插件的合并结果（给 on_config 用）：默认值打底 + 用户显式值覆盖。

    只搬运该插件自己声明过的路径（manifest.toggle 与 config.keys），
    这样插件拿不到内核键以外的整份配置，也就没法顺手改别人的段。
    """
    out = plugin_api.deep_merge(plugin_defaults(entry), {})
    paths = list(entry.toggle) + list(entry.config_keys)
    for path in paths:
        value = plugin_api.cfg_get_path(cfg, path, _MISSING)
        if value is not _MISSING:
            plugin_api.cfg_set_path(out, path, value)
    return out


class _Missing(object):
    """区分「配置里没有这个键」与「键值就是 None」。"""

    def __repr__(self):
        return '<missing>'


_MISSING = _Missing()


# ---------------------------------------------------------------- 上下文实现
class HostContext(plugin_api.PluginContext):
    """PluginContext 的真实实现：把所有能力接到宿主 / 内核 provider 上。

    内核能力（进程表、会话视图…）不在本模块实现，改用 provider 注入：
    `PluginHost.providers['procs'] = guard.read_procs` 之类。S1/S2 阶段没有
    调用方，provider 缺省时返回安全值（None / True / 空集合），保证宿主可以
    单独被测试驱动，不需要起真实守护。
    """

    def __init__(self, entry, host):
        plugin_api.PluginContext.__init__(
            self,
            id=entry.id,
            base=host.base,
            plugin_dir=entry.dir,
            data_dir=os.path.join(entry.dir, 'data'),
            api=host.api,
            manifest=entry.manifest,
            toggle=entry.toggle)
        self.host = host
        self.entry = entry

    # ---- 内部 ----
    def _provide(self, name, *args):
        fn = self.host.providers.get(name)
        if not callable(fn):
            return _MISSING
        return fn(*args)

    def _allowed_paths(self):
        allowed = set(self.entry.toggle) | set(self.entry.config_keys)
        return allowed

    # ---- 配置 ----
    def cfg(self, path, default=None):
        """读配置：先看内核当前配置，缺这个键再回落到插件自己的 DEFAULT_CONFIG。

        为什么要回落：`config.json` 缺段时若各处各写 `.get(k, 默认)`，就会出现
        「面板显示开、守护实际关」（guard_core 里那条老注释）。这里让插件少写
        一遍默认值，也就少一个口径。
        """
        value = plugin_api.cfg_get_path(self.host.cfg, path, _MISSING)
        if value is not _MISSING:
            return value
        value = plugin_api.cfg_get_path(plugin_defaults(self.entry), path, _MISSING)
        if value is not _MISSING:
            return value
        return default

    def set_cfg(self, path, value):
        if not path:
            raise PermissionError('配置路径不能为空')
        if _is_reserved_key(path) or path not in self._allowed_paths():
            raise PermissionError(
                '插件 %s 无权写配置键 %r（只允许 manifest.toggle / config.keys 声明的键）'
                % (self.id, path))
        plugin_api.cfg_set_path(self.host.cfg, path, value)
        self.host.cfg_dirty = True

    # ---- 日志 ----
    def log(self, msg):
        self.host.log('[%s] %s' % (self.id, msg))

    # ---- 内核共享能力（provider 注入）----
    def procs(self):
        value = self._provide('procs')
        return None if value is _MISSING else value

    def snapshot(self):
        value = self._provide('snapshot')
        return None if value is _MISSING else value

    def process_path(self, pid):
        value = self._provide('process_path', pid)
        return None if value is _MISSING else value

    def refresh_paths(self, procs):
        self._provide('refresh_paths', procs)

    def games_present(self, procs=None):
        # 取不到判定实现时返回 True：宁可不动作，也不要误关用户的东西
        value = self._provide('games_present', procs)
        return True if value is _MISSING else bool(value)

    def watch_names(self):
        value = self._provide('watch_names')
        return set() if value is _MISSING else value

    def session(self):
        value = self._provide('session')
        if value is _MISSING or value is None:
            return plugin_api.SessionView()
        return value

    def now(self):
        value = self._provide('now')
        return time.time() if value is _MISSING else float(value)

    def import_core(self, name):
        if name not in CORE_MODULES:
            raise KeyError('ctx.import_core 不支持 %r（可选：%s）'
                           % (name, ', '.join(sorted(CORE_MODULES))))
        return importlib.import_module(name)

    def request_fast_poll(self, until):
        self.host.request_fast_poll(until)

    def retry_close(self, delay=2.0):
        value = self._provide('retry_close', delay)
        return False if value is _MISSING else bool(value)

    # ---- 私有状态文件 ----
    def _state_path(self, name):
        safe = str(name)
        if not safe or os.sep in safe or '/' in safe or safe in ('.', '..'):
            raise ValueError('状态文件名非法：%r' % (name,))
        if not safe.endswith('.json'):
            safe += '.json'
        try:
            os.makedirs(self.data_dir, exist_ok=True)
        except OSError:
            pass
        return os.path.join(self.data_dir, safe)

    def write_state(self, name, obj):
        _atomic_write_json(self._state_path(name), obj)

    def read_state(self, name, default=None):
        path = self._state_path(name)
        try:
            with open(path, 'r', encoding='utf-8-sig') as f:
                return json.load(f)
        except (OSError, ValueError):
            return default

    def set_status(self, text, level='ok'):
        self.status_text = str(text or '')
        self.entry.state_text = self.status_text
        self.host.state_dirty = True


# ---------------------------------------------------------------- 宿主
class PluginHost(object):
    """插件宿主：发现 -> 加载 -> 分发钩子 -> 状态落盘。

    典型用法（守护进程）：
        host = PluginHost(BASE, log, cfg=raw_cfg)
        host.load_all(raw_cfg)
        cfg = merge_defaults(core_defaults.DEFAULTS, host.registry, raw_cfg)
        host.cfg = cfg                         # ctx.cfg 读这份合并结果
        host.dispatch('on_config', cfg)        # 每个插件收到自己那一份
        host.dispatch('on_start')
        ... 每轮 host.dispatch('on_tick', tick)
        host.dispatch('on_exit', 'guard_stop')

    面板进程只用 load_registry()，不会走到 on_start / on_tick。
    """

    def __init__(self, base, log_func=None, api=DEFAULT_API, cfg=None,
                 plugins_dir=None):
        self.base = base
        self.log = log_func or _noop_log
        self.api = api
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.providers = {}
        self.registry = []
        self.hook_warn_sec = HOOK_WARN_SEC
        self.err_log_window = ERR_LOG_WINDOW_SEC
        self.state_heartbeat_sec = STATE_HEARTBEAT_SEC
        self.fast_poll_until = 0.0
        self.state_file = os.path.join(base, 'plugins_state.json')
        self._plugins_dir = plugins_dir or os.path.join(base, 'plugins')
        self.cfg_dirty = False
        self.state_dirty = False
        self._err_log_at = {}          # 错误摘要 -> 上次记日志的时刻
        self._err_log_muted = 0        # 被静音的条数（下一次放行时一起报）
        self._state_sig = None
        self._state_at = 0.0
        self._loaded = False

    # ---- 只读属性 ----
    @property
    def plugins(self):
        """可用的插件实例，按 (order, id) 排序（registry 本身已排序，这里只过滤）。"""
        return [e.plugin for e in self.registry if e.usable]

    @property
    def plugins_dir(self):
        return self._plugins_dir

    def entry_by_id(self, pid):
        for entry in self.registry:
            if entry.id == pid and entry.usable:
                return entry
        return None

    # ---- 加载 ----
    def load_all(self, raw_cfg=None, write_state=True):
        """发现 + 加载全部插件。

        raw_cfg 给定时会对每个插件调用一次 migrate(raw_cfg)（配置加载期的一次性
        迁移）；无论有没有 raw_cfg，加载成功后都会调用 on_load()。
        重复调用会重新扫描并对已加载的插件再做一遍 migrate/on_load。

        write_state=False 用于面板进程：plugins_state.json 只能由守护写，
        面板跟着写会把守护报上来的运行期异常状态覆盖掉（文档 6.4）。
        """
        self.registry = discover(self.base, api=self.api, log_func=self.log,
                                 plugins_dir=self._plugins_dir)
        for entry in self.registry:
            if entry.status != STATUS_OK:
                continue
            entry.plugin = None
            self._load_one(entry)
        # 加载阶段的 migrate / on_load 也走 _invoke，异常同样被隔离
        for entry in self.registry:
            if not entry.usable:
                continue
            if isinstance(raw_cfg, dict):
                self._invoke(entry, 'migrate', raw_cfg)
            self._invoke(entry, 'on_load')
        self._loaded = True
        if write_state:
            self.write_state_file(force=True)
        errs = self.errors()
        if errs:
            self.log('[plugin] 已加载 %d 个插件，%d 个异常/未加载：%s'
                     % (len(self.plugins), len(errs),
                        ', '.join('%s=%s' % (e['id'], e['status']) for e in errs)))
        else:
            self.log('[plugin] 已加载 %d 个插件' % len(self.plugins))
        return self.registry

    def _make_ctx(self, entry):
        """建插件的上下文，并把 data 目录建好（新插件写 plugins\\<id>\\data）。"""
        ctx = HostContext(entry, self)
        try:
            os.makedirs(ctx.data_dir, exist_ok=True)
        except OSError as ex:
            self.log('[plugin] 无法创建数据目录 %s：%r' % (ctx.data_dir, ex))
        return ctx

    def _load_one(self, entry):
        """方法形式的加载入口（对应设计文档 3.5 的 PluginHost._load_one）。"""
        return _load_one(entry, self._make_ctx(entry), log_func=self.log)

    # ---- 分发 ----
    def dispatch(self, hook, *args):
        """把钩子分发给所有可用插件。**永不抛异常**（单个插件故障就地吞掉）。

        `on_config` 是唯一需要"每插件一份参数"的钩子：传进来的整份 cfg 会先按
        `plugin_cfg()` 切成该插件自己的那份（默认值打底 + 自己声明的键），
        避免插件看见/改到别人的段（文档 3.2）。
        """
        for entry in self.registry:
            if not entry.usable:
                continue
            if hook == 'on_tick':
                entry.ticks += 1
                if not self._tick_due(entry):
                    continue
            if hook == 'on_config' and args:
                ok = self._invoke(entry, hook, plugin_cfg(args[0], entry))
            else:
                ok = self._invoke(entry, hook, *args)
            # 心跳面板要显示"这个插件最近什么时候跑过一轮"：只有真的执行
            # 成功才更新（降频跳过 / 抛异常都不算）。
            if hook == 'on_tick' and ok:
                entry.last_tick_at = time.time()
        self.write_state_file()
        return None

    def _tick_due(self, entry):
        """on_tick 连续失败太多次后降频：每 TICK_SKIP_ROUNDS 轮才试一次。"""
        if entry.tick_fails < TICK_FAIL_LIMIT:
            return True
        entry.tick_skip += 1
        return entry.tick_skip % TICK_SKIP_ROUNDS == 0

    def _invoke(self, entry, hook, *args):
        """调用单个插件的单个钩子，异常一律采集不外抛。

        拦 BaseException 而不是 Exception：插件调 sys.exit() 抛的 SystemExit
        会把整个守护带走（守护是后台进程，没有"退到上一级"这回事）。
        """
        plugin = entry.plugin
        if plugin is None:
            return False
        fn = getattr(plugin, hook, None)
        if not callable(fn):
            return False
        t0 = time.time()
        try:
            fn(*args)
        except BaseException as ex:      # noqa: BLE001 - 见上面的说明
            self._record_error(entry, hook, ex)
            cost = time.time() - t0
            self._warn_slow(entry, hook, cost)
            return False
        self._record_ok(entry, hook)
        self._warn_slow(entry, hook, time.time() - t0)
        return True

    def _warn_slow(self, entry, hook, cost):
        if cost > self.hook_warn_sec:
            self.log('[plugin] 插件 %s 的 %s 耗时 %.1fs（超过 %.0fs 告警线）'
                     % (entry.id, hook, cost, self.hook_warn_sec))

    def _record_error(self, entry, hook, ex):
        """采集一次插件异常：计数 + 状态降级 + 节流日志。"""
        entry.error_count += 1
        entry.last_error = '%s: %s' % (type(ex).__name__, str(ex) or '<无消息>')
        entry.last_error_at = time.time()
        entry.detail = '%s 抛异常' % hook
        if entry.status == STATUS_OK:
            entry.status = STATUS_DEGRADED
        if hook == 'on_tick':
            entry.tick_fails += 1
        self.state_dirty = True
        self._log_error(entry, hook, ex)

    def _record_ok(self, entry, hook):
        """成功一次就恢复正常：清 on_tick 失败计数，degraded 回到 ok。"""
        if hook == 'on_tick':
            entry.tick_fails = 0
            entry.tick_skip = 0
        if entry.status == STATUS_DEGRADED:
            entry.status = STATUS_OK
            entry.detail = ''
            self.state_dirty = True
            self.log('[plugin] 插件 %s 已恢复正常（%s 成功）' % (entry.id, hook))

    def _log_error(self, entry, hook, ex):
        """同一类异常 60 秒内只记一条，避免坏插件把 guard.log 刷爆。"""
        key = (entry.id, hook, type(ex).__name__, str(ex)[:120])
        now = time.time()
        last = self._err_log_at.get(key)
        if last is not None and (now - last) < self.err_log_window:
            self._err_log_muted += 1
            return
        self._err_log_at[key] = now
        extra = ''
        if self._err_log_muted:
            extra = '（同类异常另有 %d 条未记）' % self._err_log_muted
            self._err_log_muted = 0
        self.log('[plugin] 插件 %s 的 %s 抛异常：%s%s'
                 % (entry.id, hook, entry.last_error, extra))

    # ---- 快节拍登记（poll_delay 用）----
    def request_fast_poll(self, until):
        """登记"我要快节拍到 until"，内核 poll_delay 取所有登记的最大值。"""
        try:
            until = float(until)
        except (TypeError, ValueError):
            return
        if until > self.fast_poll_until:
            self.fast_poll_until = until

    def fast_poll_active(self, now=None):
        now = time.time() if now is None else now
        return self.fast_poll_until > now

    # ---- 状态落盘 ----
    def errors(self):
        """所有非 ok 状态的插件摘要（自检 / 日志用）。"""
        out = []
        for entry in self.registry:
            if entry.status == STATUS_OK and entry.error_count == 0:
                continue
            out.append({
                'id': entry.id,
                'status': entry.status,
                'detail': entry.detail,
                'error_count': entry.error_count,
                'last_error': entry.last_error,
                'last_error_at': entry.last_error_at,
            })
        return out

    def _state_signature(self):
        return tuple((e.id, e.version, e.api, e.status, e.detail,
                      e.error_count, e.last_error) for e in self.registry)

    def write_state_file(self, force=False):
        """原子写 plugins_state.json。

        节流规则（文档 6.4）：**只在状态变化时写**，另加"最多每 30 秒一次"
        心跳。每轮 tick 都落盘会让磁盘 IO 变成主循环的固定开销。
        """
        now = time.time()
        sig = self._state_signature()
        if not force and sig == self._state_sig \
                and (now - self._state_at) < self.state_heartbeat_sec:
            return False
        payload = {
            'generated_at': round(now, 3),
            'guard_pid': os.getpid(),
            'api': self.api,
            'plugins': [e.to_state_dict() for e in self.registry],
        }
        try:
            _atomic_write_json(self.state_file, payload)
        except OSError as ex:
            self.log('[plugin] 写 %s 失败：%r' % (self.state_file, ex))
            return False
        self._state_sig = sig
        self._state_at = now
        self.state_dirty = False
        return True

    # ---- 热加载（本次未启用）----
    def reload_if_changed(self, allow=False):
        """插件目录变化后重载。

        文档第 9 节风险 6：会话中途重载会丢 ab_pid 所有权 / ACE 排队状态，
        所以只在 idle 时执行，且必须逐个 on_exit('reload') 之后再 load_all()。
        本次（S1/S2）**不启用**，恒返回 False；等内核 tick 重写（S5）后再接上。
        """
        return False


# ---------------------------------------------------------------- 面板入口
def load_registry(base, api=DEFAULT_API, log_func=None, cfg=None,
                  plugins_dir=None):
    """只读发现 + 加载，返回按 (order, id) 排序的 RegistryEntry 列表。

    面板用它取代写死的 FEATURES：需要 name/icon/tip/plugin（取 gui_state），
    但**绝不**调用 on_start / on_tick / on_config，也不依赖守护在跑。

    返回的是 entries（不是 (entries, host)），每个 entry 自带 plugin 实例；
    面板只需要 entries，多返回一个宿主对象反而会让人误以为可以驱动钩子。

    注意：**不写 plugins_state.json**（那是守护的产物），面板只能读。
    """
    host = PluginHost(base, log_func=log_func, api=api, cfg=cfg,
                      plugins_dir=plugins_dir)
    host.load_all(None, write_state=False)
    return host.registry
