# WBOX 插件开发规范

适用版本：**WBOX v2.12.1**，插件 API 版本 **`API_VERSION = 1`**
文档状态：与 `src/plugin_api.py`、`src/plugin_host.py`、`src/plugin_package.py`、
`src/guard_core.py` 的实际行为逐条对齐（2026-11-17 校对，2026-11-18 同步
v2.11.0 系统标识全量改名；同日 v2.11.3 补 `publisher` / `homepage` /
`permissions` 三个展示字段与 `.sha256` 侧车校验；2026-11-19 v2.12.0 只动
面板外观——窗口大小三档与插件设置里的版本展示；v2.12.1 继续只动面板外观——
极简档行尾版本小字、方角开关与行内 30 px 设置项，**API 与包格式不变**）。
面板操作说明按 WBOX 界面写成：标题栏字标是
**WBOX**；v2.11.0 起系统标识（exe、安装目录、计划任务、注册表键、管道/互斥体、
备份目录）也统一 WBOX——旧 Wingman 安装由安装器自动迁移。

---

## 0. 先读这一段：安全边界

> **插件不是配置项，是代码。**
>
> 插件运行在**以管理员权限常驻的守护进程内部**，与内核共用同一个 Python 解释器、
> 同一个进程、同一个令牌。宿主不提供任何沙箱、不限制任何系统调用：
> 插件可以读写任意文件、改注册表、结束任意进程、发起网络请求、加载任意 DLL。
>
> 换句话说：**装一个插件 = 允许它在这台机器上做管理员能做的一切。**
>
> 因此：
> - 只安装你自己写的、或你完全信任其作者的插件；
> - 不要"试一下"来源不明的插件包；
> - 任何人都不要以"给用户装个插件"为名义分发来路不明的代码。
>
> 宿主确实做了三件**隔离**的事，但它们是为了**稳定性**，不是安全：
> 钩子异常被 `except BaseException` 吞掉（插件崩了守护不死）、单插件失败不影响
> 其它插件、插件目录不进 `sys.path`（避免 `plugins\x\json.py` 劫持标准库导入）。
> 这三条都挡不住恶意代码。

v2.11.3 起安装框把可判断的东西摆出来（**都是线索，不是担保**）：

| 展示项 | 来自 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| 发布者 / 来源 | manifest `publisher` / `homepage` | 插件**自称**是谁 | 真实作者（没有签名） |
| 声明权限 | manifest `permissions` | 插件**声称**要碰什么 | 实际行为（内核不做限制） |
| 包完整性 | 同目录 `<包名>.zip.sha256` | 和发布时是同一份文件 | 发布者身份（侧车可被一起替换） |
| SHA-256 | 现场计算 | 你手上的这个包是什么 | — |

所以判断标准只有一条：**这个包的来源你是否信得过。** 没写 `publisher` 的包，
界面会明说「来源不明」；`builtin: true` 不算任何担保（那个字段谁都能写）。

如果你只是想让游戏联动某个程序，先看现有五个内置插件能不能改配置解决；
写代码是最后手段。

---

## 1. 插件是什么

一个插件就是 `plugins\<id>\` 目录下的一份 `plugin.json` 加一个 Python 文件。
守护进程每轮扫描这个目录，把 manifest 合法的插件 `import` 进来、实例化、按钩子调用。

| 事实 | 说明 |
| --- | --- |
| 运行宿主 | 守护进程（`WBOX.exe --guard`），管理员令牌 |
| 运行时机 | 守护冷启动、以及面板装/卸插件后的热重载（仅会话 idle 时） |
| 执行模型 | 单线程同步调用，无并发保护，**不要**在钩子里阻塞 |
| 崩溃影响 | 只影响自己：异常被捕获、计数、降级，守护继续跑 |
| 权限 | 与守护相同（管理员）。没有额外权限，也没有任何限制 |
| 卸载 | 插件设置浮层底部「卸载此插件」/ 删目录。目录被整体移入备份区，不硬删 |

插件**不能**做的事（宿主会拦，或设计上不提供）：

- 不能在面板进程里产生副作用（面板也会 `import` 你的模块，见 §5.1）；
- 不能调用 `sys.exit()`（会被 `except BaseException` 拦下，但会记为一次异常）；
- 不能写内核保留的配置键（见 §7.4）；
- 不能把自己的目录塞进 `sys.path`（宿主用合成包名，不污染全局导入）。

---

## 2. 目录结构与命名

```
<安装目录>\                     ← 一般是 C:\Program Files\WBOX
├─ WBOX.exe
├─ config.json
├─ guard.log
└─ plugins\
   ├─ afterburner\             ← 内置插件
   ├─ ace\
   ├─ gamepp\
   ├─ popup\
   └─ <你的 id>\               ← 插件目录名必须等于 manifest 的 id
      ├─ plugin.json           必填
      ├─ plugin.py             默认入口（可由 entry 改）
      ├─ helper.py             可选，用 from . import helper 引入
      └─ data\                 宿主自动创建，给你放私有状态（不进分发包）
