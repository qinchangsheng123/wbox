# -*- coding: utf-8 -*-
"""游戏库识别：读各平台自己的安装清单，不扫全盘。

思路与开源实现（GameShift、OBS Game Detector、EqualGames/game-scanner、
go-gamelocator）一致：Steam 读 libraryfolders.vdf + appmanifest_*.acf，
Epic 读 LauncherInstalled.dat，GOG / Ubisoft 读注册表，WeGame 读目录清单。
识别结果 = 「游戏目录 + 主 exe 名」，运行时用进程名/路径命中，
比"在 game_roots 里找体积大的 exe"精确，也快得多。

所有读取都是只读；结果缓存到 games_cache.json，默认 6 小时重扫一次。
"""
import json
import os
import re
import time

CACHE_NAME = 'games_cache.json'

# 目录/文件名里出现这些词一律不当游戏（启动器、运行库、反作弊、工具）
DEFAULT_EXCLUDE_NAME = [
    'uninstall', 'unins', 'setup', 'install', 'redist', 'vc_redist', 'dxsetup',
    'crash', 'report', 'launcher', 'updater', 'update', 'bootstrap', 'helper',
    'repair', 'diagnostics', 'config', 'benchmark', 'editor', 'server',
    'anticheat', 'easyanti', 'battleye', 'sguard', 'ace-', 'tp3', 'tgp',
    'wallpaper', 'cefprocess', 'cefsharp', 'webhelper', 'dotnet', 'vcredist',
    'python', 'node', 'ffmpeg', 'obs', 'steam', 'epicwebhelper', 'wegame',
    'clientbrowser', 'browser', 'apputil', 'webview', 'crashhandler',
    'd3dconfig', 'tcls', 'tenprotect', 'taslogin', 'redist', 'vconsole',
    'console', 'benchmark',
]


def _norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def _skip_dir(path):
    """公共运行库/基准测试目录不是游戏。"""
    low = (path or '').lower()
    if not os.path.isdir(path):
        return True
    base = os.path.basename(low.rstrip('\\/'))
    return any(k in base for k in _SKIP_DIRS)


def _expand(p):
    # vdf / 注册表里常写成 G:\\SteamLibrary 或带引号，统一还原成普通路径
    p = (p or '').strip().strip('"').replace('\\\\', '\\')
    return os.path.expandvars(p)


_HEX_NAME = re.compile(r'^[0-9a-f]{16,}$')
_SKIP_DIRS = ('steamworks shared', 'benchmark tool', 'steam library', 'redistribut',
              'wallpaper_engine')


# ---------- Steam ----------
def _steam_roots():
    roots = []
    try:
        import winreg
        for hive, path, value in (
            (winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamPath'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Valve\Steam', 'InstallPath'),
        ):
            try:
                with winreg.OpenKey(hive, path) as k:
                    v = winreg.QueryValueEx(k, value)[0]
                    if v:
                        roots.append(_expand(v))
            except OSError:
                continue
    except ImportError:
        pass
    return roots


def _vdf_paths(text):
    """从 libraryfolders.vdf 里抠出所有 "path" 值。"""
    return [_expand(m) for m in re.findall(r'"path"\s*"([^"]+)"', text)]


def scan_steam(limit_libs=16):
    games = []
    libs = []
    for root in _steam_roots():
        vdf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            try:
                with open(vdf, 'r', encoding='utf-8', errors='replace') as f:
                    libs.extend(_vdf_paths(f.read()))
            except OSError:
                pass
        libs.append(root)
    seen = set()
    for lib in libs[:limit_libs]:
        lib = lib.rstrip('\\/')
        if not lib or lib.lower() in seen:
            continue
        seen.add(lib.lower())
        apps_dir = os.path.join(lib, 'steamapps')
        if not os.path.isdir(apps_dir):
            continue
        try:
            names = os.listdir(apps_dir)
        except OSError:
            continue
        for fn in names:
            if not fn.lower().startswith('appmanifest_') or not fn.lower().endswith('.acf'):
                continue
            try:
                with open(os.path.join(apps_dir, fn), 'r', encoding='utf-8', errors='replace') as f:
                    txt = f.read()
            except OSError:
                continue
            m_dir = re.search(r'"installdir"\s*"([^"]+)"', txt)
            m_name = re.search(r'"name"\s*"([^"]+)"', txt)
            if not m_dir:
                continue
            gdir = os.path.join(apps_dir, 'common', m_dir.group(1))
            if not _skip_dir(gdir):
                nm = m_name.group(1) if m_name else m_dir.group(1)
                if _HEX_NAME.match(nm or ''):
                    nm = m_dir.group(1)
                games.append({'name': nm, 'dir': gdir, 'source': 'steam'})
    return games


