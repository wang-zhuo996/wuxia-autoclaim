# -*- coding: utf-8 -*-
"""领取业务: 单角色许愿/周末领取主流程 (自 wuxia-autosign autosign.py 移植)

与旧版的差异只有文案: 失效指引改为本地动作(harvest/login), 不再提 GitHub。
"""
import urllib.parse
import urllib.request

from wuxia_autoclaim import config
from wuxia_autoclaim.ams import (emit, parse_init, is_login_expired,
                                 FLOW_INIT, FLOW_SIGN, FLOW_GET)
from wuxia_autoclaim.util import cookie_header, calc_gtk, mask


def run_role(role, cfg, interactive=False, gift_id=0, claim_only=False):
    """对单个角色执行签到主流程, 返回 (ok, 消息)

    cfg 为 config.load_config() 的结果; 奖励序号取值:
    role["gift_index"](run 的 --index 注入, 最优先) > cfg 的 gift_override[uin] > gift_index
    gift_id: 直接指定奖励的 giftId, 0 表示按上述序号/interactive 选择
    claim_only: 只领奖, 不做今天的许愿
    """
    name = role.get("name") or ("QQ%s" % role.get("uin", "?"))
    jar = role.get("cookies", {})
    skey = jar.get("skey", "")
    uin = role.get("uin") or jar.get("uin", "").lstrip("o")
    area, roleid = role.get("area", ""), role.get("roleid", "")
    if not skey:
        return False, "[%s] 缺少 skey 登录态, 请跑 harvest 收割或 login 重新登录" % name
    if not area or not roleid:
        return False, "[%s] 缺少 area/roleid, 请在终端跑 bind 交互绑定(或 login 浏览器登录)" % name

    cookie, gtk = cookie_header(jar), calc_gtk(skey)
    try:
        resp = emit(cookie, gtk, FLOW_INIT, area, roleid)
    except Exception as e:
        return False, "[%s] 接口异常: %s" % (name, e)
    iret = is_login_expired(resp)
    if iret == "101":
        return False, "[%s] 登录态已失效(iRet=101), 请保持 NapCat/QQ 在线跑 harvest, 或重新 login" % name
    if iret == "99998":
        return False, "[%s] 账号未绑定大区(iRet=99998), 请跑 bind 交互完成绑定(无需浏览器)" % name

    gift_index = role.get("gift_index") or config.gift_index_for(cfg, uin)
    week, signed, gifts, toget = parse_init(resp)
    day = "一二三四五六日"[week - 1]
    lines = ["[%s] QQ=%s 大区=%s 角色=%s" % (name, mask(uin), area, mask(roleid, 4, 4)),
             "奖励序号=%s 今日周%s 已许愿=%s 待领奖=%d"
             % (gift_index, day, bool(signed), toget)]

    # 周末: 一键领取本周奖励
    if week in (6, 7) and toget > 0:
        r = emit(cookie, gtk, FLOW_GET, area, roleid)
        lines.append("周末领取: %s" % r.get("flowRet", {}).get("sMsg", ""))

    if claim_only:
        return True, "\n".join(lines)

    if signed:
        lines.append("今日已许愿, 无需操作")
        return True, "\n".join(lines)

    if not gifts:
        return False, "\n".join(lines + ["没读到奖励列表, 请稍后重试"])

    if not gift_id:
        if interactive:
            for i, g in enumerate(gifts, 1):
                print("    %d. %s (giftId=%s)" % (i, g["name"], g["giftId"]))
            while True:
                s = input("请选择奖励序号 (直接回车=%s): " % gift_index).strip()
                if not s:
                    idx = int(gift_index)
                    break
                if s.isdigit() and 1 <= int(s) <= len(gifts):
                    idx = int(s)
                    break
                print("    输入无效, 请重新输入")
        else:
            idx = int(gift_index)
        idx = max(1, min(idx, len(gifts)))
        gift_id = gifts[idx - 1]["giftId"]

    r = emit(cookie, gtk, FLOW_SIGN, area, roleid, {"giftId": gift_id})
    iret2 = is_login_expired(r)
    pkg = r.get("modRet", {}).get("sPackageName", "")
    if iret2 == "0":
        lines.append("许愿成功: %s" % (pkg or "ok"))
        return True, "\n".join(lines)
    lines.append("许愿失败: %s" % r.get("flowRet", {}).get("sMsg", iret2))
    return False, "\n".join(lines)


def safe_run_role(role, cfg, **kw):
    """包装 run_role(), 把异常转成友好提示, 避免直接抛堆栈"""
    try:
        return run_role(role, cfg, **kw)
    except Exception as e:
        return False, "[%s] 运行异常: %s" % (role.get("name") or "?", e)


def select_roles(data, sel):
    """按 序号/名称/QQ号/roleid 挑选要跑的角色; 不选则全部"""
    roles = data.get("roles", [])
    if not sel:
        return roles
    if sel.isdigit():
        i = int(sel)
        if 1 <= i <= len(roles):
            return [roles[i - 1]]
    hits = [r for r in roles
            if sel in (r.get("name") or "") or sel in str(r.get("uin") or "")
            or sel == str(r.get("roleid") or "")]
    if hits:
        return hits
    print("[!] 没有匹配的角色: %s , 现有角色:" % sel)
    for i, r in enumerate(roles, 1):
        print("    %d. %s (QQ%s %s区)" % (i, r.get("name", "?"), r.get("uin", "?"), r.get("area", "?")))
    raise SystemExit(1)


def push_serverchan(key, title, desp=""):
    """Server酱推送 https://sct.ftqq.com"""
    if not key:
        return
    data = urllib.parse.urlencode({"title": title, "desp": desp}).encode()
    req = urllib.request.Request("https://sctapi.ftqq.com/%s.send" % key, data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print("[push]", r.read().decode("utf-8")[:100])
    except Exception as e:
        print("[push failed]", e)
