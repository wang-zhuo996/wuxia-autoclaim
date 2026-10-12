# -*- coding: utf-8 -*-
"""纯函数工具: cookie 挑选/签名/脱敏 (自 wuxia-autosign autosign.py 移植)"""
from pathlib import Path

# 发给 AMS 的 cookie, 按此顺序拼接: uin/skey 是鉴权核心,
# p_uin/p_skey/pt4_token 是 game.qq.com 域的长期正本, 保留给后续自愈刷新用
COOKIE_KEEP = ("uin", "skey", "RK", "ptcz", "p_uin", "p_skey", "pt4_token")


def extract_cookies(cookie_list):
    """从 cookie 列表(元素含 name/value/domain 三键, playwright state 或
    http.cookiejar 均可套用)里挑出需要的键值。

    milo 会把 skey/uin 同时写到 .qq.com 和当前域(值相同), 这里 .qq.com 域优先去重。
    """
    jar = {}
    for c in cookie_list or []:
        n, v, dom = c.get("name"), c.get("value", ""), c.get("domain", "")
        if n in COOKIE_KEEP and v:
            if n not in jar or dom == ".qq.com":
                jar[n] = v
    return jar


def cookie_header(jar):
    return "; ".join("%s=%s" % (n, jar[n]) for n in COOKIE_KEEP if n in jar)


def calc_gtk(skey):
    """g_tk = DJB33X hash(skey)"""
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
        h &= 0xFFFFFFFF
    return h & 0x7FFFFFFF


def mask(s, head=3, tail=2):
    """脱敏: QQ号/角色ID 只留头尾, 防止日志泄露账号"""
    s = str(s)
    return s if len(s) <= head + tail else s[:head] + "*" * (len(s) - head - tail) + s[-tail:]


def secure_write(path, text):
    """UTF-8 写文件; POSIX 下顺手收紧权限到 600(凭据文件用)"""
    p = Path(path)
    p.write_text(text, "utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass                   # Windows 无 POSIX 权限, 忽略
