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


def _resp(signed=1):
    return {"flowRet": {"iRet": "0"},
            "modRet": {"jData": {"data": {
                "week": 2, "isTodaySign": signed,
                "giftNameArr": [{"giftId": 11, "name": "铸神令"},
                                {"giftId": 22, "name": "修为丹"}],
                "toGetGiftCount": 0}}}}


def _seed():
    config.save_roles({"roles": [
        {"name": "甲", "uin": "100", "area": "一区", "roleid": "r1",
         "cookies": {"skey": "x", "uin": "o100"}}]})


def _override():
    return (config.load_config().get("gift_override") or {}).get("100")


def _args(argv):
    return cli.build_parser().parse_args(["gift"] + argv)


def test_gift_set_index(monkeypatch, capsys):
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())
    assert cli.cmd_gift(_args(["--index", "2"])) == 0
    assert _override() == 2
    assert "gift_index" not in config.load_roles()["roles"][0]   # roles.json 不再存序号
    out = capsys.readouterr().out
    assert "修为丹" in out


def test_gift_index_out_of_range(monkeypatch, capsys):
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())
    assert cli.cmd_gift(_args(["--index", "5"])) == 1       # 只有 2 个物品
    assert _override() is None


def test_gift_view_only_on_eof(monkeypatch, capsys):
    """无 --index 且无终端(管道/计划任务): 列表照看, 选择保持不变"""
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())

    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert cli.cmd_gift(_args([])) == 0
    assert _override() is None
    out = capsys.readouterr().out
    assert "铸神令" in out and "修为丹" in out


def test_gift_interactive_pick(monkeypatch, capsys):
    """终端里选 1: 写入 gift_override"""
    _seed()
    monkeypatch.setattr(ams, "emit", lambda *a, **k: _resp())
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    assert cli.cmd_gift(_args([])) == 0
    assert _override() == 1


def test_gift_dead_creds(monkeypatch):
    _seed()
    monkeypatch.setattr(ams, "emit",
                        lambda *a, **k: {"flowRet": {"iRet": "101"}})
    assert cli.cmd_gift(_args(["--index", "1"])) == 1
    assert _override() is None


def test_run_role_gift_priority(monkeypatch):
    """run_role 的奖励序号取值链: 注入 role["gift_index"] > gift_override[uin] > gift_index > 4"""
    from wuxia_autoclaim import claim
    wishes = []

    def fake_emit(cookie, gtk, flow_id, sarea, srole, extra=None):
        if flow_id == ams.FLOW_INIT:
            return _resp(signed=0)
        wishes.append(extra)
        return {"flowRet": {"iRet": "0"}, "modRet": {"sPackageName": "ok"}}

    monkeypatch.setattr(claim, "emit", fake_emit)    # claim 是 from-import, 要打模块属性
    role = {"name": "甲", "uin": "100", "area": "一区", "roleid": "r1",
            "cookies": {"skey": "x", "uin": "o100"}}

    def wish(r, cfg):
        wishes.clear()
        ok, _ = claim.run_role(dict(role, **r), cfg)
        assert ok
        return wishes[0]["giftId"]

    assert wish({"gift_index": 1}, {"gift_index": 2,
                                    "gift_override": {"100": 2}}) == 11  # 注入最优先
    assert wish({}, {"gift_index": 1, "gift_override": {"100": 2}}) == 22  # override 压默认
    assert wish({}, {"gift_index": 1}) == 11                            # 默认序号生效
    assert wish({}, {}) == 22                                           # 兜底 4 越界收敛到最后一个
