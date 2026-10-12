# -*- coding: utf-8 -*-
"""纯函数单测: cookie 挑选/签名/脱敏"""
from wuxia_autoclaim.util import calc_gtk, cookie_header, extract_cookies, mask, secure_write


def test_calc_gtk_vectors():
    assert calc_gtk("") == 5381                     # DJB33X 初值
    assert calc_gtk("@abc123") == 733459617
    assert calc_gtk("测试Skey") == 1537884001


def test_calc_gtk_wraps_32bit():
    # 长输入下中间值会越过 32 位, 确认没有溢出成负数/大数
    v = calc_gtk("x" * 200)
    assert 0 <= v <= 0x7FFFFFFF


def test_cookie_header_keeps_order_and_skips_missing():
    jar = {"skey": "S", "uin": "o123", "zzz": "ignored", "p_skey": "P"}
    h = cookie_header(jar)
    parts = h.split("; ")
    assert parts[0] == "uin=o123"
    assert parts[1] == "skey=S"
    assert parts[2] == "p_skey=P"
    assert "RK=" not in h and "ptcz=" not in h and "zzz" not in h


def test_extract_cookies_prefers_qq_domain():
    cl = [
        {"name": "skey", "value": "web", "domain": "wuxia.qq.com"},
        {"name": "skey", "value": "dot", "domain": ".qq.com"},
        {"name": "p_skey", "value": "P", "domain": ".game.qq.com"},
        {"name": "junk", "value": "x", "domain": ".qq.com"},
        {"name": "uin", "value": "", "domain": ".qq.com"},          # 空值忽略
    ]
    jar = extract_cookies(cl)
    assert jar == {"skey": "dot", "p_skey": "P"}


def test_mask():
    assert mask("12345") == "12345"                # 长度 == head+tail, 原样
    assert mask("123456") == "123*56"              # 头3 + *1 + 尾2
    assert mask("ab") == "ab"                      # 短串原样
    assert mask("123456789", 3, 2) == "123****89"


def test_secure_write(tmp_path):
    p = tmp_path / "secret.json"
    secure_write(p, "你好")
    assert p.read_text("utf-8") == "你好"
