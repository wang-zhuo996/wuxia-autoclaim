# -*- coding: utf-8 -*-
"""ptlogin URL 构造单测(不触网)"""
from wuxia_autoclaim.ptlogin import xlogin_url, jump_url, APPID_WUXIA, APPID_GAME


def test_xlogin_url():
    u = xlogin_url()
    assert u.startswith("https://xui.ptlogin2.qq.com/cgi-bin/xlogin?appid=%s" % APPID_WUXIA)
    assert "proxy_url=https%3A%2F%2Fgame.qq.com%2Fcomm-htdocs%2Fmilo%2Fproxy.html" in u
    assert "s_url=https%3A%2F%2Fwuxia.qq.com%2Fcp%2Fa20230309_98549%2Findex.html" in u


def test_jump_url():
    u = jump_url("123456", "CK+/==", APPID_GAME,
                 "https://game.qq.com/comm-htdocs/milo/proxy.html")
    assert u.startswith("https://ssl.ptlogin2.qq.com/jump?clientuin=123456")
    assert "clientkey=CK%2B%2F%3D%3D" in u          # 特殊字符要转义
    assert "u1=https%3A%2F%2Fgame.qq.com%2Fcomm-htdocs%2Fmilo%2Fproxy.html" in u
    assert "appid=%s" % APPID_GAME in u
    assert "daid=8" in u and "keyindex=19" in u
