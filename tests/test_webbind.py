# -*- coding: utf-8 -*-
"""webbind 单测: 大区表/ACI 解析、编码助手、绑定表单、编排分支(不触网)

ACI 夹具按真实响应结构合成(uin/roleid/名字均为编造), 编码方式与线上一致:
中文 percent 编码、分隔符/空格原样。
"""
import urllib.parse

import pytest

from wuxia_autoclaim import config, webbind


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """每个用例独立数据目录, 用后还原"""
    monkeypatch.delenv("WUXIA_DATA_DIR", raising=False)
    config.set_data_dir(tmp_path)
    yield tmp_path
    config.set_data_dir(None)


AREA_JS = u'''
if(!WUXIAServerSelect)
{
    var WUXIAServerSelect={};
}

WUXIAServerSelect.create=function(select_array, ext_opt_array, opt_array)
{
    return new MultiSelect.create(select_array, opt_array||WUXIAServerSelect.STD_DATA, ext_opt_array||[]);
}

WUXIAServerSelect.STD_DATA=
[

    {t:"青龙乱舞", v:"7609515", opt_data_array:[

        {t: "长生剑",v: "1001",status:"1", display:"1", opt_data_array:[]}
	,
        {t: "孔雀翎",v: "1002",status:"1", display:"1", opt_data_array:[]}

]}
,
    {t:"大地飞鹰", v:"7609516", opt_data_array:[

        {t: "凤凰集",v: "2001",status:"1", display:"1", opt_data_array:[]}

]}
,
    {t:"云上之城", v:"7609857", opt_data_array:[

        {t: "长生殿",v: "1701",status:"1", display:"1", opt_data_array:[]}

]}

]
'''


def aci_body(code=0):
    """按真实响应结构合成 ACI JSONP 文本"""
    raw_data = (u"_webplat_msg=4|1112223334445556666 测试一 2|1112223334445557777 测试二 1|"
                u"&_webplat_msg_code=%d&ban_end_time=0&ban_time=1792076164"
                u"&list=4|1112223334445556666 测试一 165 9 0|1112223334445557777 测试二 90 8 0|"
                u"&result=0&uin=10000&" % code)
    quoted = urllib.parse.quote(raw_data, safe=u"|=&_ .-*")
    names = urllib.parse.quote(u"测试一*测试二", safe=u"*")
    return ("var query_role_result={version:'v2.rel.x',retCode:0, serial_num:'AMS-WUXIA-x',"
            " data:'%s',msg:'success',"
            "checkparam:'wuxia|yes|10000|2001|1112223334445556666*1112223334445557777*||||%s|"
            "|2*1*|1792076165|',"
            "md5str:'ABCDEF0123456789ABCDEF0123456789',infostr:'',checkstr:'',"
            "user_id_in_game:''};" % (quoted, names))


# ---------------------------------------------------------------- 解析器/编码

def test_parse_area_js():
    tree = webbind.parse_area_js(AREA_JS)
    assert [(g["t"], g["v"]) for g in tree] == \
        [("青龙乱舞", "7609515"), ("大地飞鹰", "7609516"), ("云上之城", "7609857")]
    assert tree[0]["servers"][1] == {"t": "孔雀翎", "v": "1002"}
    assert tree[1]["servers"] == [{"t": "凤凰集", "v": "2001"}]


def test_parse_area_js_broken():
    with pytest.raises(webbind.WebBindError):
        webbind.parse_area_js("var something_else = 1;")


def test_parse_aci_roles():
    roles, extras = webbind.parse_aci(aci_body())
    assert [r["roleid"] for r in roles] == ["1112223334445556666", "1112223334445557777"]
    assert roles[0]["name"] == "测试一" and roles[0]["level"] == "165"
    assert roles[1]["name"] == "测试二" and roles[1]["level"] == "90"
    assert extras["md5str"] == "ABCDEF0123456789ABCDEF0123456789"
    assert extras["checkparam"].startswith("wuxia|yes|10000|2001|1112223334445556666*")