```

### 2.1 `id` 命名规则

```
^[a-z][a-z0-9_]{1,31}$
```

- 必须以**小写字母**开头（不能是数字、不能是大写）；
- 只允许小写字母、数字、下划线；
- 长度 **2 ~ 32** 个字符；
- **目录名必须与 `plugin.json` 的 `id` 完全一致**（打 zip 时强校验；
  直接放目录时不一致只告警，以 `manifest.id` 为准）。

内置插件就是范例：`afterburner` / `ace` / `gamepp` / `popup`。
不要用 `my-plugin`（连字符非法）、`MyPlugin`（大写非法）、`2fast`（数字开头非法）。

### 2.2 零插件也能跑

`plugins\` 不存在、为空、全是坏插件时，守护照常运行（只是没有任何功能动作）。
你的插件不是必需的，**不要**假设自己一定被加载。

---

## 3. `plugin.json` 字段全集

只有下列字段会被读取。**写其它字段不会报错，但也不会生效** —— 别指望
`dependencies` 这类"看起来该有"的键。`publisher` / `homepage` / `permissions`
是 v2.11.3 新增的展示字段（见 §0 与下表），宿主只展示、不据此授权。

```json
{
  "id": "demo_hello",
  "name": "示例插件",
  "version": "1.0.0",
  "api": 1,
  "entry": "plugin.py:Plugin",
  "icon": "pulse",
  "order": 50,
  "home": true,
  "builtin": false,
  "requires_admin": false,
  "publisher": "你的名字或工作室",
  "homepage": "https://example.com/your-plugin",
  "permissions": ["ui.panel"],
  "toggle": ["demo_hello_enabled"],
  "tip": "鼠标悬停在主页这一行时显示的说明。\n支持换行。",
  "config": {
    "keys": ["demo_hello_enabled", "demo_hello"],
    "defaults": {}
  },
  "settings": [
    { "key": "demo_hello_enabled", "type": "bool",
      "label": "启用示例功能", "hint": "拨动立即生效" },
    { "key": "demo_hello.count", "type": "int",
      "label": "等待时间", "min": 0, "max": 60, "step": 5, "unit": "秒" },
    { "key": "demo_hello.mode", "type": "choice",
      "label": "显示位置",
      "options": [ { "value": "tr", "label": "右上" },
                   { "value": "bl", "label": "左下" } ] },
    { "key": "demo_hello.path", "type": "path",
      "label": "程序路径", "hint": "留空则自动查找" }
  ],
  "requires": [],
  "description": "给人看的简介，宿主不读，仅随包分发。"
}
```

| 字段 | 类型 | 必填 | 默认 | 校验与语义 |
| --- | --- | --- | --- | --- |
| `id` | string | **是** | — | 必须匹配 §2.1 正则；`plugins\<id>\` 的目录名要与它一致 |
| `name` | string | **是** | — | 非空白字符串。主页与菜单显示的名字 |
| `version` | string | **是** | — | 非空白字符串。建议 `X.Y.Z`，插件包文件名要用它 |
| `api` | int | **是** | — | 正整数，且 `≤ 1`（当前内核 `API_VERSION = 1`）。`true`/`"1"` 都非法 |
| `entry` | string | 否 | `plugin.py:Plugin` | `文件:类名`。文件是相对路径，不能是绝对路径、不能含 `..`；类名必须是合法 Python 标识符。省略 `:类名` 时按 `Plugin` |
| `icon` | string | 否 | `dot` | 必须是 §6 白名单里的 20 个之一，否则回落 `dot` 并记一行日志 |
| `order` | int | 否 | `100` | 主页/菜单排序键，小的在前。相同则按 `id` 字典序 |
| `home` | bool | 否 | `true` | 是否出现在主页功能行。**`toggle` 为空数组时强制按 `false` 处理** |
| `builtin` | bool | 否 | `false` | 标记"随安装包分发"。宿主只把它透传给面板，不影响加载 |
| `requires_admin` | bool | 否 | `false` | 语义提示：这个插件需要管理员权限才有效，面板在未提权时据此提示用户 |
| `publisher` | string | 否 | `""` | 发布者**自称**的名字（≤64 字）。安装框与插件浮层原样展示；缺省显示「未声明（来源不明）」。它与内置 id 同时命中官方标识时界面才按「随安装包分发」措辞 |
| `homepage` | string | 否 | `""` | 只接受 `http(s)://` 链接（≤200 字）。写别的格式整包被拒——安装框要给用户一个可核对的来源 |
| `permissions` | string[] | 否 | `[]` | 声明式权限键（词表见 §3.0）。**内核不据此限制任何行为**，只让用户看清插件声称要碰什么；未登记的键原样展示（不拒绝），最多 16 条 |
| `toggle` | string[] | 否 | `["<id>_enabled"]` | 开关对应的配置路径（点号路径）。见 §7.1 |
| `tip` | string | 否 | `""` | 主页悬停提示。`\n` 换行 |
| `config.keys` | string[] | 否 | `[]` | 插件声明"我会读/写这些配置路径"。**只有这里的路径允许 `set_cfg` 写入** |
| `config.defaults` | object | 否 | `{}` | 仅当插件类没有 `DEFAULT_CONFIG` 时用作兜底默认值（见 §7.3） |
| `settings` | object[] | 否 | `[]` | 设置面板可调项声明。每个功能行都有设置入口（见 §3.3），本字段决定入口里的可调项；空/缺省时浮层里只剩卸载。语义与校验见 §3.3 |
| `requires` | string[] | 否 | `[]` | 依赖的其它插件 id。被依赖者没加载成功 → 本插件 `status=load_failed` |
| `description` | string | 否 | — | 宿主**不读**。给分发目录/README 用 |

### 3.0 权限词表（声明式，v2.11.3）

`permissions` 不是开关，是给用户看的**声明**。写法用词表里的键：

| 键 | 安装框里的说法 |
| --- | --- |
| `process.watch` | 监视进程启动与退出 |
| `process.kill` | 结束进程 |
| `process.priority` | 调整进程优先级 |
| `window.close` | 关闭窗口/弹窗 |
| `files.plugin` | 读写自己的插件目录 |
| `files.any` | 读写任意文件 |
| `registry.write` | 修改注册表 |
| `network` | 联网收发数据 |
| `shell.run` | 启动程序或调用系统命令 |
| `config.write` | 修改 WBOX 配置 |
| `ui.panel` | 向面板添加界面元素 |

词表外的键不会导致拒绝安装，界面上标「未登记」原样展示——第三方插件难免有
自己的说法，写个新词就装不进去是不合理的。但**声明少写不影响能力**：插件
照样能做词表外的一切，见 §0。

### 3.1 布尔与整数是严格的

`"home": "true"`（字符串）**不会**被当成 `true`，而是回落到默认值 `true`——
碰巧一样所以看不出来；`"home": "false"` 则会被当成默认的 `true`，你的插件会
出现在主页上。`order` 同理：`"order": "10"` 会被当成 `100`。

