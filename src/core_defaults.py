# -*- coding: utf-8 -*-
"""WBOX - 内核默认值（唯一来源）。

为什么单独一个模块：
  1. 面板 `import guard_core` 只是为了取默认值，却把 ctypes/Win32 绑定一起拉进面板进程。
     默认值搬到这里后，面板与测试可以只 import 本模块。
  2. 插件目录 `plugins\\` 是用户可替换的目录，**安全默认值不能跟着一起被替换**：
     ACE 四道闸门的秒数、弹窗保护名单必须来自这里（随 exe 冻结）。

约定：
  - 本模块只放常量与纯数据，不 import 任何 Win32 / tkinter / 标准库以外的东西。
  - 这里出现的值一律与 v1.3.1 完全一致，改动等于改产品行为，需要单独评审。
  - guard_core 以同名模块级名字 re-export 这四个常量（保证
    `guard_core.DEFAULTS` / `guard_core.ACE_THROTTLE_DEFAULTS` 等旧引用仍然可用，
    且指向同一对象）。
"""

# 版本号单一来源：取版本号若 import settings_gui 会把 tkinter 拉进守护进程。
VERSION = '2.18.2'

# v2.11.3：本体不再附带插件，官方插件在独立仓库分发。面板的「浏览插件库」
# 与卸载提示都引用这一处，避免 URL 散落在文案里。
PLUGIN_REPO = 'https://github.com/qinchangsheng123/wbox-plugins'
PLUGIN_REPO_NAME = 'wbox-plugins'

DEFAULTS = {
    # 不写死本机路径：新机器由安装器探测写入，直接解压 payload 时由运行时兜底
    # 查常见目录/进程表；都没有就明确报错，别指向不存在的 F 盘路径。
    'afterburner_path': '',
    'afterburner_enabled': True,   # 功能开关 1：游戏 ↔ 小飞机联动（含 RTSS）
    # sorted(RTSS_NAMES)：guard_core 的 RTSS 进程名集合按字典序展开
    # （desktopoverlayhost* → encoderserver* → rtss*），值与原实现逐项一致。
    'rtss_processes': ['desktopoverlayhost.exe', 'desktopoverlayhost64.exe',
                       'desktopoverlayhostloader.exe', 'encoderserver.exe',
                       'encoderserver64.exe', 'rtss.exe', 'rtsshooksloader.exe',
                       'rtsshooksloader64.exe'],
    'game_processes': [],
    'game_roots': [],
    'exclude_processes': [],
    'exclude_dir_keywords': [],
    'exclude_name_keywords': [],
    'min_game_exe_mb': 0.2,
    'max_scan_depth': 5,
    'poll_interval_sec': 2.0,   # 空闲时节拍
    'poll_fast_sec': 0.4,       # 游戏会话 / 弹窗监视期节拍（退出检测更灵敏）
    'exit_grace_sec': 8,
    'close_wait_sec': 6,
    'force_close': True,
    'manage_rtss': True,
    'rescan_roots_sec': 300,
    'log_max_kb': 512,
    'game_lib': {
        'enabled': True,
        'rescan_hours': 6,          # 清单缓存多久重扫一次
        'steam': True,
        'epic': True,
        'wegame': True,
        'gog': True,
        'ubisoft': True,
    },
}

