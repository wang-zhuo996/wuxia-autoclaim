# -*- coding: utf-8 -*-
"""数据目录解析 + roles.json/config.yaml 读写

数据目录三种来源, 优先级从高到低:
    1. --data-dir 参数(见 cli.py 的 set_data_dir)
    2. 环境变量 WUXIA_DATA_DIR
    3. 默认:
       - 源码树/pip -e 运行(包目录向上能找到本项目的 pyproject.toml) -> <repo>/data
       - 安装态(pipx/pip) -> Win: %APPDATA%\\wuxia-autoclaim, POSIX: ~/.config/wuxia-autoclaim

所有需要人工配置的选项都在 config.yaml(napcat 端点/通道顺序/奖励序号/推送/绑定预设),
由手写的 mini-YAML 子集解析器读写(项目核心零依赖, 不引 PyYAML)。
roles.json 只存机器状态(schema 同旧项目 wuxia-autosign, 直接拷入即迁移):
    {"roles": [{"name", "uin", "area", "roleid", "cookies": {...}}]}
旧版三处配置(config.json / roles.json 的 defaults 与每角色 gift_index / bind.yaml)
在首次 load_config 时自动合并迁移到 config.yaml, 原文件改名 *.bak。
"""
import copy
import json
import os
import re
from pathlib import Path

from wuxia_autoclaim.util import mask, secure_write

DEFAULT_CHANNEL_ORDER = ("napcat", "qqnt")  # 凭据通道默认顺序
DEFAULT_CONFIG = {
    "napcat": [],                                   # [{"url": "http://127.0.0.1:3000", "token": ""}]
    "game_dir": "",                                 # QBrowser/QCache 目录(仅 game 通道)
    "channel_order": list(DEFAULT_CHANNEL_ORDER),
    "auto_bind": False,                             # 收割到新 QQ 时是否自动补齐绑定
    "gift_index": 3,                                # 默认许愿奖励序号(1 起)
    "push_key": "",                                 # Server酱 SendKey, 空=不推送
    "gift_override": {},                            # {"QQ号": 序号} 按 QQ 覆盖默认序号
    "bind": {},                                     # {"QQ号": {"server", "role"}} 自动绑定预设
}

_dir = None


class ConfigError(Exception):
    """配置/数据文件层面的致命错误(缺文件/损坏/非法), cli 层转退出码 2"""


def _package_dir():
    return Path(__file__).resolve().parent


def find_repo_root():
    """包目录向上找本项目的 pyproject.toml(判定源码树运行); 找不到返回 None"""
    for d in _package_dir().parents:
        if (d / "pyproject.toml").exists() and (d / "src" / "wuxia_autoclaim").is_dir():
            return d
    return None


def default_data_dir():
    v = os.environ.get("WUXIA_DATA_DIR", "").strip()
    if v:
        return Path(v)
    repo = find_repo_root()
    if repo:
        return repo / "data"
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "wuxia-autoclaim"


def data_dir():
    return _dir if _dir else default_data_dir()


def set_data_dir(path):
    global _dir
    _dir = Path(path) if path else None


def init_data_dir():
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def roles_file():
    return data_dir() / "roles.json"


def bindmap_file():
    """旧版 bind.yaml 的路径(仅供迁移读取; 新配置统一在 config.yaml 的 bind: 段)"""
    return data_dir() / "bind.yaml"


def config_file():
    return data_dir() / "config.yaml"


def log_file():
    return data_dir() / "autoclaim.log"


def load_roles():
    """读 roles.json; 不存在返回 None, 损坏抛 ConfigError"""
    f = roles_file()
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text("utf-8"))
    except Exception as e:
        raise ConfigError("roles.json 无法解析(%s), 请重跑 login 或修复该文件" % e)
    if not isinstance(data, dict):
        raise ConfigError("roles.json 结构不对(顶层应为对象)")
    data.setdefault("roles", [])
    return data