def test_parse_aci_no_roles():
    roles, extras = webbind.parse_aci(aci_body(code=1))
    assert roles == [] and extras["md5str"]      # 无角色也要带回 extras
    with pytest.raises(webbind.WebBindError):
        webbind.parse_aci("var totally_garbage = 1;")


def test_parse_binding():
    resp = {"modRet": {"jData": {"data": {"Farea": "2001", "FroleId": "123",
                                          "FroleName": "x"}}}}
    assert webbind.parse_binding(resp)["FroleId"] == "123"
    assert webbind.parse_binding({"modRet": {"jData": {}}}) is None
    assert webbind.parse_binding({"modRet": {"jData": {"data": {}}}}) is None


def test_uri_helpers():
    # encodeURI: | -> %7C, 已有的 % 再编一层, 中文转 percent
    assert webbind.uri("wuxia|yes") == "wuxia%7Cyes"
    assert webbind.uri("%E4%B8") == "%25E4%25B8"
    assert webbind.uri(u"凤凰集") == urllib.parse.quote(u"凤凰集", safe="")
    # 双重编码: 提交流的 sRoleName/sAreaName
    assert webbind.uri2(u"凤凰集") == urllib.parse.quote(
        urllib.parse.quote(u"凤凰集", safe=""), safe="")


def test_build_bind_body():
    s = webbind.build_bind_body("2001", u"凤凰集",
                                {"roleid": "1112223334445556666", "name": u"测试一"},
                                {"md5str": "M", "checkparam": "wuxia|yes"})
    assert s["query"] == "false"
    assert s["sRoleId"] == "1112223334445556666"
    assert s["sArea"] == "2001"
    assert s["ams_md5str"] == "M"
    assert s["ams_checkparam"] == "wuxia%7Cyes"
    assert s["sRoleName"] == webbind.uri2(u"测试一")
    assert s["sAreaName"] == webbind.uri2(u"凤凰集")
    assert "sPartition" not in s and "sCheckparam" not in s   # 真机抓包没有这两项


# ---------------------------------------------------------------- 编排(monkeypatch 掉网络)

JAR = {"uin": "o10000", "skey": "@x", "RK": "r", "ptcz": "p",
       "p_uin": "o10000", "p_skey": "ps", "pt4_token": "t"}


def test_bind_role_http_already_bound(monkeypatch):
    monkeypatch.setattr(webbind, "query_binding",
                        lambda c, g: {"Farea": "2001", "FroleId": "1112223334445556666",
                                      "FroleName": urllib.parse.quote(u"测试一")})
    monkeypatch.setattr(webbind, "_binding_alive", lambda c, g, a, r: True)
    data = {"roles": []}
    ok, msg = webbind.bind_role_http(JAR, "10000", data=data)
    assert ok and "有效绑定" in msg
    r = data["roles"][0]
    assert (r["area"], r["roleid"], r["name"]) == ("2001", "1112223334445556666", u"测试一")


def test_bind_role_http_stale_binding_noninteractive(monkeypatch):
    """合服后的陈旧绑定: 非交互时给出重绑指引"""
    monkeypatch.setattr(webbind, "query_binding",
                        lambda c, g: {"Farea": "2501", "FroleId": "10381113800437799006",
                                      "FroleName": "x"})
    monkeypatch.setattr(webbind, "_binding_alive", lambda c, g, a, r: False)
    data = {"roles": []}
    ok, msg = webbind.bind_role_http(JAR, "10000", data=data)
    assert not ok and "bind --uin 10000" in msg
    assert data["roles"] == []


def test_bind_role_http_unbound_noninteractive(monkeypatch):
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: None)
    data = {"roles": []}
    ok, msg = webbind.bind_role_http(JAR, "10000", data=data)
    assert not ok and "bind --uin 10000" in msg
    assert data["roles"] == []


