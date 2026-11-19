# -*- coding: utf-8 -*-
"""WBOX - 插件契约（纯定义，无副作用）。

本模块**只放定义**：
  - 可被守护进程、面板进程、离线测试直接 import；
  - 不 import Win32 / tkinter / 任何低层库，不读写文件，不注册 atexit；
  - 因此 `python -c "import plugin_api"` 在任何环境都能跑通。

契约的稳定性由 `API_VERSION` 保证：只加不改，删除字段属于破坏性变更。
"""
import copy

API_VERSION = 1

# 图标白名单 = settings_gui.Glyph.draw 现有分支（灰阶线性图标，无彩色、无 emoji）。
# 插件在 manifest 里写未知值会画不出东西（Glyph 没有 else 分支），宿主必须兜底成 dot。
ICON_WHITELIST = frozenset(('link', 'plane', 'pulse', 'shield', 'block',
                            'dot', 'play', 'stop', 'boot', 'rescan', 'log',
                            'cfg', 'sun', 'moon', 'check', 'close', 'plus',
                            'trash', 'folder', 'admin'))

# 会话状态机的三个状态（内核唯一时间轴，插件只读）
STATE_IDLE = 'idle'
STATE_ACTIVE = 'active'
STATE_GRACE = 'grace'


# ---------------------------------------------------------------- 路径工具
def cfg_get_path(cfg, path, default=None):
    """按点号路径读配置：cfg_get_path(cfg, 'ace_cleanup.enabled', True)。

    配置是普通嵌套 dict，缺段时返回 default（不抛异常，也不创建中间段）。
    """
    if not path:
        return default
    cur = cfg
    for part in str(path).split('.'):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def cfg_set_path(cfg, path, value):
    """按点号路径写配置，中间缺的段建成 dict。返回 True 表示写成功。

    cfg 必须已经是 dict；path 为空时什么都不做并返回 False
    （顺手拦住 manifest 里写成空字符串的 toggle，避免把整份配置写坏）。
    """
    if not isinstance(cfg, dict) or not path:
        return False
    parts = str(path).split('.')
    cur = cfg
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            # 用户把中间段写成了字符串/数字：直接覆盖成 dict，
            # 否则开关永远写不进去（面板会表现为"拨了又弹回去"）。
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return True


def deep_merge(defaults, override):
    """深合并两份配置，返回新 dict，**不修改任何入参**。

    dict 段递归合并；其它类型（含 list）由 override 整体替换——
    列表语义是"用户显式列出的项"，逐项合并会导致删不掉。
    """
    out = copy.deepcopy(defaults) if isinstance(defaults, dict) else {}
    if not isinstance(override, dict):
        return out
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def merge_name_list(user, defaults):
    """保护性名单取并集：默认项在前，用户额外项追加，大小写不敏感去重。

    为什么不用 deep_merge 的语义：deep_merge 对 list 是**整体替换**（这是对的，
    process_names / rtss_processes 这类名单用户必须能整体换成自己的）。但
    `popup_guard.protect_names` 是**保护性**名单 —— 少了 qq.exe / wechat.exe，
    游戏退出后清宿主进程树时会把常驻 QQ/微信一起结束，用户直接掉线。
    所以这一类键（见 plugin_host.SAFE_UNION_PATHS）在合并之后单独并一次。

    规则：
      - 非字符串 / 空串剔除；strip 后按小写去重（首次出现的写法与顺序保留）。
      - 用户项永远不丢，默认项永远补齐 —— 只增不减。
      - 两边都没有时返回 []（不凭空造名单）。
    """
    out = []
    seen = set()
    for name in list(defaults or []) + list(user or []):
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