# ---------- Epic ----------
def scan_epic():
    games = []
    dat = r'C:\ProgramData\Epic\UnrealEngineLauncher\LauncherInstalled.dat'
    if not os.path.isfile(dat):
        return games
    try:
        with open(dat, 'r', encoding='utf-8-sig', errors='replace') as f:
            data = json.load(f)
    except Exception:
        return games
    for item in data.get('InstallationList', []) or []:
        loc = _expand(item.get('InstallLocation'))
        if _skip_dir(loc):
            continue
        nm = item.get('AppName') or ''
        base = os.path.basename(loc.rstrip('\\/'))
        if not nm or _HEX_NAME.match(nm) or len(base) >= 4:
            nm = base if len(base) >= 4 else (nm or base)
        games.append({'name': nm, 'dir': loc, 'source': 'epic'})
    return games


# ---------- GOG / Ubisoft ----------
def _reg_subkeys(hive, path, view=0):
    out = []
    try:
        import winreg
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as k:
            n = winreg.QueryInfoKey(k)[0]
            for i in range(n):
                try:
                    out.append(winreg.EnumKey(k, i))
                except OSError:
                    break
    except (OSError, ImportError):
        pass
    return out


def _reg_values(hive, path, view=0):
    vals = {}
    try:
        import winreg
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ | view) as k:
            n = winreg.QueryInfoKey(k)[1]
            for i in range(n):
                try:
                    name, value, _ = winreg.EnumValue(k, i)
                    vals[name.lower()] = value
                except OSError:
                    break
    except (OSError, ImportError):
        pass
    return vals


def scan_gog():
    import winreg
    games = []
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        base = r'SOFTWARE\GOG.com\Games'
        for sub in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, base, view):
            vals = _reg_values(winreg.HKEY_LOCAL_MACHINE, base + '\\' + sub, view)
            path = _expand(vals.get('path'))
            if path and os.path.isdir(path):
                games.append({'name': vals.get('gamename') or sub,
                              'dir': path, 'source': 'gog'})
    return games


def scan_ubisoft():
    import winreg
    games = []
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        base = r'SOFTWARE\Ubisoft\Launcher\Installs'
        for sub in _reg_subkeys(winreg.HKEY_LOCAL_MACHINE, base, view):
            vals = _reg_values(winreg.HKEY_LOCAL_MACHINE, base + '\\' + sub, view)
            path = _expand(vals.get('installdir'))
            if path and os.path.isdir(path):
                games.append({'name': os.path.basename(path.rstrip('\\/')),
                              'dir': path, 'source': 'ubisoft'})
    return games


# ---------- WeGame / 腾讯 ----------
def scan_wegame(extra_roots=None):
    """WeGame 没有可靠的全局清单，按目录约定收集：<盘>:\\WeGameApps\\rail_apps\\<游戏>。"""
    games = []
    roots = []
    skip_children = ('rail_apps', 'rail_files', 'rail_user_data', 'common_apps',
                     'downloading', 'version', 'logs')
    for d in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        for cand in ('%s:\\WeGameApps' % d,
                     '%s:\\Program Files\\WeGameApps' % d,
                     '%s:\\Program Files (x86)\\WeGameApps' % d):
            if os.path.isdir(cand):
                roots.append(cand)
    # 配置里手填的库根，只有本身就是 WeGame 目录时才当来源，避免把 Steam 库当成 WeGame
    for r in (extra_roots or []):
        if r and os.path.basename(r.rstrip('\\/')).lower() == 'wegameapps' \
                and os.path.isdir(r):
            roots.append(r)

    seen = set()
    for root in roots:
        key = os.path.normcase(os.path.normpath(root))
        if key in seen:
            continue
        seen.add(key)
        candidates = []
        rail = os.path.join(root, 'rail_apps')
        if os.path.isdir(rail):
            try:
                candidates += [os.path.join(rail, n) for n in os.listdir(rail)]
            except OSError:
                pass
        try:
            for n in os.listdir(root):
                if n.lower() in skip_children:
                    continue
                candidates.append(os.path.join(root, n))
        except OSError:
            pass
        for gdir in candidates:
            if _skip_dir(gdir):
                continue
            games.append({'name': os.path.basename(gdir.rstrip('\\/')),
                          'dir': gdir, 'source': 'wegame'})
    return games


