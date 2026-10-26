# -*- coding: utf-8 -*-
"""run_role 周末行为单测: 领取成功/被拒/无待领, 周末不尝试许愿(monkeypatch 掉网络)"""
from wuxia_autoclaim import ams, claim

ROLE = {"name": "甲", "uin": "100", "area": "一区", "roleid": "r1",
        "cookies": {"skey": "x", "uin": "o100"}}


def _resp(week, signed=0, toget=0):
    return {"flowRet": {"iRet": "0"},
            "modRet": {"jData": {"data": {
                "week": week, "isTodaySign": signed,
                "giftNameArr": [{"giftId": 11, "name": "铸神令"}],
                "toGetGiftCount": toget}}}}


def _no_wish(get_resp=None, week=6, toget=1):
    """返回 (calls, emit 替身): INIT 按参数返回, GET 走 get_resp, SIGN 一律视为非法调用"""
    calls = []

    def emit(cookie, gtk, flow_id, sarea, srole, extra=None):
        calls.append(flow_id)
        if flow_id == ams.FLOW_INIT:
            return _resp(week, toget=toget)
        if flow_id == ams.FLOW_GET and get_resp is not None:
            return get_resp
        raise AssertionError("不该调 flow %s" % flow_id)

    return calls, emit


def test_weekend_claim_ok(monkeypatch):
    """周六领取成功: 只调 INIT+GET, 不许愿, 消息可推送"""
    calls, emit = _no_wish({"flowRet": {"iRet": "0", "sMsg": "领取成功"}})
    monkeypatch.setattr(claim, "emit", emit)
    ok, msg = claim.run_role(dict(ROLE), {})
    assert ok and "周末领取: 领取成功" in msg
    assert calls == [ams.FLOW_INIT, ams.FLOW_GET]


def test_weekend_claim_rejected(monkeypatch):
    """服务端要求先进游戏: 整个角色算失败, 让推送提醒人工处理"""
    _, emit = _no_wish({"flowRet": {"iRet": "1001", "sMsg": "少侠请先登录游戏再领取奖励"}})
    monkeypatch.setattr(claim, "emit", emit)
    ok, msg = claim.run_role(dict(ROLE), {})
    assert not ok and "请先登录游戏" in msg


def test_weekend_nothing_to_claim(monkeypatch):
    """周日无待领: 只调 INIT, 带"无需操作"静默标记不许愿"""
    calls, emit = _no_wish(week=7, toget=0)
    monkeypatch.setattr(claim, "emit", emit)
    ok, msg = claim.run_role(dict(ROLE), {})
    assert ok and "周末无待领" in msg and "无需操作" in msg
    assert calls == [ams.FLOW_INIT]


def test_weekday_signed_no_push_marker(monkeypatch):
    """工作日已许愿: 消息带"无需操作"(runner 据此静默推送)"""
    def emit(cookie, gtk, flow_id, sarea, srole, extra=None):
        return _resp(2, signed=1)
    monkeypatch.setattr(claim, "emit", emit)
    ok, msg = claim.run_role(dict(ROLE), {})
    assert ok and "今日已许愿" in msg and "无需操作" in msg
