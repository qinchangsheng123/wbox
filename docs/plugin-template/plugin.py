# -*- coding: utf-8 -*-
"""示例插件（Hello World 级）：可在 WBOX v2.11.0 上直接加载运行。

用途：作为新插件的起点。它只做三件事，不改动任何系统状态：
  1. 会话开始（idle -> active）时写一行日志，并记住本局开始时刻；
  2. 会话结束（grace -> idle）时写一行"本局持续多久"，并把局数累计进私有状态；
  3. 每轮 tick 更新面板菜单里那行摘要（ctx.set_status）。

安装方式（复制目录后必须改名，目录名 = manifest.id）：
    xcopy /E /I docs\\plugin-template "%ProgramFiles%\\WBOX\\plugins\\demo_hello"
    echo 1 > "%ProgramFiles%\\WBOX\\plugins_reload.flag"

然后打开面板，菜单「已安装插件」里会出现「示例插件」一行。

注意（规范 §1）：插件运行在管理员守护进程内部，完全可信、无沙箱。
本模板刻意不调用任何系统 API，就是要让第一次跑起来的插件不会出事。
"""
from plugin_api import WBOXPlugin

# 私有状态的磁盘文件名（落在 plugins\<id>\data\stats.json，由宿主代建目录）
STATE_NAME = 'stats'


class Plugin(WBOXPlugin):
    """最小实现：默认配置 + 三个钩子。"""

    # 配置默认值的**权威来源**。manifest.config.defaults 只是兜底，
    # 不要改 src/core_defaults.py 来放插件默认值（那是随 exe 冻结的内核默认值）。
    DEFAULT_CONFIG = {
        'demo_hello_enabled': True,
        'demo_hello': {
            'log_ticks': False,     # 调试用：每轮都写日志会刷爆 guard.log
        },
    }

    # 与 manifest.api 保持一致；不一致时宿主只记日志、以 manifest 为准。
    API = 1

    def __init__(self, ctx):
        WBOXPlugin.__init__(self, ctx)     # 必须：否则 self.ctx 不存在
        # 全部字段给默认值：任何钩子都可能在本插件"还没收到 on_config"时被调用
        self.enabled = True
        self.log_ticks = False
        self.session_started_at = 0.0
        self.tick_count = 0
        self.sessions = 0

    # ---------------------------------------------------------------- 配置
    def on_config(self, cfg):
        """首次加载 + 每次 config.json 变化后调用。

        cfg 是本插件自己那一份（只含 manifest 声明的路径 + DEFAULT_CONFIG），
        看不到也改不了内核键。要读内核键用 self.ctx.cfg('game_processes')。
        """
        self.enabled = bool(cfg.get('demo_hello_enabled', True))
        seg = cfg.get('demo_hello')
        if not isinstance(seg, dict):
            seg = {}
        self.log_ticks = bool(seg.get('log_ticks', False))

    # ---------------------------------------------------------------- 生命周期
    def on_load(self):
        """加载完成（migrate / on_config 之后）调用一次，可做一次性的目录检查。"""
        stats = self.ctx.read_state(STATE_NAME, {})
        if isinstance(stats, dict):
            self.sessions = int(stats.get('sessions', 0) or 0)
        self.ctx.log('示例插件已加载（累计 %d 局）' % self.sessions)
        self.ctx.set_status('累计 %d 局' % self.sessions)

    def on_start(self):
        """守护主循环开始前调用一次（guard.pid 已写、stop.flag 已清）。"""
        self.ctx.log('守护已就绪，示例插件待命中')

    # ---------------------------------------------------------------- 会话
    def on_session_start(self, tick):
        """idle -> active。tick.procs 为 None 表示本轮快照失败，别当空表用。"""
        if tick.procs is None:
            return
        self.session_started_at = tick.now
        games = ', '.join(tick.games) or '未识别'
        self.ctx.log('本局开始，检测到：%s' % games)
        self.ctx.set_status('本局进行中')

    def on_session_resume(self, tick):
        """grace -> active：宽限期内游戏闪断后回来了，是同一个会话，不重置状态。"""
        self.ctx.log('游戏在宽限期内恢复，沿用本局记录')

    def on_session_grace(self, tick):
        """active -> grace：游戏全退了，进入 exit_grace_sec 宽限期。"""
        self.ctx.log('游戏已全部退出，进入宽限期')

    def on_session_close(self, tick):
        """grace -> idle：宽限到期且游戏确实没回来。真正干"收尾"的地方。"""
        procs = self.ctx.procs()
        if procs is None:
            # 取不到进程表：推迟 2 秒再发一次，绝不当成"没有进程"处理
            self.ctx.retry_close(2)
            return
        lasted = 0.0
        if self.session_started_at:
            lasted = max(0.0, self.ctx.now() - self.session_started_at)
        self.sessions += 1
        self.ctx.log('本局结束，持续 %.0f 秒（累计 %d 局）' % (lasted, self.sessions))
        # 私有状态：原子写 plugins\<id>\data\stats.json，不进分发包
        self.ctx.write_state(STATE_NAME, {'sessions': self.sessions,
                                          'last_duration_sec': round(lasted, 1)})
        self.ctx.set_status('累计 %d 局' % self.sessions)

    # ---------------------------------------------------------------- 每轮
    def on_tick(self, tick):
        """每轮调用，**必然最后调用**，且在 on_session_* 之后。

        这里禁止 time.sleep()（会拖慢退出检测），也不要抛异常（会降级）。
        """
        self.tick_count += 1
        if self.log_ticks:
            self.ctx.log('tick #%d state=%s games=%s'
                         % (tick.seq, tick.session.state, ','.join(tick.games) or '-'))
        if not self.enabled:
            self.ctx.set_status('已关闭')
            return
        if tick.session.state == 'active':
            self.ctx.set_status('本局进行中 · tick %d' % self.tick_count)

    # ---------------------------------------------------------------- 退出
    def on_exit(self, reason):
        """reason ∈ {'guard_stop', 'reload', 'fatal'}。禁止阻塞超过 2 秒。"""
        self.ctx.log('插件退出（%s），累计 %d 局' % (reason, self.sessions))

    # ---------------------------------------------------------------- 自检
    def on_self_test(self, out):
        """--self-test 时参与诊断输出（out 是 list，append 纯文本行）。"""
        out.append('demo_hello      : sessions=%d ticks=%d enabled=%s'
                   % (self.sessions, self.tick_count, self.enabled))
