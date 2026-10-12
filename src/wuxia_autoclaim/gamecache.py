# -*- coding: utf-8 -*-
"""游戏缓存通道: 读天刀内嵌浏览器(QBrowser)的本地 cookie 库

自 wuxia-autosign harvest.py 移植。前提: 用户在游戏里打开过一次 wuxia 活动页,
ptlogin2 才会静默签发整套登录态写进该库(值明文)。仅适用于装了游戏的 Windows 机器,
作为 napcat/qqnt 两条主通道之外的补充。
"""
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from wuxia_autoclaim import config
from wuxia_autoclaim.util import extract_cookies

DEFAULT_CACHE = Path(r"E:\WeGameApps\天涯明月刀\QBrowser\QCache")


def cache_dir(cfg):
    """QCache 目录: config.game_dir(cfg)(含 WUXIA_GAME_DIR 覆盖) -> 默认路径"""
    v = config.game_dir(cfg)
    return Path(v) if v else DEFAULT_CACHE


def read_jar(cdir):
    """复制游戏 cookie 库后读取, 返回 (uin, jar); 库里没有登录态则 (None, {})"""
    src = Path(cdir) / "Cookies"
    if not src.exists():
        raise RuntimeError("没找到 %s (游戏目录不对? 用 config set game_dir 指定 QCache 目录)" % src)
    tmp = Path(tempfile.gettempdir()) / ("wuxia_jar_%d.db" % os.getpid())
    try:
        shutil.copyfile(src, tmp)
        db = sqlite3.connect(str(tmp))
        rows = db.execute("SELECT name, value, host_key FROM cookies").fetchall()
        db.close()
    except Exception as e:
        raise RuntimeError("读取游戏 cookie 库失败(%s)" % e)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    jar = extract_cookies(
        {"name": n, "value": v, "domain": h} for n, v, h in rows)
    return jar.get("uin", "").lstrip("o"), jar
