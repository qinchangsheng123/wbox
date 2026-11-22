# WBOX 皮肤制作规范 v1.2.0

> 适用范围：WBOX（原「僚机 Wingman」）设置面板的外观层。
> 版本：v1.2.0，2026-11-22；v1.0.0 于 2026-11-17 定稿，v2.7.0–v2.10.0 的落地实况见第 8 节。
> v1.1.0 变更：新增第 9 节「派生一套配色主题」（以默认皮肤为底、只换 token 的正式做法，
> 范例 `design/themes/kuan-green/`）；内置皮肤显示名「极简」→「默认」（id 仍是 `plain`）；
> 补 `skin_package.pack()` 打包命令；token `on` / `on_knob` 增加"选中态"用途。
> **v1.2.0 变更（骨架合并）**：`standard` 渲染骨架（圆角卡片 / 浮层设置 / tween 动效 /
> 字标菜单）随液态玻璃一起退役，面板只剩默认那套骨架。`engine` / `performance` /
> `radius` 降为元数据：写 `standard` 的老包照常加载、按默认骨架渲染；皮肤卡片上的
> 成本标签统一显示「默认骨架」。3.3 的样例换成最小可用样例，不再以液态玻璃为参照。
> WBOX v2.18.0 又删掉了齿轮浮层菜单，现在真正会建的浮层只剩皮肤面板与插件
> 设置框两个；`menu_base` token 名保留（改名会让所有已分发皮肤包失效），
> 语义已收敛为这两个浮层的底色。
>
> **术语**：文中「默认骨架」= 方角直排 + 行内展开 + 同窗换页 + 零动画，是唯一的骨架。
> `performance=minimal` 仍出现在 `skin.json` 里，但现在只表示「不带位图」。
> 官方皮肤仓库：<https://github.com/qinchangsheng123/wbox-skins>（成品 `.wbskin`、
> `templates/plain-base/` 默认底模板、独立打包工具 `tools/pack.py`）；做皮肤从模板复制，
> 不要从零写 `skin.json`。
> 关联设计稿：`design/wbox-skins.html`（皮肤管理 / 默认 / 成本对照 / 规范四视图；
> 其中「液态玻璃」视图是 v2.10.0 之前的历史留档，不代表当前产品）

---

## 0. 一句话

**皮肤是纯数据的 zip 包**，只改颜色、字号、行高和几项尺寸，不含任何可执行代码；
面板加载它只换外观，骨架、交互、布局一步不动。

---

## 1. 为什么皮肤不是"进程插件"

| | 功能插件（ACE / 小飞机 / 弹窗拦截…） | 皮肤 |
|---|---|---|
| 形态 | `plugins/<id>/plugin.py` | `plugins/skins/<id>/skin.json` |
| 运行位置 | 守护进程内，完整 Python 权限 | 面板进程内，只被解析不执行 |
| 能力 | 提权、读进程、改注册表、读写配置 | 改颜色与尺寸（不含位图） |
| 失败影响 | 可能拖垮守护 | 最多面板变丑，回退内置「默认」 |

因此皮肤走**独立目录 + 独立校验**：`plugins/skins/` 与 `plugins/` 平级，
功能插件的扫描逻辑不扫这个目录，皮肤的校验不看 `plugin.json`。

**硬规则：皮肤包内出现 `.py .pyc .dll .exe .bat .ps1 .cmd` 任一后缀，安装器直接拒绝，
并写 `guard.log` 一行 `skin.reject reason=executable`。**

---

## 2. 包结构

```
my-theme.wbskin              # 其实就是 zip，后缀换成 .wbskin
├─ skin.json                 # 必需，唯一入口
├─ preview.png               # 必需，256 × 144，卡片缩略图（16:9）
└─ README.md                 # 可选，作者与说明（也建议写配色表与对比度）
```

- 默认骨架不读位图，所以没有 `assets/` 这一层了。带 `assets` 的包会被
  「`performance=minimal` 不许有位图」这条校验拒掉（v1.2.0）。

- zip 根目录直接是 `skin.json`，不允许再套一层文件夹。
- 编码统一 UTF-8 无 BOM；`skin.json` 用 2 空格缩进（不强制，但发布版请格式化）。
- 包总大小 ≤ 4 MB；单张位图 ≤ 512 KB，长边 ≤ 2048。

---

## 3. skin.json 字段

