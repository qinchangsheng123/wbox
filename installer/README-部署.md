# WBOX 部署说明

> v2.10.0 起产品显示名统一为 **WBOX**（窗口标题、快捷方式、卸载显示名、指南标题）。
> v2.11.0 起系统标识统一 `WBOX`：exe 文件名 `WBOX.exe`、默认安装目录
> `C:\Program Files\WBOX`、计划任务名、注册表卸载键、管道/互斥体名、
> `%LOCALAPPDATA%\WBOX` 备份目录。旧 Wingman 安装在升级安装时自动迁移
> （停旧名守护、搬移目录、合并备份目录、删旧任务与旧注册表键）。

一个 exe 守三个功能（面板名称）：**小飞机关联启动**（游戏启动时拉起微星小飞机、退出后关掉）、
**ACE 防护**（游戏期间限速、退出后按四道闸门清理残留）、**弹窗关闭**（清掉退出后的
直播弹窗与后台进程）。ACE 清理已内建，**不再需要 PowerShell 脚本和 SYSTEM 计划任务**。

## 设置面板（2026-11-09 改版）

- 主页面**只有三行功能名称 + 开关**，没有编号、没有详情小字、没有状态行。
- 鼠标停在名称上 0.3 秒弹出该功能的**简介**；原来的「运行时限速 + 退出后清理」等
  详情全部收进简介里。
- 拨动开关**即时写回 `config.json`**，守护 2 秒内热加载，不用再点保存。
- 主页面右上角是**齿轮图标**（无边框，悬停才出底色）。点它展开**浏览器式弹出菜单**：
  贴齿轮右下方浮出、1px 边框、无标题栏，每行「小图标 + 名称 + 右侧状态/开关/勾选/Esc」，
  分组只用 1px 分隔线，悬停整行高亮；点击菜单外任意位置（含其它程序窗口）、按 Esc，
  或再点一次齿轮都会收起。11 个项目**各占一行、依次排列**，没有并排按钮。
- 菜单内容：守护状态、启动/停止守护、开机自动启动守护、重扫游戏库、查看运行日志、
  编辑配置文件、纯白、纯黑、保存设置、关闭，底部一行版本小字。切主题和拨开机自启
  开关时菜单保持打开；动作项点击后自动收起再执行。
- 浮层是 `overrideredirect` + `topmost` 的 Toplevel（**不能用 transient**，Windows 会把
  无标题栏浮层压到主窗口后面）；下方空间不足改向上弹，超高时 Canvas 滚轮承接。
- 纯白 `#FFFFFF` / 纯黑 `#000000` 两套主题，配 `icon-light.ico` / `icon-dark.ico`；
  主题记在 `ui_theme`。设计稿见 `design/ui-prototype.html`，实现见 `src/settings_gui.py`。
- 主程序与安装程序两个 exe 都内嵌同一套 7 尺寸设计图标；窗口另外显式设置
  `ICON_SMALL` / `ICON_BIG`（`WM_SETICON`），所以任务管理器「进程」列表、任务栏预览、
  Alt+Tab 都取设计图标，不会回退成 Tk 默认羽毛图标。`--self-check` 会输出
  `window_icons=small:True,big:True`。安装器装完调用
  `SHChangeNotify(SHCNE_ASSOCCHANGED)` 刷新 Shell 图标缓存，覆盖安装同路径时
  任务管理器不会继续显示上一版图标。
- 设计稿已升 **v2.2.0**（三段式版本号）：新增齿轮 symbol 与 11 枚菜单线性图标，UI 视图改成
  「主窗口 + 贴齿轮展开的浮层」叠加预览；`design/preview-ui-light.png` /
  `preview-ui-dark.png` 为设计稿，`preview-real-settings.png` 为实机截图。
- 安装程序 UI 自 v2.9.0 已落地（安装向导 + 完成态，与设置面板同令牌、同图标、
  同自绘开关），效果见 `design/ui-prototype.html` 的「安装程序 v2」页签；
  `/S /DIR=`、UAC 提权、配置合并与卸载逻辑保持不变。
