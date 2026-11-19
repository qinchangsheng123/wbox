# WBOX

Windows 游戏伴侣守护：开游戏自动拉起监控 / 超频软件，压住反作弊组件的资源占用，
关掉游戏退出后弹出的广告窗口，桌面上常驻一块硬件占用悬浮窗。
常驻托盘，只做进程调度与系统指标读取，**不注入、不改任何游戏或反作弊文件**。

当前版本 **v2.13.0**。原「僚机 Wingman」自 v2.10.0 起更名 WBOX，旧安装由安装器自动迁移。

## 架构：内核 + 插件

WBOX 本体是一个小内核，功能全部由插件提供：

- **会话状态机**：`idle → active → grace → idle`。检测到游戏进程开局，全部退出后
  进入宽限期再收尾。进程检测默认走 WMI 事件驱动（启动/退出即时响应），轮询只做
  低频兜底，空闲 CPU 约 0.05%。
- **插件宿主**：每个插件声明自己的开关、设置项、权限；单插件故障自动隔离
  （降级 + 降频），坏插件不拖垮守护；v2.13.0 起钩子超时还会被监控和隔离（见下）。
- **守护 ↔ 面板心跳**：命名管道实时上报 pid / 版本 / 会话 / 插件状态 / 事件通道健康度，
  面板不靠猜。
