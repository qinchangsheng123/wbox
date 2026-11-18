# -*- coding: utf-8 -*-
"""皮肤包校验与安装的回归测试（临时脚本，见 tests 目录另议）。"""
import json
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'src'))
import skins
import skin_package

TMP = 'D:/06-Temp/skintest'
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP)

GOOD = {
    "id": "test-skin", "name": "测试皮肤", "version": "1.0.0", "api": 1,
    "engine": "flat", "performance": "minimal", "chrome": "system",
    "metrics": {"row": 40, "font": 13, "icon": 16, "titlebar": 40},
    "radius": {"window": 0, "card": 0, "row": 0},
    "light": dict(skins._PLAIN_LIGHT),
}


def pack(name, mutate=None, extra=None):
    data = json.loads(json.dumps(GOOD))
    if mutate:
        mutate(data)
    path = os.path.join(TMP, name)
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('skin.json', json.dumps(data, ensure_ascii=False))
        for member, blob in (extra or []):
            z.writestr(member, blob)
    return path


def expect_reject(label, path):
    try:
        skin_package.inspect(path)
    except skin_package.SkinPackageError as e:
        print('ok   %-12s -> %s' % (label, e))
        return
    print('FAIL %-12s 没被拒' % label)


p_good = pack('good.wbskin')
print('ok   good         ->', skin_package.inspect(p_good)['id'])
expect_reject('带 py', pack('code.wbskin', extra=[('evil.py', 'print(1)')]))
expect_reject('缺 token', pack('token.wbskin', lambda d: d['light'].pop('press')))
expect_reject('路径穿越', pack('zip.wbskin', extra=[('../x.png', b'x')]))
_nest = os.path.join(TMP, 'nest.wbskin')
with zipfile.ZipFile(_nest, 'w') as _z:
    _z.writestr('subset/skin.json', json.dumps(GOOD, ensure_ascii=False))
expect_reject('套一层目录', _nest)
# 注：assets 目录是合法的，只有「根目录不是 skin.json」才算套层
print('ok   assets 目录    ->', skin_package.inspect(
    pack('assets.wbskin', lambda d: (d.__setitem__('performance', 'standard'),
         d.__setitem__('assets', {'backdrop': 'assets/b.png'})),
         extra=[('assets/b.png', b'x')]))['id'])
expect_reject('行高超限', pack('row.wbskin', lambda d: d['metrics'].__setitem__('row', 60)))
expect_reject('id 撞插件', pack('clash.wbskin', lambda d: d.__setitem__('id', 'ace')))
expect_reject('minimal 带 assets', pack('asset.wbskin', lambda d: d.__setitem__(
    'assets', {'backdrop': 'assets/b.png'}), extra=[('assets/b.png', b'x')]))

base = os.path.join(TMP, 'app')
os.makedirs(base)
s = skin_package.install_zip(p_good, base, log=print)
print('ok   installed    ->', s.id, s.name, 'tokens', len(s.tokens('light')))
print('ok   list         ->', [x.id for x in skins.all_skins(base)])
try:
    skin_package.install_zip(p_good, base)
    print('FAIL 重复安装没被拒')
except skin_package.SkinPackageError as e:
    print('ok   重复安装      ->', e)
skin_package.remove_skin('test-skin', base)
print('ok   removed      ->', [x.id for x in skins.all_skins(base)])
try:
    skin_package.remove_skin('plain', base)
    print('FAIL 内置皮肤被删了')
except skin_package.SkinPackageError as e:
    print('ok   内置不可删    ->', e)

# 老配置迁移：只有 ui_theme、没有 ui_skin
for cfg in ({'ui_theme': 'dark'}, {'ui_theme': 'light'}, {},
            {'ui_skin': 'liquid-glass', 'ui_appearance': 'dark'},
            {'ui_skin': 'not-exist', 'ui_appearance': 'light'}):
    skin, app = skins.resolve(cfg, base=base)
    print('ok   resolve %-42s -> %s / %s' % (cfg, skin.id, app))