`api` 的校验更严：`isinstance(True, int)` 为真，所以宿主显式排除了 `bool`，
`"api": true` 直接判 `bad_manifest`。

### 3.2 manifest 非法时代码不会被执行

校验顺序是**先 manifest、后 import**：`id` / `name` / `version` / `api` / `entry`
任何一项不合法，你的 `plugin.py` 根本不会被 import。所以"半成品代码"放在
非法 manifest 的目录里是安全的，不会被误执行。

### 3.3 `settings` —— 设置面板可调项（面板行设置入口）

`settings` 是一个对象数组，每项声明一个面板可调的配置键。宿主解析后交给面板。

**v2.7.0 起每个功能行都有设置入口**（卸载沉进了设置浮层，没有可调项的插件
也需要入口）：v2.10.0 起内置默认是极简皮肤，点整行（名称、图标或行尾箭头）
在本行底下行内展开可调项，再点一次收起；声明 `performance=standard` 的第三方
皮肤行尾一枚齿轮，点开是本插件的设置浮层。`settings` 缺省、为空数组、或
所有项都被丢弃时，入口仍在，只是浮层里只剩插件名、版本与「卸载此插件」
（极简档显示「这个插件没有可调项」）。

写法上只有下面两条硬规则，其余都是"写了不生效"：

1. **每项必须有 `key` / `type` / `label` 三个字段**，缺一或为空则该项被丢弃。
2. **非法项一律静默丢弃，不判 `bad_manifest`**：这是展示层字段，写坏一项不该
   让一个本来能跑的插件彻底不工作。丢弃也不打日志（面板会频繁重读注册表）。
   所以改完 manifest 后**必须在面板里亲眼看到这一项**，没看到就是被丢弃了。

#### `key` —— 点路径，落盘到 `config.json`

- 语法：`^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$`，即点号分隔的
  标识符序列，如 `gamepp.path`、`desktop_hud.show_cpu`。首段不允许数字开头。
- 面板保存时按点路径写 `config.json`（`demo_hello.count` 落盘为嵌套的
  `{"demo_hello": {"count": ...}}`），用户值优先于默认值，与 §7 的合并口径一致。
- 请只用**自己插件声明过的**键（§7.1 的 `toggle` / `config.keys`）。宿主解析
  `settings` 时**不**做内核保留键过滤（§7.4 的 `poll_*` / `exclude_*` / `_`
  前缀约束在这里不生效），往内核键上挂设置项会把面板显示和守护行为掰成两套——
  这是写法合法但后果自负的典型。

#### `type` —— 四种控件

| type | 面板控件 | 配置里的值 | 专属字段 |
| --- | --- | --- | --- |
| `bool` | 胶囊开关 | `true` / `false` | 无 |
| `int` | −/＋ 整数步进器 | 整数 | `min` / `max` / `step` / `unit` |
| `choice` | 一排文字选项按钮 | 选项的 `value` 字符串 | `options` |
| `path` | 「自动查找 / 选择」文件选择 | 路径字符串（面板统一为反斜杠） | 无 |

`type` 不是这四个之一（或写成别的类型）→ 该项丢弃。

#### 各类型的字段细则（与宿主 `_parse_settings` 逐条对应）

- **`label`**：必填，非空白字符串，去首尾空白后显示在设置行左侧。
- **`hint`**：可选字符串，去空白后作为标题下的小字说明；空串不显示。
- **`int`**：
  - `min` 默认 `0`，`max` 默认 `100`；两者都必须是整数（`"50"` / `true` 都
    非法，回落默认值）。`max < min` 时**自动对调**，不报错。
  - `step` 默认 `1`，且强制 `>= 1`（写 `0` 或负数按 `1` 处理）。
  - `unit` 可选字符串（如 `"秒"`），显示在数值旁边。
  - 面板是**整数步进器**：不支持小数。需要小数参数的插件自己在代码里算，
    不要往设置面板塞。
- **`choice`**：
  - `options` 是选项列表，每项两种写法等价：`{"value": "tr", "label": "右上"}`
    或 `["tr", "右上"]`。`value` 与 `label` 都必须是非空白字符串。
  - **清洗后一个选项都不剩 → 整项丢弃**（没有选项的控件渲染出来是空壳）。
  - 落盘的是 `value`（不是 `label`）；当前配置的值不在选项里时，面板按第一项显示。
