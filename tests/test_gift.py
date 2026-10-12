# -*- coding: utf-8 -*-
"""gift 子命令单测: 查看列表/设置序号/越界与只读模式(monkeypatch 掉网络与输入)"""
import pytest

from wuxia_autoclaim import ams, cli, config


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("WUXIA_DATA_DIR", raising=False)
    config.set_data_dir(tmp_path)
    yield tmp_path
    config.set_data_dir(None)


def _resp():
    return {"flowRet": {"iRet": "0"},
            "modRet": {"jData": {"data": {
                "week": 2, "isTodaySign": 1,
                "giftNameArr": [{"giftId": 11, "name": "铸神令"},
                                {"giftId": 22, "name": "修为丹"}],
                "toGetGiftCount": 0}}}}


def _seed():
    config.save_roles({"defaults": {"gift_index": 3}, "roles": [
        {"name": "甲", "uin": "100", "area": "一区", "roleid": "r1",
         "cookies": {"skey": "x", "uin": "o100"}}]})


def _args(argv):
    return cli.build_parser().parse_args(["gift"] + argv)


def test_gift_set_index(monkeypatch, capsys):
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())
    assert cli.cmd_gift(_args(["--index", "2"])) == 0
    data = config.load_roles()
    assert data["roles"][0]["gift_index"] == 2
    out = capsys.readouterr().out
    assert "修为丹" in out


def test_gift_index_out_of_range(monkeypatch, capsys):
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())
    assert cli.cmd_gift(_args(["--index", "5"])) == 1       # 只有 2 个物品
    assert "gift_index" not in config.load_roles()["roles"][0]


def test_gift_view_only_on_eof(monkeypatch, capsys):
    """无 --index 且无终端(管道/计划任务): 列表照看, 选择保持不变"""
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())

    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert cli.cmd_gift(_args([])) == 0
    assert "gift_index" not in config.load_roles()["roles"][0]
    out = capsys.readouterr().out
    assert "铸神令" in out and "修为丹" in out


def test_gift_interactive_pick(monkeypatch, capsys):
    """终端里选 1: 写入角色配置"""
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    assert cli.cmd_gift(_args([])) == 0
    assert config.load_roles()["roles"][0]["gift_index"] == 1


def test_gift_dead_creds(monkeypatch):
    _seed()
    monkeypatch.setattr(ams, "emit",
                        lambda *a, **k: {"flowRet": {"iRet": "101"}})
    assert cli.cmd_gift(_args(["--index", "1"])) == 1
    assert "gift_index" not in config.load_roles()["roles"][0]