### 3.1 必需

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 小写字母 + 连字符，`^[a-z][a-z0-9-]{1,31}$`。装过之后不允许改，改名会被当成另一个皮肤 |
| `name` | string | 显示名，≤ 12 个汉字 |
| `version` | string | 三段式 `X.Y.Z` |
| `api` | int | 皮肤 API 版本，当前 `1` |
| `engine` | string | 历史字段：`flat` 或 `bitmap`；v1.2.0 起不参与渲染，新皮肤写 `flat` |
| `performance` | string | 历史字段：`minimal` 或 `standard`；v1.2.0 起只作「有没有位图」的成本声明，见第 5 节 |
| `chrome` | string | `custom`（自绘标题栏）或 `system`（系统标题栏） |
| `metrics` | object | `row` / `font` / `icon` / `titlebar` 四个整数，单位 px |
| `radius` | object | `window` / `card` / `row` 三个整数 |
| `light` | object | 26 个颜色 token，见第 4 节 |

### 3.2 可选

| 字段 | 类型 | 说明 |
|---|---|---|
| `author` | string | 作者 |
| `homepage` | string | 只做展示，面板不会去访问 |
| `dark` | object | 暗色 token；缺省时复用 `light` |
| `assets` | object | **已停用**：默认骨架不读位图，带了会被校验拒掉（保留字段只为老包能报出准确错误） |
| `description` | string | 一句话，显示在卡片副标题 |

### 3.3 完整样例（最小可用）

下面这份能通过校验，只用来对照字段结构。**做真实皮肤不要从零写**——把
`src/skins.py` 里 `_PLAIN_LIGHT` / `_PLAIN_DARK` 两份 token 拷出来改值就行
（流程见第 9 节），那样 26 个键一个不漏。

```json
{
  "id": "my-theme",
  "name": "我的主题",
  "version": "1.0.0",
  "api": 1,
  "engine": "flat",
  "performance": "minimal",
  "chrome": "custom",
  "author": "你",
  "description": "一句话说明",
  "metrics": { "row": 40, "font": 13, "icon": 16, "titlebar": 36 },
  "radius": { "window": 0, "card": 0, "row": 0 },
  "light": { "fg": "#0A0A0A", "t2": "#595959", "t3": "#767676", "t4": "#A3A3A3",
             "hair": "#E6E6E6", "hair_strong": "#CFCFCF",
             "fill": "#F0F0F0", "fill_2": "#F7F7F7", "press": "#E6E6E6",
             "inset": "#FAFAFA",
             "glass": "#FFFFFF", "glass_hi": "#FFFFFF",
             "glass_line": "#E6E6E6", "glass_edge": "#B0B0B0",
             "switch_off": "#C9C9C9", "knob": "#FFFFFF",
             "on": "#111111", "on_knob": "#FFFFFF",
             "icon_bg": "#111111", "icon_fg": "#FFFFFF",
             "menu_base": "#FFFFFF", "pop_base": "#FFFFFF",
             "amb0": "#FFFFFF", "amb1": "#F5F5F5", "amb2": "#E8E8E8",
             "amb3": "#FAFAFA" },
  "dark": { "同结构，26 个键一个不少": "" }
}
```

颜色写法：`#RRGGBB` 或 `#RRGGBBAA`（8 位带 alpha）。tkinter 不认 8 位十六进制，
落地时由 `skins.py` 的 `flatten_tokens()` 合成到窗口底色上换成不透明 `#RRGGBB`。

**v2.17.0 起 `engine` / `performance` / `radius` 只是元数据**：骨架只有默认那套，
写什么都不换骨架，`radius` 也恒按 0 渲染。声明 `standard` 的老包不报错、照常加载，
按默认骨架显示；新皮肤一律写 `minimal` + `flat`。`assets` 不要带——默认骨架不读
位图，带了会被「`performance=minimal` 不许有位图」这条校验直接拒掉。

---

## 4. 26 个颜色 token

名字固定、语义固定，皮肤只能改值。缺任意一个 → 拒绝加载，回退内置默认皮肤。
（初稿写 25 个是数漏了 `glass_edge`；实现以 `skins.TOKENS` 为准，共 26 个。）

| 组 | token | 用途 |
|---|---|---|
| 文本四级 | `fg` `t2` `t3` `t4` | 主文字 / 次要 / 提示 / 禁用 |
| 线与填充 | `hair` `hair_strong` | 1 px 分隔线 / 边框 |
| | `fill` `fill_2` `press` | 悬停底 / 按压底 / 选中底 |
| 卡片与窗口 | `inset` | 分组列表的内凹底 |
| | `glass` `glass_hi` `glass_line` `glass_edge` | 窗口底 / 顶部高光 / 内描边 / 外描边 |
| 开关 | `switch_off` `knob` `on` `on_knob` | 关闭轨 / 关闭钮 / 开启轨 / 开启钮 |
| 强调（复用于开关之外） | `on` `on_knob` | v2.15.0 起 `on` 还承担**选中态强调**：皮肤单选圈、明暗 / 窗口大小分段选中格、标题栏字标、插件状态点的"生效中"；`on_knob` 同时是选中格上的文字色 |
| 图标 | `icon_bg` `icon_fg` | 应用图标与托盘的两色 |
| 浮层 | `menu_base` `pop_base` | 皮肤面板 / 插件设置框（v2.18.0 起齿轮菜单已退役，`menu_base` 只是历史 token 名） |
| 舞台 | `amb0` `amb1` `amb2` `amb3` | 仅设计稿使用，落地忽略 |