# ---------------------------------------------------------------- 只读视图
class SessionView(object):
    """会话只读视图（持有者：内核）。插件不得修改。

    用 property 而不是普通属性是为了让越权写直接抛 AttributeError：
    插件把 `tick.session.active = True` 写坏内核状态是本设计要避免的错。
    """

    __slots__ = ('_state', '_active', '_games', '_started_at',
                 '_grace_until', '_resumed')

    def __init__(self, state=STATE_IDLE, games=None, started_at=0.0,
                 grace_until=0.0, resumed=False):
        object.__setattr__(self, '_state', str(state or STATE_IDLE))
        object.__setattr__(self, '_active', str(state or STATE_IDLE) == STATE_ACTIVE)
        object.__setattr__(self, '_games', list(games or []))
        object.__setattr__(self, '_started_at', float(started_at or 0.0))
        object.__setattr__(self, '_grace_until', float(grace_until or 0.0))
        object.__setattr__(self, '_resumed', bool(resumed))

    def __setattr__(self, name, value):
        raise AttributeError('SessionView 是只读视图，插件不得修改')

    @property
    def state(self):
        return self._state

    @property
    def active(self):
        return self._active

    @property
    def games(self):
        return self._games

    @property
    def started_at(self):
        return self._started_at

    @property
    def grace_until(self):
        return self._grace_until

    @property
    def resumed(self):
        return self._resumed

    def __repr__(self):
        return 'SessionView(state=%r, games=%r, resumed=%r)' % (
            self._state, self._games, self._resumed)


class Tick(object):
    """每轮传给插件的数据包。构造后内核不再改它，插件也不该改（约定，不强制）。"""

    __slots__ = ('seq', 'now', 'procs', 'names', 'games', 'session', 'events')

    def __init__(self, seq=0, now=0.0, procs=None, names=None, games=None,
                 session=None, events=()):
        self.seq = int(seq or 0)
        self.now = float(now or 0.0)
        # procs 必须是 None 或 dict：None 表示"本轮进程快照失败"，
        # 与 {} （快照成功但一个进程都没有）语义完全不同，插件必须区别对待。
        self.procs = procs
        self.names = names if names is not None else set()
        self.games = list(games or [])
        self.session = session if session is not None else SessionView()
        self.events = frozenset(events or ())

    def __repr__(self):
        return 'Tick(seq=%d, games=%r, state=%r)' % (
            self.seq, self.games, self.session.state)