# ---------- 主 exe 识别 ----------
def _pick_exe(game_dir, name, min_mb=0.2, max_depth=3):
    """在游戏目录里选最像主程序的 exe：名字像游戏名、在 bin/x64 下、体积大者优先。"""
    want = _norm(name) or _norm(os.path.basename(game_dir.rstrip('\\/')))
    # 词首字母缩写：Counter-Strike 2 -> cs2，Path of Exile -> poe
    initials = _norm(''.join(w[0] for w in re.findall(r'[A-Za-z0-9]+', name or '')))
    best = None
    best_score = -999
    base_depth = game_dir.rstrip('\\/').count(os.sep)
    for dp, dirs, files in os.walk(game_dir):
        if dp.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
        low_dp = dp.lower()
        for fn in files:
            if not fn.lower().endswith('.exe'):
                continue
            low = fn.lower()
            if any(k in low for k in DEFAULT_EXCLUDE_NAME):
                continue
            full = os.path.join(dp, fn)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if size < min_mb * 1048576:
                continue
            stem = _norm(os.path.splitext(fn)[0])
            score = 0
            if stem and want and (stem == want or stem.startswith(want) or want.startswith(stem)):
                score += 5
            elif stem and want and stem in want:
                score += 3
            elif initials and stem == initials:
                score += 4
            if '\\win64' in low_dp or '\\x64' in low_dp or 'x64_' in low_dp \
                    or low_dp.endswith('\\win64'):
                score += 3
            elif '\\binaries\\' in low_dp or '\\bin\\' in low_dp:
                score += 1
            if score > best_score or (score == best_score and best
                                      and size > os.path.getsize(best)):
                best_score, best = score, full
    return best


def platform_roots(cfg=None):
    """换机器部署时用它生成 game_roots：Steam 各库的 common、WeGameApps 目录。"""
    roots = []
    libs = []
    for root in _steam_roots():
        vdf = os.path.join(root, 'steamapps', 'libraryfolders.vdf')
        if os.path.isfile(vdf):
            try:
                with open(vdf, 'r', encoding='utf-8', errors='replace') as f:
                    libs.extend(_vdf_paths(f.read()))
            except OSError:
                pass
        libs.append(root)
    for lib in libs:
        common = os.path.join(lib.rstrip('\\/'), 'steamapps', 'common')
        if os.path.isdir(common):
            roots.append(common)
    for d in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        for cand in ('%s:\\WeGameApps' % d,
                     '%s:\\Program Files\\WeGameApps' % d,
                     '%s:\\Program Files (x86)\\WeGameApps' % d):
            if os.path.isdir(cand):
                roots.append(cand)
    out, seen = [], set()
    for r in roots:
        k = os.path.normcase(os.path.normpath(r))
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def scan(cfg=None, log=None, min_mb=0.2):
    """扫全部平台，返回 {'games': [...], 'sources': {...}}。"""
    cfg = cfg or {}
    gl = cfg.get('game_lib') or {}
    games = []
    sources = {}
    tasks = []
    if gl.get('steam', True):
        tasks.append(('steam', scan_steam))
    if gl.get('epic', True):
        tasks.append(('epic', scan_epic))
    if gl.get('gog', True):
        tasks.append(('gog', scan_gog))
    if gl.get('ubisoft', True):
        tasks.append(('ubisoft', scan_ubisoft))
    if gl.get('wegame', True):
        tasks.append(('wegame', lambda: scan_wegame(cfg.get('game_roots'))))
    for label, fn in tasks:
        try:
            got = fn()
        except Exception as e:
            if log:
                log('游戏库扫描 %s 失败: %r' % (label, e))
            got = []
        for g in got:
            g['exe'] = _pick_exe(g['dir'], g['name'], min_mb)
            if g['exe']:
                games.append(g)
        sources[label] = len(got)

    # 同一 exe 只留一条（Steam 与 Epic 重复安装、同路径不同写法都合并）
    uniq = {}
    for g in games:
        key = os.path.normcase(os.path.normpath(g.get('exe') or ''))
        if key and key not in uniq:
            uniq[key] = g
        elif key and key in uniq:
            # 优先保留名字更人类可读的那条（不是哈希、不是目录名）
            old = uniq[key]
            if len(g['name']) > len(old['name']) and not _HEX_NAME.match(g['name']):
                uniq[key] = g
    games = sorted(uniq.values(), key=lambda g: g['name'].lower())
    return {'games': games, 'sources': sources, 'scanned_at': time.time()}


def summary(lib):
    games = (lib or {}).get('games') or []
    src = (lib or {}).get('sources') or {}
    parts = ['%s %d' % (k, v) for k, v in sorted(src.items()) if v]
    return '%d 个游戏（%s）' % (len(games), '，'.join(parts) if parts else '无来源')


def _cache_path(base):
    return os.path.join(base, CACHE_NAME)


def load(cfg, base, log=None, force=False):
    """缓存优先；过期或 force 时重扫。缓存坏了就当没有。"""
    gl = cfg.get('game_lib') or {}
    path = _cache_path(base)
    if not gl.get('enabled', True):
        return {'games': [], 'sources': {}, 'scanned_at': 0}
    hours = float(gl.get('rescan_hours', 6))
    if not force and os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if time.time() - float(data.get('scanned_at', 0)) < hours * 3600:
                return data
        except Exception:
            pass
    lib = scan(cfg, log)
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(lib, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception as e:
        if log:
            log('游戏库缓存写入失败: %r' % (e,))
    if log:
        log('游戏库识别：%s' % summary(lib))
    return lib