**降级约定**：`engine=flat` 的皮肤不读 `glass*` 与 `assets.*`，这几个 token 仍需存在（可填纯色）。

---

## 5. 性能分级（硬限制，不是建议）

| 档 | 位图 | 每行自绘元素 | 定时器 | 圆角 | 行高 | 允许引擎 |
|---|---|---|---|---|---|---|
| `minimal` | 0 张 | 0（全原生控件） | 0 条过渡链 | 0（方角） | ≤ 44 | `flat` |
| `standard` | ≤ 3 张整窗 | ≤ 6 个 | 按需 tween 链 | 任意 | ≤ 56 | `bitmap` / `flat` |
| `rich` | 不限 | 不限 | 不限 | 任意 | 任意 | 暂不开放安装 |

- 「定时器」只数**过渡动画**：玻璃档每个动效键（hover / 开关把手 / 开关轨道 /
  浮层入场）各开一条 16ms `after` 链，完成或同键重开立即回收，空闲 0 条常驻
  过渡链；状态刷新 2s、托盘命令泵 200ms 这类**功能轮询**两档共有，不算过渡。
  极简档 `anim_ms` 恒 0，所有过渡直跳终态。

- 安装时按声明值静态校验；加载时再核对一遍实际行为（例如 `performance=minimal` 却提供 `assets`，直接判不合格）。
- 设置页的皮肤卡片上必须显示成本标签（v1.2.0 起统一为「默认骨架」），不许隐藏——
  用户有权知道一套皮肤贵不贵。
- 内置只有 `plain`（显示名「默认」，唯一内置）。`standard` 只是老包里会出现的
  历史声明：加载不受影响，渲染一律按默认骨架——不换卡片、不换浮层、不开动效。

### 两档的实测口径

| 项目 | 液态玻璃（v2.10.0 前，已退役） | 默认 |
|---|---|---|
| 整窗位图 | **0 张**（原计划 3 张 / 2.6 MB，落地改为 flat + 预合成） | 0 |
| 位图常驻内存 | 0 | 0 |
| 每行自绘元素 | 4 个 canvas item | 0 |
| 过渡定时器 | 按需 16ms tween 链，完成即回收（空闲 0 常驻） | 0（直跳终态） |
| 浮层窗口 | 1 个 Toplevel | 0（行内展开） |
| 圆角处理 | mask 位图 | 方角，免 SetWindowRgn |
| 行高 / 字号 / 图标 | 52 / 15 / 22 | 40 / 13 / 16（v2.10.0 起，原 44） |
| 首帧预算 | ≤ 180 ms | ≤ 120 ms |
| 空闲 CPU 预算 | 0.0 % | 0.0 % |
| 私有内存预算 | ≤ 32 MB | ≤ 26 MB |

**落地修正（v2.7.0）**：液态玻璃没有走位图路线，改成 `engine=flat` + 把半透明色
预合成到舞台色上（`skins.flatten_tokens()`）。理由有两条：

1. tkinter 不支持逐像素透明，位图背景只能靠「截桌面 → 模糊 → 当窗口底」实现，
   每帧或每次移动都要重做，而且窗口一移就穿帮；
2. 位图路线把「换配色」变成「重做三张图」，第三方作者根本做不了。

代价：玻璃在两套皮肤里其实都是不透明色，只是浅色主题下看不出差别、深色主题下
靠 `switch_off` / `hair` 的弱化拉开层次，观感接近磨砂玻璃但不透明。

前六行是设计阶段就能锁死的量，写死在 `skin.json` 里可静态校验；
后三行是运行时预算，落地后用 `design/perf-bench.py`（首帧、每帧重绘）
与 `design/perf-probe.py`（空闲 CPU、浮层成本）复核，结果回填本表。

---

## 6. 五条禁忌

1. **不带代码**：不能执行任何东西，包括字符串 `eval`、`__import__` 拼路径。
2. **不联网**：安装与加载全离线；位图不能是远程 URL。
3. **不碰系统**：不写注册表、不读用户文件、不改窗口属性以外的东西。
4. **不改布局语义**：只改颜色、圆角、字号、行高；不改控件顺序、不隐藏功能、不加按钮。
5. **不超预算**：见第 5 节表格。