# ---------------------------------------------------------------- 上下文基类
class PluginContext(object):
    """每个插件实例一个，只暴露受控能力。

    这里只放**接口**：属性在 __init__ 里赋值，方法一律 NotImplementedError。
    真正实现挂在 plugin_host.HostContext（它继承本类并接上内核 provider），
    这样面板/测试可以拿本类做类型判断，而不必 import 宿主。
    """

    def __init__(self, id='', base='', plugin_dir='', data_dir='',
                 api=API_VERSION, manifest=None, toggle=None):
        self.id = id                                  # 插件 id（= manifest.id）
        self.base = base                              # 安装根目录（config.json/guard.log）
        self.plugin_dir = plugin_dir                  # plugins\<id>
        self.data_dir = data_dir                       # plugins\<id>\data（宿主已 makedirs）
        self.api = api                                 # 内核提供的 API 版本
        self.manifest = dict(manifest or {})            # 只读副本，供插件读自己的展示字段
        self.toggle = list(toggle or [])                # 开关配置路径（点号）
        self.status_text = ''                           # set_status() 写入的摘要

    # ---- 配置 ----
    def cfg(self, path, default=None):
        """读配置（含内核段），path 支持点号：ctx.cfg('ace_cleanup.grace_sec', 180)。"""
        raise NotImplementedError('PluginContext.cfg')

    def set_cfg(self, path, value):
        """只允许写自己 manifest 里 toggle / config.keys 声明的路径，越权抛 PermissionError。"""
        raise NotImplementedError('PluginContext.set_cfg')

    # ---- 日志 ----
    def log(self, msg):
        """自动加 '[<id>] ' 前缀后写 guard.log（走内核同一个 log，保留轮转）。"""
        raise NotImplementedError('PluginContext.log')

    # ---- 内核共享只读能力 ----
    def procs(self):
        """本轮进程表 {pid: name}；本轮快照失败时返回 None（必须当"未知"，不是空表）。"""
        raise NotImplementedError('PluginContext.procs')

    def snapshot(self):
        """现取一份进程表（关闭流程里同步复核用）；失败返回 None。"""
        raise NotImplementedError('PluginContext.snapshot')

    def process_path(self, pid):
        """复用内核 path_cache 取进程 exe 路径。"""
        raise NotImplementedError('PluginContext.process_path')

    def refresh_paths(self, procs):
        """让内核按本轮进程表刷新路径缓存。"""
        raise NotImplementedError('PluginContext.refresh_paths')

    def games_present(self, procs=None):
        """内核判定"游戏是否还在跑"（白名单 + 路径兜底）；取不到表返回 True。"""
        raise NotImplementedError('PluginContext.games_present')

    def watch_names(self):
        """当前监控的进程名集合（小写）。"""
        raise NotImplementedError('PluginContext.watch_names')

    def session(self):
        """-> SessionView"""
        raise NotImplementedError('PluginContext.session')

    def now(self):
        """本轮 tick 的墙钟（同一轮内恒定，便于离线断言）。"""
        raise NotImplementedError('PluginContext.now')

    def import_core(self, name):
        """取内核低层库：'ace_native' / 'popup_guard' / 'game_lib'；未知名字抛 KeyError。"""
        raise NotImplementedError('PluginContext.import_core')

    def request_fast_poll(self, until):
        """登记"我要快节拍到 until 时刻"，内核 poll_delay 取所有登记的最大值。"""
        raise NotImplementedError('PluginContext.request_fast_poll')

    def retry_close(self, delay=2.0):
        """**仅在 on_session_close 分发期间有效**：把会话从"即将 idle"拉回 grace。

        为什么需要它：v1 的 close_owned() 在取不到进程表时把 pending_stop 回退 2 秒，
        于是"小飞机没关掉"这件事下一轮还会再试。没有这个能力，快照抖一下小飞机
        就再也关不掉了（design\\plugin-arch.md §5.3 标注的最易漏语义）。

        返回 True 表示内核接受了重试；在其它钩子里调用返回 False（被忽略）。
        """
        raise NotImplementedError('PluginContext.retry_close')

    def spawn(self, fn, *args, **kwargs):
        """在后台线程里执行可能阻塞的操作，立即返回线程对象。

        **必须异步的调用**（v2.13.0 明确写入契约，文档 §9 同步更新）：

          | 操作 | 典型耗时 | 为什么要异步 |
          |---|---|---|
          | `ctx.snapshot()` / `ctx.procs()` 之外的全表轮询 | 5~50ms | 进程多时线性增长 |
          | 关闭进程、等待退出（WM_CLOSE / taskkill / 句柄等待） | 0.3~6s | 会占满 close_wait 期限 |
          | 启动外部程序（Popen / ShellExecute） | 10ms~数秒 | 冷启动受杀软/磁盘影响 |
          | 驱动/PDH/DXGI/ADL 查询（见 desktop_hud） | 10ms~数秒 | 驱动卡住是常见故障 |
          | 读写大文件、注册表全量枚举、`icacls`/PowerShell | 10ms~1s | 冷启动抖动大 |

        `on_tick` 里对上述操作一律走 `ctx.spawn(...)`；`on_session_close`
        这类"必须在本轮决定是否重试"的钩子仍要同步判断（快照失败就
        `ctx.retry_close`），但真正的关闭动作可以交给后台线程。
        后台线程里的异常由宿主捕获并写日志/事件流，不会带走守护。
        """
        raise NotImplementedError('PluginContext.spawn')

    # ---- 私有状态文件 ----
    def write_state(self, name, obj):
        """原子写 data_dir\\<name>.json（tmp + os.replace）。"""
        raise NotImplementedError('PluginContext.write_state')

    def read_state(self, name, default=None):
        """读 data_dir\\<name>.json；文件不存在/坏了返回 default。"""
        raise NotImplementedError('PluginContext.read_state')

    def set_status(self, text, level='ok'):
        """面板「插件」分组显示的一行摘要。"""
        raise NotImplementedError('PluginContext.set_status')


