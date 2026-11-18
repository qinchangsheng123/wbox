# -*- coding: utf-8 -*-
"""皮肤层：token 模型、内置极简档、配置解析。

设计约束见 `docs/SKIN-SPEC.md`。三条要记住的：

1. **皮肤是纯数据**，不含任何可执行代码。这里只读 json，不做 eval/import。
2. **tkinter 不认带 alpha 的颜色**（`#RRGGBBAA` 会 TclError）。所以落地前必须
   把 8 位色按 alpha 合成到「舞台色」上，换成不透明 `#RRGGBB`。合成顺序固定：
   `glass` 先叠到舞台 → 其余 token 再叠到 glass 的结果。玻璃档因此是
   「预合成的磨砂色」，不是真透视——Tk 没有逐像素透明，这是硬限制，别在
   代码里假装能做到。
3. **内置皮肤写死在代码里**，不读外部文件：内置皮肤随 exe 走版本，不需要
   经过包校验；用户皮肤才是外部数据，走 `skin_package.py` 的校验。
   v2.10.0 起内置只发极简一档（产品决定：先打磨极简）；standard 引擎保留，
   第三方皮肤仍可声明 standard 走圆角 / 浮层 / 动效骨架。

token 一共 26 个（不是 25——规范初稿数漏了 `glass_edge`，这里以实际为准）。
"""
import json
import os
import re

# 26 个颜色 token，名字与语义固定，皮肤只能改值。缺任意一个 → 拒绝加载。
TOKENS = (
    'fg', 't2', 't3', 't4',
    'hair', 'hair_strong',
    'fill', 'fill_2', 'press',
    'inset',
    'glass', 'glass_hi', 'glass_line', 'glass_edge',
    'switch_off', 'knob', 'on', 'on_knob',
    'icon_bg', 'icon_fg',
    'menu_base', 'pop_base',
    'amb0', 'amb1', 'amb2', 'amb3',
)

# 落地时忽略的 token（只给设计稿用）。
DESIGN_ONLY = ('amb0', 'amb1', 'amb2', 'amb3')

# 合成舞台：桌面平均色。玻璃档的半透明色都叠在它上面变成不透明色。
# 换一个桌面壁纸，实际观感会偏一点点，这是「预合成」路线的固有代价。
STAGE = {'light': '#EDEDF1', 'dark': '#101014'}

API_VERSION = 1
ID_RE = re.compile(r'^[a-z][a-z0-9-]{1,31}$')
# 功能插件占用的 id，皮肤不许重名（否则设置页两张卡片同名，用户分不清）
PLUGIN_IDS = frozenset(('ace', 'afterburner', 'gamepp', 'popup', 'desktop_hud'))

METRIC_KEYS = ('row', 'font', 'icon', 'titlebar')
RADIUS_KEYS = ('window', 'card', 'row')
ENGINES = ('flat', 'bitmap')
PERF = ('minimal', 'standard')
CHROME = ('system', 'custom')

# ---- 内置皮肤 -------------------------------------------------------------
# 值直接抄自 design/wbox-skins.html 的 CSS 变量，两边对不上就是设计稿被改过
# 而代码没跟，改的时候一起改。
# v2.10.0：液态玻璃不再内置（用户决策：先去掉玻璃、打磨极简）。_GLASS_* 令牌
# 随之删除；standard 档的渲染骨架仍在 settings_gui，第三方皮肤可声明使用。
_PLAIN_LIGHT = {
    'fg': '#000000', 't2': '#595959', 't3': '#767676', 't4': '#A3A3A3',
    'hair': '#E6E6E6', 'hair_strong': '#CFCFCF',
    'fill': '#F0F0F0', 'fill_2': '#F7F7F7', 'press': '#E6E6E6',
    'inset': '#FAFAFA',
    'glass': '#FFFFFF', 'glass_hi': '#FFFFFF',
    'glass_line': '#E6E6E6', 'glass_edge': '#B0B0B0',
    'switch_off': '#C9C9C9', 'knob': '#FFFFFF',
    'on': '#111111', 'on_knob': '#FFFFFF',
    'icon_bg': '#111111', 'icon_fg': '#FFFFFF',
    'menu_base': '#FFFFFF', 'pop_base': '#FFFFFF',
    'amb0': '#FFFFFF', 'amb1': '#F5F5F5', 'amb2': '#E8E8E8', 'amb3': '#FAFAFA',
}
_PLAIN_DARK = {
    'fg': '#F2F2F2', 't2': '#B3B3B3', 't3': '#8C8C8C', 't4': '#5E5E5E',
    'hair': '#242424', 'hair_strong': '#333333',
    'fill': '#1E1E1E', 'fill_2': '#181818', 'press': '#2A2A2A',
    'inset': '#141414',
    'glass': '#111111', 'glass_hi': '#111111',
    'glass_line': '#2A2A2A', 'glass_edge': '#3A3A3A',
    'switch_off': '#3A3A3A', 'knob': '#EDEDED',
    'on': '#EDEDED', 'on_knob': '#111111',
    'icon_bg': '#FFFFFF', 'icon_fg': '#000000',
    'menu_base': '#111111', 'pop_base': '#111111',
    'amb0': '#0A0A0A', 'amb1': '#141414', 'amb2': '#000000', 'amb3': '#111111',
}