- **`path`**：无专属字段。值为空时面板显示「自动查找」，点「选择」弹文件对话框；
  面板保存时把 `/` 统一替换成 `\`。
- **`bool`**：无专属字段。当前配置缺这个键时面板按 `false` 显示。

#### 浮层里显示的值从哪来

面板按 §7 的三段合并（内核默认 → 插件 `DEFAULT_CONFIG` → 用户 `config.json`）
后的配置显示当前值。所以：**每个 `settings.key` 都该出现在你的
`DEFAULT_CONFIG` 里**，否则面板显示的不是守护实际在跑的值。

#### 与 `config.keys` 的关系

`settings` 声明的是"面板替用户写哪些键"，`config.keys` / `toggle` 声明的是
"插件自己有权读写哪些键"。两者写同样的键最不容易出错；注意 `set_cfg` 的权限
只认后者（§7.4），面板写的键插件自然能从 `ctx.cfg()` 读到。


---

## 4. Python API

模块：`plugin_api`（已在 `sys.path` 里，直接 `from plugin_api import ...`）。

### 4.1 三个数据类

#### `SessionView` —— 会话只读视图

由内核构造，插件**只能读**（`__setattr__` 已封死，写入抛 `AttributeError`）。

| 属性 | 类型 | 语义 |
| --- | --- | --- |
| `.state` | `str` | `'idle'` / `'active'` / `'grace'` 三态之一 |
| `.active` | `bool` | 等价于 `state == 'active'` |
| `.games` | `list[str]` | 本轮判定为"在跑的游戏"的进程名（小写） |
| `.started_at` | `float` | 本会话 `idle -> active` 的墙钟时刻（0.0 表示无） |
| `.grace_until` | `float` | 宽限期的截止墙钟；非 grace 状态为 0.0 |
| `.resumed` | `bool` | 本会话是否经历过"宽限期内游戏回来"（`on_session_resume`） |

常量：`STATE_IDLE` / `STATE_ACTIVE` / `STATE_GRACE`。

#### `Tick` —— 每轮的数据包

内核每轮构造一次，构造后不再修改（插件也**不要**改）。

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `.seq` | `int` | 单调递增的轮次序号，从 1 开始。判断"这一轮和第几轮"用它 |
| `.now` | `float` | 本轮的统一墙钟。**同一轮内所有钩子拿到的 `now` 相同**，离线测试靠它断言 |
| `.procs` | `dict[int, str]` **或 `None`** | `{pid: 小写进程名}`。`None` 表示"本轮快照失败"，与 `{}`（成功但一个进程都没有）语义完全不同 |
| `.names` | `set[str]` | `set(procs.values())` 的便捷视图（快照失败时是空集，别用它判断"没有进程"） |
| `.games` | `list[str]` | 同 `session.games` |
| `.session` | `SessionView` | 会话视图快照 |
| `.events` | `frozenset` | 预留事件集合，当前恒为空 |

#### `PluginContext` —— 每个插件实例一个

构造函数参数（一般不用自己构造，宿主会给你）：`id` / `base` / `plugin_dir` /
`data_dir` / `api` / `manifest` / `toggle`。

**属性**（只读约定）：

| 属性 | 语义 |
| --- | --- |
| `ctx.id` | 插件 id（= `manifest.id`） |
| `ctx.base` | 安装根目录（`config.json`、`guard.log` 所在处） |
| `ctx.plugin_dir` | `plugins\<id>` 的绝对路径 |
| `ctx.data_dir` | `plugins\<id>\data`，宿主已 `makedirs` |
| `ctx.api` | 内核提供的 API 版本（当前 1） |
| `ctx.manifest` | manifest 的 dict 副本 |
| `ctx.toggle` | 规范化后的开关路径列表（至少 `['<id>_enabled']`，除非你显式写 `[]`） |
| `ctx.status_text` | `set_status()` 写入的摘要 |

**方法**：

| 签名 | 语义 | 可空 | 备注 |
| --- | --- | --- | --- |
| `ctx.cfg(path, default=None)` | 按点号路径读配置，缺键回落 `DEFAULT_CONFIG`，再缺返回 `default` | — | `ctx.cfg('demo_hello.speed', 3)` |
| `ctx.set_cfg(path, value)` | 写配置（内存），内核负责落盘 | 越权抛 `PermissionError` | 只允许 `toggle` + `config.keys` 声明的路径 |
| `ctx.log(msg)` | 写 `guard.log`，自动加 `[<id>] ` 前缀 | — | 唯一正确的日志方式，见 §9.1 |
| `ctx.procs()` | 本轮进程表 | **可能为 `None`** | 等价于 `tick.procs`，但在非 tick 钩子里也能用 |
| `ctx.snapshot()` | **现取**一份进程表（关闭流程里复核用） | **可能为 `None`** | 比 `procs()` 新，但有系统调用开销 |
| `ctx.process_path(pid)` | 进程 exe 全路径（走内核路径缓存） | **可能为 `None`** | 取不到通常是权限或进程已退出 |
| `ctx.refresh_paths(procs)` | 让内核按这张表刷新路径缓存 | — | 一般不用手动调 |
| `ctx.games_present(procs=None)` | 内核判定"游戏是否还在跑" | 取不到表时返回 **`True`** | 拿不到信息时**宁可不动作** |
| `ctx.watch_names()` | 当前监控的进程名集合（小写） | 可能为空集 | 配置 + 自动发现 |
| `ctx.session()` | `SessionView` | 不会为 `None` | provider 缺失时返回 idle 视图 |
| `ctx.now()` | 本轮墙钟 | — | provider 缺失时退化为 `time.time()` |
| `ctx.import_core(name)` | 取内核低层库 | 未知名字抛 `KeyError` | 见 §4.2 |
| `ctx.request_fast_poll(until)` | 登记"到 `until` 之前我要 0.4 秒快节拍" | — | 内核取所有插件的最大值 |
| `ctx.retry_close(delay=2.0)` | **仅 `on_session_close` 内有效**：把会话从"即将 idle"拉回 grace | 返回 `bool` | 其它钩子里调用返回 `False` |
| `ctx.write_state(name, obj)` | 原子写 `data\<name>.json`（`tmp` + `os.replace`） | 抛 `OSError` | `name` 不能含路径分隔符，自动补 `.json` |
| `ctx.read_state(name, default=None)` | 读 `data\<name>.json` | 文件缺失/损坏返回 `default` | |
| `ctx.set_status(text, level='ok')` | 面板菜单里该插件行右侧的一行摘要 | — | `level` 当前只记录不参与渲染 |

### 4.2 `ctx.import_core()` —— 唯一允许的内核访问方式

```python
core = self.ctx.import_core('guard_core')
core.run_hidden([core.TASKKILL, '/PID', str(pid), '/F'])
```

白名单只有六个（`plugin_host.CORE_MODULES`）：

| 名字 | 里面有什么 | 典型用途 |
| --- | --- | --- |
| `guard_core` | `snapshot()` / `process_path(pid)` / `run_hidden(args)` / `run_capture(args)` / `shell_execute_runas(path)` / `enable_debug_privilege()` / 常量 `TASKKILL` `TASKLIST` `POWERSHELL` `CREATE_NO_WINDOW` `RTSS_NAMES` | 启动/结束进程、提权、取路径 |
| `ace_native` | ACE 清理的原生实现（四道闸门在内） | ACE 相关动作 |
| `popup_guard` | 弹窗拦截的原生实现（窗口枚举 / WM_CLOSE / 进程树） | 弹窗相关动作 |
| `game_lib` | Steam / Epic / WeGame / GOG / Ubisoft 库扫描 | 找游戏目录 |
| `core_defaults` | 内核默认值常量（`DEFAULTS` / `ACE_CLEANUP_DEFAULTS` / …） | 读安全默认值 |
| `plugin_api` | 本模块自身 | 取 `SessionView`、常量 |

取进程表**不要**调内核的 `Guard.read_procs`（它是守护实例的方法，插件拿不到），
用 `ctx.procs()` / `ctx.snapshot()`。

**为什么必须这样，而不是 `import guard_core`：**

1. 内核库是被冻结进 exe 的，插件目录里没有它们；在插件里直接 `import` 会在
   冻结环境下拿到另一个实例或直接 `ModuleNotFoundError`（PyInstaller 的静态
   分析看不到插件目录里的导入）。
2. 面板进程也会 `import` 你的插件。用 `ctx.import_core()` 懒加载，面板走
   `gui_*` 时就不会把 Win32 那一套拉进面板进程（守护模式的常驻内存也是这么
   保住的：守护进程的模块表里完全不含 tkinter，由 `test/test_guard_no_tkinter.py`
   在干净子进程里钉死）。
3. 白名单是**契约**：想加一个内核模块进白名单属于改内核，需要评审。不要在
   插件里复制一份内核逻辑——复制出来的那份会绕过四道闸门。

### 4.3 `WBOXPlugin` 基类（旧名 `WingmanPlugin` 保留为别名）

```python
from plugin_api import WBOXPlugin

