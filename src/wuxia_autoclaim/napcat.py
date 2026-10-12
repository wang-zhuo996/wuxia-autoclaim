# -*- coding: utf-8 -*-
"""NapCat 通道: OneBot 11 HTTP -> clientkey -> ptlogin jump 签发全套凭据

NapCat(NapNeko/NapCatQQ) 是基于 QQNT 的无头机器人框架, 适合服务器 7x24 保持
QQ 在线。它提供两个关键 OneBot 扩展 action(已从 main 分支源码确认):

    get_login_info  -> {"user_id": QQ号, "nickname": 昵称}     (探明端点登录的是谁)
    get_clientkey   -> {"clientkey": "..."}                    (等价本地桥的 clientkey)
    get_cookies     -> {"cookies": "k=v; ...", "bkn": "..."}   (按域名取 cookie, 备用)

拿到 clientkey 后走 ptlogin.mint() 两次 jump 签发 7 键 cookie, 与本地桥同源同链路,
区别只在 clientkey 从 HTTP 接口来 -> Linux 服务器也能收割凭据。

前置配置(NapCat WebUI -> 网络配置 -> 新建 HTTP 服务, 详见 README):
    监听端口(默认示例 3000) + 强烈建议设置 token(clientkey 等同登录态!)
然后:  wuxia-autoclaim config napcat-add http://127.0.0.1:3000 <token>
"""
import json
import urllib.error
import urllib.request

from wuxia_autoclaim import ams
from wuxia_autoclaim.log import log
from wuxia_autoclaim.ptlogin import PtClient
from wuxia_autoclaim.util import mask, COOKIE_KEEP

# get_cookies 兜底时依次尝试的域名(NT 内核支持的域名清单有限, 能取几键算几键)
FALLBACK_DOMAINS = ("game.qq.com", "qq.com", "qun.qq.com")


class NapCatError(RuntimeError):
    """NapCat 接口调用失败(连不上/鉴权错/action 不存在等)"""


def call(url, token, action, params=None, timeout=10):
    """调一个 OneBot HTTP action, 返回 data 字段(dict)"""
    body = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise NapCatError("鉴权失败(HTTP %d): 检查 config 里该端点的 token" % e.code)
        raise NapCatError("HTTP %d: %s (地址/端口对吗?)" % (e.code, getattr(e, "reason", "")))
    except urllib.error.URLError as e:
        raise NapCatError("连不上 %s (%s): NapCat WebUI 里开了 HTTP 服务吗?(见 README 部署节)"
                          % (url, getattr(e, "reason", e)))
    except (ValueError, OSError) as e:
        raise NapCatError("响应无法解析(%s): %s" % (url, e))
    if payload.get("retcode") != 0:
        msg = payload.get("message") or payload.get("wording") or ""
        raise NapCatError("action %s 失败(retcode=%s %s): NapCat 版本过旧或会话掉线?"
                          % (action, payload.get("retcode"), msg))
    return payload.get("data") or {}


def parse_cookie_str(s):
    """把 get_cookies 返回的 "uin=o...; skey=...;" 解析成 dict"""
    jar = {}
    for part in (s or "").split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            if k and v:
                jar[k] = v
    return jar


def _get_cookies_fallback(url, token):
    """jump 链路失败时的备用: 直接向 NapCat 要各域 cookie, 拼 7 键"""
    jar = {}
    for dom in FALLBACK_DOMAINS:
        try:
            data = call(url, token, "get_cookies", {"domain": dom}, timeout=10)
        except NapCatError as e:
            log("[napcat] get_cookies(%s) 失败: %s" % (dom, e))
            continue
        for k, v in parse_cookie_str(data.get("cookies", "")).items():
            if k in COOKIE_KEEP and k not in jar:
                jar[k] = v
    if not jar.get("uin"):
        jar["uin"] = "o"          # 占位, 调用方拿 uin 从账号信息补
    return jar