def test_bind_role_http_interactive_flow(monkeypatch):
    seq = [None, {"Farea": "2001", "FroleId": "1112223334445556666",
                  "FroleName": urllib.parse.quote(u"测试一")}]      # 初查未绑定, 回查已绑定
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: seq.pop(0))
    monkeypatch.setattr(webbind, "roleauth", lambda c, **kw: None)
    monkeypatch.setattr(webbind, "fetch_area_tree",
                        lambda: [{"t": "大地飞鹰", "v": "7609516",
                                  "servers": [{"t": "凤凰集", "v": "2001"}]}])
    monkeypatch.setattr(webbind, "scan_all_roles", lambda c, t: [
        {"group": "大地飞鹰", "server": "凤凰集", "area": "2001",
         "roles": [{"roleid": "1112223334445556666", "name": u"测试一", "level": "165"}],
         "extras": {"md5str": "M", "checkparam": "C"}}])
    submitted = []

    def fake_submit(cookie, gtk, area, server, role, extras):
        submitted.append((area, server, role["roleid"]))
        return True, "iRet=0 ok"
    monkeypatch.setattr(webbind, "submit_bind", fake_submit)

    data = {"roles": []}
    seen = []

    def pick(entries, uin):
        seen.extend(entries)
        return entries[0]
    ok, msg = webbind.bind_role_http(JAR, "10000", data=data, pick_role=pick)
    assert ok and "绑定成功" in msg
    assert submitted == [("2001", "凤凰集", "1112223334445556666")]
    assert seen[0]["role"]["name"] == u"测试一"
    assert data["roles"][0]["roleid"] == "1112223334445556666"


# ---------------------------------------------------------------- bind 预设(config.yaml)

def write_bindmap(text):
    """把旧格式的预设文本(QQ号顶层)整体缩进一层包进 config.yaml 的 bind: 段"""
    body = "".join(("    " + line) if line.strip() else line
                   for line in text.splitlines(True))
    (config.data_dir() / "config.yaml").write_text(u"bind:\n" + body, "utf-8")


def patch_preset_net(monkeypatch, tree_servers=((u"凤凰集", "2001"),),
                     roles_by_area=None, submit_ok=True):
    """预设链路的通用桩: 大区表/角色列表/提交; scan_all_roles 被调到直接失败"""
    monkeypatch.setattr(webbind, "roleauth", lambda c, **kw: None)
    monkeypatch.setattr(webbind, "fetch_area_tree",
                        lambda: [{"t": u"大地飞鹰", "v": "7609516",
                                  "servers": [{"t": t, "v": v} for t, v in tree_servers]}])
    monkeypatch.setattr(webbind, "scan_all_roles",
                        lambda c, t: pytest.fail("预设绑定不应全服扫描"))

    def fake_roles(cookie, area):
        roles = roles_by_area.get(area, []) if roles_by_area is not None else \
            [{"roleid": "1112223334445556666", "name": u"测试一", "level": "165"}]
        return roles, {"md5str": "M", "checkparam": "C"}
    monkeypatch.setattr(webbind, "fetch_roles", fake_roles)
    submitted = []

    def fake_submit(cookie, gtk, area, server, role, extras):
        submitted.append((area, server, role["roleid"]))
        return submit_ok, "iRet=%s" % ("0" if submit_ok else "400")
    monkeypatch.setattr(webbind, "submit_bind", fake_submit)
    return submitted


def test_bind_preset_flow(monkeypatch):
    """未绑定 + 预设命中: 非交互直绑目标服务器, 不全服扫描"""
    write_bindmap(u"10000:\n    server: 凤凰集\n    role: 测试一\n")
    seq = [None, {"Farea": "2001", "FroleId": "1112223334445556666",
                  "FroleName": urllib.parse.quote(u"测试一")}]      # 初查未绑定, 回查已绑定
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: seq.pop(0))
    submitted = patch_preset_net(monkeypatch)
    data = {"roles": []}
    ok, msg = webbind.bind_role_http(JAR, "10000", data=data)      # pick_role=None 也成功
    assert ok and "绑定成功" in msg
    assert submitted == [("2001", u"凤凰集", "1112223334445556666")]
    assert (data["roles"][0]["area"], data["roles"][0]["name"]) == ("2001", u"测试一")