BUILTIN = (
    {
        'id': 'plain', 'name': '极简', 'version': '1.1.0', 'api': 1,
        'engine': 'flat', 'performance': 'minimal', 'chrome': 'custom',
        'author': 'WBOX', 'description': '方角纯色，零位图零动画',
        # v2.10.0 打磨：行高 44→40、标题栏 40→36，窗口基准宽 640→480
        # （settings_gui.PLAIN_W），整屏更紧凑；字号 13 保持可读下限。
        'metrics': {'row': 40, 'font': 13, 'icon': 16, 'titlebar': 36},
        'radius': {'window': 0, 'card': 0, 'row': 0},
        'light': _PLAIN_LIGHT, 'dark': _PLAIN_DARK,
    },
)
# v2.10.0 起默认即极简（唯一内置）。FALLBACK 同 id：选的皮肤不存在 /
# 加载失败时兜底，保证面板永远不会因为皮肤问题变白板。
DEFAULT_ID = 'plain'
FALLBACK_ID = 'plain'


class SkinError(Exception):
    """皮肤不合格 / 加载失败，消息可直接展示给用户。"""


# ---- 颜色工具 -------------------------------------------------------------

def _parse(color):
    """'#RRGGBB' / '#RRGGBBAA' -> (r, g, b, a)，a 是 0..1。非法抛 SkinError。"""
    if not isinstance(color, str):
        raise SkinError('颜色必须是字符串：%r' % (color,))
    s = color.strip()
    if not s.startswith('#'):
        raise SkinError('颜色必须以 # 开头：%r' % color)
    body = s[1:]
    if len(body) == 3:                       # #RGB 展开
        body = ''.join(ch * 2 for ch in body)
    if len(body) not in (6, 8):
        raise SkinError('颜色长度必须是 6 或 8 位十六进制：%r' % color)
    try:
        vals = [int(body[i:i + 2], 16) for i in range(0, len(body), 2)]
    except ValueError:
        raise SkinError('颜色含非法十六进制字符：%r' % color)
    if len(vals) == 3:
        vals.append(255)
    return vals[0], vals[1], vals[2], vals[3] / 255.0


def _hex(rgb):
    return '#%02X%02X%02X' % rgb


def _over(fg, bg):
    """fg（可带 alpha）叠到 bg（不透明 rgb 三元组）上，返回不透明 rgb 三元组。"""
    r, g, b, a = _parse(fg)
    if a >= 1.0:
        return (r, g, b)
    return (int(round(r * a + bg[0] * (1 - a))),
            int(round(g * a + bg[1] * (1 - a))),
            int(round(b * a + bg[2] * (1 - a))))


def flatten_tokens(raw, appearance='light'):
    """原始 token（可能带 alpha）-> tkinter 能直接用的不透明色。

    glass 先叠到舞台色，其余 token 再叠到 glass 的结果上——这个顺序不能反：
    反过来会把 fill（本来就该浮在窗口底上）算到桌面色上，浅色主题看不出差别，
    深色主题下所有填充都会偏亮一档。
    """
    stage = _parse(STAGE.get(appearance, STAGE['light']))[:3]
    base = _over(raw['glass'], stage)
    out = {}
    for key, val in raw.items():
        if key in DESIGN_ONLY:
            continue
        if key == 'glass':
            out[key] = _hex(base)
        elif key == 'glass_hi':
            # 顶部高光在 Tk 里没有渐变承载，退化成「比窗口底亮一点」的纯色
            out[key] = _hex(_over(val, base))
        else:
            out[key] = _hex(_over(val, base))
    return out


# ---- 皮肤对象 -------------------------------------------------------------

