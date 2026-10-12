# -*- coding: utf-8 -*-
"""无头绑定: 用收割到的凭据打开活动页, 自动发现 area/roleid/playername

组合自旧项目:
    - renew.py 的 cookie 注入方案(DOMAIN_MAP, 按 cookie 分层写回对应域)
    - login.py 的 iframe 读取(EXPR, 从活动 iframe src 里抠角色三件套)

价值: 服务器上从 NapCat 拿到凭据后不用人工扫码, 直接无头完成角色绑定;
限制: 账号首次进入活动页需要手动选大区时无头模式等不到, 超时后提示改在
桌面环境跑一次 `login`(bind 的结果对同账号所有机器通用, roles.json 拷走即可)。
"""
import time

from wuxia_autoclaim import ams, config
from wuxia_autoclaim.login import read_role, ensure_playwright, MissingPlaywright
from wuxia_autoclaim.log import log
from wuxia_autoclaim.util import extract_cookies, mask

# 各 cookie 要写入的域(登录态分层, milo 双写时 .qq.com 为准)
DOMAIN_MAP = {
    "uin": ".qq.com", "skey": ".qq.com", "RK": ".qq.com", "ptcz": ".qq.com",
    "p_uin": ".game.qq.com", "p_skey": ".game.qq.com", "pt4_token": ".game.qq.com",
}


def build_cookies(jar):
    out = []
    for n, dom in DOMAIN_MAP.items():
        v = jar.get(n)
        if v:
            out.append({"name": n, "value": v, "domain": dom, "path": "/"})
    return out


def bind_role(jar, uin, data=None, headed=False, timeout=90):
    """无头打开活动页读取角色信息并写入 roles 数据, 返回 (ok, 消息)。

    data 传入时就地更新但不落盘(调用方决定何时 save); 不传则加载并落盘。
    """
    ensure_playwright()
    own_data = data is None
    if own_data:
        data = config.load_roles() or {"defaults": dict(config.DEFAULTS), "roles": []}
    from playwright.sync_api import sync_playwright
    area = roleid = playername = ""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        ctx = browser.new_context()
        ctx.add_cookies(build_cookies(jar))
        page = ctx.new_page()
        try:
            page.goto(ams.URL_ACT, wait_until="domcontentloaded", timeout=45000)
            deadline = time.time() + timeout
            while time.time() < deadline:
                area, roleid, playername = read_role(page)
                if roleid:
                    break
                time.sleep(5)
            # 页面自愈链路可能刷新了 cookie, 顺手收一把新的
            new_jar = extract_cookies(ctx.cookies())
            if new_jar.get("skey"):
                jar = new_jar
        except Exception as e:
            browser.close()
            return False, "QQ%s 绑定失败: 页面加载异常(%s)" % (mask(uin), e)
        browser.close()
    if not roleid:
        return False, ("QQ%s 绑定超时: 账号可能需要首次手选大区, 请在桌面环境跑一次"
                       " login --headed 后再回来" % mask(uin))
    config.upsert_role(data, area, roleid, playername, jar, uin)
    if own_data:
        config.save_roles(data)
    return True, "QQ%s 已绑定角色 %s (%s区)" % (mask(uin), playername or "?", area)


def cmd(args):
    """统一 CLI 的入口(args 需带 uin/headed/timeout 属性)"""
    data = config.load_roles()
    if data is None:
        log("[!] 未找到 %s, 请先跑 login 或 harvest" % config.roles_file())
        return 2
    roles = data.get("roles", [])
    if args.uin:
        targets = [r for r in roles if r.get("uin") == args.uin]
        if not targets:
            log("[!] roles.json 里没有 QQ%s" % args.uin)
            return 2
    else:
        targets = [r for r in roles if not (r.get("area") and r.get("roleid"))]
    if not targets:
        log("所有角色都已绑定大区/角色, 无需 bind")
        return 0
    try:
        ok_n = 0
        for r in targets:
            jar = r.get("cookies", {})
            if not jar.get("skey"):
                log("[bind] %s 缺 skey, 先跑 harvest 收割凭据" % (r.get("name") or r.get("uin")))
                continue
            ok, msg = bind_role(jar, r.get("uin"), data=data,
                                headed=args.headed, timeout=args.timeout)
            log(msg)
            ok_n += 1 if ok else 0
        if ok_n:
            config.save_roles(data)
            log("已回写 %s (%d 个角色绑定)" % (config.roles_file(), ok_n))
        return 0 if ok_n == len(targets) else 1
    except MissingPlaywright as e:
        log("[!] %s" % e)
        return 2


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="无头绑定角色(自动发现 area/roleid)")
    ap.add_argument("--uin", default="", help="只处理该 QQ 号(缺省=所有缺绑定信息的)")
    ap.add_argument("--headed", action="store_true", help="有头模式, 观察页面过程")
    ap.add_argument("--timeout", type=int, default=90, help="等待秒数(默认90)")
    args = ap.parse_args(argv)
    return cmd(args)