- **主程序不带任何插件**（v2.11.3 起）：装好是一个空面板，五个官方插件在
  [wbox-plugins](https://github.com/qinchangsheng123/wbox-plugins) 仓库单独下载、
  单独发版，面板里「＋」一键安装、2 秒内热重载。

## 功能（官方插件）

| 插件 | 版本 | 干什么 | 下载 |
|---|---|---|---|
| `afterburner` 小飞机联动 | 2.2.0 | 开游戏自动拉起 MSI Afterburner / RTSS，退出后只关本局新起的；可设关闭倒计时 | [zip][r-ab] |
| `gamepp` 游戏加加联动 | 1.2.0 | 开游戏拉起游戏加加，退出后只关本局新起的；路径自动探测；可设关闭倒计时 | [zip][r-gp] |
| `ace` ACE 防护 | 2.2.0 | 游戏期间压低反作弊进程 CPU / 磁盘占用，退出后按四道闸门清残留；判定留决策记录 | [zip][r-ace] |
| `popup` 弹窗关闭 | 2.1.0 | 会话中提前关 ClientBrowser 直播 / 广告弹窗，退出后清宿主进程树；观察模式只记录不动手 | [zip][r-pop] |
| `desktop_hud` 桌面监控 | 1.6.1 | 桌面半透明悬浮窗：CPU / GPU / 显存 / 内存占用与频率，游戏中自动隐藏 | [zip][r-hud] |

每个插件的详细说明（工作原理、全部设置项、安全边界、排查方法）见
[插件仓库 README](https://github.com/qinchangsheng123/wbox-plugins#插件详解)。

[r-ab]: https://github.com/qinchangsheng123/wbox-plugins/releases/tag/afterburner-v2.2.0
[r-gp]: https://github.com/qinchangsheng123/wbox-plugins/releases/tag/gamepp-v1.2.0
[r-ace]: https://github.com/qinchangsheng123/wbox-plugins/releases/tag/ace-v2.2.0
[r-pop]: https://github.com/qinchangsheng123/wbox-plugins/releases/tag/popup-v2.1.0
[r-hud]: https://github.com/qinchangsheng123/wbox-plugins/releases/tag/desktop_hud-v1.6.1

## 可靠性设计（v2.13.0）

- **进程身份校验**：关任何进程前用「pid + 创建时刻」双确认。Windows 会复用退出
  进程的 pid，只按 pid 关会误伤用户后来自己开的同名程序；拿不到创建时间的一律
  不动（宁可不关，不能关错）。小飞机 / 游戏加加 / ACE / 弹窗四条链路同一口径。
- **钩子耗时监控 + 卡死隔离**：每个插件钩子计时，超 5 秒记警告，连续 3 次慢钩子
  把该插件降级隔离（面板可见、可一键恢复）；插件可用 `ctx.spawn()` 把耗时操作
  丢到异步线程，不阻塞守护主循环。
- **配置统一校验**：声明式 schema，非法值自动修正或回退默认，每次修正留痕到
  `config_issues.json`；面板与守护用同一套规则，不会「面板显示开、守护实际关」。
- **配置迁移可见可回滚**：升级合并配置前自动备份（`config.json.bak*`），迁移写日志。
- **安装事务**：安装 / 升级 / 卸载中断后可恢复（事务状态在 `%ProgramData%\WBOX`），
  不留半装状态；插件安装同样有事务日志 + 启动自愈。
- **结构化事件流 + 导出诊断**：关键动作写 `events.jsonl`（JSONL、1 MB 轮转、
  写入永不抛错）；齿轮菜单「导出诊断」或 `WBOX.exe --diag` 一键打包
  日志 / 事件 / 配置 / 插件状态成 zip，报问题直接甩这个包。

## 安装

1. 从 [Releases](https://github.com/qinchangsheng123/wbox/releases/latest) 下载
   `Setup_WBOX_v<版本>.exe`（旁边有 `.sha256` 侧车，`sha256sum -c` 可校验）；
2. 双击安装，默认装到 `C:\Program Files\WBOX`；
3. 首次运行请求管理员权限（改进程优先级、注册计划任务需要）。

覆盖升级会**逐键保留你的配置**、原样保留已装插件；卸载会把插件备份到
`%LOCALAPPDATA%\WBOX\removed-plugins-<时间戳>\`，不硬删。

静默 / 批量部署：

```
Setup_WBOX.exe /S /DIR="D:\Tools\WBOX" /AUTO=1 /LAUNCH=1
```

| 参数 | 说明 |
|---|---|
| `/S` | 静默，无界面 |
| `/DIR=路径` | 安装目录（默认 `C:\Program Files\WBOX`） |
| `/AUTO=1\|0` | 开机自启（登录时计划任务，默认 1） |
| `/LAUNCH=1\|0` | 装完立即启动守护（默认 0） |
| `/TASK=名称` | 覆盖计划任务名（默认 `WBOX`，多套并存时用） |
| `/NOTASK` | 完全不碰计划任务（调试用） |
| `/uninstall` | 卸载；`/DIR`、`/TASK` 要跟安装时一致 |

装完安装目录里带完整开发资料：`docs\PLUGIN-SPEC.md`（插件规范）、
`docs\插件开发指南.html`（自包含单页，离线双击可读）、`docs\plugin-template\`
（改名即用的最小插件）。

## 使用

面板内置「极简」皮肤（方角纯色、480 逻辑像素宽），明暗跟随系统，窗口大小三档。
左上角字标 WBOX 可拖动窗口；极简档右上角「设置」进设置页，玻璃档字标即菜单入口。
开启「启动后收进托盘」后关窗收进托盘（图标自愈：explorer 重启 / 首挂失败都会自动补挂），
托盘左键叫回窗口、右键「打开设置 / 退出」。

- 主页每行一个插件功能开关，改动即时写盘、守护 2 秒内热加载；
- 行尾滑块打开设置浮层：插件自己的设置项、版本 / 发布者 / 权限、「卸载此插件」；
- 齿轮菜单：守护状态 / 启停、开机自启、收进托盘、游戏结束提示、重扫游戏库、
  查看日志、**导出诊断**、编辑配置文件、外观与皮肤、浏览插件库、插件开发指南；
- 「已安装插件」分组显示版本与状态（正常 / 运行异常 N 次 / 加载失败 /
  需要更新的内核 / ID 冲突；钩子卡顿降级也计入「运行异常」）。

日志与运行数据都在安装目录：

| 文件 | 内容 |
|---|---|
| `guard.log` | 逐行运行日志（时间戳），超上限自动轮转 |
| `events.jsonl` | 结构化事件流（配置加载 / 会话 / 插件动作 / ACE 判定…） |
| `config.json` | 用户配置（升级逐键保留） |
| `config_issues.json` | 最近一次配置校验的修正记录（没有问题就没有这个文件） |
| `plugins_state.json` | 各插件落盘状态（如弹窗最近动作 `popup_recent`） |

装到 `Program Files` 时普通权限写不了配置，程序会自动提权重开一次；
拒绝 UAC 时界面整体置灰并说明原因，不会崩。

## 插件

> **插件仓库：<https://github.com/qinchangsheng123/wbox-plugins>**
> **下载页：<https://github.com/qinchangsheng123/wbox-plugins/releases>**（每个插件一个 Release，带 `.sha256`）

- **加插件**：下载 `<id>-v<版本>.zip`（不用解压），点标题栏「＋」选中它。
  安装前弹来源确认：发布者、声明权限翻成人话、文件数、整包 SHA-256；
  校验通过后自动生效（游戏会话中会等本局结束，不打断限速 / 弹窗监视）。
- **删插件**：设置浮层底部「卸载此插件」，目录移进备份区，面板那一行随之消失。
- 升级安装原样保留已装插件；覆盖安装旧版插件先备份到
  `%LOCALAPPDATA%\WBOX\replaced-plugins-<时间戳>\`。

### 插件包规范 `wbox-plugin-zip-v1`

zip 分发，顶层目录名必须等于 `manifest.id`：

```
<id>-vX.Y.Z.zip
  └─ <id>/plugin.json       必填：id / name / version / api（≤ 1）
     <id>/plugin.py         默认入口，可用 manifest.entry 改
     <id>/...               其它源码 / 说明 / 小图标
```

- 允许 `.py .json .md .txt .cfg .ini .yaml .yml .csv .xml .png .ico`；
  可执行文件（`.exe/.dll/.pyd/.bat/.cmd/.ps1/.vbs/.lnk` 等）一律拒绝；
- 拒绝多顶层目录、路径穿越、绝对路径、符号链接、加密条目、zip bomb
  （文件数 ≤ 64、单文件 ≤ 1 MB、解压总量 ≤ 2 MB、包 ≤ 8 MB）；
- 先只读 `inspect_zip()`，落盘暂存后再走一遍宿主 `parse_manifest()`，
  任何失败不留半成品；`.sha256` 侧车存在且不一致时拒绝安装。

完整规范见 `docs/PLUGIN-SPEC.md`（含钩子耗时约定 §5.6、事件流 §9.6）；
打包工具在插件仓库 `tools\pack.py`。

**信任边界**：插件是在守护进程（管理员权限）里运行的 Python 代码，装进来就等于
完全信任作者。包校验拦得住路径穿越 / 超大包 / 可执行文件，拦不住插件代码本身做什么。
官方插件源码全部公开在插件仓库；装第三方插件前先读源码。

## 从源码构建

需要 Python 3.8（带 tkinter）、PyInstaller 5.13.2。

```
build_exe.bat                                   # 主程序 -> app\
python installer\build_setup.py                 # 安装包 -> installer\dist\Setup_WBOX.exe
```

- `build_exe.bat` 支持 `OUTDIR`（打到临时目录，不覆盖运行中的 `app\`；目标 exe
  被占用时直接拒绝构建）；构建会清空 `%OUTDIR%\plugins\`，`build_setup.py`
  在 payload 里发现 `plugins/` 条目直接拒绝打包（本体不带插件）。
- `build_setup.py` 支持 `ABG_APP` / `ABG_PAYLOAD` / `ABG_OUT` / `--payload-only`；
  配置模板由 `installer\factory_config.py` 生成（绝不拷生产配置）。
- **可复现构建**：`PYTHONHASHSEED=0`、`SOURCE_DATE_EPOCH=1000000000` 写死在
  构建链里，同一条命令两次出同一个字节。比体积 / 比 md5 的正确姿势是重打一遍对照，
  不是拿历史 md5 当基线。
- **共享运行时**：payload 不重复携带安装器自带的 Python / Tcl 运行时（68 项），
  安装时从 `_MEIPASS` 释放并逐项 md5 比对。因此 **payload.zip 不能单独解包当程序用**。
- spec 注意：tkinter 子模块与插件内核模块（`plugin_api / plugin_host /
  plugin_package / core_defaults / ace_native / popup_guard / game_lib / events`）
  必须显式写进 `hiddenimports`——运行时动态导入，modulegraph 看不见。

## 测试

```
python test\run_all_inproc.py        # 全套 49 个测试，单进程顺序跑（查跨模块污染）
for %f in (test\test_*.py) do @python %f    # 逐个独立进程（最严格，CI 口径）
```

脚本式测试（顶层断言 + `sys.exit()`），**不适用 `unittest discover`**（只 import
不执行，会把 `sys.exit(0)` 记成 error）。另有：

- `test\test_qa_build_setup_guard.py`：出包守卫 43 项（payload 边界 / 出厂模板 /
  docs 随包 / 两份 spec 裁剪规则一致…）；
- 冻结 exe 三项自检：`--self-check`（插件真加载 + 菜单渲染）、`--self-test`、
  `--layout-check`（三档大小 × 两档皮肤 × 六档 DPI，overflow=0）。

## 边界与说明

- ACE 相关只调整进程优先级和 IO 优先级，不注入、不改文件、不拦截通信；
  清理只针对没退干净的残留进程，四道闸门 + 身份校验，取不到创建时间一律不动。
- 弹窗拦截只针对 `ClientBrowser.exe`（游戏内嵌浏览器宿主），进程名与路径双命中；
  QQ / 微信 / TIM / WeGame 主干等常驻进程在保护名单，绝不误伤。
- 本项目只在一台 Windows 10 19045 和一台 Windows 11 虚拟机上验证过，其他环境未测。
- 离线产品：不联网、不上报、无遥测；「浏览插件库」只是打开浏览器访问 GitHub。

## 版本
**v2.13.0**（2026-11-19）

一轮稳定性专项（9 项），全部带回归测试，全套 49 个测试单进程通过：

- **HUD 采样线程重入修复**：上一轮采样没结束就不再叠新一轮，慢机器上不再
  堆积采样线程。
- **插件钩子耗时监控 + 卡死隔离**：每个钩子计时，超 5 秒记警告，连续 3 次
  慢钩子把该插件降级隔离（面板可见、可一键恢复）；新增 `ctx.spawn()` 把耗时
  操作丢到异步线程，退出钩子里不再阻塞守护主循环。
- **进程身份统一校验**：关进程前用「pid + 创建时刻」双确认，pid 被系统复用
  也不会关错；小飞机 / 游戏加加 / ACE / 弹窗四条链路同一套口径。
- **ACE 清理决策记录**：每次判定（清 / 不清 / 为什么）写结构化记录，
  事后可查「当时为什么没动手」。
- **配置统一校验**：`config_schema.py` 声明式校验，非法值修正或回退默认，
  修正留痕到 `config_issues.json`；面板与守护同一套规则。
- **配置迁移可见可回滚**：升级合并配置前自动备份，迁移写入日志，出问题
  能退回上一份。
- **安装事务**：安装 / 升级 / 卸载中断后可恢复（`install_txn.py`，事务状态
  在 `%ProgramData%\WBOX`），不再留下半装状态。
- **结构化事件流 + 导出诊断**：`events.jsonl`（JSONL、1 MB 轮转、写入永不
  抛错）；齿轮菜单新增「导出诊断」，一键打包日志 / 事件 / 配置 / 插件状态
  成 zip，也支持 `WBOX.exe --diag`。
- **设置面板拆分**：超大的 `settings_gui.py` 拆出 `panel_env` / `win_env` /
  `panel_state` / `ui_gfx` / `ui_widgets` 五个模块，行为不变。
- 插件同步升级：popup 2.1.0 新增**观察模式**（只记录本应关闭的窗口 / 进程，
  不动手）+ 最近动作记录；afterburner 2.2.0 / gamepp 1.2.0 新增**关闭倒计时**
  （退出后先等 N 秒再关，期间回到游戏自动取消）；ace 2.2.0 决策记录；
  desktop_hud 1.6.1 采样重入修复。插件规范新增 §5.6（钩子耗时 / 异步约定）
  与 §9.6（事件流 / 诊断包）。

**v2.12.2**（2026-11-19）

- **字标不再重复当设置入口**：极简档右上角已经有「设置」，左上角 WBOX 再挂一份
  同样的入口就是两个控件干一件事，误触也多。现在极简档字标只做身份标识（仍可
  拖动窗口），玻璃档保留——它没有「设置」按钮，字标就是菜单入口。Tab 链同步：
  极简档是「＋ → 设置 → 各行」，玻璃档仍是「＋ → 字标 → 各行」。
- **托盘「有时候不显示」**：三个真原因一起修。
  1. 首次 `Shell_NotifyIcon(NIM_ADD)` 失败（开机自启时 explorer 还没建好通知区）
     后旧实现不再重试，**整个会话没有图标**。现在 `TrayIcon.start()` 允许重建，
     面板按 1/2/4/8/15 秒退避重试，每次失败写一行 `[tray]` 日志。
  2. explorer 重启会清空通知区：注册 `TaskbarCreated`，收到广播就把图标重新挂上。
  3. 消息线程意外退出没人管：面板每 200 ms 的轮询发现 `active=False` 就重建一枚。
  另外勾了「收进托盘」但图标补不上时，关窗**不再隐藏窗口**——旧行为会让窗口和
  图标一起消失，只能重新启动面板；现在第一次保持可见并记日志，再点一次才退出。
- 回归：新增 `test\test_ui_brand_tray.py`（极简/玻璃两档字标 + 托盘重试、自愈、
  TaskbarCreated、关窗保护），全套 44 个测试单进程通过。

**v2.12.1**（2026-11-19）

对着 `design/wbox-skins.html` 的「极简」逐项校准，四处偏离设计稿的地方：

- **点插件名打不开设置**：根因不是绑定丢了，是展开后窗口按新高度**重新居中**，
  409 → 744 时顶边从 845 跳到 565，点中的那一行整个被顶走，看起来像"点了没反应"。
  现在窗口顶边钉住、只向下长（装不下才上移），行内展开前后那一行原地不动。
- **极简档看不到版本**：极简没有设置浮层，版本只在浮层里等于没有。行尾补
  `v2.1.1` 小字（设计稿 `.hint` 位），展开后的来源行改成
  `版本 v2.1.1 · 发布者：… · 权限：…` 开头；两处都能点开设置。
- **按钮和设计稿不一样**：开关从胶囊改成**方角**（36×18，行内 30×16，
  两块矩形，不再走光栅胶囊）；行内设置项从 60+ px 压到 30 px（`.p-item`）、
  hint 收进 Tooltip；卸载从一行红字改成 1px 描边的方角按钮（`.p-del`）；
  皮肤行从「● / ○」字符改成 13 px 圆圈单选（`.p-radio`）。
  展开块底色改回 `.p-expand` 的 `#F7F7F7` / 深色 `#181818`。
- **黑白只剩白**：极简设置页的「外观」组原来只有皮肤清单，**明暗入口只存在于
  「外观与皮肤…」浮层**，进了设置页就只剩白色。现在补 `明暗 · 跟随系统 / 浅色 /
  深色` 一行，选完留在本页（之前会被 apply_skin 弹回列表页），并立即落盘
  `ui_appearance`。

回归：新增 `test\test_ui_plain_v121.py`（顶边不动 / 展开只长 110 px / 版本两处可见 /
方角开关 / 30 高行内项 / 描边卸载 / 明暗三档且留在本页）。

**v2.12.0**（2026-11-19）

外观多了「窗口大小」，插件设置能直接读到版本，两件都是"用户自己就能核对"的小口子：

- **窗口大小 小 / 中 / 大**：外观面板与极简设置页的「外观」组各加一段三档胶囊，
  落盘 `ui_size`（`small` / `medium` / `large`，倍率 1.00 / 1.15 / 1.30）。
  倍率乘在系统 DPI 之上，`px()` 一处生效——窗口宽高、字号、行高、图标、浮层
  全部等比，不用给每个控件再开一套尺寸。**默认「小」就是升级前的尺寸**：
  老配置没有 `ui_size`、值写坏、配置读不出来，一律回落 small，观感不变。
  `layout-check` 从「两档骨架 × 六档 DPI」扩成「三档大小 × 两档骨架 × 六档 DPI」，
  `overflow=0`、浮层全部 `in_area=True`。
- **插件设置里能核对版本**：浮层头部原来的小灰字 `v2.1.1` 改成右侧底片胶囊
  「版本 v2.1.1」，并补一行「插件接口 API 1 · 发布者 WBOX（· 需要管理员权限）」。
  齿轮菜单仍只回答"能不能用"，不塞版本号（文案规范 §1 口径不变）。
- **回归**：新增 `test\test_ui_size.py`（默认小 / 三档切换落盘 / 重开保持 /
  明暗不冲掉大小 / 外观入口可点 / 版本胶囊文案 / 坏值回落）。

**v2.11.3**（2026-11-18）

本版按审查意见收口四件事，全部围绕"用户能不能判断 / 出了问题能不能自己好"：

- **插件信任边界摆到明面上**：`plugin.json` 新增 `publisher` / `homepage` /
  `permissions`（`src/plugin_host.py` 解析，只展示不授权）。面板「＋」装包时
  先弹一屏来源确认：发布者（官方 / 第三方 / 来源不明三种措辞）、来源链接、
  声明权限翻成人话（词表见 `plugin_package.PERMISSION_LABELS`）、文件数与体积、
  整包 SHA-256、包完整性，末尾一句"安装即等于完全信任作者"。`pack_plugin`
  同时产出 `.sha256` 侧车；侧车存在且不一致时**拒绝安装**（包被替换/传坏）。
  侧车不是签名，界面措辞不夸大。插件浮层底部也补了一行发布者 / 权限。
- **安装被打断能自愈**：`install_zip` 换入前写 `.plugin-install.json` 事务日志，
  异常就地回滚；新增 `recover()`，守护启动（`Guard()` 之前）与面板启动时各调一次：
  目标缺失有备份就搬回，没备份但暂存完整就补完，坏日志/孤儿暂存目录一并清掉。
  判定只看文件系统现状，不信日志里的 state（崩溃点可能落在两次写日志之间）。
- **进程事件可诊断、可自恢复**：`proc_events` 每次失败记**阶段名 + HRESULT**，
  `describe_hr()` 翻成一句人话（WMI 服务未启动 / 拒绝访问 / RPC 不可用…）。
  订阅失败不再"一次失败永久轮询"：60s 起、600s 封顶退避重订阅，WMI 服务回来
  自动恢复事件模式；`Next` 连续硬失败重建枚举器。新增 `status()` 供心跳上报。
- **"进程在"与"全功能在"分开说**：心跳 payload 增 `events` / `health` 两个只增
  字段；事件降级时状态行显示「运行中 · 空闲待命 · 轮询兜底」，设置页新增
  「进程事件」「插件健康」两行 + 原因说明（含已唤醒次数）。旧版守护没有这些
  字段时按老文案显示，不误报降级。
- **顺带修一个真 bug**：`run()` 里看门狗的启动条件写成 `ev is not None and ev.ok`，
  但订阅是后台线程建立的、那一刻 `ok` 必为 False —— 看门狗从来没起来过。
  退避重订阅成功、等待放宽到 30s 后，「停止守护」最多要等半分钟。现在挂着
  `ev` 就起（看门狗只做 stat + SetEvent，与订阅状态无关）。
- **本体与插件分仓**：本版起主程序**不再附带任何插件**（`build_exe.bat` 清空
  `%OUTDIR%\plugins\`，`build_setup.py` 见到 `plugins/` 条目直接拒绝打包，
  `uninstall_core.BUILTIN_PLUGINS` 改为空集）。五个官方插件迁到独立仓库
  [wbox-plugins](https://github.com/qinchangsheng123/wbox-plugins) 单独发版；
  面板新增「浏览插件库」，空面板给出"从插件库下载后安装"的引导。
  升级安装仍会整目录暂存并原样还原已装插件，卸载仍整份备份。
- **仓库脱敏**：删掉几张整屏截图（含桌面背景 / 其它窗口 / 头像），测试与设计稿里的
  本机绝对路径换成中性示例（`<安装盘>:\...`、`<游戏盘>:\WeGameApps\DemoGame\...`），
  源码注释与文档里的具体游戏名、活动页地址改为通用说法。
- 测试：`test_plugin_package.py` 新增 22 项（信任边界 / 完整性 13 + 中断恢复 9）、
  `test_proc_events.py` 新增 11 项（诊断 / status / 重订阅）、
  `test_guard_beat.py` 新增 8 项（降级文案与旧版兼容）；
  `test_qa_build_setup_guard.py` 的 payload 断言改为"0 个插件"。
  插件相关用例通过 `test/_plugin_src.py` 定位同级检出的插件仓库，找不到时打印
  跳过原因并以 0 退出（跳过会在输出里写明，不伪装成通过）。

**v2.11.2**（2026-11-18）

本版修用户实测发现的「任务栏 / 通知区都没有 WBOX」（截图红圈），并修两处
随之暴露的回归：

- **任务栏按钮**：根因是 overrideredirect 的 wrapper 在 map 时才被 Tk 创建并带
  `WS_EX_TOOLWINDOW`，init 期加的 `WS_EX_APPWINDOW` 落在客户区子窗口上；且可见
  路径 shell 在 wrapper 首次 show 时就按 TOOLWINDOW 缓存排除决定（外部
  hide/show 对照截图实证，`evidence\deploy_fulltb*.png`）。现 pre-map 补位 +
  `<Map>` 钩子对 wrapper 清 TOOL / 加 APP 并 hide/show 刷新一次。刷新会清键盘
  焦点（焦点 None 时 Tk 丢弃键事件、菜单 Esc 全链路失效，齿轮菜单回归曾因此
  红），故刷新前记焦点、刷新后 `focus_force` 回填（`focus_set` 在非前景进程
  不生效，bisect 实测）。`test_taskbar_live.py` 10 项：风格位、诱导重置刷新
  回路、焦点/Esc、withdraw/最小化还原各路径。
- **托盘图标存在性默认常驻**：面板进程活着就在通知区有一枚 W（随主题换
  glyph）；「启动后收进托盘 / 游戏结束提示」仍 opt-in，只决定关窗行为与气泡
  前提。消息窗口类改按实例命名并在销窗后 `UnregisterClassW`——旧同名类进程
  全局，同进程再建托盘（套件内 plain_ui/motion 的 probe=False 段之后）会
  `RegisterClassW` 失败静默死掉。`test_tray_live.py` 12 项真 Shell_NotifyIcon。
- **修 v2.11.1 部署删生产配置注释键**：`merge_config` 约定「_ 开头注释始终用
  包内最新文案」，但 v2.11.1 出厂模板没有注释键，部署把生产 7 个注释键删光
  （34→27 键）。`installer\factory_config.py` 补 COMMENTS 单一来源（文案更新
  到 WBOX 当前界面口径），生产配置恢复 34 键、用户值逐键不变；
  `test_qa_config_compat.py` 35/35。
- 回归：41/41 单进程全绿（含齿轮菜单连跑 6 次稳定）；QA 出包守卫 42/42
  （新增「出厂模板含全部注释键」正向断言）；`--self-check` OK；
  `--layout-check` 两档 × 六档 dpi 全 overflow=0。
- 实测产物：`Setup_WBOX_v2.11.2.exe` 5,981,637 B（5.70 MB，md5
  `9845ed32dd1e08d0c4beb08112795914`）、`payload.zip` 1,554,840 B（1.48 MB，md5
  `613ab657d751828f95b8a144cfe4eeb8`）；较 v2.11.1 传输 +3,374 B（注释键文案）。
  归档 `releases\v2.11.2\`（pre-change 反向还原备份 + 证据：部署后面板 / 任务栏
  按钮 / 托盘主区图标截图、机制对照截图、全链日志）。
- **已部署生产**：静默升级，部署后核验 16/16（首跑一项瞬态、复跑全绿）；
  取证截图确认任务栏按钮与通知区 W 均可见，溢出 chevron 隐藏（无溢出图标）。

**v2.11.1**（2026-11-18）

本版是发版包 hygiene 与体积审计的修复版（无功能变更），四条审查建议的实测与
决策见 `installer\README-部署.md`「体积与分发审计（v2.11.1）」：

- **修生产配置混入 payload**：实测 v2.11.0 及以前 payload 的 config.json 与生产
  逐字节相同（用户小飞机路径 / 游戏库根 / 个人开关随每个版本包分发）。新增
  `installer\factory_config.py` 出厂模板生成器（运行期三段合并的前两段：内核
  DEFAULTS → 内置插件 DEFAULT_CONFIG，2,741 B、键序可复现）；`build_exe.bat` 不再
  拷生产配置，`build_setup.py` 即使 `ABG_APP` 指向生产目录也强制用出厂件入包。
  安装时旧机器配置仍逐键保留。QA 新增三条断言：模板字节一致 / 无机器路径 /
  无运行状态文件。
- **运行数据边界设防**：payload 24 条目全为程序文件 / docs / 出厂模板；
  `guard.log*`、`guard.pid`、`games_cache.json`、`ace_state.json`、
  `plugins_state.json`、`config.json.bak*` 等由 SKIP_NAMES + 前缀过滤 + QA 负向
  断言三层挡住。
- **传输体积审计结论（维持现状）**：payload 早已 zlib level 9（再提增益 0 B）；
  CArchive 6→9 离线模拟仅省 5,788 B（0.10%），PyInstaller 5.13.2 无公开压缩级
  参数，不为 0.1% 碰私有类属性；LZMA 不被该版 CArchive 支持、7z SFX 违背单文件
  离线交付，均否决。exe/dll 已 UPX。传输体积与磁盘占用口径分开记录。
- **Tk/Python 运行时不再硬裁剪**：src + 五插件 38 个顶层 import 与 spec 31 项
  EXCLUDES 零交集；验证链 = 40 项单进程回归 + 冻结 exe self-check（五插件真加载）
  + layout 两档 × 六档 dpi + 隔离安装 + 共享运行时 68 项比对。再裁 ttk 主题 /
  编码表收益百 KB 级、风险启动级，否决。
- **desktop_hud 维持随包内置**：实测 23,788 B zip、占传输 0.40%；按需分发
  （安装器勾选 + 独立可选 zip / 首启下载）因离线约束与更新矩阵复杂度否决。
- 回归：40/40 单进程全绿；QA 出包守卫 41/41；`--self-check` OK（出厂配置下
  AB/ACE/THROTTLE 真加载）；`--layout-check` 两档 × 六档全 overflow=0。
- 实测产物：`Setup_WBOX_v2.11.1.exe` 5,978,263 B（5.70 MB，md5
  `95a57b25b634b58d0bfb7cb093581ad5`）、`payload.zip` 1,551,477 B（1.48 MB，md5
  `650d7ed5b76fcc34a299e157b867ca72`）；较 v2.11.0 传输 -2,720 B（模板瘦身）。
  归档 `releases\v2.11.1\`（pre-change 反向还原备份 + 生产 app 快照 + 证据）。
- **已部署生产**：静默升级，部署后核验 15/15。

**v2.11.0**（2026-11-18）

本版把 v2.10.0 有意保留的旧系统标识全量换成 WBOX，并部署到生产；安装器界面
同步改极简档。迁移与落地实况见 `installer\README-部署.md` 与本版归档：

- **系统标识全量改名**：`WBOX.exe`、默认安装目录 `C:\Program Files\WBOX`、计划任务
  `WBOX`、注册表卸载键 `...\Uninstall\WBOX`、心跳管道 `WBOXGuardBeat`、单实例 mutex、
  备份目录 `%LOCALAPPDATA%\WBOX`；构建链产物 `Setup_WBOX.exe`、包格式
  `wbox-plugin-zip-v1`、共享运行时标记 `.wbox-shared-runtime`、日志 `wbox_setup.log`。
  插件 API 主类改 `WBOXPlugin`，`WingmanPlugin` 保留为别名（旧插件不改代码仍能装）。
- **一次性迁移 `migrate_legacy`**：停旧名守护（含目标目录内跑着的旧 exe，不停会撞
  覆盖解包的占用锁）、删旧计划任务与旧注册表键、旧默认目录搬移或并存暂存、旧备份
  目录归并；升级继承已注册 InstallLocation（自定义目录只换标识不改目录名），并删除
  目标目录里的旧名主程序。`test\test_setup_migrate.py` 13 项（含真进程停止用例）。
- **安装器极简档**：480×460、与面板共用 4× 超采样抗锯齿 sprite，idle/busy/done/fail
  四态、明暗双主题、键盘可达；`test\test_setup_gui.py` 43 项 + 八张真窗口截图归档。
- **修三个实测缺陷**：`log_line` 双通道（路径 / 内存 sink），修 GUI 真安装首行日志
  TypeError；`run_all_inproc` 文件间主线程 `gc.collect()`，修跨文件 Tcl_AsyncDelete
  硬中断；`BUILTIN_PLUGINS` 补 `desktop_hud`（此前升级/卸载把它误分类为用户插件），
  `pack_plugin` 改读单一来源，QA 新增交叉断言「payload 内置名单 = BUILTIN_PLUGINS」。
- **文档同步**：`docs\PLUGIN-SPEC.md` 适用版本 v2.11.0、内置插件口径改五个；指南 HTML
  重编随包；部署文档卸载示例与产物体积更新。
- 回归：40 项测试单进程全绿；QA 出包守卫 38/38；共享运行时 68 项与 app 字节一致；
  `--self-check` OK；`--layout-check` plain + layout-std 两档 × 96/120/144/168/192/240
  六档全 `overflow=0`。
- 实测产物：`Setup_WBOX_v2.11.0.exe` 5,980,983 B（5.70 MB，md5
  `a8e86196faf6e453007dd1ab2e64ba24`）、`payload.zip` 1,554,186 B（1.48 MB，md5
  `ce86d8866d7d54194ba647101083bec3`）；可复现构建（PYTHONHASHSEED=0 /
  SOURCE_DATE_EPOCH=1000000000）。归档 `releases\v2.11.0\`（含改动前全量备份与
  生产 app 快照、回归/出包/部署日志、安装器八态截图与部署后面板真窗口截图）。
- **已部署生产**：静默安装继承旧键 InstallLocation（仓库 `app\`），迁移链日志齐全；
  部署后核验 15/15（新旧任务/注册表键/进程/心跳管道/桌面快捷方式/备份归并）。

**v2.10.0**（2026-11-18）

本版收口三件事：液态玻璃退役、极简档打磨、用户可见层改名 WBOX。规矩与落地实况
见 `docs\SKIN-SPEC.md`「落地实况（v2.10.0，2026-11-18）」：

- **液态玻璃不再内置**：内置只发 `plain` 一档（v1.1.0）；旧配置 `ui_skin=liquid-glass`
  启动时回退 plain 并一次性写回（guard.log 留痕）；液态玻璃降为第三方参考样例
  （测试 fixture `test\skinfix.py`），standard 渲染骨架保留不删。
- **极简档打磨**：窗宽 640→480、行高 40 / 标题栏 36、正文 12px / 辅助 11px（随 dpi
  缩放）；开关 / 箭头 / 加号 / 窗口按钮等 Canvas 控件改 4× 超采样程序化抗锯齿 sprite
  （按几何键缓存，minimal 档专用），锯齿边消除；96/144 dpi 真窗口截图明暗双主题复核。
- **修极简插件设置点不开**：Tk 事件不穿透 Canvas 子窗口，行内图标 / 名称 / 行尾箭头
  子控件各自绑点击；点整行在本行底下行内展开可调项（≤3 项直接列，更多走
  「全部 N 项设置…」），再点收起，开关本身只切换不展开；`test\test_plain_ui.py` 34 项断言。
- **改名 WBOX（仅用户可见层）**：字标 / 窗口标题、桌面与开始菜单快捷方式（WBOX 文件夹）、
  卸载显示名、安装 / 卸载对话框文案、指南标题、崩溃日志头、README 与部署文档；
  安装器顺带清理旧名快捷方式与开始菜单文件夹。**系统标识有意保留 Wingman**：
  `Wingman.exe`、安装目录、计划任务、注册表卸载键、心跳管道、单实例 mutex、
  `%LOCALAPPDATA%\Wingman` 备份；插件 API 与包格式不变。
- **文档同步**：`docs\PLUGIN-SPEC.md` 适用版本 v2.10.0、§3.3 改极简行内展开口径；
  指南 HTML 重编随包（QA 确认内嵌 payload 含 docs 四件套）。
- 回归：39 项测试单进程全绿（`test\run_all_inproc.py`，含新增 test_plain_ui；
  standard 档旧回归改用用户皮肤 fixture + 显式 `ui_skin`）；`--self-check` OK；
  `--layout-check` plain + layout-std 两档 × 96/120/144/168/192/240 六档全
  `overflow=0`；QA 出包守卫 37 项全过；共享运行时 68 项与 app 字节一致。
- 实测产物：`Setup_Wingman_v2.10.0.exe` 5,975,075 B（5.70 MB，md5
  `64547bbdf3b6f2ae64e3634e7709f00b`）、`payload-v2.10.0.zip` 1,552,363 B
  （1.48 MB，md5 `4da2d89e416666d84e8a68710c745ac5`）；可复现构建
  （PYTHONHASHSEED=0 / SOURCE_DATE_EPOCH=1000000000），归档见 `releases\v2.10.0\`。

**v2.9.0**（2026-11-18）

接 v2.8.0 对着设计稿（`design\wbox-preview.html` v3.4.0、`design\wbox-skins.html`
v1.0.0、安装器 `v-setup` 视图）逐项复核，把剩余 UI 缺口全部闭环；规矩与落地实况
见 `docs\SKIN-SPEC.md` 第 8 节「落地实况（v2.9.0）」：

- **安装器对齐 `v-setup`**：620×540 玻璃窗、idle/busy/done/fail 四态卡片、胶囊按钮、
  46×28 开关、5px 进度条、失败详情 pane；`test\test_setup_gui.py` 38 项断言
  （可脚本化 mainloop 驱动四态，不靠真安装）。
- **面板动效**：ease-out 单曲线 + 按需 tween 链（hover 160 / press 120 / 开关 240 /
  浮层淡入 200ms）；系统「在窗口中显示动画」关、极简档、probe 一律 0ms，
  空闲 0 条常驻过渡链；`test\test_ui_motion.py`。
- **托盘与游戏结束通知**（opt-in、默认关）：菜单新增「启动后收进托盘 / 游戏结束提示」
  两行；托盘左键叫回、右键「打开设置 / 退出」；气泡仅在窗口收在托盘且
  active→grace/idle 时发；`test\test_tray_live.py` 真 Shell_NotifyIcon 实测。
- **皮肤面板**：卡片 / 缩略图 / 标准·最省 pill、明暗三段落盘、api=2「需要更新」卡、
  `.wbskin` 拒绝理由（含可执行后缀 / api 不符）、超屏滚轮、96–240 dpi 不越界；
  `test\test_skin_panel.py`。
- **文案红线与键盘可达**：动词按钮、默认焦点落安全键、Esc=取消、Enter 不默认触发
  危险动作、显式 Tab 链 + 焦点环、置灰必配解释；`test\test_ui_copy_keyboard.py`。
- **面板功能与溢出**：菜单内核行 11→13（`CORE_MENU_ROWS` 单一来源）；长路径胶囊
  240dpi 换行、不再把「选择」按钮顶出胶囊；菜单与插件浮层 scrollregion 跟随内容
  高度（含 place 之后才到的 Configure），滚到底卸载入口可达；
  `test\test_plugin_gui_overflow.py` 扩到 24 项断言。
- **指南同步**：`docs\PLUGIN-SPEC.md` 适用版本 v2.9.0，面板操作说明按 WBOX 入口
  校对，HTML 重编随包（`--check` 逐字节通过）。
- **品牌迁移范围：本次仅更新界面品牌**（与 v2.8.0 同口径）：安装目录、`Wingman.exe`、
  计划任务、注册表卸载项、心跳管道仍有意沿用 Wingman；插件 API 与包格式不变。
- **明示取舍**（决定不做，不是没做完）：安装器 hover 即时不引 tween；焦点环内缩
  2px；安装器标题栏带版本号；托盘 / 通知默认关；投影不做——逐条理由见 SKIN-SPEC。
- **构建链补漏**：`build_exe.bat` 补拷 `desktop_hud`——分发集合一直是 5 个插件
  （QA 出包守卫口径），以前靠旧 OUTDIR 残留掩盖，全新目录构建会静默丢它；
  现在拷贝 / 存在性检查 / `__pycache__` 清理都覆盖 5 个目录。
- 回归：38 项测试单进程全绿（`test\run_all_inproc.py`）+ 独立进程抽查；
  `--self-check` OK；`--layout-check` 96/120/144/168/192/240 六档 36 行全
  `overflow=0`；QA 出包守卫 37 项全过。
- 实测产物：`Setup_Wingman_v2.9.0.exe` 5,972,070 B（5.70 MB，md5
  `3838334d9d11332eb8a2c47332f6c558`）、`payload-v2.9.0.zip` 1,549,286 B
  （1.48 MB，md5 `1e47a817c29944578b8a5f9bd004cc8f`）；可复现构建
  （PYTHONHASHSEED=0 / SOURCE_DATE_EPOCH=1000000000），归档见 `releases\v2.9.0\`。

**v2.8.0**（2026-11-17）

对着 `design\wbox-preview.html`（v3.4.0）与 `design\wbox-skins.html`（v1.0.0）
把界面做到位，规矩写在 `docs\SKIN-SPEC.md`：

- **自绘标题栏**：皮肤里 `chrome=custom` 时 `overrideredirect` 掉系统标题栏，整行
  自己画（玻璃 48 / 极简 40）——字标 `WBOX` + 下拉箭头就是菜单入口，右侧一枚
  32 圆加号、最小化、关闭。拖动、双击、任务栏图标（`WS_EX_APPWINDOW`）都要留住，
  最小化得先短暂恢复 frame 再 `iconify()`。
- **圆角窗口**：Tk 画不了圆角窗口，靠 `SetWindowRgn(CreateRoundRectRgn)` 裁；半径读
  `radius.window`，极简档半径 0 时一次 API 都不调。
- **圆角卡片列表**：每一行自己是一块 Canvas（子控件用 `create_window` 摆位），
  卡片 20 圆角、行 14 圆角悬停底板、行尾一枚 30×30 单入口。
- **极简档**：方角、行高 44、无卡片；行内展开设置块（不建 Toplevel）；标题栏
  右侧纯文字「设置」→ 同窗换页（`pane=list|set`），设置页行高 36 / 分组头 24。
- **大写 W 图标**：`design\generate_icons.py` 重写（纯标准库扫描线 + 4× 超采样），
  9 个尺寸；≤24 px 去容器只留 W；exe / 安装器 / 面板同源。
- 皮肤是**纯数据包**（`.wbskin` = zip，26 个颜色 token + 圆角 / 行高 / 字号），
  含可执行后缀的包直接拒绝；内置两套（`liquid-glass` / `plain`）代码内联、随 exe 走。
- 33 项测试全绿（`test\run_all_inproc.py`），`--self-check` 与 `--layout-check`
  （96/120/144/168/192/240 dpi）全部 `overflow=0`。
- **品牌迁移范围：本次仅更新界面品牌**。设置窗口标题与标题栏字标改为 `WBOX`，
  其余系统项目**有意沿用 Wingman**：安装包 `Setup_Wingman.exe`、默认安装目录
  `C:\Program Files\Wingman`、主程序 `Wingman.exe`、计划任务名 `Wingman`、
  注册表卸载项 `Uninstall\Wingman`（显示名「僚机 Wingman」）、开始菜单目录与
  快捷方式、`%LOCALAPPDATA%\Wingman` 插件备份区、心跳管道 `WingmanGuardBeat`。
  这些属于改名迁移，要连老用户升级路径一起改，不与视觉改版混做
  （`src\settings_gui.py` 窗口标题处注释、`src\uninstall_core.py` 常量）。
  插件 API、包格式 `wingman-plugin-zip-v1` 与命令行参数均不变。
- 《插件开发指南》适用版本标注同步为 v2.8.0，面板操作说明按 WBOX 入口校对
  （字标菜单 / 标题栏「＋」/ 设置浮层底部卸载）；`docs\PLUGIN-SPEC.md` 重编
  HTML 并铺进 `app\docs\`，`--check` 逐字节通过。已出的 v2.8.0 安装包内仍是
  旧版指南，重新打包后生效。

**v2.5.0**（2026-11-16）

- **崩溃落盘**：守护入口装三层兜底（`faulthandler.enable` + `sys.excepthook` +
  `threading.excepthook`），未捕获异常 / 段错误把 traceback + 时间戳 + 版本号写进
  安装根的 `guard_crash.log`（追加写，1 MB 上限轮转保留一代 `.1`）。正常退出不留
  任何文件——0 字节占位在退出清理时删掉。实现 `src\crash_log.py`，测试
  `test\test_crash_log.py`（含真实段错误子进程验证）。
- **守护↔面板心跳**：守护起 named pipe 服务（`\\.\pipe\WingmanGuardBeat`，纯
  ctypes，零新依赖），面板 2 秒一次连管道读一行状态（pid / 版本 / 会话 / 各插件
  最近 tick）。连不上就明说「守护没在运行」，不再靠 pid 文件猜。管道空闲时服务
  线程睡在内核等待队列，空闲 CPU 实测 +0.03pp（0.135% → 0.168%，可忽略）；
  面板来连时 +0.10pp（真实工作量）。实现 `src\guard_beat.py`，测试
  `test\test_guard_beat.py`。注意：外线程对阻塞中的 `ConnectNamedPipe` 调
  `DisconnectNamedPipe` 会死锁，停机走"自连接唤醒"模式。
- **事件驱动进程检测**：ctypes 直连 WMI COM（`Win32_ProcessStartTrace` /
  `StopTrace`）做「唤醒器」——游戏启动/退出即时跑一轮判定；轮询降为低频兜底
  （空闲 2s→30s、会话 0.4s→2.0s；插件登记的弹窗监视快节拍不放宽）。事件通道
  不可用（未提权 / COM 被禁）自动回落纯轮询并记日志。**空闲 CPU 实测 0.168% →
  0.052%（约 1/3）**。闸门与插件判定逻辑零改动。实现 `src\proc_events.py`，
  评估与踩坑记录 `design\proc-events.md`，测试 `test\test_proc_events.py`。
  低频兜底另配 1 秒标记看门狗：stop.flag / 热重载标记 / config.json 变更立即
  叫醒主循环，「停止守护」「改动立即生效」时延不回退（测试 [5] 盯着 ≤4s）。

**v2.4.0**（2026-11-16）

- 插件设置弹框（v2.3.0 系列的收尾）：5 个内置插件 manifest 声明 `settings`
  （bool / int / choice / path 四类控件，点路径 key），面板解析渲染成每插件设置
  弹框；`manifest.settings` 为空的插件**不显示齿轮**。改动单项写盘、其余键不动。
- 精确齿轮语义测试（头部齿轮 1 + 插件行齿轮=声明了 settings 的插件数，不许放宽
  成 >=1）+ 弹框全链路测试（点开 / 四类控件落盘 / 重建与 DPI 切换销毁不留残骸）。
- `docs\PLUGIN-SPEC.md` 新增 §3.3 `settings` 字段章节（从 `plugin_host` 解析代码
  反查，只写实现支持的写法），HTML 重编过 `--check`。
- payload 重打修复 `test_qa_build_setup_guard.py`（守卫没错，构建陈旧）。

**v2.3.0**（2026-11-15）

- 新增《插件开发指南》：`docs\PLUGIN-SPEC.md`（规范正文）+ `docs\plugin-template\`
  （可直接改名使用的最小示例）+ `docs\插件开发指南.html`（自包含单页，样式内嵌、
  不联网、双击即读）。面板齿轮菜单新增「插件开发指南」，开始菜单也放一份。
  HTML 由 `installer/build_plugin_doc.py` 从 Markdown 每次构建重新编译，不会与
  正文走偏：`--check` 会逐字节比对磁盘上的 HTML 与"用当前 Markdown 重编"的结果，
  不一致或文件缺失就 exit 1（可当提交前的守门员）；`build_exe.bat` 自动把三件套
  铺进 `app\docs\`，`test_qa_build_setup_guard.py` 的 `[5b]` 则守着它们必须进 payload。
- 瘦身：再裁 6 个永远不会被 source 的 Tk 脚本（`xmfbox` / `iconlist` / `clrpick` /
  `palette` / `choosedir` / `optMenu`），`app\tk\` 522,636 B / 51 文件 →
  439,739 B / 45 文件（**−82,897 B，−15.9%**）；`tk\ttk\*` 一个没动。
  判据是"只在 tclIndex 里作为按需自动加载条目出现、且本程序从不调那条命令"。
  两份 spec 的清单必须逐字一致（构建后有 md5 逐项校验，只改一份会判构建失败）。
- 内存结论（实测，未改动）：守护模式早已不加载 GUI —— `--guard` 进程 32 个模块、
  模块表里没有 `_tkinter.pyd` / `tcl86t.dll` / `tk86t.dll`（自测 WorkingSet 6.29 MB；
  QA 在已连续运行数天的生产实例上读到 7.35 MB，两者不可比，但"模块表里没有 tk"
  这一条双方一致）；面板模式 63 个模块。`abg_main.py` 的 `import settings_gui` 写在
  函数体里，导入链上没有 tkinter，所以这 2.2 MB 只在真正开面板时才付出 ——
  **这一条线（GUI 按需导入）确实没有空间了**，本次也没动它。但"整体没有优化空间"
  不成立：安装器侧曾白付 123 KB，见下面的复核补丁。
- 安装器：快捷方式改用原生 `IShellLinkW` + `IPersistFile`（纯 ctypes，零第三方依赖、
  不再为每个 .lnk 拉起一个 PowerShell 进程），失败自动回落旧实现。
  `Setup_Wingman.exe` 旁边多出一份带版本号的 `Setup_Wingman_v2.3.0.exe`。
- 修复：`build_exe.bat` 往全新 `OUTDIR` 出包时不再漏掉 `icon-light/icon-dark.ico`
  （以前只有生产 `app\` 里手动放着这两张图，临时目录构建会静默丢图标）。
- **复核补丁**（QA 验收后修，随 v2.3.0 一起发，不单独升版本号）：
  1. `installer/build_plugin_doc.py --check` 以前是空壳（只在内存里重算一遍就打印
     "校验通过"，从不读磁盘）——改正文 4 个字、甚至删掉 HTML 都 exit 0。现在真比
     字节：不一致/缺失都 exit 1，并打印双方大小、md5 与第一处差异的位置和上下文。
  2. 「指南随包分发」补上自动化守卫：`test_qa_build_setup_guard.py` 新增 `[5b]`，
     payload 必含 docs 四件套、各出现一次、无 `.pyc`、HTML 严格 UTF-8 且中文正文
     够量、无外链；`[6]` 追加"内嵌 payload 里也有 docs"。
  3. `build_exe.bat` 不再可能把产物写进正在运行的安装目录：构建前对
     `%OUTDIR%\Wingman.exe` 做独占写探测（`shared_runtime.outdir_busy`），被占用就
     `exit /b 1` 并打印处置办法。旧行为是 exe 换不掉、dll/tcl 却换得掉，静默产出
     半新半旧的混合安装还报 BUILD OK。
  4. 回收 123,525 B：安装器以前 `import uuid` 只为解析一个 COM GUID，PyInstaller
     顺着 `uuid -> platform -> ctypes.util` 与 `uuid._netbios_getnode() -> win32wnet`
     把 pywin32 三件（`pywintypes38.dll` / `win32wnet.pyd` / `VCRUNTIME140_1.dll`）
     和 10 个模块拖进了安装包。改成手写十六进制解析后，这些条目在内核里**完全消失**，
     单测用 `uuid.UUID(...).bytes_le` 做独立裁判逐字节核对 4 个 GUID。
  5. 测试卫生：`test_ace_state` / `test_quiet_gate` 的 `ace_native.process_age_sec`
     替身改为 `try/finally` 还原（以前污染同进程后跑的模块）；两条退化成同义反复的
     菜单行数断言改成"写死应有行数 + 数真实 Tk 控件"；版本号正则 `\d` → `[0-9]`。
- 验证：28 个单测全绿（独立进程逐个跑 + 单进程顺序跑各一轮）；frozen `--self-check`
  rc=0（4 插件 ok）、`--layout-check` 六档 DPI 全部 `overflow=0`；
  `test_qa_shared_runtime` 复刻"从 Setup 的 CArchive 还原安装"全流程并通过
  （68 项共享运行时逐字节一致）；`build_plugin_doc.py --check` 三场景实测
  （未改动 0 / 改一字 1 / 删掉 HTML 1）。
- 体积对照（同机实测）：

  | 项 | v2.2.0 | v2.3.0 | 差 |
  |---|---|---|---|
  | `Wingman.exe` | 1,149,374 B | 1,150,048 B | +674 B（菜单多一行 + 版本号 + 注释） |
  | `app\` 运行时（不含 `docs\`） | 5,571,809 B | 5,489,589 B | **−82,220 B（−1.48%）** |
  | 其中 `app\tk\` | 522,636 B / 51 文件 | 439,739 B / 45 文件 | −82,897 B |
  | `app\` 整目录（含新增 `docs\`） | 5,571,809 B | 5,581,330 B | +9,521 B |
  | `payload.zip` | 1,363,194 B / 14 条目 | 1,399,888 B / 18 条目 | +36,694 B（多 4 个文档） |
  | 共享运行时清单 | 74 项 | 68 项 | −6（裁掉的 Tk 脚本） |
  | `Setup_Wingman.exe` | 5,763,549 B | 5,779,574 B | **+16,025 B（+0.28%）** |

  安装包只多了 15.7 KB，而且**增的全部是文档**。逐项归因用 PyInstaller 5.13.2 的
  `CArchiveReader` 条目表（`(offset, length, ulength, cflag, typecode)`，`length` 才是
  归档里实际占的字节），基线取 `D:/06-Temp/wingman-v220/out/dist/Setup_Wingman.exe`
  （2026-11-12 23:29，5,763,549 B）。下表**全部是压缩后口径**，且闭合到文件字节：

  | 条目 | 压缩后 Δ | 解压后 Δ | 说明 |
  |---|---|---|---|
  | `payload.zip` | **+36,468** | +36,694 | 指南三件套（原文 91,741 B） |
  | `setup_abg`（安装器主脚本） | +2,791 | +5,257 | 原生 IShellLink + 占用探测 + 手写 GUID |
  | `PYZ-00.pyz` | +1,419 | +1,419 | 模块数 93 → 93，与 v2.2.0 相同 |
  | 6 个 Tcl 脚本（条目消失） | **−24,381** | −82,897 | `xmfbox` `iconlist` `clrpick` `palette` `choosedir` `optMenu` |
  | 归档合计 | +16,297 | — | |
  | 非归档区（PE 头 + 节对齐填充） | −272 | — | 268,360 → 268,088 |
  | **文件合计** | **+16,025** | — | 与 5,779,574 − 5,763,549 逐字节吻合 |

  条目数 85 → 79，消失的正好是上表 6 个 Tcl 脚本，**新增条目 0 个**；内核里
  `pywintypes38.dll` / `win32wnet.pyd` / `VCRUNTIME140_1.dll` 与 `uuid` / `platform` /
  `netbios` / `ctypes.util` / `ctypes.macholib` 全部不存在（只剩 PyInstaller 自带的
  `pyimod04_pywin32` 运行钩子，与 uuid 无关）。
  二进制的 UPX 压缩未受影响（`python38.dll` 1,436,848 B、`tcl86t.dll` 636,064 B、
  `tk86t.dll` 609,440 B，与 v2.2.0 逐字节同尺寸）。

- **两份 spec 的裁剪规则有自动守卫了**（v2.3.0 复核补丁 R3）：`test_qa_build_setup_guard`
  的 `[5c]` 用 AST 抽出 `KEEP_ENC` / `DROP_DIR_PREFIX` / `DROP_FILES` / `EXCLUDES` 四块
  集合与 `trim_tk_data` 函数体（只允许 print 前缀 `[spec]` / `[setup spec]` 不同），
  逐块比对两份 spec，差哪一项直接点名（如 `abg 多 ['tk/xxx.tcl']`）；另加一条
  "`DROP_FILES` 必须全小写"（`trim_tk_data` 比的是 `lower()` 后的路径，
  写 `tk/optMenu.tcl` 是永远匹配不上的死条目，v2.2.0 踩过）。**不比整文件字节**：
  两份 spec 的入口、`datas`、`hiddenimports` 本来就不同。
  顺手清掉 `abg.spec` 里 `EXCLUDES` 的 2 个重复项（`bz2` / `lzma`），
  两份现在都是 31 项。已验证去重只影响列表长度、不影响产物：同一 `PYTHONHASHSEED`
  下"去重前 / 去重后"两次构建的 `Wingman.exe` 大小相同、只有 6 个字节不同，
  且全部落在 PE 的 `TimeDateStamp` 字段上，PYZ 里 102 个模块逐字节一致。

- **可复现构建（同一条命令两次出同一个 md5）**：默认的 PyInstaller 构建**不是**
  可复现的 —— ① `PYTHONHASHSEED` 随机化会改变 `ftplib` / `hashlib` / `zipfile` / `netrc`
  这类模块里字符串常量的排列（实测同源码不同 seed，`marshal.dumps(compile(...))` 的
  md5 三个 seed 三个值）；② PE 头 `TimeDateStamp` 写的是构建时间；③ `payload.zip` 里
  每个条目的 DOS 时间戳取的是文件 mtime / 当前时间。三处都钉死之后，整条链逐字节可复现。

  两个变量写在两处，**都是写死的常量，不是"外面的环境变量"**：
  ```bat
  rem build_exe.bat（主程序打包之前）
  set PYTHONHASHSEED=0
  set SOURCE_DATE_EPOCH=1000000000
  ```
  `installer\build_setup.py` 里另有一份常量，并**显式 `env=` 传给 PyInstaller 子进程**
  （从 IDE / 计划任务 / CI 直接调本脚本时继承不到 bat 里 `set` 的值；父进程环境不符时
  会打印一行"强制覆盖成…"），`payload.zip` 的每个 `ZipInfo` 也用
  `time.gmtime(SOURCE_DATE_EPOCH)` 写死 `date_time`，`create_system` / `external_attr`
  一并固定（否则换个操作系统打包字节就不同）。`1000000000` = **2001-09-09T01:46:40Z**，
  换算是 UTC，避开 zip 的 1980 年下限。

  **以后比体积 / 比 md5 的正确姿势是「同一条命令重打一遍，应当逐字节相同」**，
  而不是拿历史 md5 当基线去比：改这两个值（或换 Python/UPX/PyInstaller 版本）会让
  所有历史产物一次性对不上，那是预期行为，不是回归。实测连打两轮完整链
  （`build_exe.bat` → `build_setup.py`，中间不改任何文件）：
  `Setup_Wingman.exe` 两次都是 **5,779,574 B / md5 `d728b4cd3dfd886f8548f0029a5bc90f`**，
  `payload.zip` 两次都是 **1,399,888 B / md5 `a49ad4429abea5870bfd2e4450ebe1ce`**，
  `Wingman.exe` 两次都是 **1,150,048 B / md5 `6577e2de86aa8b73d1394e99e18c0e76`**。
  再把父进程环境故意污染成 `PYTHONHASHSEED=12345` / `SOURCE_DATE_EPOCH=1` 打第三轮，
  三个 md5 依旧一模一样 —— bat 里的 `set` 与 `build_setup.py` 的强制覆盖都真的生效，
  不依赖调用者先把环境设对。

  踩出来的坑：**"只改 spec 里的注释/去重，md5 必须不变"是个达不到的判据**。同源码
  不同 `PYTHONHASHSEED` 会让上面那几个标准库模块的字符串常量排列变化，进而改变
  `PYZ` 里的字节；判断"到底改动了什么"要看**内容**（CArchive 条目表 + PYZ 逐模块
  md5），不是 md5 本身。这也是当初 `EXCLUDES` 去重（33 → 31 项、集合不变）时
  "产物 md5 变了"的真正原因：我在同一 seed 下做了去重/不去重两次对照，`Wingman.exe`
  大小相同、只有 6 个字节不同且全在 `TimeDateStamp`，`PYZ` 里 102 个模块逐字节一致。

- **测试怎么跑**（v2.3.0 复核补丁 R2）：这批 test_*.py 文件是**脚本式**测试（顶层执行断言、
  末尾 `sys.exit()`），不是 `unittest.TestCase`，所以有三种口径，前两种才是有效口径：
  ```bat
  :: (1) 逐个文件独立进程 —— 最严格，CI 用这个
  for %f in (test\test_*.py) do @C:\Python38\python.exe %f
  :: (2) 单进程顺序跑 —— 专门查跨模块污染
  C:\Python38\python.exe test\run_all_inproc.py
  :: (3) unittest discover —— 本项目不适用
  C:\Python38\python.exe -m unittest discover -s test -p "test_*.py"
  ```
  `discover` **不适用**且不要当作覆盖率证据：它只 `import` 不执行，脚本末尾的 `sys.exit(0)`
  会被记成 `SystemExit: 0` 的 error；实际情况是 `Ran 4 tests / FAILED (errors=4)`，
  而这 4 个 error **全是 import 期 `sys.exit(0)`，0 个断言失败**（`test_plugin_popup_config` /
  `test_popup_guard` / `test_popup_kill` / `test_setup_args`），它连一个断言也跑不到。
  `run_all_inproc.py` 在同一解释器里按顺序 `runpy.run_path` 跑完 28 个文件、**不清理**
  `ace_native` / `guard_core` 等模块的全局状态 —— 正是它抓出了"`test_ace_state` 的
  `process_age_sec` 替身不还原、把 `test_quiet_gate[8]` 带偏"这个问题。它只清理
  `settings_gui`（`BASE` / `PLUGINS_DIR` 是 import 期常量），各 GUI 测试自己也都会在
  `import settings_gui` 之前 `sys.modules.pop('settings_gui', None)`，所以任意顺序组合都成立。

**v2.2.0**（2026-11-12）

- 界面统一：设置面板与安装程序收敛到同一套极简黑白组件（10 个中性灰阶 token、
  8px 栅格、直角、无阴影）。安装器不再用 ttk `vista` 原生控件，改 `tk` 原生自绘，
  按系统深浅色自动选主题，去掉 `#555` / `#f4f4f4` 硬编码。
- 图标：品牌盾改成三块实心 + V 形双翼留白（旧版「实心盾 + 背景色上箭头镂空」
  在行 hover 时会穿帮）；功能图标全线性，新增 `plane` / `pulse` / `folder` / `admin`，
  小飞机与游戏加加不再共用 `link`。图标白名单 14 → 20，未知 kind 兜底成点不留空白。
- 文案：六个内部名词退出界面（`config.json` / `plugins\` / `%LOCALAPPDATA%` / `.zip` /
  `热重载` / `计划任务`）；菜单统一 4–6 字（开机自启 / 重新扫描游戏 / 查看日志 /
  浅色主题 / 深色主题）；错误框改「发生了什么 + 下一步」，不再抛 `repr(e)`；
  安装器副标题从写死三功能改成「按需启用功能，随时可以卸载。」
- 插件显示名：小飞机联动 / 游戏加加联动 / ACE 防护 / 弹窗拦截。
- 验证：28 个单测全绿（新增 `test_glyph_v22.py`）；frozen 三项自检通过；
  6 档 DPI 0 溢出；安装器 GUI 自检 `GUI OK 990x780 (dpi=144)`。
- 产物：`Wingman.exe` 1,149,374 B；`Setup_Wingman.exe` 5,763,549 B；
  `payload.zip` 1,363,194 B / 14 文件。

**v2.1.1**（2026-11-12）

- 瘦身：`Setup_Wingman.exe` 从 9,085,170 B 降到约 6 MB —— payload 不再重复打包
  安装器自带的 9 个运行时二进制与 88 个 Tcl/Tk 数据文件，安装时从 `_MEIPASS` 复用；
  构建后逐项 md5 校验，杜绝两份运行时不一致装出残废程序。
- 瘦身：两份 spec 的 Tcl 裁剪收窄到 4 张编码表，删掉 `tcl8\*.tm` / `tcl\http1.0` /
  8 个 tk 脚本；`tk\ttk` 主题文件必须全留（`ttk::LoadThemes` 会逐个 source）。
- 瘦身：`base_library.zip` 分发用 STORED、落地用 DEFLATE，磁盘占用 1.03 MB → 468 KB。
- 结论：主 exe 无法再压 —— UPX 对带 CFG 的 PyInstaller bootloader 直接拒绝，
  `--force` 只省 140 KB 且会破坏控制流保护；python38.dll 已 UPX，改回原始 PE 让
  ZIP 压反而更大（1.86 MB vs 1.40 MB）。

**v2.1.0**（2026-11-12）

- 加：面板标题栏齿轮左边「＋」添加插件、每行开关右边「垃圾桶」删除插件；
  插件目录不可写时两个按钮置灰并提示提权，删除走备份区不做硬删除。
- 加：统一插件包格式 `wingman-plugin-zip-v1`（.zip），`plugin_package.py` 负责
  只读校验 / 暂存解压 / 覆盖备份 / 卸载备份 / 打包，`installer\pack_plugin.py` 生成内置插件分发包。
- 加：守护插件热重载 —— 面板写 `plugins_reload.flag`，守护在会话 idle 时重建插件宿主、
  重新三段合并配置并给新插件发 `on_config`；不重启进程、不丢会话状态机。
- 加：内置插件 `gamepp`（游戏加加联动），与 afterburner 同一套「所有权 + 基线」语义，
  用户自己常驻的游戏加加不会被关闭；源码不含任何本机绝对路径。
- 改：安装器 / 构建脚本的内置插件名单补 `gamepp`，payload 排除热重载标记与
  插件安装暂存目录。

**v2.0.0**（2026-11-12）

- 架构：抽出插件内核（`plugin_api` / `plugin_host` / `core_defaults`），三个功能变成
  `plugins\` 下的三个插件；守护的 `tick()` 从 122 行单体缩成 47 行会话状态机，
  加功能/减功能都不用改内核代码。
- 加：插件 manifest（id / name / version / api / icon / order / toggle / tip），
  单插件故障隔离（degraded + 降频，坏插件永远不拖垮守护），零插件也能正常跑。
- 加：会话状态机 `idle → active → grace → idle`，`ctx.retry_close()` 支持关闭失败重试，
  `ctx.request_fast_poll()` 让插件按需申请快节拍。
- 面板：主页面与齿轮菜单完全由注册表驱动，新增「插件」分组（版本 + 运行状态 + 打开目录），
  删掉插件目录那一行就消失；配置显示改用与守护一致的三段合并。
- 加：`build_exe.bat` 支持 `OUTDIR`，可以往临时目录出包做冒烟测试，不再覆盖运行中的 `app\`。
- 加：升级安装保留用户自装插件，卸载前把它们备份到
  `%LOCALAPPDATA%\Wingman\removed-plugins-<时间戳>\`。
- 打包：`abg.spec` 的 `hiddenimports` 补上 `plugin_api / plugin_host / core_defaults /
  ace_native / popup_guard / game_lib`（插件是运行时动态导入，分析器看不见）。
- 修：`installer\build_setup.py` 对 payload 加硬断言 —— `ABG_APP` 里没有
  `Wingman.exe` / `plugins\`，或 `plugins\*\plugin.json` 数为 0，直接退出并删掉半成品
  `payload.zip`；宁可打包失败，也不交付一个"装完三个功能全丢"的安装包。
- 修：`popup_guard.protect_names` 改为**安全并集**（`plugin_host.SAFE_UNION_PATHS`）：
  默认 10 项 + 用户项去重，用户按 v1 模板只写 5 项时 qq.exe / wechat.exe /
  weixin.exe 不会再从保护名单里消失（`deep_merge` 对 list 的整体替换语义没动，
  `config.json` 也不会被回写）。
- 加：面板齿轮菜单的插件行追加启用态（`· 已启用` / `· 已禁用`，读面板实时值），
  manifest 声明 `requires_admin` 且当前未提权时再追加 `· 需要管理员`。

**v1.3.1**（2026-11-12）

- 修：日志里「本局提前拦截 N 次」恒为 0（先清零再打印）
- 修：安装被中断后留下的目录没有继承权限，普通用户进不去且重装不自愈
- 修：只读判定只看文件可写，漏了保存所需的目录写权限
- 修：payload 里混进上一版卸载器，安装包体积翻倍
- 加：配置保存前留 `.bak`
- 加：关闭小飞机等待期间复核游戏是否回归，回归即中止
- 加：启动小飞机前检查所在目录是否对普通用户可写
- 加：系统时间跳变检测

**v1.3.0**：更名僚机 Wingman；修 4 个 P0（提权、快照失败误判、PATH 劫持、弹窗误杀）。