class Skin(object):
    """一套皮肤。`tokens(appearance)` 返回的是**已合成**的不透明色。"""

    def __init__(self, data, path=None, builtin=False):
        self.path = path
        self.builtin = builtin
        self.id = data['id']
        self.name = data['name']
        self.version = data['version']
        self.api = data['api']
        self.engine = data['engine']
        self.performance = data['performance']
        self.chrome = data['chrome']
        self.author = data.get('author') or ''
        self.description = data.get('description') or ''
        self.metrics = dict(data['metrics'])
        self.radius = dict(data['radius'])
        self.assets = dict(data.get('assets') or {})
        self._light = dict(data['light'])
        self._dark = dict(data.get('dark') or data['light'])
        self._cache = {}

    def tokens(self, appearance='light'):
        key = 'dark' if appearance == 'dark' else 'light'
        if key not in self._cache:
            self._cache[key] = flatten_tokens(
                self._dark if key == 'dark' else self._light, key)
        return dict(self._cache[key])

    @property
    def perf_label(self):
        return '最省' if self.performance == 'minimal' else '标准'

    def metric(self, key, scale=1.0, dpi_scale=None):
        """把 skin.json 里的逻辑 px 换算成当前 DPI 的设备像素。"""
        base = self.metrics.get(key, 0)
        s = dpi_scale if dpi_scale is not None else scale
        return max(1, int(round(base * s)))

    def __repr__(self):
        return '<Skin %s v%s %s/%s>' % (self.id, self.version, self.engine,
                                        self.performance)


# ---- 校验 -----------------------------------------------------------------

def validate(data, origin='skin.json', expect_id=None):
    """七项校验里与 json 有关的部分（zip 层在 skin_package 做）。"""
    if not isinstance(data, dict):
        raise SkinError('%s 顶层必须是对象' % origin)
    for key in ('id', 'name', 'version', 'api', 'engine', 'performance',
                'chrome', 'metrics', 'radius', 'light'):
        if key not in data:
            raise SkinError('%s 缺少必填字段 %s' % (origin, key))
    sid = data['id']
    if not isinstance(sid, str) or not ID_RE.match(sid):
        raise SkinError('id 必须是小写字母开头、只含小写字母数字连字符：%r' % (sid,))
    if expect_id and sid != expect_id:
        raise SkinError('id (%s) 与目录名 (%s) 不一致' % (sid, expect_id))
    if sid in PLUGIN_IDS:
        raise SkinError('id %s 与功能插件重名' % sid)
    if not isinstance(data['name'], str) or not data['name'].strip():
        raise SkinError('name 必须是非空字符串')
    if len(data['name']) > 12:
        raise SkinError('name 不能超过 12 个字')
    if not isinstance(data['version'], str) or not re.match(r'^\d+\.\d+\.\d+$', data['version']):
        raise SkinError('version 必须是三段式 X.Y.Z：%r' % (data['version'],))
    api = data['api']
    if isinstance(api, bool) or not isinstance(api, int) or api <= 0:
        raise SkinError('api 必须是正整数')
    if api > API_VERSION:
        raise SkinError('皮肤要求 api=%d，当前只支持 api=%d' % (api, API_VERSION))
    if data['engine'] not in ENGINES:
        raise SkinError('engine 只能是 %s' % ' / '.join(ENGINES))
    if data['performance'] not in PERF:
        raise SkinError('performance 只能是 %s' % ' / '.join(PERF))
    if data['chrome'] not in CHROME:
        raise SkinError('chrome 只能是 %s' % ' / '.join(CHROME))

    metrics = data['metrics']
    if not isinstance(metrics, dict):
        raise SkinError('metrics 必须是对象')
    for key in METRIC_KEYS:
        val = metrics.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise SkinError('metrics.%s 必须是正整数' % key)
    radius = data['radius']
    if not isinstance(radius, dict):
        raise SkinError('radius 必须是对象')
    for key in RADIUS_KEYS:
        val = radius.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise SkinError('radius.%s 必须是非负整数' % key)

    for key in ('light', 'dark'):
        if key not in data:
            continue
        block = data[key]
        if not isinstance(block, dict):
            raise SkinError('%s 必须是对象' % key)
        missing = [t for t in TOKENS if t not in block]
        if missing:
            raise SkinError('%s 缺少 %d 个 token：%s' % (key, len(missing), ', '.join(missing)))
        for tok in TOKENS:
            _parse(block[tok])              # 格式不对直接抛

    # performance 档位与声明必须自洽：minimal 不许有位图，行高也不许超过 44
    if data['performance'] == 'minimal':
        if data.get('assets'):
            raise SkinError('performance=minimal 的皮肤不许带 assets')
        if metrics.get('row', 0) > 44:
            raise SkinError('performance=minimal 的行高不得超过 44')
    elif data['performance'] == 'standard' and metrics.get('row', 0) > 56:
        raise SkinError('performance=standard 的行高不得超过 56')
    return True


def from_dict(data, path=None, builtin=False):
    validate(data)
    return Skin(data, path=path, builtin=builtin)


# ---- 加载 -----------------------------------------------------------------

def builtin_skins():
    return [Skin(d, builtin=True) for d in BUILTIN]


def skins_dir(base):
    return os.path.join(base, 'skins')