def save_roles(data):
    secure_write(roles_file(), json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- mini-YAML 解析
# 支持的子集(读写都由本模块控制, 超出子集在读取时报错, 绝不猜):
#   缩进块映射 / "- key: v" + 续行对齐的映射列表 / "[a, b]" 流式标量列表 /
#   引号或裸标量 / int 与 bool 自动推导 / # 注释(只认 "# 前有空白") / UTF-8 BOM
# 已知限制: 值里出现 " #"(空格+井号)会被当行内注释截断, 引号也救不回, 不要这样写。

def _unquote(s):
    """剥掉成对的单/双引号(YAML 引号写法兼容)"""
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    return s


def _strip_comment(line):
    """去掉行内注释: 只认 "# 前有空白" 的写法, 值里裸 # 不受影响"""
    m = re.search(r"\s#", line)
    return line[:m.start()] if m else line


def _token_lines(text, src):
    """逐行切成 (行号, 缩进, 是否列表项, 键, 原始值)"""
    toks = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        ws = line[:len(line) - len(line.lstrip())]
        if "\t" in ws:
            raise ConfigError("%s 第 %d 行: 不要用 tab 缩进, 请用空格" % (src, n))
        item = stripped.startswith("- ") or stripped == "-"
        content = stripped[2:].strip() if item else stripped
        key, sep, value = content.partition(":")
        key, value = _unquote(key.strip()), value.strip()
        if not sep or not key:
            raise ConfigError("%s 第 %d 行无法解析: %r" % (src, n, stripped[:60]))
        toks.append((n, len(ws), item, key, value))
    return toks


def _scalar(s):
    """标量类型推导: 引号保 str; true/false -> bool; null/~/空 -> ""; 纯数字 -> int"""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        return s[1:-1]
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "~", ""):
        return ""
    if s.isdigit():
        return int(s)
    return s


def _flow_seq(value):
    """解析 "[a, b]" 流式列表(仅标量成员)"""
    inner = value[1:-1].strip()
    if not inner:
        return []
    out = []
    for part in inner.split(","):
        part = part.strip()
        if part:
            out.append(_scalar(part))
    return out


def _value_of(value, n, src):
    if value.startswith("["):
        if not value.endswith("]"):
            raise ConfigError("%s 第 %d 行: 流式列表缺右中括号" % (src, n))
        return _flow_seq(value)
    return _scalar(value)


def _parse_item(toks, i, indent, src):
    """解析一个 "- key: value" 列表项(项内续行键要对齐到缩进 indent+2)"""
    n, _, _, key, value = toks[i]
    entry = {}
    i += 1
    if value:
        entry[key] = _value_of(value, n, src)
    else:
        if i >= len(toks) or toks[i][1] != indent + 2 or toks[i][2]:
            raise ConfigError("%s 第 %d 行: 列表项 %s 的值为空" % (src, n, key))
        entry[key], i = _parse(toks, i, indent + 2, src)
    while i < len(toks) and toks[i][1] == indent + 2 and not toks[i][2]:
        n2, _, _, k2, v2 = toks[i]
        if not v2:
            raise ConfigError("%s 第 %d 行: %s 的值为空" % (src, n2, k2))
        entry[k2] = _value_of(v2, n2, src)
        i += 1
    if i < len(toks) and toks[i][1] > indent:
        raise ConfigError("%s 第 %d 行: 缩进与上文对不上(列表项的续行要对齐到第 %d 列)"
                          % (src, toks[i][0], indent + 2))
    return entry, i


