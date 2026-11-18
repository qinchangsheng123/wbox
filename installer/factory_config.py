# -*- coding: utf-8 -*-
"""出厂配置模板生成器（payload config.json 的单一来源）。

payload 里的 config.json 是安装时的"合并模板"：新机器拿它得出厂值，旧机器
只靠它补新增键。所以它不能带任何本机运行数据（afterburner_path / game_roots /
个人开关）。v2.11.0 实测踩过：build_exe.bat 把生产 app\\config.json 拷进构建
目录，payload 配置与生产逐字节相同，用户机器路径随每个版本包重复分发。

生成规则 = 运行期三段合并的前两段：内核 DEFAULTS -> 各内置插件 DEFAULT_CONFIG
（plugin_host.merge_defaults，用户段传空）。键序跟随 dict 插入序，输出可复现。
build_exe.bat 与 build_setup.py 都调本模块，两条路径产出的模板字节一致。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

import core_defaults  # noqa: E402
import plugin_host  # noqa: E402

# 说明性注释键（_ 开头）：安装合并时 merge_config 跳过用户旧值、始终用包内
# 文案，所以模板必须自带它们——v2.11.1 的模板没有注释键，部署把生产配置里
# 7 个注释键删光了（v2.11.2 修复）。文案跟随当前界面口径更新。
COMMENTS = {
    '_说明': '改完保存即生效，守护空闲每 2 秒、游戏会话期间每 0.4 秒热加载，'
             '无需重启（poll_interval_sec / poll_fast_sec）。新游戏装在 '
             'game_roots 下会自动识别；装在别处才需要往 game_processes 加进程名。',
    '_开关': '双击安装目录\\WBOX.exe 打开设置面板：列表页每个功能插件一行 + '
             '开关，鼠标停在名称上看简介，拨动开关即时写回本文件，守护 2 秒内'
             '热加载；左上角字标 WBOX 展开齿轮菜单（开机自启 / 收托盘 / 游戏结束'
             '提示 / 外观与皮肤 / 全局设置等），标题栏右侧「设置」同窗换页。'
             '对应配置：afterburner_enabled、ace_cleanup.enabled + '
             'ace_throttle.enabled（一个开关同时控制清理与限速）、'
             'popup_guard.enabled、ui_skin / ui_appearance。计划任务用 --guard '
             '参数启动常驻守护。其余：exit_grace_sec=退出宽限；manage_rtss='
             '连带关 RTSS；force_close=WM_CLOSE 未退出时强制结束。',
    '_ACE限速': 'ace_throttle 用 SetPriorityClass(IDLE) 降 CPU + '
                'NtSetInformationProcess(ProcessIoPriority) 降磁盘 IO 优先级，'
                '只改调度参数，不写内存、不注入、不加载驱动，可随时还原'
                '（PROCESS_MODE_BACKGROUND_BEGIN 跨进程会被系统拒绝，返回 87，'
                '故拆成两条通道）。processes=要限制的进程；update_processes='
                '出现即暂停限制（避免拖死升级）；delay_sec=进程出现后等多久才'
                '动手（避开启动自检）；io_priority=0 very-low / 1 low / 2 '
                'normal；affinity_last_core=是否再把 CPU 亲和性绑到最后一个核心。',
    '_排除': 'exclude_dir_keywords / exclude_name_keywords 同时用于目录自动发现'
             '和按路径判断，防腾讯反作弊常驻进程被误判成游戏。',
    '_ACE': '清理已内建（ace_native.py）：停 AntiCheatExpert 服务 + 结束残留'
            '进程，四道闸门全过才动手。grace_sec 退出宽限 / min_ace_age_sec '
            '保护期 / quiet_after_update_sec 见到更新组件后静默多久 / '
            'cooldown_sec 冷却；dry_run=true 只判定不动手。闸门 4 只看"最后一次'
            '观察到更新组件的时刻"，不再看文件 mtime。',
    '_游戏库': 'game_lib 读各平台自己的清单（Steam libraryfolders.vdf+'
               'appmanifest、Epic LauncherInstalled.dat、WeGame 目录、'
               'GOG/Ubisoft 注册表），结果缓存 games_cache.json，rescan_hours '
               '小时自动重扫；设置面板有"重扫游戏库"。',
    '_弹窗': 'popup_guard 按 exe 名 + 路径片段定位弹窗宿主（WeGame 系游戏为 '
             'clientbrowser2\\ClientBrowser.exe），process_names 与 path_hints '
             '必须同时命中才动手。取证发现窗口比「游戏全部退出」早约 7 秒就'
             '显示，所以分两段：会话期间 early_close_enabled=true 时窗口一露面'
             '就发 WM_CLOSE 提前拦截（只关窗口、不结束进程，内嵌页面不受影响；'
             'WM_CLOSE 无效最多试 early_max_tries 轮）；游戏全退时再结束它和'
             '整个 CEF 子进程树，watch_sec 秒内再冒头的也拦。protect_names 里的'
             '常驻主干和 conhost/csrss 等系统进程一律不杀。delay_sec / '
             'scan_interval_sec 默认 0，扫描跟随守护节拍；kill_tree=false 则只发 '
             'WM_CLOSE 关窗口。',
}
# 注释键插在其说明的段/键前面，读配置时注释贴着正文。
_ANCHOR = {
    'afterburner_enabled': '_开关',
    'ace_throttle': '_ACE限速',
    'exclude_processes': '_排除',
    'ace_cleanup': '_ACE',
    'game_lib': '_游戏库',
    'popup_guard': '_弹窗',
}


def factory_config(plugins_dir=None):
    plugins_dir = plugins_dir or os.path.join(ROOT, 'plugins')
    entries = [e for e in plugin_host.discover(ROOT, plugins_dir=plugins_dir)
               if e.status == 'ok']
    merged = plugin_host.merge_defaults(core_defaults.DEFAULTS, entries, {})
    out = {'_说明': COMMENTS['_说明']}
    for key, value in merged.items():
        comment = _ANCHOR.get(key)
        if comment:
            out[comment] = COMMENTS[comment]
        out[key] = value
    return out


def factory_bytes(plugins_dir=None):
    return (json.dumps(factory_config(plugins_dir), ensure_ascii=False, indent=2)
            + '\n').encode('utf-8')


def main(argv):
    out = None
    if '--out' in argv:
        out = argv[argv.index('--out') + 1]
    data = factory_bytes()
    if out:
        with open(out, 'wb') as f:
            f.write(data)
        print('factory config -> %s (%d B)' % (out, len(data)))
    else:
        sys.stdout.write(data.decode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))