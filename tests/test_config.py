# -*- coding: utf-8 -*-
"""config 单测: 目录解析/通道顺序/环境变量覆盖/upsert_role/mini-YAML 读写回环/旧配置迁移"""
import json

import pytest

from wuxia_autoclaim import config


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """每个用例独立数据目录, 用后还原"""
    for k in ("WUXIA_DATA_DIR", "WUXIA_GAME_DIR", "WUXIA_GIFT_INDEX", "WUXIA_PUSH_KEY",
              "WUXIA_NAPCAT_URL", "WUXIA_NAPCAT_TOKEN", "WUXIA_CHANNEL_ORDER",
              "WUXIA_AUTO_BIND", "WUXIA_BIND"):
        monkeypatch.delenv(k, raising=False)
    config.set_data_dir(tmp_path)
    yield tmp_path
    config.set_data_dir(None)


def test_env_data_dir_wins(monkeypatch, tmp_path):
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("WUXIA_DATA_DIR", str(other))
    assert config.default_data_dir() == other


def test_channel_order():
    cfg = {}
    assert config.channel_order(cfg) == ["napcat", "qqnt"]             # 默认(game 通道已下线)
    assert config.channel_order(cfg, "qqnt,napcat") == ["qqnt", "napcat"]
    assert config.channel_order(cfg, "junk,game") == ["napcat", "qqnt"]  # 非法项丢弃后回落默认
    assert config.channel_order(cfg, "junk") == ["napcat", "qqnt"]     # 全非法回落默认
    cfg2 = {"channel_order": ["qqnt"]}
    assert config.channel_order(cfg2) == ["qqnt"]


def test_apply_env(monkeypatch):
    d = {"gift_index": 3, "push_key": "", "gift_override": {"100": 2}}
    monkeypatch.setenv("WUXIA_GIFT_INDEX", "5")
    monkeypatch.setenv("WUXIA_PUSH_KEY", "SCTxxx")
    forced = config.apply_env(d)
    assert forced == {"gift_index", "push_key"}
    assert d["gift_index"] == 5 and d["push_key"] == "SCTxxx"
    assert d["gift_override"] == {}                    # 强制序号时清空按 QQ 覆盖

    monkeypatch.setenv("WUXIA_GIFT_INDEX", "abc")      # 非数字忽略
    monkeypatch.delenv("WUXIA_PUSH_KEY")
    d2 = {"gift_index": 3, "push_key": "", "gift_override": {"100": 2}}
    assert config.apply_env(d2) == set()
    assert d2["gift_index"] == 3 and d2["gift_override"] == {"100": 2}


def test_gift_index_for():
    cfg = {"gift_index": 3, "gift_override": {"100": 2}}
    assert config.gift_index_for(cfg, "100") == 2
    assert config.gift_index_for(cfg, 100) == 2        # 容忍 int 型 uin
    assert config.gift_index_for(cfg, "200") == 3      # 无覆盖回落默认
    assert config.gift_index_for({}, None) == 3


def test_upsert_role():
    jar1 = {"skey": "a", "uin": "o100"}
    data = {"roles": []}
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
    data = {"roles": [
        {"name": "甲", "uin": "100", "area": "一区", "roleid": "r1", "cookies": {"skey": "x"}}]}
    config.save_roles(data)
    back = config.load_roles()
    assert back["roles"][0]["name"] == "甲"

    (config.data_dir() / "roles.json").write_text("{bad json", "utf-8")
    with pytest.raises(config.ConfigError):
        config.load_roles()


def test_game_dir_env_overrides(monkeypatch):
    monkeypatch.setenv("WUXIA_GAME_DIR", "D:/games/QCache")
    assert config.game_dir({"game_dir": "E:/other"}) == "D:/games/QCache"
    monkeypatch.delenv("WUXIA_GAME_DIR")
    assert config.game_dir({"game_dir": "E:/other"}) == "E:/other"


# ---------------------------------------------------------------- mini-YAML 解析