def test_bind_preset_bad_server(monkeypatch):
    write_bindmap(u"10000:\n    server: 不存在的服\n    role: 测试一\n")
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: None)
    submitted = patch_preset_net(monkeypatch)
    ok, msg = webbind.bind_role_http(JAR, "10000", data={"roles": []})
    assert not ok and "大区表" in msg
    assert submitted == []


def test_bind_preset_role_missing(monkeypatch):
    write_bindmap(u"10000:\n    server: 凤凰集\n    role: 测试一\n")
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: None)
    patch_preset_net(monkeypatch, roles_by_area={"2001": [
        {"roleid": "2", "name": u"别人", "level": "1"}]})
    ok, msg = webbind.bind_role_http(JAR, "10000", data={"roles": []})
    assert not ok and u"没有找到角色「测试一」" in msg and u"别人" in msg


def test_bind_preset_rebind_on_diff(monkeypatch):
    """有效绑定但角色与预设不符: 以预设为准重绑"""
    write_bindmap(u"10000:\n    server: 凤凰集\n    role: 测试一\n")
    seq = [{"Farea": "2001", "FroleId": "999", "FroleName": urllib.parse.quote(u"旧角色")},
           {"Farea": "2001", "FroleId": "1112223334445556666",
            "FroleName": urllib.parse.quote(u"测试一")}]
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: seq.pop(0))
    monkeypatch.setattr(webbind, "_binding_alive", lambda c, g, a, r: True)
    submitted = patch_preset_net(monkeypatch)
    data = {"roles": []}
    ok, msg = webbind.bind_role_http(JAR, "10000", data=data)
    assert ok and "绑定成功" in msg
    assert submitted == [("2001", u"凤凰集", "1112223334445556666")]
    assert data["roles"][0]["roleid"] == "1112223334445556666"


def test_bind_preset_same_binding_kept(monkeypatch):
    """有效绑定且角色与预设一致: 不重绑, 走补写"""
    write_bindmap(u"10000:\n    server: 凤凰集\n    role: 测试一\n")
    monkeypatch.setattr(webbind, "query_binding",
                        lambda c, g: {"Farea": "2001", "FroleId": "1112223334445556666",
                                      "FroleName": urllib.parse.quote(u"测试一")})
    monkeypatch.setattr(webbind, "_binding_alive", lambda c, g, a, r: True)
    submitted = patch_preset_net(monkeypatch)
    data = {"roles": []}
    ok, msg = webbind.bind_role_http(JAR, "10000", data=data)
    assert ok and "有效绑定" in msg
    assert submitted == []
    assert data["roles"][0]["name"] == u"测试一"


def test_bind_preset_stale_binding_rescued(monkeypatch):
    """合服失效的旧绑定 + 预设: 非交互也能直接重绑(不再要求终端交互)"""
    write_bindmap(u"10000:\n    server: 凤凰集\n    role: 测试一\n")
    seq = [{"Farea": "2501", "FroleId": "10381113800437799006", "FroleName": "x"},
           {"Farea": "2001", "FroleId": "1112223334445556666",
            "FroleName": urllib.parse.quote(u"测试一")}]
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: seq.pop(0))
    monkeypatch.setattr(webbind, "_binding_alive", lambda c, g, a, r: False)
    submitted = patch_preset_net(monkeypatch)
    ok, msg = webbind.bind_role_http(JAR, "10000", data={"roles": []})
    assert ok and "绑定成功" in msg
    assert submitted == [("2001", u"凤凰集", "1112223334445556666")]


def test_bind_bad_bindmap_surfaces(monkeypatch):
    """config.yaml 的 bind 预设写坏: 报错而非静默忽略"""
    write_bindmap(u"not-a-qq:\n  server: x\n  role: y\n")
    monkeypatch.setattr(webbind, "query_binding", lambda c, g: None)
    ok, msg = webbind.bind_role_http(JAR, "10000", data={"roles": []})
    assert not ok and "config.yaml" in msg
