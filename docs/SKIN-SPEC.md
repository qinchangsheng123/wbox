# WBOX 皮肤制作规范 v1.0.0

> 适用范围：WBOX（原「僚机 Wingman」）设置面板的外观层。
> 版本：v1.0.0，2026-11-17 定稿；v2.7.0–v2.10.0 已落地（实况见第 8 节）。
> 关联设计稿：`design/wbox-skins.html`（皮肤管理 / 液态玻璃 / 极简 / 成本对照 / 规范五视图）

---

## 0. 一句话

**皮肤是纯数据的 zip 包**，只改颜色、圆角、字号、行高和可选的位图，不含任何可执行代码；
面板加载它只换外观，不动任何一行逻辑。

---

## 1. 为什么皮肤不是"进程插件"

| | 功能插件（ACE / 小飞机 / 弹窗拦截…） | 皮肤 |
|---|---|---|
| 形态 | `plugins/<id>/plugin.py` | `plugins/skins/<id>/skin.json` |
| 运行位置 | 守护进程内，完整 Python 权限 | 面板进程内，只被解析不执行 |
| 能力 | 提权、读进程、改注册表、读写配置 | 改颜色与尺寸，加载 PNG |
| 失败影响 | 可能拖垮守护 | 最多面板变丑，回退内置极简 |

因此皮肤走**独立目录 + 独立校验**：`plugins/skins/` 与 `plugins/` 平级，
功能插件的扫描逻辑不扫这个目录，皮肤的校验不看 `plugin.json`。

**硬规则：皮肤包内出现 `.py .pyc .dll .exe .bat .ps1 .cmd` 任一后缀，安装器直接拒绝，
并写 `guard.log` 一行 `skin.reject reason=executable`。**

---

## 2. 包结构

```
liquid-glass.wbskin          # 其实就是 zip，后缀换成 .wbskin
├─ skin.json                 # 必需，唯一入口
├─ preview.png               # 必需，256 × 144，卡片缩略图
├─ README.md                 # 可选，作者与说明
└─ assets/                   # 可选，只允许 png
   ├─ backdrop.png
   ├─ mask.png
   └─ shadow.png
```

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
| `engine` | string | `bitmap`（有位图）或 `flat`（纯色） |
| `performance` | string | `minimal` 或 `standard`，见第 5 节 |
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
| `assets` | object | `backdrop` / `mask` / `shadow` 三个键，值为包内相对路径 |
| `description` | string | 一句话，显示在卡片副标题 |

### 3.3 完整样例（液态玻璃，第三方皮肤参照）

v2.10.0 起液态玻璃**不再内置**（产品决定：先打磨极简），本样例保留作第三方
`standard` 档皮肤的字段参照；standard 渲染骨架仍在面板里，装包校验通过后即可用。

```json
{
  "id": "liquid-glass",
  "name": "液态玻璃",
  "version": "1.0.0",
  "api": 1,
  "engine": "bitmap",
  "performance": "standard",
  "chrome": "custom",
  "author": "WBOX",
  "description": "半透明 + 背景模糊 + 圆角阴影",
  "metrics": { "row": 52, "font": 15, "icon": 22, "titlebar": 48 },
  "radius": { "window": 26, "card": 20, "row": 14 },
  "assets": {
    "backdrop": "assets/backdrop.png",
    "mask": "assets/mask.png",
    "shadow": "assets/shadow.png"
  },
  "light": {
    "fg": "#0A0A0A", "t2": "#949494", "t3": "#B0B0B0", "t4": "#C8C8C8",
    "hair": "#16161616", "hair_strong": "#24242424",
    "fill": "#0B0B0B0B", "fill_2": "#13131313", "press": "#1A1A1A1A",
    "inset": "#FFFFFF6B", "glass": "#FFFFFF8A", "glass_hi": "#FFFFFFEB",
    "glass_line": "#FFFFFF8C", "glass_edge": "#00000012",
    "switch_off": "#7878804D", "knob": "#FFFFFF", "on": "#000000", "on_knob": "#FFFFFF",
    "icon_bg": "#000000", "icon_fg": "#FFFFFF",
    "menu_base": "#FCFCFDB8", "pop_base": "#FCFCFDF7",
    "amb0": "#E7E7EC", "amb1": "#FFFFFF", "amb2": "#C4C4CF", "amb3": "#FBFBFD"
  },
  "dark": { "...": "同结构，26 个键" }
}
```

颜色写法：`#RRGGBB` 或 `#RRGGBBAA`（8 位带 alpha）。tkinter 不认 8 位十六进制，
落地时由 `skins.py` 转成 `#RRGGBB` + 独立 alpha，合成到窗口底色上。

---

## 4. 26 个颜色 token

名字固定、语义固定，皮肤只能改值。缺任意一个 → 拒绝加载，回退内置极简皮肤。
（初稿写 25 个是数漏了 `glass_edge`；实现以 `skins.TOKENS` 为准，共 26 个。）

| 组 | token | 用途 |
|---|---|---|
| 文本四级 | `fg` `t2` `t3` `t4` | 主文字 / 次要 / 提示 / 禁用 |
| 线与填充 | `hair` `hair_strong` | 1 px 分隔线 / 边框 |
| | `fill` `fill_2` `press` | 悬停底 / 按压底 / 选中底 |
| 卡片与窗口 | `inset` | 分组列表的内凹底 |
| | `glass` `glass_hi` `glass_line` `glass_edge` | 窗口底 / 顶部高光 / 内描边 / 外描边 |
| 开关 | `switch_off` `knob` `on` `on_knob` | 关闭轨 / 关闭钮 / 开启轨 / 开启钮 |
| 图标 | `icon_bg` `icon_fg` | 应用图标与托盘的两色 |
| 浮层 | `menu_base` `pop_base` | 齿轮菜单 / 行设置浮层 |
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
- 设置页的皮肤卡片上必须显示这张标签：`标准` / `最省`，不许隐藏。
- 目前内置只发一档：`plain`（minimal，v2.10.0 起唯一内置）。`standard` 档渲染
  骨架保留，第三方皮肤可声明使用（液态玻璃样例见 3.3）。

### 两档的实测口径

| 项目 | 液态玻璃 | 极简 |
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

违反任一条 → 拒绝加载 → 面板回退 `plain` → `guard.log` 记一行。

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
# 校验
python -c "import sys; sys.path.insert(0, 'src'); import skin_package; \nprint(skin_package.inspect(r'dist\mytheme.wbskin')['id'])"

# 打包：按第 3 节结构把 skin.json 与 assets 写进 zip（.wbskin 就是普通 zip），
# 打完务必用 inspect() 自检一遍；没有独立 CLI
```

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

**落地顺序建议**：先做 `plain`（原生控件、零位图，最快跑通数据链路），
再把现有的液态玻璃搬进 `plugins/skins/liquid-glass/`。反过来做会把位图管线先钉死，
之后想简化就得拆一遍。

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
| 自检口径 | `--layout-check` 两遍：解析出的皮肤 + 合成 `layout-std`（standard）各扫一遍 96–240 dpi，内置单档后 standard 路径仍被覆盖 |

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

## 9. 极简皮肤（plain）的实现要点

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

## 10. 版本与兼容

- `api` 只在 token 集合或字段语义发生**不兼容变化**时 +1；加可选字段不升 api。
- 面板遇到 `api` 高于自身支持的皮肤：卡片标「需要更新」，不加载、不报错。
- `performance` 降档不算破坏兼容；升档（`minimal` → `standard`）在同一 api 内允许，但用户会看到标签变化。