违反任一条 → 拒绝加载 → 面板回退 `plain`（显示名「默认」） → `guard.log` 记一行。

---

## 7. 校验（七项，固定顺序）

1. zip 完整性（能被 `zipfile` 打开，无路径穿越条目）
2. 后缀白名单：只允许 `.json .png .md`
3. `skin.json` 可解析、必需字段齐全、类型正确
4. 26 个 token 齐全、颜色格式合法
5. 位图尺寸与体积合规，`assets` 引用的文件真实存在
6. `performance` 档位与实际声明匹配
7. `id` 不与功能插件重名（`ace` / `afterburner` / `gamepp` / `popup` / `desktop_hud`）

```
# 校验（只读，返回 id / name / version / members / total_bytes）
python -c "import sys; sys.path.insert(0,'src'); import skin_package; print(skin_package.inspect('dist/kuan-green.wbskin')['id'])"

# 打包（v2.15.0 起有正式函数，打完自动 inspect 自检并返回 id/version）
python -c "import sys; sys.path.insert(0,'src'); import skin_package; \
r=skin_package.pack('design/themes/kuan-green','release/kuan-green.wbskin'); print(r['id'], r['version'])"

# 安装回归：装进临时目录再 load_dir，确认 catalog 能列出、几何与默认一致
python -c "import sys,tempfile,shutil; sys.path.insert(0,'src'); import skin_package, skins; \
d=tempfile.mkdtemp(); sk=skin_package.install_zip('release/kuan-green.wbskin', d); \
print(sk.id, sk.name, len(sk.tokens('light'))); shutil.rmtree(d, ignore_errors=True)"
```

