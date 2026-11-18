# -*- coding: utf-8 -*-
"""示例插件（离线测试夹具，不参与生产加载）。

放在 test\\fixtures\\plugins\\ 下，测试会把它整棵拷到临时目录再加载，
所以本目录保持只读、不落任何运行期文件。
"""
from plugin_api import WBOXPlugin

# 测试用它断言钩子确实被调用了（模块级列表，加载后由测试读取）
CALLS = []


class Plugin(WBOXPlugin):
    DEFAULT_CONFIG = {
        'demo_sample_enabled': True,
        'demo_sample': {
            'greeting': 'hello',
            'times': 3,
        },
    }

    def migrate(self, raw):
        # 只有自己是配置主人时才搬段：改名 demo_old -> demo_sample
        if 'demo_old' in raw and 'demo_sample' not in raw:
            raw['demo_sample'] = raw.pop('demo_old')

    def on_config(self, cfg):
        # 记下整份 cfg：测试用它断言宿主只给了本插件声明过的键
        CALLS.append(('on_config', dict(cfg)))

    def on_load(self):
        CALLS.append(('on_load', self.ctx.id))

    def on_start(self):
        CALLS.append(('on_start', self.ctx.watch_names() is not None))

    def on_tick(self, tick):
        CALLS.append(('on_tick', tick.seq, tick.session.state))

    def on_session_start(self, tick):
        CALLS.append(('on_session_start', tick.seq))

    def on_session_resume(self, tick):
        CALLS.append(('on_session_resume', tick.seq))

    def on_session_grace(self, tick):
        CALLS.append(('on_session_grace', tick.seq))

    def on_session_close(self, tick):
        CALLS.append(('on_session_close', tick.seq))

    def on_exit(self, reason):
        CALLS.append(('on_exit', reason))

    def gui_menu_rows(self, cfg):
        return [{'text': '示例插件', 'icon': 'log', 'hint': '', 'action': None}]

    def on_self_test(self, out):
        out.append('demo_sample: greeting=%s' %
                   self.ctx.cfg('demo_sample.greeting', '(缺)'))