def _parse(toks, i, indent, src):
    """解析缩进为 indent 的块(首 token 是列表项则按列表, 否则按映射); 返回 (容器, 下一下标)"""
    if toks[i][2]:
        out = []
        while i < len(toks) and toks[i][1] == indent and toks[i][2]:
            entry, i = _parse_item(toks, i, indent, src)
            out.append(entry)
        return out, i
    out = {}
    while i < len(toks) and toks[i][1] == indent and not toks[i][2]:
        n, _, _, key, value = toks[i]
        if value:
            out[key] = _value_of(value, n, src)
            i += 1
            continue
        if i + 1 >= len(toks) or toks[i + 1][1] <= indent:
            raise ConfigError("%s 第 %d 行: %s 的值为空" % (src, n, key))
        out[key], i = _parse(toks, i + 1, toks[i + 1][1], src)
    return out, i


def parse_mini_yaml(text, src="config.yaml"):
    """解析 mini-YAML 子集文本, 返回顶层 dict; 结构对不上抛 ConfigError(带行号)"""
    toks = _token_lines(text, src)
    if not toks:
        return {}
    if toks[0][1] != 0:
        raise ConfigError("%s 第 %d 行: 顶层键不能有缩进" % (src, toks[0][0]))
    out, i = _parse(toks, 0, 0, src)
    if i < len(toks):
        raise ConfigError("%s 第 %d 行: 缩进与上文对不上" % (src, toks[i][0]))
    return out


# bind 预设每个条目的必填字段(服务器名/角色名)
BIND_FIELDS = ("server", "role")


def _validate_bindmap(bind):
    """校验 bind 预设形状: {QQ号: {server, role}}, 键纯数字、字段非空"""
    out = {}
    for uin, ent in bind.items():
        u = str(uin)
        if not u.isdigit():
            raise ConfigError("bind 预设的键 %r 不是 QQ 号(要纯数字)" % u[:30])
        if not isinstance(ent, dict):
            raise ConfigError("bind 预设 QQ%s 的值要写成缩进两行 server:/role:" % u)
        extra = [k for k in ent if k not in BIND_FIELDS]
        if extra:
            raise ConfigError("bind 预设 QQ%s 有不认识的字段 %r(只有 %s)"
                              % (u, extra[0], "/".join(BIND_FIELDS)))
        if not ent.get("server") or not ent.get("role"):
            raise ConfigError("bind 预设 QQ%s 缺 %s 或值为空"
                              % (u, "/".join(k for k in BIND_FIELDS if not ent.get(k))))
        out[u] = {"server": str(ent["server"]), "role": str(ent["role"])}
    return out


def parse_bindmap(text):
    """兼容入口: 解析旧版 bind.yaml 文本(顶层直接是 QQ号 -> server/role)"""
    top = parse_mini_yaml(text, src="bind.yaml")
    return _validate_bindmap(top)


def load_bindmap():
    """读 bind 预设(config.yaml 的 bind: 段); 未配置返回 {}, 损坏抛 ConfigError"""
    return load_config().get("bind") or {}


# ---------------------------------------------------------------- mini-YAML 输出