class Plugin(WBOXPlugin):
    DEFAULT_CONFIG = {'demo_hello': {'enabled': True}}
    API = 1

    def __init__(self, ctx):
        WBOXPlugin.__init__(self, ctx)   # 必须，否则 self.ctx 不存在
```

不继承也能被加载（鸭子类型，宿主会记一行提示），但 `gui_state()` /
`gui_toggle()` 的默认实现就没了，面板开关会失灵。**请继承**。

---

## 5. 钩子与调度

### 5.1 完整调用顺序

```
守护冷启动
  ├─ discover()                      扫描目录 + 校验 manifest（不 import）
  ├─ _load_one()                     import 入口文件 → 实例化 cls(ctx)
  │    └─ 失败 → status=load_failed，后续钩子全部不发
  ├─ migrate(raw_cfg)                每次加载一次；可原地改 raw_cfg（搬段/改名）
  ├─ on_load()                       加载完成，做一次性目录检查
  ├─ on_config(自己的cfg)            每插件一份，只含自己声明的路径
  ├─ on_start()                      主循环开始前（guard.pid 已写、stop.flag 已清）
  └─ 循环 tick():
       ├─ reload_config()            config.json 的 mtime 变了 → 再发一次 on_config
       ├─ read_procs()
       │    └─ 返回 None → 本轮**什么都不发**（前 2 次），第 3 次起沿用上一轮结果
       ├─ 会话迁移钩子（本轮最多 1 个）
       │    ├─ on_session_start   idle -> active
       │    ├─ on_session_resume  grace -> active（宽限期内游戏闪断后回来）
       │    ├─ on_session_grace   active -> grace（游戏全退，进入 exit_grace_sec）
       │    └─ on_session_close   grace -> idle（宽限到期且游戏确实没回来）
       └─ on_tick(tick)              **必然最后调用**，每轮最多一次
退出
  └─ on_exit(reason)                 reason ∈ {'guard_stop', 'reload', 'fatal'}
```

要点：

- **`on_tick` 一定在会话钩子之后**。同一轮里可能出现 `on_session_close` +
  `on_tick`，不会出现两个会话钩子。
- `on_config` 在**每次** `config.json` mtime 变化后重发（热加载），不只是启动时。
- 热重载插件（面板装/卸）会走 `on_exit('reload')` → 重建宿主 → `on_load` →
  `on_config`；**不重发 `on_start`**（它只负责冷启动那一轮的残留清理）。
- `on_exit` 禁止阻塞超过 2 秒：卸载程序在等它。

### 5.2 `procs is None` 时不分发

`read_procs()` 失败（连续 1~2 次）时，内核直接 `return`，本轮**不发任何钩子**。
你的插件不会收到"这一轮没有游戏"的假信号，也不会因为空表误关东西。

连续 3 次失败后内核降级：沿用上一轮的成功结果继续跑，并在日志里写一行。

所以：**任何"关东西"的动作都要先看 `procs` / `snapshot()` 是不是 `None`**，
是 `None` 就调 `ctx.retry_close()` 推迟，而不是当空表处理。

```python
def on_session_close(self, tick):
    procs = self.ctx.procs()
    if procs is None:
        self.ctx.retry_close(2)      # 2 秒后再发一次 on_session_close
        return
    ...
```

### 5.3 异常隔离

每个钩子调用都包在 `try/except BaseException` 里（含 `SystemExit`、`KeyboardInterrupt`）：

- 异常被记到该插件的 `error_count` / `last_error` / `last_error_at`；
- 状态从 `ok` 降为 `degraded`（**仍然继续收钩子**）；
- 同类异常 60 秒内只写一条日志，避免刷爆 `guard.log`；
- 钩子耗时超过 **5 秒**记一条警告（Python 无法安全中断同步调用，只告警不终止）；
- 下一次同钩子成功即恢复 `ok`，日志里会写"已恢复正常"。

结论：**抛异常不会拖垮守护**，但会降级并让面板显示异常——不要用异常做流程控制。

### 5.4 连败 10 次自动降频

`on_tick` 连续失败 **10 次**后，宿主把 `on_tick` 降频为**每 30 轮调用一次**；
成功任意一次立即恢复每轮调用。

后果（务必知道）：降频期间你的插件会"看起来反应很慢"。如果是因为某次
`tick` 里访问了不稳定的东西（权限、已退出进程、坏路径），先修根因，不要靠
降频兜。

### 5.5 `on_config` 拿到的是"你自己那份"

宿主会先按 `manifest.toggle` + `manifest.config.keys` 把你的配置切出来：

```python
def on_config(self, cfg):
    # cfg 只有 demo_hello_enabled 与 demo_hello 两个路径（外加 DEFAULT_CONFIG 打底）
    seg = cfg.get('demo_hello') or {}
    self.enabled = bool(cfg.get('demo_hello_enabled', True))
    self.speed = float(seg.get('speed', 3))