def load_dir(path, expect_id=None):
    """读一个皮肤目录（`<base>/skins/<id>/skin.json`）。"""
    manifest = os.path.join(path, 'skin.json')
    if not os.path.isfile(manifest):
        raise SkinError('缺少 skin.json：%s' % path)
    try:
        with open(manifest, 'r', encoding='utf-8-sig') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise SkinError('skin.json 读不出来：%r' % (e,))
    if expect_id is None:
        expect_id = os.path.basename(os.path.normpath(path))
    validate(data, expect_id=expect_id)
    return Skin(data, path=path, builtin=False)


def all_skins(base, log=None):
    """内置在前、用户在后。单个用户皮肤坏掉只跳过它，不连累其它。"""
    out = builtin_skins()
    root = skins_dir(base)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        try:
            out.append(load_dir(sub, expect_id=name))
        except SkinError as e:
            if log:
                log('皮肤 %s 加载失败：%s' % (name, e))
    return out


MANIFEST_NAME = 'skin.json'


def _read_meta(d):
    """读目录里 skin.json 的原始 meta；读不到返回 None（不抛）。"""
    try:
        with open(os.path.join(d, MANIFEST_NAME), 'r', encoding='utf-8-sig') as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else None
    except Exception:
        return None


def catalog(base=None, log=None):
    """皮肤全目录（含「api 过新」条目），供面板外观卡片用。

    与 all_skins 的唯一差别：api > API_VERSION 的皮肤不静默丢弃，而是返回
    skin=None / reason='api' 的条目，让面板画「需要更新」卡（SKIN-SPEC §10：
    不能假装它不存在）。其余坏皮肤照旧记日志跳过。
    """
    def entry(s, builtin, reason=''):
        return {'skin': s, 'builtin': builtin, 'reason': reason, 'id': s.id,
                'name': s.name, 'version': s.version,
                'description': s.description, 'api': s.api}

    found = [entry(Skin(d, builtin=True), True) for d in BUILTIN]
    if not base:
        return found
    root = skins_dir(base)
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return found
    for name in names:
        sub = os.path.join(root, name)
        if not os.path.isdir(sub):
            continue
        try:
            s = load_dir(sub, expect_id=name)
        except SkinError as e:
            if log:
                log('皮肤 %s 加载失败：%s' % (name, e))
            raw = _read_meta(sub)
            if raw and isinstance(raw.get('api'), int) \
                    and not isinstance(raw.get('api'), bool) \
                    and raw['api'] > API_VERSION:
                found.append({'skin': None, 'builtin': False, 'reason': 'api',
                              'id': name, 'name': raw.get('name') or name,
                              'version': raw.get('version') or '?',
                              'description': raw.get('description') or '',
                              'api': raw['api']})
            continue
        found = [x for x in found if x['id'] != s.id]
        found.append(entry(s, False))
    return found


def get(skin_id, base=None, log=None):
    """按 id 找皮肤；找不到返回 None（调用方决定回退）。"""
    if base:
        for s in all_skins(base, log=log):
            if s.id == skin_id:
                return s
    for s in builtin_skins():
        if s.id == skin_id:
            return s
    return None


def resolve(cfg, base=None, log=None):
    """从 config 解出 (skin, appearance)。

    老配置只有 `ui_theme`（light/dark），没有 `ui_skin`：明暗直接继承，
    皮肤拿默认极简。v2.10.0 之前显式写过 ui_skin=liquid-glass 的老配置，
    因内置已移除会走「不存在 → 回退 plain」并留一行日志，界面不突变白板。
    """
    cfg = cfg or {}
    appearance = str(cfg.get('ui_appearance') or '').lower()
    if appearance not in ('system', 'light', 'dark'):
        # 老配置没有 ui_appearance，只有 ui_theme（light/dark）：直接继承，
        # 老用户升级后不该被系统主题改掉自己选过的明暗。
        legacy = str(cfg.get('ui_theme') or '').lower()
        appearance = legacy if legacy in ('light', 'dark') else 'system'
    if appearance == 'system':
        appearance = detect_appearance()
    sid = cfg.get('ui_skin')
    if not isinstance(sid, str) or not sid:
        sid = DEFAULT_ID
    skin = get(sid, base=base, log=log)
    if skin is None:
        if log:
            log('皮肤 %s 不存在，回退 %s' % (sid, FALLBACK_ID))
        skin = get(FALLBACK_ID) or builtin_skins()[-1]
    return skin, appearance


def detect_appearance():
    """读系统「应用主题」：0 深色 / 1 浅色。读不到就当浅色。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            val, _ = winreg.QueryValueEx(k, 'AppsUseLightTheme')
        return 'light' if int(val) else 'dark'
    except Exception:
        return 'light'