**产物别放 `dist\`**：那是 PyInstaller 的输出目录，`build_exe.bat` 结尾会
`rmdir /S /Q dist`，打好的皮肤包会跟着一起被删。用 `release\` 或任意仓库外目录。

**`.zip` 与 `.wbskin` 是同一个包**：`.wbskin` 就是普通 zip，这个后缀只做品牌和
文件对话框的默认筛选。v2.15.0 起面板的「从文件安装皮肤…」同时接受 `*.wbskin`
和 `*.zip`，所以作者可以直接：
`copy release\kuan-green.wbskin release\kuan-green.zip` 两个后缀各发一份，
不必让用户改扩展名或切「所有文件」。

校验实现放在 `src/plugin_package.py` 旁边新增的 `skin_package.py`，
与插件包共用 zip 安全读取函数，避免两份路径穿越检查各写一遍。

---

## 8. 落地落点（确认后才动）

| 现在 | 改成 |
|---|---|
| `settings_gui.THEMES`（light / dark 两套 12 键） | `skins.py` 加载器 + 内置两套皮肤，键扩到 25 个 |
| `config.json` 的 `ui_theme`（light / dark） | `ui_skin`（皮肤 id）+ `ui_appearance`（system / light / dark） |
| 齿轮菜单「外观：玻璃白 / 玻璃黑」 | 「外观与皮肤…」打开皮肤页 |
| 硬编码的行高 52 / 字号 15 / 图标 22 | 全部读 `skin.json` 的 `metrics` |

迁移注意：
- `ui_theme` 是旧字段，读不到 `ui_skin` 时按 `ui_theme` 猜默认皮肤，并写回一次新字段，别让老配置变白板。
- 内置皮肤随 exe 打包（`build_exe.bat` 的 datas 里加 `plugins/skins/`），
  与功能插件一样有"新增内置插件要同步四处"的问题，这次只加一处 datas。
- 第三方皮肤装在 `%LOCALAPPDATA%\WBOX\skins\<id>\`，不写注册表，
  卸载 = 删目录 + 若当前正在用则回退 `plain`。

**落地顺序建议**（v1.2.0 起只剩一条路）：骨架只有默认一套，所以先做配色主题
（零位图，最快跑通数据链路），再考虑要不要调 `metrics` 尺寸；位图管线已不存在，
没有"先做位图皮肤"这条分支了。

### 落地实况（v2.7.0，2026-11-17）

| 计划 | 实际 |
|---|---|
| `THEMES` 两套 12 键 | `skins.py`：26 个 token + 6 个接口字段；`THEMES` 保留为兼容壳 |
| `ui_skin` + `ui_appearance` | 已落地；`ui_theme` 仍读，并作为快照一起写回 |
| 菜单「玻璃白 / 玻璃黑」 | 已改为「外观与皮肤…」一行，右侧显示当前皮肤名 |
| 行高 52 / 字号 15 / 图标 22 硬编码 | 全部读 `metrics`；`resize_fonts` / `Switch` / `Glyph` 都从皮肤取 |
| 内置皮肤以 `plugins/skins/` 随包分发 | **改为代码内联**（`skins.BUILTIN`）。内置皮肤不经过包校验、不占 payload、随 exe 版本走；用户皮肤才走 `skins/` 目录 |
| `liquid-glass` 走 `engine=bitmap` | 改为 `engine=flat` + 预合成，0 位图（原因见第 5 节） |
| 极简的全局设置「同窗换页」 | v2.7.0 未做；**v2.8.0 已实现**（见下表和 9-8） |

### 落地实况（v2.8.0，2026-11-17 晚）

| 设计稿要求 | 实际 |
|---|---|
| 标题栏一行装下字标 / 加号 / 窗口控制 | 已实现：`chrome=custom`，`overrideredirect` + 自绘标题栏（玻璃 48 / 极简 40），字标兼菜单入口带下拉箭头，右侧 32 圆加号 + 34×26 窗口控制 |
| 窗口 26 圆角 | 已实现：`SetWindowRgn(CreateRoundRectRgn)`，半径读 `radius.window`；极简档半径 0，一次 API 都不调 |
| 分组列表 = 20 圆角卡片 + 14 圆角行 | 已实现：卡片画在 Canvas 上，行是独立 Canvas，悬停时画 14 圆角底板并同步子控件底色 |
| 行尾单入口（30×30 滑块） | 已实现；极简档换成右箭头 + 行内展开 |
| 极简：行内展开、同窗换页 | 已实现：`pane=list/set` 两页共用窗口，展开块插在行下方，不新建 Toplevel |
| 大写 W 图标（黑白两色） | 已实现：`design/generate_icons.py` 重写，9 个尺寸；≤24 px 去容器只留 W；exe / 安装器 / 面板图标同源 |
| 投影 | **不做**：DWM 只给带 frame 的窗口投影，要拦 WndProc 才能补，收益一个观感、代价整条消息链 |

补记（同日复核）：上面「已实现」是第一次过完的样子，对着截图逐项比对又修了三处，
都记在这里免得以后被当成"设计稿本来就这样"：

- **圆角是采样的，不是样条**。`round_rect` 原用 `smooth=True` 让 Tk 插值，直边被
  样条一起拉弯，卡片 1 px 描边在 150% 缩放下会断成虚线。改成每个角采样 8 段真圆弧、
  `smooth=False`，描边才连续（开关胶囊、行悬停底板、圆角加号共用同一个函数）。
- **极简设置页的行高是写死的 36**，不是靠 `pady` 撑。靠 pady 撑出来只有 27 左右，
  整页比列表页挤一档；分组头固定 24，且**分组头下面不画线**（设计稿 `.p-grp` 没有
  border，每行自带 `border-bottom`）。右侧值改等宽 11 px，和 `.p-line .val` 对齐。
- **设置页里不出现 ＋**：设计稿在 `[data-pane=set]` 把 `.p-add` 一起 `display:none`，
  装插件和改设置不是一件事。

### 落地实况（v2.9.0，2026-11-18）

| 设计稿要求 | 实际 |
|---|---|
| 安装器对齐 `v-setup` 视图 | 已实现：620×540 玻璃窗、idle/busy/done/fail 四态卡片、胶囊按钮、46×28 开关、5px 进度条、失败详情 pane；`test_setup_gui.py` 38 项断言 |
| 面板动效 | 已实现：ease-out 单曲线 + 按需 tween 链；系统「在窗口中显示动画」关 / 极简档 / probe 一律 0ms；`test_ui_motion.py` |
| 托盘与游戏结束通知 | 已实现：图标存在性默认常驻（v2.11.2，面板进程活着就在通知区），随主题换 glyph；消息窗口类按实例命名并在销窗后 UnregisterClassW（v2.11.2，同名类进程全局，同进程再建托盘曾静默死掉）；「启动后收进托盘 / 游戏结束提示」两开关仍 opt-in（默认关），只决定关窗收托盘 vs 退出、气泡前提；托盘左键叫回、右键「打开设置 / 退出」；气泡仅在 active→grace/idle 且窗口收在托盘时发；`test_tray_live.py` 真 Shell_NotifyIcon 实测 |
| 任务栏按钮 | 已修（v2.11.2）：overrideredirect wrapper 在 map 后才被 Tk 创建并带 WS_EX_TOOLWINDOW，init 期的 WS_EX_APPWINDOW 加错对象致按钮永不出现；现 pre-map 清 TOOL+加 APP，`<Map>` 钩子对 wrapper 重补。可见路径 shell 在首次 show 时按 TOOLWINDOW 缓存排除决定，改位后必须 hide/show 刷新一次才出按钮（外部 hide/show 对照截图实证）；刷新会清键盘焦点致菜单 Esc 失效，故刷新前记焦点、刷新后 `focus_force` 回填（`focus_set` 在非前景进程不生效）。`test_taskbar_live.py` 风格位+诱导重置刷新回路+焦点/Esc 断言，按钮视觉存在由发版截图兜底 |
| 皮肤面板超屏 / 高 DPI | 已实现：Canvas 视口滚轮、600 逻辑宽夹进工作区、96–240 dpi 不越界；`test_skin_panel.py` |
| 文案红线与键盘 | 动词按钮 / 默认焦点安全键 / Esc=取消 / 置灰必配解释 / Tab 链与焦点环；`test_ui_copy_keyboard.py` |
| 菜单行数 | `CORE_MENU_ROWS` 11→13（托盘 / 通知两行），`menu_row_count` 单一来源，两个测试写死行数交叉回怼 |
| 长路径胶囊（240dpi） | 已修：按钮先 pack 占右侧、路径 Label 拿剩余宽度按胶囊宽换行；此前长路径把「选择」顶出胶囊 15px（`test_plugin_gui_overflow` 240dpi 段） |
| scrollregion 跟随内容 | 已修：菜单与插件浮层都在 inner/body 的 `<Configure>` 里重设 scrollregion——换行 / 字体度量晚于 place 到达时不再差几像素滚不到底 |
| 分发插件集合 | 已修：`build_exe.bat` 补拷 `desktop_hud`（分发集合 5 个，QA 守卫口径）；旧 OUTDIR 残留曾掩盖新目录丢插件 |

### 落地实况（v2.10.0，2026-11-18）

| 变更 | 实际 |
|---|---|
| 内置皮肤集合 | 只留 `plain` v1.1.0；`liquid-glass` 内置删除，`DEFAULT_ID` / `FALLBACK_ID` 均 `plain`。老配置 `ui_skin=liquid-glass` 走「不存在 → 回退 plain」并留日志，init 时把解析结果写回一次（非只读、非 probe） |
| 极简窗口尺寸 | 基准宽 640 → **480** 逻辑像素（`PLAIN_W`）；standard 档仍 640。行高 44 → 40、标题栏 40 → 36（`metrics`），字号 13 不变 |
| 辅助字号 | 极简档正文 `base-1`、小字 `base-2`（原 -2 / -3），10px 级小字抬到可读下限；standard 档不变 |
| 抗锯齿 | Tk Canvas 无 AA，极简档开关 / 图标改**程序化超采样光栅**：4×4 子采样覆盖率 → 按覆盖率混 ink/bg → `tk.PhotoImage.put` 整幅贴入；覆盖率按几何键缓存，状态 / 配色不变不重算。standard 档保持矢量（每帧 tween 要重画，光栅反而贵） |
| 行内展开点不开 | 已修：行面大部分面积被子控件（图标 / 名称 / 箭头 Canvas·Label）盖住，Tk 事件不穿透 `create_window`；给这三个子控件补 `<ButtonRelease-1>` → `toggle_inline`，开关除外 |
| 用户皮肤目录 | 实际落点 `<安装目录>/skins/<id>/`（与 `config.json` 同级，`skins.skins_dir(BASE)`）；第 8 节计划表里的 `%LOCALAPPDATA%\WBOX\skins` 与 `plugins/skins/` 均未采用 |
| 显示名与系统标识 | v2.11.0 起全部统一 WBOX（窗口 / 快捷方式 / 卸载显示名 / 指南标题 / exe / 安装目录 / 任务 / 注册表键 / 管道 / 互斥体 / `%LOCALAPPDATA%\WBOX`）；旧 Wingman 安装由安装器 `migrate_legacy()` 自动迁移 |
| 自检口径 | `--layout-check` 两遍：解析出的皮肤 + 一枚合成 `layout-legacy`（声明 standard 的老包）各扫一遍 96–240 dpi，确认降级渲染照样不越界 |

**与设计的明示取舍**（不是没做完，是决定不做）：

- 安装器 hover / press 即时换色、不引 tween 链（安装路径保持轻量）；面板才有 160ms 渐变。
- 焦点环画在控件**内**缩 2px（设计稿外扩），避免被行容器裁掉。
- 安装器标题栏带版本号（与面板 / 注册表同源，当前 v2.11.0），排障时对齐。
- 安装器 v2.11.0 起也用 plain 极简语言：480×460 方窗、36×18 超采样开关、6px 圆角 chip 按钮，与面板共用 `src/sprite.py`。
- 「装完立即启动」默认关、「开机自动运行」默认开（设计稿 aria-checked 口径）。
- 安装位置保留可编辑 Entry（设计稿只读路径 + 更改），手输与静默安装同权。
- 托盘 / 通知行为默认关：设计稿托盘开关 aria-checked=true，产品按 opt-in 发，
  不改变老用户「关窗即退出」的肌肉记忆。v2.11.2 起仅图标存在性默认常驻
  （默认看不到任何托盘存在曾被用户当成「没做」），收托盘 / 气泡仍 opt-in。
- 投影不做（v2.8.0 已记，DWM 只给带 frame 窗口投影）。

---

## 9. 派生一套配色主题（以默认皮肤为底）

WBOX 的「主题」= 用户皮肤，不是引擎里的一条分支。所以做一套配色主题的做法固定：
**把默认皮肤拷出来，只换 26 个 token，几何一个字不动。**
成品范例：`design/themes/kuan-green/`（酷安绿，v1.0.0）。

### 9.1 骨架五项不许动

| 字段 | 值 | 为什么 |
|---|---|---|
| `engine` | `flat` | 配色主题不需要位图；要位图就不是配色主题了 |
| `performance` | `minimal` | 保住零位图 / 零动画 / 零浮层，开销与默认完全一致 |
| `chrome` | `custom` | 自绘标题栏，字标与窗口控制跟着皮肤走 |
| `metrics` | `40 / 13 / 16 / 36` | 行高 / 字号 / 图标 / 标题栏 |
| `radius` | `0 / 0 / 0` | 方角 |

这五项必须与 `skins.BUILTIN['plain']` 逐项相同。v1.2.0 起面板里已经没有
`if self.glass` 这类骨架分支了，五项相同是**换配色不会换布局**的静态证据；
`metrics` 改大虽然不换布局，但窗口会跟着长高，自检要重跑。

自检可以一行验完：

```python
plain = [x for x in skins.builtin_skins() if x.id == 'plain'][0]
assert s.metrics == plain.metrics and s.radius == plain.radius
assert s.engine == plain.engine and s.performance == plain.performance
assert s.chrome == plain.chrome
```

### 9.2 只改 26 个 token

`skin.json` 的 `light` / `dark` 各给一套，26 个键一个不少（键名固定，见第 4 节）。
落地时 `skins.flatten_tokens()` 会把 8 位带 alpha 的值合成到窗口底色上，
面板实际画出来的是**不透明色**——所以对比度要拿合成后的值算。

22 个落地 + 4 个舞台占位（`amb0`–`amb3` 只在设计稿里用，落地忽略，但不许缺）。

### 9.3 配色三条硬规则

1. **全屏只有一个彩色**。`on` 是唯一强调色，v2.15.0 起它同时承担开关开态轨道、
   单选圈、分段选中格、标题栏字标、插件状态点的「生效中」。
   不要为「文字用深一点的绿」再加第二个绿——挑一个在两个方向都能用的值
   （酷安绿：白字压 `#0C8040` 是 5.03:1，它当绿字压白底是 4.96:1）。
