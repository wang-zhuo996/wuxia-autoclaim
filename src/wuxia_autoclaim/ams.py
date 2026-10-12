# -*- coding: utf-8 -*-
"""活动接口层: 天刀「周周载愿」AMS 只读/写接口 (自 wuxia-autosign autosign.py 移植)

活动换期时只需要改这里的常量(actId/sSDID/e_code/flowId)。
"""
import json
import urllib.parse
import urllib.request

from wuxia_autoclaim.util import cookie_header, calc_gtk

ACT_ID = "625474"
SDID = "f049bee175806c1823da9aa01cedb2aa"
E_CODE = "536206"
BASE = "https://comm.ams.game.qq.com/ams/ame/amesvr"
URL_ACT = "https://wuxia.qq.com/cp/a20230309_98549/index.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

FLOW_INIT = "1021555"   # 初始化/查状态(只读, 也用作凭据探测)
FLOW_SIGN = "1020763"   # 每日许愿(签到)
FLOW_GET = "1020761"    # 周末一键领取


def emit(cookie, gtk, flow_id, sarea, srole, extra=None):
    """调一次 AMS 接口, 返回解析后的 JSON"""
    body = {
        "sServiceType": "wuxia", "iActivityId": ACT_ID,
        "sServiceDepartment": "group_9", "iFlowId": flow_id,
        "g_tk": gtk, "e_code": E_CODE, "g_code": "0",
        "sArea": sarea, "sRole": srole,
    }
    if extra:
        body.update(extra)
    url = BASE + "?sServiceType=wuxia&iActivityId=" + ACT_ID \
        + "&sServiceDepartment=group_9&sSDID=" + SDID
    req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode())
    req.add_header("Cookie", cookie)
    req.add_header("Referer", "https://wuxia.qq.com/")
    req.add_header("User-Agent", UA)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_init(resp):
    d = resp["modRet"]["jData"]["data"]
    return int(d["week"]), int(d["isTodaySign"]), d["giftNameArr"], int(d["toGetGiftCount"])


def is_login_expired(resp):
    """返回 iRet 字符串: 0=正常; 101=登录态失效; 99998=未绑定大区"""
    return str(resp.get("flowRet", {}).get("iRet", resp.get("modRet", {}).get("iRet", "0")))


def probe(jar, role):
    """用 FLOW_INIT 只读探测凭据有效性(不产生签到/许愿副作用), 返回 (ok, 说明)"""
    try:
        resp = emit(cookie_header(jar), calc_gtk(jar["skey"]),
                    FLOW_INIT, role.get("area", ""), role.get("roleid", ""))
    except Exception as e:
        return False, "接口异常: %s" % e
    iret = is_login_expired(resp)
    # 0=正常; 99998=登录态有效但该角色未绑定大区; 101=凭据已死
    return iret in ("0", "99998"), "iRet=%s" % iret