```

**不要**指望在这里看到 `game_processes` 之类内核键——即使写了也读不到。
要读内核键用 `ctx.cfg('game_processes')`（读是允许的，写不允许）。

---

## 6. 图标

`icon` 只接受下列 20 个 kind（`plugin_api.ICON_WHITELIST`，由
`settings_gui.Glyph` 绘制；面板自用的箭头、窗口控制等图标不在插件白名单里）：

```
link  plane  pulse  shield  block  dot  play  stop  boot  rescan
log   cfg    sun    moon    check  close  plus  trash  folder  admin
```

- 写未知值 → 回落 `dot`，并写一行日志 `icon %r 不在白名单，回落 dot`；
- 图标是**灰阶线性**风格（`shield` 是唯一实心品牌盾），没有彩色、没有 emoji；
- 想加新图标**不是插件侧能解决的事**：要同时改 `design/generate_icons.py`
  与 `settings_gui.Glyph.draw` 两处并重新出图，属于内核改动。

现有 4 个内置插件的选择可作参考：`afterburner=plane`、`gamepp=pulse`、
`ace=shield`、`popup=block`。

---

## 7. 配置

### 7.1 两种放法，任选其一

**布尔开关（推荐，最少代码）**

manifest 不写 `toggle`，宿主自动补成 `['<id>_enabled']`：

```json
{ "id": "demo_hello", "name": "示例", "version": "1.0.0", "api": 1 }
```

于是 `config.json` 里是扁平键：

```json
{ "demo_hello_enabled": true }
```

**对象段（要放多个参数时）**

```json
"toggle": ["demo_hello.enabled"],
"config": { "keys": ["demo_hello"] }
```

```json
{ "demo_hello": { "enabled": true, "speed": 3, "path": "" } }
```

`toggle` 写 `[]`（显式空数组）表示"这个插件没有开关"，宿主会强制
`home = false`，它就不出现在主页上（只在菜单的插件分组里出现一行）。

### 7.2 `config.json` 是 mtime 热加载

守护每轮 `reload_config()` 检查 `config.json` 的 mtime；变了就重新合并并给
每个插件发 `on_config`。**不需要重启守护**，也**不要**自己去读 `config.json`
文件——那会绕过合并规则（默认值打底、保留键过滤、保护名单并集）。

写配置一律用 `ctx.set_cfg(path, value)`，内核会异步落盘。

### 7.3 默认值写在自己的代码里

```python
class Plugin(WBOXPlugin):
    DEFAULT_CONFIG = {
        'demo_hello': {'enabled': True, 'speed': 3},
    }
```

`DEFAULT_CONFIG` 是**权威来源**，`manifest.config.defaults` 只在插件类没有
`DEFAULT_CONFIG` 时兜底。

**不要**改 `src/core_defaults.py` 来塞插件默认值：那个模块是随 exe 冻结的
安全默认值唯一来源（ACE 四道闸门的秒数、弹窗保护名单都在里面），改它等于改
内核行为，还要重新出包。

### 7.4 内核保留键：只能读，不能写

`_RESERVED_KEYS` / `_RESERVED_PREFIXES`：

| 类别 | 具体 |
| --- | --- |
| 保留键 | `game_processes`、`game_roots`、`exit_grace_sec`、`log_max_kb`、`ui_theme` |
| 保留前缀 | `poll_`、`exclude_`、`_`（下划线开头） |

- 写在 `manifest.toggle` / `config.keys` 里会被**静默剔除**；
- `ctx.set_cfg()` 写这些路径会抛 `PermissionError`；
- `ctx.cfg()` 读它们是完全允许的。

### 7.5 保护性名单是"只增不减"

`popup_guard.protect_names` 属于 `SAFE_UNION_PATHS`：用户值与内核默认值做并集
（默认项在前、大小写不敏感去重），用户删不掉默认的保护项（QQ/微信等）。
这是产品语义，不是 bug。其它 list 键仍是"整体替换"语义。

---

## 8. 打包与分发

### 8.1 包格式：`wbox-plugin-zip-v1`

```
demo_hello-v1.0.0.zip
└─ demo_hello/                ← 唯一顶层目录，名字必须等于 manifest.id
   ├─ plugin.json
   ├─ plugin.py
   └─ ...
```

规则（`plugin_package.inspect_zip` 全量校验，任一不过即拒绝安装）：

| 项 | 限制 |
| --- | --- |
| 顶层目录 | **有且只有一个**，且名字必须匹配 id 正则、且等于 `plugin.json` 的 `id` |
| 文件数 | ≤ **64** |
| 单文件大小 | ≤ **1 MB** |
| 解压后总量 | ≤ **2 MB** |
| 压缩包大小 | ≤ **8 MB** |
| 文件类型 | 白名单：`.py .json .md .txt .cfg .ini .yaml .yml .csv .xml .png .ico` |
| 路径 | 拒绝绝对路径、盘符、`..`、空条目、尾点/尾空格、符号链接、加密条目 |
| 必需文件 | `<id>/plugin.json` + `entry` 指向的入口文件 |

**不接受**可执行文件与脚本（`.exe .dll .pyd .bat .cmd .ps1 .vbs .lnk .reg` 等），
白名单之外的扩展名一律拒。真需要带二进制，请让插件在运行时从官方渠道下载，
并自己校验签名。

面板「＋」安装与安装器的插件保留逻辑**共用同一份校验**，所以"能被打出来的
就一定装得进去，装得进去的一定能跑"。

### 8.2 打包命令

```bat
rem 在插件仓库 wbox-plugins 根执行；不带参数 = 打包 plugins\ 下全部插件到 dist\
C:\Python38\python.exe tools\pack.py