def test_parse_full_sample():
    text = u'''\
# wuxia-autoclaim 配置
napcat:                       # 端点(可多个)
    - url: http://127.0.0.1:3000
      token: "0123"
    - url: http://192.168.1.8:3000
      token: ''
game_dir: "E:/WeGameApps/QCache"
channel_order: [napcat, qqnt]
auto_bind: true
gift_index: 5
push_key: SCTxxxx
gift_override:
    123456789: 2
bind:
    123456789:
        server: 凤凰集
        role: 测试一
    "10086":
        server: 长生剑
        role: '测试 二'
'''
    cfg = config.parse_mini_yaml(text)
    assert cfg["napcat"] == [{"url": "http://127.0.0.1:3000", "token": "0123"},
                             {"url": "http://192.168.1.8:3000", "token": ""}]
    assert cfg["game_dir"] == "E:/WeGameApps/QCache"
    assert cfg["channel_order"] == ["napcat", "qqnt"]
    assert cfg["auto_bind"] is True
    assert cfg["gift_index"] == 5
    assert cfg["push_key"] == "SCTxxxx"
    assert cfg["gift_override"] == {"123456789": 2}
    assert cfg["bind"]["123456789"] == {"server": u"凤凰集", "role": u"测试一"}
    assert cfg["bind"]["10086"] == {"server": u"长生剑", "role": u"测试 二"}


@pytest.mark.parametrize("text, expect", [
    (u"a: 3\n", {"a": 3}),
    (u'a: "3"\n', {"a": "3"}),                       # 引号保 str, 不转 int
    (u"a: true\na2: False\n", {"a": True, "a2": False}),   # 大小写容忍
    (u"a: null\nb: ~\n", {"a": "", "b": ""}),
    (u"a: []\n", {"a": []}),
    (u"a: [x, 'y z', 3]\n", {"a": ["x", "y z", 3]}),
    (u"a: 名#字\n", {"a": u"名#字"}),                 # 裸 # 不当注释
    (u"# 只有注释\n\n# 还没填\n", {}),
    (u"a:\n    b: 1\n", {"a": {"b": 1}}),
    (u"a:\n    - k: 1\n      j: 2\n    - k: 3\n", {"a": [{"k": 1, "j": 2}, {"k": 3}]}),
])
def test_parse_scalars(text, expect):
    assert config.parse_mini_yaml(text) == expect


@pytest.mark.parametrize("text", [
    u"\ta: 1\n",                                     # tab 缩进
    u"  a: 1\n",                                     # 顶层键带缩进
    u"a: 1\n  b: 2\n",                               # 缩进与上文对不上
    u"a: [1, 2\n",                                   # 流式列表缺右括号
    u"a:\n",                                         # 悬置键无子块(值为空)
    u"a: 1\nb\n",                                    # 没有冒号
    u"napcat:\n    - url: x\n     token: y\n",       # 列表项续行没对齐
    u"napcat:\n    - url:\n",                        # 列表项悬置键无子块
])
def test_parse_bad_raises(text):
    with pytest.raises(config.ConfigError):
        config.parse_mini_yaml(text)


def test_parse_error_has_lineno():
    with pytest.raises(config.ConfigError) as ei:
        config.parse_mini_yaml(u"gift_index: 3\nauto_bind:\n")
    assert "第 2 行" in str(ei.value)


# ---------------------------------------------------------------- dump 与回环

def test_dump_parse_roundtrip():
    cfg = {"napcat": [{"url": "http://127.0.0.1:3000", "token": "0123"}],
           "game_dir": "D:/g", "channel_order": ["qqnt", "napcat"],
           "auto_bind": True, "gift_index": 4, "push_key": "SCTxx",
           "gift_override": {"100": 2, "200": 1},
           "bind": {"100": {"server": u"凤凰集", "role": u"测试一"}}}
    back = config.parse_mini_yaml(config.dump_config(cfg))
    assert back == cfg                               # 纯数字 token 加引号回读不丢前导零


def test_dump_empty_sections_are_comments():
    text = config.dump_config(dict(config.DEFAULT_CONFIG))
    assert "#gift_override:" in text and "#bind:" in text     # 空段以注释示例呈现
    back = config._normalize(config.parse_mini_yaml(text))    # 缺的键由归一化补默认
    assert back["gift_override"] == {} and back["bind"] == {}


def test_config_load_merge():
    assert config.load_config()["napcat"] == []      # 无文件 -> 全默认
    assert not (config.data_dir() / "config.yaml").exists()   # 且不落盘
    cfg = config.load_config()
    cfg["napcat"] = [{"url": "http://127.0.0.1:3000", "token": "0123"}]
    cfg["unknown_key"] = 1
    config.save_config(cfg)
    back = config.load_config()
    assert back["napcat"][0]["token"] == "0123"
    assert back["unknown_key"] == 1                 # 未知键透传保留
    assert back["game_dir"] == "" and back["gift_index"] == 3