def harvest_napcat(data, cfg, bind_new=False, dead_uins=None):
    """遍历配置的 napcat 端点, 给 roles 里匹配的角色签发新凭据(就地更新 data)。

    dead_uins 是本轮判定失效的 QQ 号集合; 命中且探测有效才计入"修复"。
    返回 (更新数, 修复的失效账号数); 端点全不可用时抛 NapCatError。
    """
    eps = cfg.get("napcat") or []
    if not eps:
        log("[napcat] 未配置 napcat 端点(README 部署节, 或 config napcat-add <url> [token])")
        return 0, 0
    updated = fixed = 0
    alive_eps = 0
    seen = set()
    for ep in eps:
        url, token = (ep.get("url") or "").strip(), (ep.get("token") or "").strip()
        if not url:
            continue
        try:
            info = call(url, token, "get_login_info")
        except NapCatError as e:
            log("[napcat] %s" % e)
            continue
        alive_eps += 1
        uin = str(info.get("user_id", ""))
        nick = info.get("nickname", "")
        if not uin or uin in seen:
            continue
        seen.add(uin)
        matched = [r for r in data.get("roles", []) if r.get("uin") == uin]
        if not matched:
            log("[napcat] QQ%s(%s) 不在 roles.json, 跳过(纳入: config set auto_bind true"
                " 或 harvest --bind-new)" % (mask(uin), nick))
            if bind_new or cfg.get("auto_bind"):
                _bind_new(url, token, uin, nick, data)
            continue
        jar = None
        try:
            ck = call(url, token, "get_clientkey").get("clientkey", "")
            if not ck:
                raise RuntimeError("clientkey 为空(QQ 被踢/未登录?)")
            jar = PtClient().mint(uin, ck)
        except (NapCatError, RuntimeError) as e:
            log("[napcat] clientkey 签发失败(%s), 转备用通道 get_cookies" % e)
        if not jar or not jar.get("skey"):
            jar = _get_cookies_fallback(url, token)
            jar["uin"] = "o" + uin
        if not jar.get("skey"):
            log("[napcat] QQ%s 备用通道也没拿到 skey, 放弃该端点" % mask(uin))
            continue
        ok, desc = ams.probe(jar, matched[0])
        if not ok:
            log("[napcat] QQ%s 新签凭据探测失败(%s), 不写入" % (mask(uin), desc))
            continue
        if dead_uins is None or uin in dead_uins:
            fixed += 1
        changed = any(r.get("cookies", {}).get("skey") != jar.get("skey") for r in matched)
        for r in matched:
            r["cookies"] = dict(jar)
        if changed:
            updated += len(matched)
            log("[napcat] QQ%s(%s) 已签发并更新 %d 个角色(%s, 正本=%s)"
                % (mask(uin), nick, len(matched), desc,
                   "有" if jar.get("pt4_token") else "无"))
        else:
            log("[napcat] QQ%s(%s) 凭据与当前一致(%s)" % (mask(uin), nick, desc))
    if not alive_eps:
        raise NapCatError("所有 napcat 端点都不可用")
    return updated, fixed


def _bind_new(url, token, uin, nick, data):
    """收割到新 QQ 时尝试无头绑定角色(bind 模块延迟导入, 缺 playwright 时给指引)"""
    try:
        ck = call(url, token, "get_clientkey").get("clientkey", "")
        if not ck:
            raise RuntimeError("clientkey 为空")
        jar = PtClient().mint(uin, ck)
    except (NapCatError, RuntimeError) as e:
        log("[napcat] 新账号 %s(%s) 拿凭据失败(%s), 不绑定" % (mask(uin), nick, e))
        return
    from wuxia_autoclaim import bind           # 延迟导入(playwright 相关)
    ok, msg = bind.bind_role(jar, uin, data=data)
    log("[napcat] 新账号自动绑定: %s" % msg)