rem 打包指定插件目录（目录名必须等于 manifest.id）
C:\Python38\python.exe tools\pack.py plugins\demo_hello

rem 只校验一个已有 zip（不安装、不写盘）
C:\Python38\python.exe tools\pack.py --check dist\demo_hello-v1.0.0.zip
```

输出：`dist\<id>-v<version>.zip` 和同名 `.sha256` 侧车。产出的包会
**立刻自检一遍**（条目集合 / 体积 / 侧车哈希），打不出来就说明你的目录结构有问题。
第三方作者也可以按同一约定自己打 zip：根下只有一个 `<id>\` 目录，
`<id>\plugin.json` 是清单，入口文件由 `entry` 指定。

分发时**把 `.zip` 和 `.zip.sha256` 一起发**：用户装的时候，安装框会核对侧车，
不一致直接拒绝（包被替换或传坏时能发现）。侧车不是签名，只能证明"和发布时
是同一份文件"，不要拿它当作者身份。

打包时会自动跳过：`__pycache__`、`data`、`.git`、`.idea` 目录，以及
`.pyc .pyo .bak .tmp .log .zip .flag` 后缀和 `Thumbs.db` / `desktop.ini`。

### 8.3 安装 / 卸载 / 升级

- **安装**：面板标题栏的「＋」→ 选 zip。校验通过后先弹一屏**来源确认**
  （发布者 / 来源 / 声明权限 / 文件数与体积 / 包完整性 / SHA-256），确认后才安装。
  已存在同 id 时再弹一次覆盖确认；旧目录先移到
  `%LOCALAPPDATA%\WBOX\replaced-plugins-<时间戳>\`（**移动不是删除**）。
- **安装被打断也能收尾**：换入前写 `.plugin-install.json` 事务日志。若在
  "旧版已移走、新版还没换入"之间断电/被杀，下次启动（守护或面板）会自动
  把备份搬回；首发安装被打断则用完整暂存目录补完；两者都不行才报"需要重装"。
  孤儿暂存目录（`.plugin-staging-*`，超过 6 小时无人认领）一并清掉。
- **卸载**：插件行尾齿轮 → 设置浮层底部「卸载此插件」（极简档点行展开，
  在展开块底部）。目录移到
  `%LOCALAPPDATA%\WBOX\removed-plugins-<时间戳>\`。内置插件也允许删。
- **生效**：面板写 `plugins_reload.flag`；守护在**会话 idle** 时重建宿主，
  最多 2 秒延迟。游戏会话中安装的插件从**下一局**开始接管。
- **升级安装包不会覆盖用户插件**：安装器只覆盖 `afterburner/ace/gamepp/popup/desktop_hud`
  五个内置目录，其它目录在升级/卸载前会先备份。

---

## 9. 调试

### 9.1 日志：只用 `ctx.log()`

```python
self.ctx.log('检测到 foo.exe 退出，准备关闭')
# guard.log 里出现：[demo_hello] 检测到 foo.exe 退出，准备关闭
```

- 写入 `<安装目录>\guard.log`（512 KB 轮转到 `guard.log.1`）；
- 自动加 `[<id>] ` 前缀，方便过滤；
- **不要用 `print()`**：守护进程以 `console=False` 打包，`sys.stdout` 是 `None`，
  `print()` 会抛 `AttributeError`，然后被记成一次插件异常（你自己给自己降级）。

### 9.2 `plugins_state.json` 只读

守护写的运行期状态（`status` / `error_count` / `last_error` / `ticks` / `guard_pid`），
面板读它显示异常。**插件不要写这个文件**——面板写会被宿主拒绝
（`write_state=False`），你写了就是和守护抢同一份状态。

自己需要持久化的东西放 `ctx.write_state('mine', {...})` →
`plugins\<id>\data\mine.json`（原子写，`data\` 不进分发包）。

### 9.3 热重载标记 `plugins_reload.flag`

面板装/卸插件后写这个文件；守护看到后在**会话 idle** 时执行
`on_exit('reload')` → 重建宿主。游戏会话中只记一行日志、等本局结束。

手动触发（调试用）：

```bat
rem 内容写什么无所谓，只要文件存在
echo 1 > "C:\Program Files\WBOX\plugins_reload.flag"
```

### 9.4 自检

```bat
rem 守护只读自检：进程表 / 配置 / 插件状态，结果写 self_test.txt
"C:\Program Files\WBOX\WBOX.exe" --self-test
```

在 `on_self_test(self, out)` 里 `out.append('一行诊断')` 即可参与自检。

### 9.5 本地迭代循环（推荐）

```bat
rem 1) 把插件目录软链/复制到安装目录（或以管理员身份直接编辑）
rem 2) 改完 plugin.py 后写重载标记
echo 1 > "C:\Program Files\WBOX\plugins_reload.flag"
rem 3) 看日志最后 30 行
powershell -NoProfile -Command "Get-Content 'C:\Program Files\WBOX\guard.log' -Tail 30"
```

改 `plugin.json` 一定要重新触发重载（manifest 只在加载期读一次）。

---

## 10. 常见错误与排查

| 现象（面板 / `plugins_state.json`） | `detail` 原文 | 原因 | 处理 |
| --- | --- | --- | --- |
| 菜单插件行显示"未能加载" | `缺少 plugin.json` | 文件名拼错 / 放错层级（应在 `plugins\<id>\` 下） | 检查文件名大小写 |
| 同上 | `plugin.json 读取/解析失败：...` | JSON 语法错误、多了逗号、编码不是 UTF-8 | 用 `--check` 校验；存成 UTF-8（可带 BOM） |
| 同上 | `id 非法（需匹配 ^[a-z][a-z0-9_]{1,31}$）：'DemoHello'` | 大写、连字符、数字开头、太短/太长 | 改成 `demo_hello` 这种 |
| 同上 | `manifest 缺少 name` / `version` / `api` | 必填字段漏写 | 四个必填：`id` / `name` / `version` / `api` |
| 同上 | `api 必须是整数：'1'` | 写成了 `"1"` 或 `true` | 写裸整数 `1` |
| 同上 | `entry 非法（应形如 plugin.py:Plugin）：'../x.py:Plugin'` | 绝对路径、含 `..`、类名不是标识符 | 只写相对文件名 |
| 同上 | `导入失败：ModuleNotFoundError: ...` | 依赖了未随包分发的第三方库 | 只用标准库 + `ctx.import_core()` |
| 同上 | `导入失败：SystemExit: ...` | 模块顶层调了 `sys.exit()`／有副作用 | 顶层只放定义 |
| 同上 | `类 Plugin 不存在（plugin.py）` | `entry` 的类名与文件里的类名不一致 | 统一叫 `Plugin` |
| 同上 | `构造失败：...` | `__init__` 里做了危险的事（读文件/建窗口） | `__init__` 只赋初值 |
| 同上 | `依赖插件未加载：xxx` | `requires` 里的插件没成功加载 | 先修被依赖的插件 |
| 同上 | `插件要求 api=2，当前内核 api=1，需要更新的内核` | 插件比内核新 | 降 `api` 或升级 WBOX |
| 状态 `conflict` | `id 与 plugins\xxx 冲突` | 两个目录声明了同一个 id | 删掉一个 |
| 面板显示"需要管理员权限" | — | manifest 写了 `requires_admin: true` | 正常提示，不影响加载 |
| 每分钟一次"抛异常"日志 | `on_tick 抛异常：...` | 钩子里访问了不稳定的东西 | 看 `last_error`，修根因（不要靠降频） |
| 反应变慢（`on_tick` 每 30 轮才调一次） | — | `on_tick` 连败 ≥10 次已降频 | 同上 |
| 面板「＋」拒绝安装 | `插件包必须只有一个顶层目录` | zip 里直接放了文件，或两个插件目录 | 重新按 §8.1 打包 |
| 同上 | `插件包不允许的文件类型 '.dll'` | 带了二进制 | 去掉，或改为运行时下载 |
| 同上 | `解压后总量超过 2 MB，拒绝安装` | 数据文件太大 | 精简，或运行时下载 |
| 同上 | `plugin.json 的 id (a) 与顶层目录 (b) 不一致` | 目录名与 manifest 不一致 | 改名 |
| 改了代码但没变化 | — | 没触发重载，或 manifest 改了没重载 | 写 `plugins_reload.flag` |
| 改内核文件无效 | — | `ace_native.py` / `popup_guard.py` 等冻结在 exe 里 | 插件侧无法修改，必须改内核并重新出包 |

---

## 11. 版本与兼容

### 11.1 声明自己支持的 API 版本

```json
{ "api": 1 }
```

```python
class Plugin(WBOXPlugin):
    API = 1        # 与 manifest.api 一致；不一致时宿主记日志、以 manifest 为准