2. **中性色带主色色相**。正文不用纯黑，用主色的深色衍生（酷安绿是 `#0A2216`）；
   窗口底、发丝线、悬停底同理。纯灰底配彩点，那个绿看起来就是贴上去的。
3. **对比度达标才发**，见 9.4。不达标宁可加深主色，不要靠加大字重糊过去。

### 9.4 对比度门槛（发布前必测）

| 组合 | 最低 | 用途 |
|---|---|---|
| `fg` / 窗口底 | 12 | 正文、行名 |
| `t2` / 窗口底 | 7 | 次级文字 |
| `t3` / 窗口底 | 4.5 | 提示、版本号 |
| `t3` / 悬停底 `fill_2` | 4.5 | 最不利情形（悬停时提示文字在 hover 底上） |
| 白字 / `on`（浅色） | 4.5 | 开关把手、分段选中格文字 |
| 近黑字 / `on`（深色） | 4.5 | 同上（深色主题的 `on` 是亮色，字要压深色） |

测法：先 `flatten_tokens()` 合成再算，别拿设计稿里的半透明值算。酷安绿的实测值
写在它的 `README.md` 里，可直接对表。

### 9.5 命名与版本

- `id`：小写 + 连字符，**必须等于目录名**（`skins\kuan-green\`）。
  `pack()` 会拦下不一致的情况——装完的落点就是 `skins\<id>\`，两者不一致时
  卡片能列出、点下去找不到皮肤。
- `name`：≤ 4 个汉字（外观列表一行放得下）。
- `version`：三段式。只调颜色升 PATCH；加/改 token 语义升 MINOR；换骨架升 MAJOR。

### 9.6 制作流程（六步）

1. 拷 `design/themes/kuan-green/` 整个目录，目录名改成新 `id`。
2. 改 `skin.json`：`id` / `name` / `version` / `description` / `light` / `dark`。
3. 出设计预览：目录里的 `preview.html` 是自包含的（无外链），改 token 后
   用 Edge headless 截图逐段核对；四张面板按真机尺寸（480 宽 / 行 40 / 标题栏 36）。
4. 生成 `preview.png`：256 × 144（16:9），从预览截图里裁面板区域缩下来。
5. 打包：`skin_package.pack()`，打完自动 `inspect()`；命令见第 7 节。
6. 真机看：面板「从文件安装皮肤…」装 `dist/<id>.wbskin`，或手动放
   `<安装目录>\skins\<id>\`。看三样：列表页、设置页、深色。

### 9.7 搭配 v2.15.0 的状态点

插件行的状态点取 `on`（生效中）/ `t4`（已关闭）/ `danger`（运行异常）。
配色主题不用为它单独准备 token——它复用的就是开关开态那一档。
如果某套主题的 `on` 是深色（如默认皮肤的近黑），点会跟着变深，这是对的：
点表达的是「当前主题的强调」，不是「绿色」。

---

## 10. 默认皮肤（plain）的实现要点

它存在的唯一理由是"最不吃性能"，所以下面每一条都是硬约束：

1. **不贴任何位图资源**：窗口底色是 `#FFFFFF`（暗色 `#111111`）一个颜色，包内零 png。
   v2.10.0 起开关 / 图标用程序化超采样光栅（`tk.PhotoImage` 小 sprite，见第 8 节
   v2.10.0 表）：那是抗锯齿手段，不是位图资源——不读文件、不随包分发、按几何键缓存。