- v2.10.0 起设置面板走皮肤系统：内置仅「极简」一档（方角纯色、零位图零动画、
  480 逻辑像素宽），液态玻璃不再内置；第三方皮肤经 `.wbskin` 包校验后安装，
  规范见 `docs/SKIN-SPEC.md`。下文 2026-11-09 的齿轮菜单/纯白纯黑描述为历史版本行为。

## 弹窗关闭：直播弹窗拦截

DemoGame退出后会弹一个斗鱼直播推荐窗。取证结果：宿主是游戏目录下的

```
<游戏盘>\WeGameApps\DemoGame\Binaries\Win64\clientbrowser2\ClientBrowser.exe
命令行：clientbrowser2\ClientBrowser.exe https://<活动页域名>/cp/a2021xxxxlivetv/index.html ...
```

游戏启动那一刻就把它拉起来并加载直播页，窗口先隐藏，退出后 `ShowWindow` 出来。
类名 `#32770`、无标题，靠窗口标题抓不到，所以按 **exe 名 + 路径片段** 定位。

取证发现窗口比「游戏进程全部消失」早约 7 秒就显示，只等退出才动手会让人盯着弹窗挂好几秒。
2026-11-09 起改成两段（配置都在 `popup_guard` 段）：

| 阶段 | 动作 | 延迟 |
|---|---|---|
| 游戏会话中 | 窗口一露面就发 `WM_CLOSE`（`early_close_enabled`，最多试 `early_max_tries` 轮） | ≤ 0.4 s |
| 游戏全退 | 结束宿主进程及其整个 CEF 子进程树 | ≤ 0.4 s |
| 退出后 90 秒 | 新冒头的窗口继续拦（`watch_sec`） | ≤ 0.4 s |

守护采用双速轮询：空闲 2 s（`poll_interval_sec`），游戏会话和弹窗监视期 0.4 s
（`poll_fast_sec`）；`delay_sec` / `scan_interval_sec` 默认 0，扫描跟随主循环不再叠节流。
实测每轮窗口枚举 + 进程快照共约 2 ms，0.4 s 节拍只占单核约 0.5%。

安全边界：会话中**只关窗口、不结束进程**（内嵌页面还要用），进程树仍只在游戏全退后清；
`wegame.exe` / `tcls_core.exe` / `tgame.exe` 等常驻主干在保护名单；系统进程
（`conhost` / `csrss` 等）即使出现在进程树里也不碰。若 `WM_CLOSE` 对某个宿主无效，
日志会留「WM_CLOSE 连发 … 窗口仍在」，此时把 `early_close_enabled` 关掉即可退回旧行为。

## 文件

| 文件 | 用途 |
|---|---|
| `Setup_WBOX.exe` | 安装程序（自带程序本体，约 5.7 MB） |
| `payload.zip` | 安装包内嵌的程序文件，约 1.5 MB / 24 个文件（共享运行时 68 项安装时从主程序释放），供重新打包用 |
| `icon-light.ico` / `icon-dark.ico` | exe 内嵌资源 + 窗口大小图标（任务管理器、任务栏预览、Alt+Tab、快捷方式），切换主题时同步换色 |