# ---------------------------------------------------------------- 插件基类
class WBOXPlugin(object):
    """插件基类。全部钩子都是空实现，子类按需覆盖。

    硬性约定（文档第 9 节）：
      - 模块顶层不得有副作用（面板进程也会 import 它）；
      - 不得调用 sys.exit()（宿主会拦 SystemExit）；
      - on_tick 里不得 time.sleep()（会拖慢退出检测）。
    """

    DEFAULT_CONFIG = {}       # {顶层键: 默认值}；dict 段由内核 deep-merge 用户配置
    API = API_VERSION         # 插件实现所遵循的 API 版本（与 manifest.api 一致）

    def __init__(self, ctx):
        self.ctx = ctx

    # ---- 配置 ----
    def migrate(self, raw):
        """可选。配置加载时、合并默认值之前调用一次（每次守护进程启动）。

        raw 是 config.json 的原始 dict，可直接原地改写（改名/搬段）。
        只允许动自己命名空间内的键。异常 = 记日志并忽略迁移。
        """

    def on_config(self, cfg):
        """首次加载 + 每次 config.json mtime 变化后调用。

        cfg 是本插件的合并结果（已含默认值），只读副本。
        插件在此重建自己的低层对象（如 AceCleaner/PopupGuard），须轻量（<50ms）。
        """

    # ---- 生命周期 ----
    def on_load(self):
        """插件加载成功、migrate/on_config 之后调用一次。可做一次性的目录检查。"""

    def on_start(self):
        """守护主循环开始前调用（guard.pid 已写、stop.flag 已清）。"""

    def on_tick(self, tick):
        """每轮调用，**必然最后调用**，且在 on_session_* 之后。"""

    # ---- 会话事件（每个迁移点最多一个）----
    def on_session_start(self, tick):
        """idle -> active（新会话）。"""

    def on_session_resume(self, tick):
        """grace -> active（宽限期内游戏闪断后回来）。
        不重开会话、不重置所有权/基线，只取消排队中的清理/监视。
        """

    def on_session_grace(self, tick):
        """active -> grace（游戏全部退出，进入 exit_grace_sec 宽限）。"""

    def on_session_close(self, tick):
        """grace -> idle（宽限到期且游戏确实没回来）。"""

    def on_exit(self, reason):
        """退出清理。reason ∈ {'guard_stop', 'reload', 'fatal'}。禁止阻塞超过 2 秒。"""

    # ---- 面板（在**面板进程**内调用，不在守护进程）----
    def gui_state(self, cfg):
        """-> bool，开关初始状态。默认实现：manifest.toggle 里的路径全部为真才算开。"""
        paths = self._toggle_paths()
        if not paths:
            return False
        for path in paths:
            if not bool(cfg_get_path(cfg, path, False)):
                return False
        return True

    def gui_toggle(self, cfg, on):
        """把开关写进 cfg（内存），内核负责 save_cfg。默认实现：所有 toggle 路径写同一个值。

        返回 True 表示至少写入了一个路径；manifest 没声明 toggle 时返回 False
        （面板应把它当"无开关插件"，不显示开关）。
        """
        paths = self._toggle_paths()
        if not paths:
            return False
        for path in paths:
            cfg_set_path(cfg, path, bool(on))
        return True

    def gui_menu_rows(self, cfg):
        """-> [{'text','icon','hint','action'}]，可选，插入菜单「插件」分组。"""
        return []

    # ---- 自检 ----
    def on_self_test(self, out):
        """--self-test 时调用，out 是 list，插件 append 自己的诊断行（纯文本）。"""

    # ---- 内部工具 ----
    def _toggle_paths(self):
        """取 manifest 声明的 toggle 路径（宿主已把缺省值补成 ['<id>_enabled']）。"""
        ctx = getattr(self, 'ctx', None)
        if ctx is None:
            return []
        return [p for p in (getattr(ctx, 'toggle', None) or []) if isinstance(p, str) and p]


# v2.11.0 改名：旧类名保留为别名，第三方插件源码不改也能继续加载。
WingmanPlugin = WBOXPlugin
