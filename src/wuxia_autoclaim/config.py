# -*- coding: utf-8 -*-
"""数据目录解析 + roles.json/config.json 读写

数据目录三种来源, 优先级从高到低:
    1. --data-dir 参数(见 cli.py 的 set_data_dir)
    2. 环境变量 WUXIA_DATA_DIR
    3. 默认:
       - 源码树/pip -e 运行(包目录向上能找到本项目的 pyproject.toml) -> <repo>/data
       - 安装态(pipx/pip) -> Win: %APPDATA%\\wuxia-autoclaim, POSIX: ~/.config/wuxia-autoclaim

roles.json 的 schema 与旧项目 wuxia-autosign 完全一致, 直接拷入即迁移:
    {"defaults": {"gift_index": 3, "push_key": ""},
     "roles": [{"name", "uin", "area", "roleid", "cookies": {...}, "gift_index"?}]}
config.json 只放基础设施(凭据端点/通道顺序等), 业务默认值留在 roles.json 的 defaults。
"""
import json
import os
from pathlib import Path

from wuxia_autoclaim.util import secure_write

DEFAULTS = {"gift_index": 3, "push_key": ""}       # roles.json 的 defaults 兜底值
DEFAULT_CHANNEL_ORDER = ("napcat", "qqnt", "game")  # 凭据通道默认顺序
DEFAULT_CONFIG = {
    "napcat": [],                                   # [{"url": "http://127.0.0.1:3000", "token": ""}]
    "game_dir": "",                                 # QBrowser/QCache 目录(仅 game 通道)
    "channel_order": list(DEFAULT_CHANNEL_ORDER),
    "auto_bind": False,                             # 收割到新 QQ 时是否自动无头绑定角色
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


def config_file():
    return data_dir() / "config.json"


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
    data.setdefault("defaults", {})
    data.setdefault("roles", [])
    return data


def save_roles(data):
    init_data_dir()
    secure_write(roles_file(), json.dumps(data, ensure_ascii=False, indent=2))


def load_config():
    """读 config.json 并补齐缺省键(浅合并)"""
    cfg = dict(DEFAULT_CONFIG)
    f = config_file()
    if f.exists():
        try:
            user = json.loads(f.read_text("utf-8"))
        except Exception as e:
            raise ConfigError("config.json 无法解析(%s): %s" % (f, e))
        if isinstance(user, dict):
            cfg.update(user)
    return cfg


def save_config(cfg):
    init_data_dir()
    secure_write(config_file(), json.dumps(cfg, ensure_ascii=False, indent=2))


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


def apply_env(defaults):
    """允许用环境变量覆盖 roles.json 的 defaults, 免去重新登录。

    返回被强制覆盖的键集合; gift_index 被覆盖时对所有角色生效。
    """
    forced = set()
    for env_key, key, is_int in (("WUXIA_GIFT_INDEX", "gift_index", True),
                                 ("WUXIA_PUSH_KEY", "push_key", False)):
        v = os.environ.get(env_key, "").strip()
        if not v:
            continue
        if is_int:
            if not v.isdigit():
                continue
            v = int(v)
        defaults[key] = v
        forced.add(key)
    return forced


def upsert_role(data, area, roleid, playername, jar, uin):
    """把登录/绑定到的角色写入 roles.json 数据(不落盘, 由调用方 save)。

    同一 QQ 的所有旧角色统一换上新 cookie(登录态是账号级的);
    同 (uin, roleid) 的条目原地更新, 否则追加新角色。
    """
    data.setdefault("defaults", {})
    for k, v in DEFAULTS.items():
        data["defaults"].setdefault(k, v)
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
