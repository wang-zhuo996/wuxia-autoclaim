# -*- coding: utf-8 -*-
"""napcat 单测: OneBot HTTP 响应解析/错误分类/cookie 兜底解析(monkeypatch 掉网络)"""
import json
import urllib.error

import pytest

from wuxia_autoclaim import napcat
from wuxia_autoclaim.napcat import NapCatError, call, parse_cookie_str


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_parse_cookie_str():
    s = "uin=o123456; skey=@abc; p_skey=xyz"
    assert parse_cookie_str(s) == {"uin": "o123456", "skey": "@abc", "p_skey": "xyz"}
    assert parse_cookie_str("") == {}
    assert parse_cookie_str(None) == {}


def test_call_ok(monkeypatch):
    monkeypatch.setattr(napcat.urllib.request, "urlopen",
                        lambda req, timeout: FakeResp(
                            {"status": "ok", "retcode": 0,
                             "data": {"user_id": 123456, "nickname": "昵称"}}))
    data = call("http://127.0.0.1:3000", "T", "get_login_info")
    assert data["user_id"] == 123456


def test_call_sends_auth_and_body(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return FakeResp({"retcode": 0, "data": {}})

    monkeypatch.setattr(napcat.urllib.request, "urlopen", fake)
    call("http://127.0.0.1:3000", "secret", "get_clientkey")
    # OneBot 11 路径式: action 在 URL 路径, body 只装 params
    assert seen["url"] == "http://127.0.0.1:3000/get_clientkey"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"] == {}
    call("http://x/", "secret", "get_clientkey", {"domain": "qq.com"})
    assert seen["url"] == "http://x/get_clientkey"      # 尾斜杠不产生 //
    assert seen["body"] == {"domain": "qq.com"}


def test_call_errors(monkeypatch):
    # retcode != 0: action 不存在/掉线
    monkeypatch.setattr(napcat.urllib.request, "urlopen",
                        lambda req, timeout: FakeResp({"retcode": 1200, "message": "nope"}))
    with pytest.raises(NapCatError) as e:
        call("http://x", "", "get_clientkey")
    assert "retcode=1200" in str(e.value)

    # 401/403: token 错
    def raise_401(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
    monkeypatch.setattr(napcat.urllib.request, "urlopen", raise_401)
    with pytest.raises(NapCatError) as e:
        call("http://x", "bad", "get_clientkey")
    assert "token" in str(e.value)

    # 连不上
    def raise_url(req, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(napcat.urllib.request, "urlopen", raise_url)
    with pytest.raises(NapCatError) as e:
        call("http://x", "", "get_clientkey")
    assert "HTTP 服务" in str(e.value)


def test_harvest_no_endpoints(capsys):
    # 无端点配置: 记日志并返回 (0,0), 不抛
    assert napcat.harvest_napcat({"roles": []}, {}) == (0, 0)


def test_harvest_empty_login_info_logged(monkeypatch, capsys):
    # 端点在线但 get_login_info 回空 data(NapCat 4.x 对旧式调用就是这样):
    # 必须留日志, 不能静默空转
    monkeypatch.setattr(napcat, "call", lambda *a, **k: {})
    cfg = {"napcat": [{"url": "http://127.0.0.1:3000", "token": "t"}]}
    assert napcat.harvest_napcat({"roles": []}, cfg) == (0, 0)
    assert "返回空数据" in capsys.readouterr().out