2026-11-09 把交付包从 14.05 MB 压到 8.57 MB（-39.0%），部署目录从 9.1 MB 到 6.6 MB：
安装器与主程序都启用 `src/tools/upx.exe`，排除用不到的 TLS / LZMA / Unicode 数据库模块
（`libcrypto-1_1.dll`、`libssl-1_1.dll`、`_ssl.pyd`、`unicodedata.pyd` 等），并按
`abg.spec` / `setup_abg.spec` 同一套规则裁掉 Tcl 的 tzdata(606 文件)、多语言消息、
demos、冗余东亚编码表。`build_exe.bat` 现在每次构建先清旧产物，避免 spec 里删掉的文件
残留在 `app\` 又被打进 payload。程序只用本地文件/进程/窗口 API，不联网、不解压，
这些裁剪已用 `--self-test` / `--self-check` / `--layout-check` 和隔离安装验证。

## 体积与分发审计（v2.11.1，2026-11-18）

**程序文件与运行数据的边界**。payload.zip 24 个条目逐条分类：程序本体
（`WBOX.exe`、`base_library.zip`、`_queue.pyd`、两个图标）、五个内置插件 13 个文件、
docs 四件套、共享运行时清单、出厂配置模板。**不含任何运行期状态**：`guard.log*`、
`guard.pid`、`games_cache.json`、`ace_state.json`、`plugins_state.json`、`stop.flag`、
`plugins_reload.flag`、`config.json.bak*` 都在 `build_setup.py` 的 SKIP_NAMES /
前缀过滤里，QA 出包守卫另有负向断言永久设防。

**修掉的实际混入（v2.11.0 及以前）**：`build_exe.bat` 曾把生产 `app\config.json`
拷进构建目录当"模板"，实测 payload 配置与生产逐字节相同 —— 用户的小飞机路径、
游戏库根、个人开关随每个版本包重复分发。v2.11.1 起模板由
`installer\factory_config.py` 生成（内核 DEFAULTS → 内置插件 DEFAULT_CONFIG，
即运行期三段合并的前两段，2,871 B、键序可复现）；`build_exe.bat` 与
`build_setup.py` 都调它，后者即使 `ABG_APP` 指向生产目录也强制用出厂件替换
payload 里的 config.json。安装时旧机器配置仍逐键保留，模板只补新增项。

**v2.11.2 补记（注释键回归）**：`merge_config` 约定「`_` 开头的说明性注释始终
用包内最新文案」，但 v2.11.1 的出厂模板没有注释键，部署合并把生产配置里 7 个
注释键删光（34→27 键）。v2.11.2 起 `factory_config.COMMENTS` 是注释键单一来源
（文案更新到 WBOX 当前界面口径，插在其说明的段前），模板 5,908 B / 29 键；
生产配置已恢复 34 键、用户值逐键不变。教训：模板「只补新增项」的合并语义下，
模板缺键 = 删键。v2.11.2 在 QA 出包守卫加正向断言「出厂模板含全部注释键」
（`test_qa_build_setup_guard.py`，42 项），生产侧另有
`test_qa_config_compat.py` 的注释键保留断言兜底。

**传输体积 vs 磁盘占用**。Setup 单文件约 5.7 MB 是传输体积；装完目录约 6.1 MB
是磁盘占用，两者口径不同。CArchive 实测构成（压缩后）：payload 1.55 MB、
python38.dll 1.40 MB、PYZ 0.79 MB、tcl86t/tk86t 1.16 MB、base_library 0.31 MB、
Tcl 保留数据与 pyd 约 0.5 MB。压缩杠杆实测：payload 早已 zlib level 9（再提增益
0 B）；CArchive 默认 level 6，离线模拟 6→9 全条目仅省 5,788 B（0.10%），而
PyInstaller 5.13.2 没有公开压缩级参数（`_COMPRESSION_LEVEL` 是私有类属性），
不为 0.1% 碰私有 API；LZMA 不被该版 CArchive 支持，7z SFX 会换掉 PyInstaller
引导并引第三方依赖，均否决。exe/dll 已 UPX。**结论：传输体积已在该格式底部，
维持现状**；要再降只能换分发格式（违背单文件离线交付约束）。

**Tk/Python 运行时不再硬裁剪**。Tcl/Tk 数据已裁到 62/920（tzdata 606、多语言
143、冗余编码 75 等），保留编码仅 utf-8/ascii/iso8859-1/cp936（cp936 供 mbcs
解码）。依赖审计：src + 五个插件共 38 个顶层 import 模块，与 spec 的 31 项
EXCLUDES **零交集**；兼容验证链 = 40 项单进程回归 + 冻结 exe `--self-check`
（插件真加载）+ `--layout-check` 两档 × 六档 dpi + 隔离安装测试 + 共享运行时
68 项逐字节比对 + 生产守护五插件常驻。再裁 ttk 主题 / 编码表会直接碰面板渲染
与中文解码，收益百 KB 级、风险启动级，**否决**。

**desktop_hud 维持随包内置**。payload 实测 79,221 B raw / 23,788 B zip，占传输
体积 0.40%。按需分发候选方案（安装器勾选 + 独立可选 zip、面板首启下载）均否决：
产品约束离线不联网，首启下载不可行；双资产包让更新矩阵与校验翻倍；0.4% 的体积
换首启摩擦与更新复杂度不值。v2.11.0 已把它补进 `BUILTIN_PLUGINS`（此前被误分类
为用户插件），升级覆盖与卸载备份口径随之正确。

## 安装（图形）

双击 `Setup_WBOX.exe`，弹出 UAC 点是。可选安装目录、是否开机自启、装完是否立即启动。

默认装到 `C:\Program Files\WBOX`，并做这些事：

1. 解包程序到目标目录；已有 `config.json` 会**逐键保留用户改动**，模板只补缺失项
   （含功能段内新增的参数；`_` 开头的说明性注释用包内最新文案）
2. 自动找 MSI Afterburner（卸载项 → 常见目录 → 运行中进程），写进 `afterburner_path`
3. 自动找 Steam / WeGame 游戏库根，写进 `game_roots`
4. 建登录自启计划任务 `WBOX`（最高权限，`--guard` 常驻）
5. 开始菜单快捷方式（`WBOX` 目录）：`WBOX 设置`、`查看运行日志`、`插件开发指南`；
   桌面 `WBOX 设置`。覆盖升级时清掉旧名入口（`僚机 Wingman 设置.lnk`、开始菜单 `Wingman` 目录）
6. 写卸载项（控制面板/设置里可卸载）
7. 刷新 Shell 图标缓存，任务管理器立即显示设计图标
8. 按需启动守护

## 安装（静默 / 批量部署）

```bat
Setup_WBOX.exe /S /DIR="D:\Tools\WBOX" /AUTO=1 /LAUNCH=1
```

| 参数 | 说明 |
|---|---|
| `/S` | 静默，无界面 |
| `/DIR=路径` | 安装目录，默认 `C:\Program Files\WBOX` |
| `/AUTO=1\|0` | 是否建开机自启计划任务（默认 1） |
| `/LAUNCH=1\|0` | 装完是否立即启动守护（默认 0） |
| `/TASK=名称` | 覆盖计划任务名（默认 `WBOX`，多套并存时用） |
| `/NOTASK` | 完全不碰计划任务（调试用） |
| `/uninstall` | 卸载；配合 `/S` 静默，`/DIR`、`/TASK` 要跟安装时一致 |

日志：`%TEMP%\wbox_setup.log`。

## 卸载

设置 → 应用 → `WBOX` → 卸载；或（v2.6.0 起卸载入口并进主 exe，
安装目录里不再有 `Uninstall_Wingman.exe` 副本）

```bat
"C:\Program Files\WBOX\WBOX.exe" /S /uninstall
```

批量或远程部署时，用安装包本体卸载更可靠（不依赖分离子进程）：

```bat
Setup_WBOX.exe /S /uninstall /DIR="C:\Program Files\WBOX" /TASK=WBOX
```

`/DIR`、`/TASK` 必须和安装时一致；漏掉 `/TASK` 会按默认名删任务，多套并存时容易误删。
会停守护、删计划任务、删快捷方式、删注册表项、删目录。

## 装完要检查的

1. 打开 `WBOX 设置`，主页面三行名称与开关是否符合预期；
   名称悬停能看到简介，右上角**齿轮图标**能展开浏览器式弹出菜单（11 项逐行），
   点菜单外或按 Esc 能收起；三个开关拨动后即时生效。
   任务管理器「进程」列表里 `WBOX` 的图标应为盾形上箭头，而不是通用 exe 图标。
2. 小飞机不在常见位置时：安装程序按卸载项、常见目录、运行中进程探测；探测不到就清空
   `afterburner_path`，不再留别的机器上的死路径，守护运行时会再兜底探测并在日志里明确报错。
   此时点「编辑配置文件」填实际路径。
3. 启动一个游戏，看 `guard.log` 里有没有「检测到游戏」和「启动小飞机」。
4. 腾讯游戏跑一局，看退出后是否出现 `ACE 判定 ...`；正常会因保护期或静默窗口而
   NOACE/YOUNG，属预期。ACE 进程取不到创建时间时一律判 YOUNG（宁可漏清不误清），
   日志会写明原因。
5. 跑一局DemoGame并退出：弹窗应在几乎看不见的情况下消失。日志里出现「提前拦截弹窗」
   表示会话期窗口拦截生效；若只有「WM_CLOSE 连发 … 窗口仍在」，说明该宿主不响应
   WM_CLOSE，把情况反馈后再调整（现有进程树清理仍会在退出瞬间兜底）。

## 配置与判定要点

- 三个功能的默认值以 `guard_core.DEFAULTS` 为唯一来源；`config.json` 缺 `ace_throttle` 段时，
  守护、设置面板、自检都按默认开启，不会出现「面板显示开、守护实际关」。
- 设置面板（开关即时保存或齿轮菜单的「保存设置」）按默认值补齐整个功能段，
  不会只写一个 `enabled` 把其它参数抹掉；`ui_theme` 只影响界面，守护忽略它。
- 「重扫游戏库」强制扫描；改了平台开关或 `game_roots` 也会立即重扫，不再等 6 小时缓存。
- 弹窗宿主路径按反斜杠归一化匹配；腾讯改 `clientbrowser2` 目录名时改 `popup_guard.path_hints`。
- 轮询双速：`poll_interval_sec=2`（空闲）/ `poll_fast_sec=0.4`（游戏会话与弹窗监视期）。
  想更快可把 `poll_fast_sec` 调到 0.2（代码下限），代价是常驻开销略增；想省电把它设成
  和 `poll_interval_sec` 一样即退回单速。

## 已知边界

- 纯黑主题覆盖窗口内容区；Windows 系统标题栏由 DWM 绘制。本机 Win10 19045 实测：
  单独开一个最小 Tk 窗口调用 `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE)`
  返回成功（hr=0），标题栏仍是系统白条，非本程序逻辑问题。程序已按主题调用该 API，
  在支持它的系统上会自动变深；本机这圈白边属于系统限制，未用自绘标题栏去换掉系统
  窗口的拖动、贴边和任务栏行为。
- 守护与安装程序都要求管理员权限（小飞机 manifest 要管理员、ACE 服务控制也要）。
- 同一台机器只跑一个守护（命名互斥体）；多套安装会互相顶掉，除非用不同计划任务名且只启一个。
- 单机没有 ACE 环境时，清理逻辑不会动作，只看得到判定日志。
- 「从安装目录里点卸载」靠一个搬到 `%TEMP%` 的分离副本执行（父 exe 正在运行、占着自身文件，必须先退出）。
  在会强制清理后代进程的受限会话（自动化沙箱、部分企业 Job 管控）里，副本可能来不及启动，表现为点卸载后
  目录/任务/注册表都还在、日志里也没有 `=== 卸载 ===`（父进程会留一行「卸载副本 …已启动」便于区分）。
  此时改用上面的安装包本体卸载命令；副本若已搬到 `%TEMP%`，也可直接跑
  `%TEMP%\abg_uninstall.exe /uninstall /S /DIR=... /TASK=... /FROMTEMP=1`。