def _fmt_scalar(v):
    """标量序列化: 需要保 str 的(空串/纯数字/bool字面/带空白或逗号/#开头/[开头)加引号"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if (s == "" or s.isdigit() or s.lower() in ("true", "false", "null")
            or s != s.strip() or s.startswith(("[", "#")) or "," in s):
        return '"%s"' % s
    return s


def _dump_value(key, v, indent):
    """未知键的兜底序列化(递归任意 dict/list/标量)"""
    pad = " " * indent
    if isinstance(v, dict):
        out = ["%s%s:" % (pad, key)]
        for k, item in v.items():
            out.extend(_dump_value(str(k), item, indent + 4))
        return out
    if isinstance(v, list):
        out = ["%s%s:" % (pad, key)]
        for item in v:
            if isinstance(item, dict):
                first = True
                for k, sub in item.items():
                    head = "- " if first else "  "
                    out.extend(_dump_value(head + str(k), sub, indent + 4))
                    first = False
            else:
                out.append("%s    - %s" % (pad, _fmt_scalar(item)))
        return out
    return ["%s%s: %s" % (pad, key, _fmt_scalar(v))]


def dump_config(cfg):
    """按固定键序 + 中文注释生成 config.yaml 文本(dump -> parse 可回环)"""
    def row(key, val, comment):
        return "%s: %-28s # %s" % (key, val, comment)

    L = ["# wuxia-autoclaim 配置(UTF-8); 所有需要人工配置的选项都在这个文件。",
         "# roles.json 只存机器状态(角色/凭据), 由工具自动维护, 不要手工编辑。",
         ""]
    L.append("napcat:                       # NapCat OneBot HTTP 端点(可多个, 部署见 README)")
    eps = cfg.get("napcat") or []
    if eps:
        for ep in eps:
            L.append("    - url: %s" % _fmt_scalar(ep.get("url", "")))
            L.append("      token: %s" % _fmt_scalar(ep.get("token", "")))
    else:
        L[-1] = "napcat: []                     # NapCat OneBot HTTP 端点(可多个, 部署见 README; 添加: config napcat-add <url> <token>)"
    L.append(row("game_dir", _fmt_scalar(cfg.get("game_dir", "")), "游戏 QBrowser/QCache 目录(仅 game 通道, 一般留空)"))
    L.append(row("channel_order",
                 "[%s]" % ", ".join(_fmt_scalar(c) for c in (cfg.get("channel_order") or [])),
                 "凭据收割通道顺序(可用: napcat/qqnt)"))
    L.append(row("auto_bind", _fmt_scalar(cfg.get("auto_bind", False)), "收割到新 QQ 时自动补齐绑定(需活动已有绑定或 bind 预设)"))
    L.append(row("gift_index", _fmt_scalar(cfg.get("gift_index", 3)), "默认许愿奖励序号(1 起); 想换物品跑 gift 命令最直观"))
    L.append(row("push_key", _fmt_scalar(cfg.get("push_key", "")), "Server酱 SendKey(空 = 不推送)"))
    ov = cfg.get("gift_override") or {}
    if ov:
        L.append("gift_override:               # 按 QQ 覆盖奖励序号(优先于 gift_index, gift 命令自动维护)")
        for uin in sorted(ov):
            L.append("    %s: %d" % (uin, ov[uin]))
    else:
        L.append("#gift_override:              # 按 QQ 覆盖奖励序号(优先于 gift_index, gift 命令自动维护)")
        L.append("#    123456789: 2")
    bind = cfg.get("bind") or {}
    if bind:
        L.append("bind:                        # 自动绑定预设 QQ号 -> 服务器/角色名; 命中即免交互直绑")
        for uin in sorted(bind, key=lambda u: int(u)):
            ent = bind[uin]
            L.append("    %s:" % uin)
            L.append("        server: %s" % _fmt_scalar(ent.get("server", "")))
            L.append("        role: %s" % _fmt_scalar(ent.get("role", "")))
    else:
        L.append("#bind:                       # 自动绑定预设 QQ号 -> 服务器/角色名; bind/harvest --bind-new 免交互直绑")
        L.append("#    123456789:")
        L.append("#        server: 凤凰集")
        L.append("#        role: 测试一")
    known = set(DEFAULT_CONFIG)
    extra = [k for k in cfg if k not in known]
    if extra:
        L.append("")
        L.append("# ---- 非标准键(原样保留) ----")
        for k in extra:
            L.extend(_dump_value(str(k), cfg[k], 0))
    return "\n".join(L) + "\n"


def _atomic_write(path, text):
    """原子落盘: 同目录 tmp 写好再替换, 读的一方永远不会看到半截文件"""
    tmp = path.with_name("%s.%d.tmp" % (path.name, os.getpid()))
    tmp.write_text(text, "utf-8")
    try:
        os.chmod(str(tmp), 0o600)
    except OSError:
        pass
    os.replace(str(tmp), str(path))


# ---------------------------------------------------------------- 归一化与迁移

def _normalize(user):
    """把解析出的 dict 归一到 DEFAULT_CONFIG 形状(类型强转/校验, 防手写踩坑)"""
    if not isinstance(user, dict):
        raise ConfigError("config.yaml 结构不对(顶层应为键值对)")
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    cfg.update(user)
    eps = cfg.get("napcat") or []
    if not isinstance(eps, list):
        raise ConfigError("config.yaml: napcat 要是端点列表")
    norm = []
    for ep in eps:
        if not isinstance(ep, dict):
            raise ConfigError("config.yaml: napcat 端点要写成缩进的 url:/token: 两行")
        norm.append({"url": str(ep.get("url") or ""), "token": str(ep.get("token") or "")})
    cfg["napcat"] = norm
    cfg["game_dir"] = str(cfg.get("game_dir") or "")
    cfg["push_key"] = str(cfg.get("push_key") or "")
    co = cfg.get("channel_order")
    if isinstance(co, str):
        co = [s.strip() for s in co.split(",") if s.strip()]
    if not isinstance(co, list):
        raise ConfigError("config.yaml: channel_order 要是通道列表")
    cfg["channel_order"] = [str(c) for c in co]
    cfg["auto_bind"] = bool(cfg.get("auto_bind"))
    gi = str(cfg.get("gift_index", 3))
    if not gi.isdigit():
        raise ConfigError("config.yaml: gift_index 要是数字(1 起)")
    cfg["gift_index"] = int(gi)
    ov = cfg.get("gift_override") or {}
    if not isinstance(ov, dict):
        raise ConfigError("config.yaml: gift_override 要是 QQ号->序号 的映射")
    for k, v in ov.items():
        if not str(k).isdigit() or isinstance(v, bool) or not str(v).isdigit():
            raise ConfigError("config.yaml: gift_override 的 %r: %r 不合法(要 QQ号: 数字)" % (k, v))
    cfg["gift_override"] = {str(k): int(v) for k, v in ov.items()}
    cfg["bind"] = _validate_bindmap(cfg.get("bind") or {})
    return cfg


def _collect_legacy():
    """收拢旧版三处配置; 返回 (cfg, 待改名旧文件列表, 是否有旧配置)"""
    from wuxia_autoclaim.log import log
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    renames, has = [], False

    jf = data_dir() / "config.json"
    if jf.exists():
        has = True
        try:
            old = json.loads(jf.read_text("utf-8"))
        except Exception as e:
            raise ConfigError("config.json 无法解析(%s): %s" % (jf, e))
        if not isinstance(old, dict):
            raise ConfigError("config.json 结构不对(顶层应为对象)")
        for k in ("napcat", "game_dir", "channel_order", "auto_bind"):
            if k in old:
                cfg[k] = old[k]
        renames.append(jf)

    data = load_roles()
    if data is not None:
        defaults = data.get("defaults") or {}
        gi = defaults.get("gift_index")
        if gi is not None and str(gi).isdigit():
            cfg["gift_index"] = int(str(gi))
            has = True
        if defaults.get("push_key"):
            cfg["push_key"] = str(defaults["push_key"])
            has = True
        for r in data.get("roles", []):              # 每角色 gift_index -> gift_override
            rgi, uin = r.get("gift_index"), str(r.get("uin") or "")
            if isinstance(rgi, int) and not isinstance(rgi, bool) and uin:
                if uin in cfg["gift_override"]:
                    log("[config] roles.json 里 QQ%s 的多个角色写了奖励序号, 取最后一个 %d"
                        % (mask(uin), rgi))
                cfg["gift_override"][uin] = rgi
                has = True

    bf = bindmap_file()
    if bf.exists():
        has = True
        try:
            cfg["bind"] = parse_bindmap(bf.read_text("utf-8-sig"))
        except (ConfigError, UnicodeDecodeError) as e:
            log("[config] 旧 bind.yaml 无法解析(%s), 未迁入; 请手工整理到 config.yaml 的 bind: 段(原件保留)" % e)
        else:
            renames.append(bf)
    return cfg, renames, has


def _write_migrated(cfg, renames):
    """迁移落盘: 原子写 config.yaml, 旧文件改名 *.bak(roles.json 不动)"""
    from wuxia_autoclaim.log import log
    try:
        _atomic_write(config_file(), dump_config(cfg))
    except OSError as e:
        log("[config] 旧配置迁移写盘失败(%s), 本次按内存合并结果继续" % e)
        return
    for src in renames:
        try:
            os.replace(str(src), str(src) + ".bak")
        except OSError:
            pass
    log("[config] 旧版配置已合并迁移到 %s%s; 原文件改名 *.bak(确认无误后可删)"
        % (config_file(), "".join("、" + s.name for s in renames)))


def load_config():
    """读 config.yaml 并归一化; 不存在时自动迁移旧版三处配置(见模块 docstring)"""
    f = config_file()
    if f.exists():
        try:
            text = f.read_text("utf-8-sig")          # 容忍记事本存的 BOM
        except UnicodeDecodeError as e:
            raise ConfigError("config.yaml 不是 UTF-8 编码(%s)" % e)
        return _normalize(parse_mini_yaml(text))
    cfg, renames, has = _collect_legacy()
    if has:
        _write_migrated(cfg, renames)
    return _normalize(cfg)


def save_config(cfg):
    init_data_dir()
    _atomic_write(config_file(), dump_config(cfg))


# ---------------------------------------------------------------- 取值助手

def game_dir(cfg):
    """游戏 QBrowser/QCache 目录: 环境变量 WUXIA_GAME_DIR 优先于 config"""
    v = os.environ.get("WUXIA_GAME_DIR", "").strip()
    return v or cfg.get("game_dir", "")


def channel_order(cfg, override=""):
    """通道顺序: 逗号分隔的 override > config.channel_order > 默认; 非法项丢弃"""
    src = override or ",".join(cfg.get("channel_order") or [])
    order = [s.strip() for s in src.split(",") if s.strip()]
    order = [c for c in order if c in DEFAULT_CHANNEL_ORDER]
    return order or list(DEFAULT_CHANNEL_ORDER)


def apply_env(cfg):
    """允许用环境变量覆盖 config.yaml, 免去改文件。

    返回被强制覆盖的键集合; WUXIA_GIFT_INDEX 覆盖时清空 gift_override(对所有角色生效)。
    """
    forced = set()
    v = os.environ.get("WUXIA_GIFT_INDEX", "").strip()
    if v.isdigit():
        cfg["gift_index"] = int(v)
        cfg["gift_override"] = {}
        forced.add("gift_index")
    v = os.environ.get("WUXIA_PUSH_KEY", "").strip()
    if v:
        cfg["push_key"] = v
        forced.add("push_key")
    return forced


def gift_index_for(cfg, uin):
    """角色的生效奖励序号: gift_override[uin] > gift_index > 3。
    (命令行 --index / run 的注入不经过这里, 在 claim.run_role 里最优先)"""
    return cfg.get("gift_override", {}).get(str(uin or "")) or cfg.get("gift_index", 3)


def upsert_role(data, area, roleid, playername, jar, uin):
    """把登录/绑定到的角色写入 roles.json 数据(不落盘, 由调用方 save)。

    同一 QQ 的所有旧角色统一换上新 cookie(登录态是账号级的);
    同 (uin, roleid) 的条目原地更新, 否则追加新角色。
    """
    roles = data.setdefault("roles", [])
    for r in roles:
        if r.get("uin") == uin:
            r["cookies"] = dict(jar)
    for r in roles:
        if r.get("uin") == uin and r.get("roleid") == roleid:
            r["area"] = area
            if playername:
                r["name"] = playername
            break
    else:
        roles.append({"name": playername or ("QQ" + uin), "uin": uin,
                      "area": area, "roleid": roleid, "cookies": dict(jar)})
    return data
