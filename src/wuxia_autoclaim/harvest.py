# -*- coding: utf-8 -*-
"""凭据收割编排: 体检 -> 按通道顺序收割 -> 探测活的才回写

自 wuxia-autosign harvest.py 移植, 去掉 GitHub 同步; 通道从 2 条扩到 3 条:

    0. 体检现有凭据   roles 里每个角色的 cookie 用 FLOW_INIT 只读探测,
       全部有效则直接收工(周期任务多数时候走到这就结束)
    1. napcat         NapCat OneBot HTTP 的 clientkey -> jump 签发(服务器可用)
    2. qqnt           本地 QQNT 客户端桥 SSO(纯 HTTP, 客户端要在线)
    3. game           游戏目录 QBrowser/QCache/Cookies 缓存

通道顺序默认 napcat -> qqnt -> game, config.json 的 channel_order 可改。
每条通道取到凭据后同样用 FLOW_INIT 只读探测(不产生签到副作用), 活的才回写。
"""
from wuxia_autoclaim import ams, config
from wuxia_autoclaim.log import log
from wuxia_autoclaim.util import mask


def _match(data, uin):
    return [r for r in data.get("roles", []) if r.get("uin") == uin]


def check_roles(data):
    """体检 roles 现有凭据, 返回 (失效角色列表)"""
    dead = []
    for r in data.get("roles", []):
        name = r.get("name") or ("QQ%s" % r.get("uin", "?"))
        jar = r.get("cookies", {})
        if not jar.get("skey"):
            log("[check] %s 没有 skey, 视为失效" % name)
            dead.append(r)
            continue
        ok, info = ams.probe(jar, r)
        if ok:
            log("[check] %s 有效(%s)" % (name, info))
        else:
            log("[check] %s 已失效(%s)" % (name, info))
            dead.append(r)
    return dead


def try_game(data, cfg, dead_uins=None):
    """通道 game: 游戏目录缓存。返回 updated(已更新) / ok(有效无需动) / fail(转下一通道)"""
    from wuxia_autoclaim.gamecache import read_jar, cache_dir
    try:
        uin, jar = read_jar(cache_dir(cfg))
    except RuntimeError as e:
        log("[game] %s, 转下一通道" % e)
        return "fail"
    if not uin or not jar.get("skey"):
        log("[game] 游戏 cookie 库里没有登录态(游戏重启后需开一次活动页), 转下一通道")
        return "fail"
    if dead_uins is not None and uin not in dead_uins:
        log("[game] 游戏 cookie 库的 QQ(%s) 不在失效名单, 转下一通道" % mask(uin))
        return "fail"
    matched = _match(data, uin)
    if not matched:
        log("[game] 游戏 cookie 库的 QQ(%s) 不在 roles.json, 转下一通道" % mask(uin))
        return "fail"
    changed = any(r.get("cookies", {}).get("skey") != jar.get("skey") for r in matched)
    ok, info = ams.probe(jar, matched[0])
    if not ok:
        log("[game] 游戏缓存凭据已失效(%s), 转下一通道" % info)
        return "fail"
    if not changed:
        log("[game] 凭据无变化且有效(%s)" % info)
        return "ok"
    for r in matched:
        r["cookies"] = dict(jar)
    log("[game] 已更新 %d 个角色的凭据(%s)" % (len(matched), info))
    return "updated"


def try_qqnt(data, dead_uins=None):
    """通道 qqnt: QQNT 桥 SSO 多账号(见 qqnt.py)。返回 updated / ok / fail"""
    from wuxia_autoclaim.qqnt import update_roles
    try:
        updated, fixed = update_roles(data, dead_uins)
    except RuntimeError as e:
        log("[qqnt] %s" % e)
        return "fail"
    if updated:
        return "updated"
    if fixed:
        return "ok"
    return "fail"


def try_napcat(data, cfg, bind_new=False, dead_uins=None):
    """通道 napcat: OneBot HTTP clientkey 签发(见 napcat.py)。返回 updated / ok / fail"""
    from wuxia_autoclaim.napcat import harvest_napcat, NapCatError
    try:
        updated, fixed = harvest_napcat(data, cfg, bind_new=bind_new, dead_uins=dead_uins)
    except NapCatError as e:
        log("[napcat] %s" % e)
        return "fail"
    if updated:
        return "updated"
    if fixed:
        return "ok"
    return "fail"


def harvest_once(data, cfg, order=None, bind_new=False, dead=None):
    """体检 -> 按通道顺序收割; 有更新时回写 roles.json。

    dead 允许传入调用方刚体检出的失效列表(runner 场景), 避免重复探测。
    返回 True=凭据全活或已修复; False=仍有失效。roles.json 不存在/为空由调用方先拦。
    """
    order = order or config.channel_order(cfg)
    if dead is None:
        dead = check_roles(data)
    if data.get("roles") and not dead:
        log("[check] %d 个角色凭据全部有效, 无需收割" % len(data["roles"]))
        return True
    dead_uins = {r.get("uin") for r in dead}

    channels = {
        "napcat": lambda: try_napcat(data, cfg, bind_new=bind_new, dead_uins=dead_uins),
        "qqnt": lambda: try_qqnt(data, dead_uins=dead_uins),
        "game": lambda: try_game(data, cfg, dead_uins=dead_uins),
    }
    for name in order:
        status = channels[name]()
        if status == "updated":
            config.save_roles(data)
            log("已回写 %s" % config.roles_file())
            return True
        if status == "ok":
            return True
    still = ", ".join((r.get("name") or r.get("uin", "?")) for r in dead)
    log("所有通道都没能修复失效角色: %s" % still)
    return False