2. **不画圆角**：方角，`radius.window/card/row` 全 0，代码里连 `SetWindowRgn` 都不调，
   省掉 mask 位图与圆角处的重绘裁剪。
3. **不做过渡**：悬停只换底色，不做 160 ms 插值；展开收合立刻生效。进程里不许有
   常驻**过渡** `after` 链（状态刷新等功能轮询与过渡无关，两档共有，见第 5 节）。
4. **不开浮层**：插件设置插在行下方（内联展开）；全局设置用同一窗口换页，都不新建 `Toplevel`，少一个窗口句柄与一套 z 序。
5. **控件按需**：标题与行名用 `tk.Label`，开关与图标用 `tk.Canvas` 自绘（v2.8.0 起两档共用同一套
   行构建路径，极简档只是不画圆角底板、不做过渡，行高与字号减半）。
6. **像素更少**：行高 40、字号 13、图标 16（v2.10.0 起），一屏比玻璃版少画约三成。
7. **高度随内容**：窗口高度按行数算，不做滚动容器（滚动要 canvas，反而贵）。
8. **设置入口（同窗换页）**：v2.8.0 已落地。标题栏右侧一枚纯文字 `设置`，点它把同一个窗口
   换成设置页（`App.pane = 'list' | 'set'`），标题栏改显「← 设置」面包屑，设置页分组为
   外观 / 启动与守护 / 内核 / 关于，返回即切回列表。**没有**新增窗口、没有浮层、没有滚动容器：
   窗口高度按内容长（`place()` 读 `winfo_reqheight()`）。

   当初评估的成本（"要拆成 AppBase + GlassView + PlainView"）最后没走拆分路线——
   两档共用的部分（标题栏、行、开关、设置行工厂）本来就只差材质参数，
   所以做成 `self.glass` 分支 + `metrics/radius` 驱动，比拆两个类少一半代码。
   `--layout-check` 与 33 项测试覆盖了这条路径（`test_plugin_gui_overflow` 等）。

它放弃的：玻璃通透感、圆角、弹出动画。这三项是明确取舍，不是没做完。

---

## 11. 版本与兼容

- `api` 只在 token 集合或字段语义发生**不兼容变化**时 +1；加可选字段不升 api。
- 面板遇到 `api` 高于自身支持的皮肤：卡片标「需要更新」，不加载、不报错。
- `performance` 自 v1.2.0 起只是元数据：降档 / 升档都不影响渲染，也不影响成本标签
  （统一显示「默认骨架」），不再算兼容性变化。声明 `standard` 的老包继续加载，
  用户无需迁移。