def test_load_bom_tolerated():
    (config.data_dir() / "config.yaml").write_text(u"gift_index: 6\n", "utf-8-sig")
    assert config.load_config()["gift_index"] == 6


def test_load_bad_yaml_raises():
    (config.data_dir() / "config.yaml").write_text(u"a: [1, 2\n", "utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config()


@pytest.mark.parametrize("user", [
    {"gift_index": "abc"}, {"napcat": "x"}, {"gift_override": {"abc": 1}},
    {"bind": {"abc": {"server": "a", "role": "b"}}},
])
def test_normalize_rejects_bad(user):
    with pytest.raises(config.ConfigError):
        config._normalize(user)


# ---------------------------------------------------------------- 旧配置迁移

def _write_legacy_config_json():
    (config.data_dir() / "config.json").write_text(json.dumps({
        "napcat": [{"url": "http://127.0.0.1:3000", "token": "t"}],
        "channel_order": ["qqnt"], "auto_bind": True, "game_dir": "D:/g",
        "run_time": "08:00"}), "utf-8")              # 未知旧键不迁


def test_migrate_from_legacy():
    _write_legacy_config_json()
    config.save_roles({"defaults": {"gift_index": 2, "push_key": "SCTx", "run_time": "x"},
                       "roles": [{"name": "甲", "uin": "100", "area": "一区", "roleid": "r1",
                                  "cookies": {"skey": "x"}, "gift_index": 5}]})
    (config.data_dir() / "bind.yaml").write_text(u"100:\n    server: 凤凰集\n    role: 甲\n", "utf-8")

    cfg = config.load_config()
    assert cfg["napcat"] == [{"url": "http://127.0.0.1:3000", "token": "t"}]
    assert cfg["channel_order"] == ["qqnt"]
    assert cfg["auto_bind"] is True
    assert cfg["game_dir"] == "D:/g"
    assert cfg["gift_index"] == 2
    assert cfg["push_key"] == "SCTx"
    assert cfg["gift_override"] == {"100": 5}
    assert cfg["bind"] == {"100": {"server": u"凤凰集", "role": u"甲"}}

    # 迁移落盘 config.yaml, 旧文件改名 *.bak, roles.json 原样保留
    d = config.data_dir()
    assert (d / "config.yaml").exists()
    assert (d / "config.json.bak").exists() and not (d / "config.json").exists()
    assert (d / "bind.yaml.bak").exists() and not (d / "bind.yaml").exists()
    roles_now = json.loads((d / "roles.json").read_text("utf-8"))
    assert roles_now["defaults"]["gift_index"] == 2

    assert config.load_config() == cfg               # 二次读取走 config.yaml, 结果一致


def test_migrate_roles_defaults_only():
    """只有 roles.json 带业务值也会迁移(不需要 config.json/bind.yaml)"""
    config.save_roles({"defaults": {"gift_index": 4}, "roles": []})
    assert config.load_config()["gift_index"] == 4
    assert (config.data_dir() / "config.yaml").exists()
    assert (config.data_dir() / "roles.json").exists()          # roles.json 从不改名


def test_migrate_bad_bindmap_not_blocking():
    _write_legacy_config_json()
    (config.data_dir() / "bind.yaml").write_text(u"!!!\n", "utf-8")
    cfg = config.load_config()                       # 坏 bind.yaml 不阻塞其余迁移
    assert cfg["auto_bind"] is True
    assert cfg["bind"] == {}
    d = config.data_dir()
    assert (d / "bind.yaml").exists() and not (d / "bind.yaml.bak").exists()
    assert (d / "config.json.bak").exists()


def test_migrate_corrupt_config_json_raises():
    (config.data_dir() / "config.json").write_text("{bad", "utf-8")
    with pytest.raises(config.ConfigError):
        config.load_config()


def test_migrate_write_fail_degrades(monkeypatch):
    _write_legacy_config_json()

    def boom(path, text):
        raise OSError("disk full")
    monkeypatch.setattr(config, "_atomic_write", boom)
    cfg = config.load_config()                       # 写盘失败: 内存合并值照常可用
    assert cfg["auto_bind"] is True


# ---------------------------------------------------------------- bind 预设解析

def test_parse_bindmap():
    text = u'''# 自动绑定预设
123456789:
    server: 凤凰集
    role: 测试一

"10086":                                # 引号键/行内注释兼容
    server: 长生剑
    role: '测试 二'

10000:
    server: 名#字里带井号               # 裸 # 不当注释
    role: 角#色
'''
    assert config.parse_bindmap(text) == {
        "123456789": {"server": u"凤凰集", "role": u"测试一"},
        "10086": {"server": u"长生剑", "role": u"测试 二"},
        "10000": {"server": u"名#字里带井号", "role": u"角#色"},
    }


def test_load_bindmap_from_config():
    assert config.load_bindmap() == {}               # 未配置
    config.save_config({"bind": {"100": {"server": u"凤凰集", "role": u"一"}}})
    assert config.load_bindmap() == {"100": {"server": u"凤凰集", "role": u"一"}}


@pytest.mark.parametrize("text, why", [
    (u"  server: 凤凰集\n", "缩进出现在 QQ 号之前"),
    (u"abc:\n  server: a\n  role: b\n", "顶层键不是 QQ 号"),
    (u"123456: 凤凰集\n  role: b\n", "QQ 号后直接跟值"),
    (u"1:\n  srv: a\n  role: b\n", "不认识的字段"),
    (u"1:\n  server: a\n  role:\n", "字段值为空"),
    (u"1:\n  server: 凤凰集\n", "缺 role"),
    (u"1:\n  role: 一\n", "缺 server"),
    (u"123\n", "没有冒号"),
])
def test_bindmap_bad_raises(text, why):
    with pytest.raises(config.ConfigError):
        config.parse_bindmap(text)


# ---------------------------------------------------------------- 环境变量落盘(from-env)

def test_parse_bindmap_json():
    assert config.parse_bindmap_json(
        json.dumps({"100": {"server": u"凤凰集", "role": u"测试一"},
                    "200": {"server": u"长生剑", "role": u"测试二"}})) == {
        "100": {"server": u"凤凰集", "role": u"测试一"},
        "200": {"server": u"长生剑", "role": u"测试二"}}
    assert config.parse_bindmap_json("{}") == {}


@pytest.mark.parametrize("text, why", [
    ("{bad json", "不是 JSON"),
    ('["a"]', "顶层不是对象"),
    ('{"abc": {"server": "a", "role": "b"}}', "键不是 QQ 号"),
    ('{"100": {"server": "a"}}', "缺 role"),
])
def test_parse_bindmap_json_bad(text, why):
    with pytest.raises(config.ConfigError):
        config.parse_bindmap_json(text)


def test_materialize_env_full(monkeypatch):
    """非空变量逐键落盘, 回读一致"""
    monkeypatch.setenv("WUXIA_NAPCAT_URL", "http://10.0.0.2:3000, http://10.0.0.3:3000")
    monkeypatch.setenv("WUXIA_NAPCAT_TOKEN", "t1,t2")
    monkeypatch.setenv("WUXIA_CHANNEL_ORDER", "napcat")
    monkeypatch.setenv("WUXIA_AUTO_BIND", "true")
    monkeypatch.setenv("WUXIA_GIFT_INDEX", "5")
    monkeypatch.setenv("WUXIA_PUSH_KEY", "SCTenv")
    monkeypatch.setenv("WUXIA_BIND",
                       json.dumps({"100": {"server": u"凤凰集", "role": u"测试一"}}))
    wrote, warns = config.materialize_env()
    assert warns == []
    assert set(wrote) == {"napcat", "channel_order", "auto_bind",
                          "gift_index", "push_key", "bind"}
    back = config.load_config()                          # 落盘后回读一致
    assert back["napcat"] == [{"url": "http://10.0.0.2:3000", "token": "t1"},
                              {"url": "http://10.0.0.3:3000", "token": "t2"}]
    assert back["channel_order"] == ["napcat"]
    assert back["auto_bind"] is True
    assert back["gift_index"] == 5
    assert back["push_key"] == "SCTenv"
    assert back["bind"] == {"100": {"server": u"凤凰集", "role": u"测试一"}}


def test_materialize_env_token_alignment(monkeypatch):
    """token 恰一段时对多端点复用; 缺位为空"""
    monkeypatch.setenv("WUXIA_NAPCAT_URL", "http://a:3000,http://b:3000,http://c:3000")
    monkeypatch.setenv("WUXIA_NAPCAT_TOKEN", "only")
    wrote, _ = config.materialize_env()
    assert wrote == ["napcat"]
    eps = config.load_config()["napcat"]
    assert [e["token"] for e in eps] == ["only"] * 3
    monkeypatch.setenv("WUXIA_NAPCAT_TOKEN", "t1")
    monkeypatch.setenv("WUXIA_NAPCAT_URL", "http://a:3000,http://b:3000")
    monkeypatch.setenv("WUXIA_NAPCAT_TOKEN", "t1,t2,t3")
    config.materialize_env()                             # URL 两段, token 三段: 多余忽略
    assert config.load_config()["napcat"] == [
        {"url": "http://a:3000", "token": "t1"},
        {"url": "http://b:3000", "token": "t2"}]


def test_materialize_env_preserves_unset_keys(monkeypatch):
    """变量未设置/为空时不动文件里已有的值"""
    cfg = config.load_config()
    cfg["push_key"] = "SCTfile"
    cfg["gift_index"] = 2
    cfg["bind"] = {"100": {"server": u"凤凰集", "role": u"一"}}
    config.save_config(cfg)
    monkeypatch.setenv("WUXIA_AUTO_BIND", "1")
    monkeypatch.setenv("WUXIA_PUSH_KEY", "")             # 空串=不接管
    wrote, warns = config.materialize_env()
    assert wrote == ["auto_bind"] and warns == []
    back = config.load_config()
    assert back["auto_bind"] is True
    assert back["push_key"] == "SCTfile"                 # 未被空串清掉
    assert back["gift_index"] == 2 and back["bind"]["100"]["server"] == u"凤凰集"


def test_materialize_env_replaces_whole_keys(monkeypatch):
    """写则以变量为准: bind 整段替换, napcat 列表不再叠加旧端点"""
    config.save_config({"napcat": [{"url": "http://old:3000", "token": "x"}],
                        "bind": {"100": {"server": u"旧服", "role": u"旧角色"}}})
    monkeypatch.setenv("WUXIA_NAPCAT_URL", "http://new:3000")
    monkeypatch.setenv("WUXIA_BIND",
                       json.dumps({"200": {"server": u"新服", "role": u"新角色"}}))
    config.materialize_env()
    back = config.load_config()
    assert back["napcat"] == [{"url": "http://new:3000", "token": ""}]
    assert back["bind"] == {"200": {"server": u"新服", "role": u"新角色"}}


def test_materialize_env_bad_values_only_warn(monkeypatch):
    """单变量值不合法只记告警, 不写该键也不影响其余变量"""
    monkeypatch.setenv("WUXIA_GIFT_INDEX", "abc")
    monkeypatch.setenv("WUXIA_BIND", "{bad json")
    monkeypatch.setenv("WUXIA_PUSH_KEY", "SCTok")
    wrote, warns = config.materialize_env()
    assert wrote == ["push_key"]
    assert len(warns) == 2 and any("WUXIA_GIFT_INDEX" in w for w in warns) \
        and any("WUXIA_BIND" in w for w in warns)
    back = config.load_config()
    assert back["gift_index"] == 3 and back["bind"] == {} and back["push_key"] == "SCTok"


def test_materialize_env_noop():
    """没有任何变量: 不写盘(config.yaml 不出现)"""
    wrote, warns = config.materialize_env()
    assert wrote == [] and warns == []
    assert not (config.data_dir() / "config.yaml").exists()


def test_materialize_env_container_localhost_hint(monkeypatch):
    """容器内 127.0.0.1/localhost 端点: 照写不拦, 但提示改用 host.docker.internal"""
    monkeypatch.setattr(config, "_in_container", lambda: True)
    monkeypatch.setenv("WUXIA_NAPCAT_URL", "http://127.0.0.1:3000,http://localhost:3001")
    wrote, warns = config.materialize_env()
    assert wrote == ["napcat"]
    assert len(warns) == 1 and "host.docker.internal" in warns[0]
    assert len(config.load_config()["napcat"]) == 2       # 值原样落盘, 不代改
    # 非容器环境(默认)或非本机地址: 不提示
    monkeypatch.setattr(config, "_in_container", lambda: False)
    _, warns = config.materialize_env()
    assert warns == []
