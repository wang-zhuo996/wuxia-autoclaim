# -*- coding: utf-8 -*-
"""ams 单测: 解析/探测(monkeypatch 掉网络)"""
from wuxia_autoclaim import ams


def _resp_0():
    return {"flowRet": {"iRet": "0"},
            "modRet": {"jData": {"data": {
                "week": 3, "isTodaySign": 0,
                "giftNameArr": [{"giftId": 11, "name": "A"}, {"giftId": 22, "name": "B"}],
                "toGetGiftCount": 0}}}}


def test_is_login_expired_variants():
    assert ams.is_login_expired({"flowRet": {"iRet": 0}}) == "0"
    assert ams.is_login_expired({"flowRet": {"iRet": "101"}}) == "101"
    assert ams.is_login_expired({"modRet": {"iRet": "99998"}}) == "99998"   # 无 flowRet 时兜底
    assert ams.is_login_expired({}) == "0"


def test_parse_init():
    week, signed, gifts, toget = ams.parse_init(_resp_0())
    assert (week, signed, toget) == (3, 0, 0)
    assert [g["name"] for g in gifts] == ["A", "B"]


def test_probe_ok(monkeypatch):
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp_0())
    ok, desc = ams.probe({"skey": "x"}, {"area": "a", "roleid": "r"})
    assert ok and desc == "iRet=0"


def test_probe_unbound_area_counts_alive(monkeypatch):
    monkeypatch.setattr(ams, "emit", lambda *a, **k: {"flowRet": {"iRet": "99998"}})
    ok, desc = ams.probe({"skey": "x"}, {})
    assert ok and desc == "iRet=99998"


def test_probe_dead_and_neterror(monkeypatch):
    monkeypatch.setattr(ams, "emit", lambda *a, **k: {"flowRet": {"iRet": "101"}})
    assert ams.probe({"skey": "x"}, {})[0] is False

    def boom(*a, **k):
        raise OSError("timeout")
    monkeypatch.setattr(ams, "emit", boom)
    ok, desc = ams.probe({"skey": "x"}, {})
    assert not ok and "接口异常" in desc
