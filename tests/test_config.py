# -*- coding: utf-8 -*-
"""config 单测: 目录解析/通道顺序/环境变量覆盖/upsert_role/读写回环"""
import json

import pytest

from wuxia_autoclaim import config


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """每个用例独立数据目录, 用后还原"""
    monkeypatch.delenv("WUXIA_DATA_DIR", raising=False)
    monkeypatch.delenv("WUXIA_GAME_DIR", raising=False)
    config.set_data_dir(tmp_path)
    yield tmp_path
    config.set_data_dir(None)


def test_env_data_dir_wins(monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("WUXIA_DATA_DIR", str(other))
    assert config.default_data_dir() == other


def test_channel_order():
    cfg = {}
    assert config.channel_order(cfg) == ["napcat", "qqnt", "game"]     # 默认
    assert config.channel_order(cfg, "qqnt,napcat") == ["qqnt", "napcat"]
    assert config.channel_order(cfg, "junk,game") == ["game"]          # 非法项丢弃
    assert config.channel_order(cfg, "junk") == ["napcat", "qqnt", "game"]
    cfg2 = {"channel_order": ["game"]}
    assert config.channel_order(cfg2) == ["game"]


def test_apply_env(monkeypatch):
    d = {"gift_index": 3, "push_key": ""}
    monkeypatch.setenv("WUXIA_GIFT_INDEX", "5")
    monkeypatch.setenv("WUXIA_PUSH_KEY", "SCTxxx")
    forced = config.apply_env(d)
    assert forced == {"gift_index", "push_key"}
    assert d == {"gift_index": 5, "push_key": "SCTxxx"}

    monkeypatch.setenv("WUXIA_GIFT_INDEX", "abc")                      # 非数字忽略
    monkeypatch.delenv("WUXIA_PUSH_KEY")
    d2 = {"gift_index": 3, "push_key": ""}
    assert config.apply_env(d2) == set()
    assert d2["gift_index"] == 3


def test_upsert_role():
    jar1 = {"skey": "a", "uin": "o100"}
    data = {"defaults": {}, "roles": []}
    config.upsert_role(data, "微信1区", "r1", "玩家甲", jar1, "100")
    assert data["roles"][0]["name"] == "玩家甲"

    # 同 QQ 新角色: 追加, 且老角色 cookie 一并换新
    jar2 = {"skey": "b", "uin": "o100"}
    config.upsert_role(data, "微信2区", "r2", "玩家乙", jar2, "100")
    assert len(data["roles"]) == 2
    assert data["roles"][0]["cookies"]["skey"] == "b"

    # 同 (uin, roleid): 原地更新
    jar3 = {"skey": "c", "uin": "o100"}
    config.upsert_role(data, "微信3区", "r2", None, jar3, "100")
    assert len(data["roles"]) == 2
    r2 = data["roles"][1]
    assert r2["area"] == "微信3区" and r2["name"] == "玩家乙" and r2["cookies"]["skey"] == "c"


def test_roles_roundtrip():
    assert config.load_roles() is None                    # 空目录
    data = {"defaults": {"gift_index": 2}, "roles": [
        {"name": "甲", "uin": "100", "area": "一区", "roleid": "r1", "cookies": {"skey": "x"}}]}
    config.save_roles(data)
    back = config.load_roles()
    assert back["roles"][0]["name"] == "甲"
    assert back["defaults"]["gift_index"] == 2

    (config.data_dir() / "roles.json").write_text("{bad json", "utf-8")
    with pytest.raises(config.ConfigError):
        config.load_roles()


def test_config_load_merge():
    cfg = config.load_config()                            # 无文件 -> 全默认
    assert cfg["napcat"] == [] and cfg["auto_bind"] is False
    cfg["napcat"] = [{"url": "http://127.0.0.1:3000", "token": "t"}]
    cfg["unknown_key"] = 1
    config.save_config(cfg)
    back = config.load_config()
    assert back["napcat"][0]["url"] == "http://127.0.0.1:3000"
    assert back["unknown_key"] == 1                       # 未知键透传保留
    assert back["game_dir"] == ""


def test_game_dir_env_overrides(monkeypatch):
    monkeypatch.setenv("WUXIA_GAME_DIR", "D:/games/QCache")
    assert config.game_dir({"game_dir": "E:/other"}) == "D:/games/QCache"
    monkeypatch.delenv("WUXIA_GAME_DIR")
    assert config.game_dir({"game_dir": "E:/other"}) == "E:/other"