```

### 11.2 内核的行为

| 情况 | 行为 |
| --- | --- |
| `api == 1` | 正常加载 |
| `api < 1`（0 或负数） | `status=bad_manifest`，`detail=api 必须 >= 1` |
| `api > 1` | `status=too_new`，**不 import 代码**，面板显示"需要更新的内核" |
| 插件内 `API` 与 manifest 不一致 | 记一行日志，**以 manifest 为准**，不阻止加载 |
| `api` 小于内核的 `API_VERSION`（将来可能发生） | 当前内核 `API_VERSION = 1`，还没有"更低的合法值"，所以这一格暂时不会出现；内核将来升到 2 时，`api: 1` 的旧插件按"只加不改"的契约继续加载，新增钩子走默认实现 |

版本策略是**只加不改**：新增字段/新增钩子会提升 `API_VERSION`，删除字段或改变
语义属于破坏性变更，需要新的主版本。所以放心按 `api: 1` 写，内核升级不会悄悄
改你的钩子签名。

---

## 12. 参考实现

| 插件 | 位置 | 值得看的地方 |
| --- | --- | --- |
| `gamepp` | `plugins\gamepp\plugin.py` | 最简洁的完整例子：所有权语义（只关自己拉起的）、`retry_close` 用法、注册表探测 |
| `afterburner` | `plugins\afterburner\plugin.py` | 父子进程树处理、RTSS 家族 |
| `ace` | `plugins\ace\plugin.py` | `ctx.import_core('ace_native')` + 四道闸门的正确调用姿势 |
| `popup` | `plugins\popup\plugin.py` | `request_fast_poll` 登记快节拍、`protect_names` 保护名单 |

随安装包分发的最小示例见同目录下的 `plugin-template\`
（`plugin.json` + `plugin.py`，复制到 `plugins\<你的id>\` 并改名即可）。

---

## 附录：字段速查（一页版）

```json
{
  "id": "demo_hello",
  "name": "示例插件",
  "version": "1.0.0",
  "api": 1,
  "entry": "plugin.py:Plugin",
  "icon": "pulse",
  "order": 50,
  "home": true,
  "toggle": ["demo_hello_enabled"],
  "tip": "说明",
  "config": {"keys": ["demo_hello"], "defaults": {}},
  "settings": [
    { "key": "demo_hello_enabled", "type": "bool", "label": "启用示例功能" },
    { "key": "demo_hello.mode", "type": "choice", "label": "显示位置",
      "options": [ ["tr", "右上"], ["bl", "左下"] ] }
  ],
  "requires": [],
  "builtin": false,
  "requires_admin": false
}
```

```python
from plugin_api import WBOXPlugin


class Plugin(WBOXPlugin):
    DEFAULT_CONFIG = {'demo_hello_enabled': True}

    def on_config(self, cfg):
        self.enabled = bool(cfg.get('demo_hello_enabled', True))

    def on_session_start(self, tick):
        if tick.procs is None:
            return
        self.ctx.log('会话开始：%s' % ', '.join(tick.games))

    def on_session_close(self, tick):
        if self.ctx.procs() is None:
            self.ctx.retry_close(2)
            return

    def on_tick(self, tick):
        if tick.session.state != 'active':
            return
```