# ACE 清理（原生实现）默认值；四道闸门与 v2 的 ps1 一致
ACE_CLEANUP_DEFAULTS = {
    'enabled': True,
    'grace_sec': 180,           # 游戏退出后等多久才允许动手
    'min_ace_age_sec': 300,     # ACE 进程自身要活过多久
    'quiet_after_update_sec': 30,   # 见到 ACE 启动/更新组件后静默多久才允许动手
    'cooldown_sec': 600,        # 两次清理的最小间隔
    'eval_interval_sec': 60,    # 排队后多久复核一次
    'max_idle_sec': 1200,       # 排队最长维持多久
    'dry_run': False,
    'ace_processes': ['SGuard64.exe', 'SGuardSvc64.exe', 'ACE-Service64.exe',
                      'ACE-Tray.exe', 'ACE-Helper.exe', 'ACE-Base64.exe'],
    'ace_services': ['AntiCheatExpert Service', 'AntiCheatExpert Protection'],
    'ace_dirs': [r'C:\Program Files\AntiCheatExpert', r'C:\Windows\system32\drivers'],
    'blockers': ['SGuardUpdate64.exe', 'ACE-Setup64.exe', 'TASLogin.exe',
                 'TP3Helper.exe', 'GameLoader.exe', 'ACE-Helper.exe'],
}
DEFAULTS['ace_cleanup'] = dict(ACE_CLEANUP_DEFAULTS)

# 游戏退出后的弹窗拦截（腾讯系直播/赛事/推荐）。
# 实测取证：WeGame 系游戏的弹窗宿主是游戏目录下 clientbrowser2\ClientBrowser.exe，
# 类名 #32770、无标题，靠窗口标题抓不到，所以主手段是按 exe 名 + 路径片段
# 结束宿主进程树；protect_names 里的常驻主干（wegame.exe 等）绝不碰。
POPUP_DEFAULTS = {
    'enabled': True,
    'delay_sec': 0,            # arm() 已立即清进程，窗口层不再额外等
    'watch_sec': 90,           # 监视持续多久，过期自动收摊
    'scan_interval_sec': 0,    # 0 = 跟随守护主循环，不额外节流
    # 取证：弹窗窗口比"游戏全部退出"早约 7 秒就显示。游戏还在跑时先关窗口，
    # 不动进程（ClientBrowser 仍承载游戏内嵌页面），进程树留到退出后清。
    'early_close_enabled': True,
    'early_max_tries': 8,      # WM_CLOSE 无效时最多重试几轮，之后交给 sweep
    'kill_tree': True,         # 结束宿主进程及其 CEF 子进程树
    'process_names': ['clientbrowser.exe'],
    'path_hints': ['\\clientbrowser2\\'],
    'title_keywords': ['直播', '赛事', 'zhibo', 'live'],
    'path_keywords': ['wegame', 'txgame', 'wegameapps', 'tencent', 'tcls'],
    # 常驻聊天/安全主干一律保护：它们的窗口也常带"直播 / 赛事 / 推荐"字样，
    # 一旦被当成弹窗宿主结束，用户会直接掉线。
    'protect_names': ['explorer.exe', 'wegame.exe', 'wegame_env.exe',
                      'tcls_core.exe', 'tgame.exe',
                      'qq.exe', 'wechat.exe', 'weixin.exe', 'tim.exe',
                      'qqprotect.exe'],
    'log_all_new': False,      # 默认不把浏览器/任务栏窗口记成"新窗口"噪声
    # v2.13.0：观察模式。只判定、只记录（进程名/标题/pid/规则），不发 WM_CLOSE、
    # 不结束进程 —— 怀疑误拦时先开一轮观察，看日志里"本应关闭谁"再决定。
    'observe_only': False,
}
DEFAULTS['popup_guard'] = dict(POPUP_DEFAULTS)

# ACE 运行时限速。守护 / 设置面板 / 自检必须都从这里取默认值：
# config 缺段时若各写各的 .get(..., 默认)，会出现「面板显示开、守护实际关」。
ACE_THROTTLE_DEFAULTS = {
    'enabled': True,
    'processes': ['SGuard64.exe', 'SGuardSvc64.exe', 'ACE-Service64.exe'],
    'update_processes': ['SGuardUpdate64.exe', 'SGuardUpdate.exe',
                         'ACE-Update.exe', 'ACE-Update64.exe'],
    'delay_sec': 20,
    'io_priority': 0,
    'affinity_last_core': False,
}
DEFAULTS['ace_throttle'] = dict(ACE_THROTTLE_DEFAULTS)
