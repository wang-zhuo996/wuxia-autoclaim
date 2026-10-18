# -*- coding: utf-8 -*-
"""纯 HTTP 绑定: 活动自带的大区/角色绑定流, 无需浏览器

链路(2026-10-17 全程实测, 参照活动页 a20240325zzqy/index0925.js 与真机抓包):

    大区表    GET  gameact.qq.com/.../wuxia_server_select.js (GBK 静态两级树,
              大区组 v=7609515... -> 服务器 v=2001..., 服务器 v 即 amesvr 的 sArea)
    查询绑定  amesvr iFlowId=1021030 + query=true -> modRet.jData.data
              = {Farea, FroleId, FroleName, FareaName, ...} 或空(未绑定)
    角色列表  GET  comm.aci.game.qq.com/main?...&area=<服务器v> -> _webplat_msg/list
              (按服务器过滤; checkparam/md5str 供提交流, 含时间戳会过期)
    角色授权  GET  comm.aci.main?sCloudApiName=ams.gameattr.roleauth&operation=auth
              (浏览器绑定前必调, 幂等)
    提交绑定  amesvr iFlowId=1021029 + query=false + sRoleId/sRoleName(双重encodeURI)
              /sArea/sAreaName(双重encodeURI)/ams_md5str/ams_checkparam(单次encodeURI)

注意: amesvr 的角色校验(iRet=400)要求 sArea/sRole 与活动侧存储的绑定一致;
合服等历史原因会让旧绑定失效(即使参数一致也 400), 这时要重新提交一次绑定。

免交互自动化: config.yaml 的 bind: 段写好 QQ号 -> server/role 预设后, bind/harvest --bind-new
遇到该 QQ 直接按预设绑定(只查目标服务器, 不做全服扫描); 预设与现有有效绑定的
角色不同时以预设为准自动重绑(见 _bind_preset)。
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from wuxia_autoclaim import ams, config
from wuxia_autoclaim.log import log
from wuxia_autoclaim.util import calc_gtk, cookie_header, mask

FLOW_BIND = "1021029"          # 提交绑定大区(通用大区绑定模块)
FLOW_BIND_QUERY = "1021030"    # 查询绑定大区

ACI_URL = "https://comm.aci.game.qq.com/main"
AREA_JS_URL = "https://gameact.qq.com/comm-htdocs/js/game_area/wuxia_server_select.js"
SCAN_INTERVAL = 0.1            # 全服扫描的相邻请求间隔(秒), 温和一些

# encodeURI 的不转义字符集(JS), 提交流的编码要跟浏览器一致
_URI_SAFE = "!#$&'()*+,;=:@/?~-_."


class WebBindError(RuntimeError):
    """绑定链路的接口/解析失败"""


# ---------------------------------------------------------------- 编码助手

def uri(s):
    """等价 JS encodeURI"""
    return urllib.parse.quote(str(s), safe=_URI_SAFE)


def uri2(s):
    """等价 JS encodeURI(encodeURI(x)), 提交流的 sRoleName/sAreaName 用"""
    return uri(uri(s))


# ---------------------------------------------------------------- 纯函数解析器

def parse_area_js(text):
    """解析大区表 JS(GBK 已解码)的两级 STD_DATA 树。

    返回 [{"t": 大区组名, "v": 组id, "servers": [{"t": 服务器名, "v": 服务器id}]}]
    """
    groups = []
    for m in re.finditer(r'\{t:\s*"([^"]+)",\s*v:\s*"(\d+)",\s*opt_data_array:\s*\['
                         r'(.*?)\r?\n\s*\]\}', text, re.S):
        servers = [{"t": t, "v": v}
                   for t, v in re.findall(r'\{t:\s*"([^"]+)",\s*v:\s*"(\d+)"', m.group(3))]
        if servers:
            groups.append({"t": m.group(1), "v": m.group(2), "servers": servers})
    if not groups:
        raise WebBindError("大区表格式变了, 无法解析: %s" % text[:200])
    return groups


def _aci_field(body, key):
    m = re.search(r"%s:\s*'([^']*)'" % key, body)
    return m.group(1) if m else ""


def parse_aci(body):
    """解析 ACI 角色列表 JSONP 文本, 返回 (roles, extras)。

    roles = [{"roleid", "name", "level"}]; extras 含提交流要的 checkparam/md5str。
    该服务器没有角色时 roles 为空; 结构对不上抛 WebBindError(绝不猜)。
    """
    m = re.search(r"retCode:\s*(-?\d+)", body)
    if not m:
        raise WebBindError("ACI 响应无法解析: %s" % body[:200])
    extras = {"checkparam": _aci_field(body, "checkparam"),
              "md5str": _aci_field(body, "md5str")}
    if int(m.group(1)) != 0:
        return [], extras
    data = urllib.parse.unquote(_aci_field(body, "data"))
    mc = re.search(r"_webplat_msg=(.*?)&_webplat_msg_code=(\d+)", data)
    if not mc:
        raise WebBindError("ACI data 里没有 _webplat_msg: %s" % data[:200])
    if mc.group(2) != "0":
        return [], extras                     # 1 = 该服务器没有角色
    roles = {}
    # _webplat_msg: plat|roleid 名字 [下个plat] |...  奇数段才是角色
    for seg in mc.group(1).split("|")[1:]:
        t = seg.split(" ")
        if not t[0].isdigit() or len(t[0]) < 10:
            continue                          # 段首的 plat 号/空段
        name = " ".join(x for x in t[1:] if x)
        name = re.sub(r"(?:^| )\d+$", "", name).strip()   # 去掉尾随的下个 plat 号
        roles[t[0]] = {"roleid": t[0], "name": name, "level": ""}
    # list 段字段更全: plat|roleid 名字 等级 X 0|...
    ml = re.search(r"&list=(.*?)&result=", data)
    if ml:
        for seg in ml.group(1).split("|"):
            t = seg.split(" ")
            if len(t) >= 5 and t[0].isdigit() and len(t[0]) >= 10:
                roles[t[0]] = {"roleid": t[0], "name": t[1],
                               "level": t[2] if t[2].isdigit() else ""}
    return list(roles.values()), extras


def parse_binding(resp):
    """从查询流(1021030)响应里取绑定, 返回 dict 或 None(未绑定)"""
    try:
        d = resp["modRet"]["jData"]["data"]
    except (KeyError, TypeError):
        return None
    return d if isinstance(d, dict) and d.get("FroleId") else None


def build_bind_body(area, server_name, role, extras):
    """提交流(1021029)的表单体, 与活动页 milo-next roleCallback 的字段一致"""
    return {"query": "false",
            "sRoleId": role["roleid"], "sRoleName": uri2(role["name"]),
            "sArea": str(area), "sAreaName": uri2(server_name),
            "ams_md5str": extras.get("md5str", ""),
            "ams_checkparam": uri(extras.get("checkparam", ""))}


# ---------------------------------------------------------------- HTTP 封装

def _get(url, cookie, referer="https://wuxia.qq.com/"):
    req = urllib.request.Request(url)
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", ams.UA)
    req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def _amesvr_post(cookie, gtk, flow_id, extra):
    """按 ams.emit 的形态 POST amesvr(绑定流不带 sArea/sRole 标准位)"""
    body = {"sServiceType": "wuxia", "iActivityId": ams.ACT_ID,
            "sServiceDepartment": "group_9", "iFlowId": flow_id,
            "g_tk": gtk, "e_code": ams.E_CODE, "g_code": "0"}
    body.update(extra)
    url = ams.BASE + "?sServiceType=wuxia&iActivityId=" + ams.ACT_ID \
        + "&sServiceDepartment=group_9&sSDID=" + ams.SDID
    req = urllib.request.Request(url, data=urllib.parse.urlencode(body).encode())
    req.add_header("Cookie", cookie)
    req.add_header("User-Agent", ams.UA)
    req.add_header("Referer", "https://wuxia.qq.com/")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def query_binding(cookie, gtk):
    """查活动侧已绑定的大区/角色; 返回绑定 dict 或 None(未绑定)"""
    try:
        resp = _amesvr_post(cookie, gtk, FLOW_BIND_QUERY, {"query": "true"})
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WebBindError("查询绑定接口异常: %s" % e)
    iret = ams.is_login_expired(resp)
    if iret == "101":
        raise WebBindError("查询绑定 iRet=101 登录态失效")
    if iret != "0":
        raise WebBindError("查询绑定 iRet=%s %s" % (iret, (resp.get("flowRet") or {}).get("sMsg", "")))
    return parse_binding(resp)


def fetch_area_tree():
    """拉取并解析大区表(静态 JS, GBK)"""
    try:
        raw = _get(AREA_JS_URL, cookie="", referer="https://game.qq.com/")
    except (urllib.error.URLError, OSError) as e:
        raise WebBindError("大区表拉取失败: %s" % e)
    return parse_area_js(raw.decode("gbk", errors="replace"))


def fetch_roles(cookie, area):
    """查指定服务器上的角色, 返回 (roles, extras)"""
    url = ACI_URL + "?" + urllib.parse.urlencode(
        {"sCloudApiName": "ams.gameattr.role", "game": "wuxia",
         "area": str(area), "callback": "cb", "returnCodec": "utf8"})
    try:
        body = _get(url, cookie).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        raise WebBindError("ACI 接口异常(area=%s): %s" % (area, e))
    return parse_aci(body)


def scan_all_roles(cookie, tree):
    """全服扫描, 返回 [{group, server, area, roles, extras}](只含有角色的服务器)"""
    servers = [(g["t"], s["t"], s["v"]) for g in tree for s in g["servers"]]
    log("[bind] 扫描 %d 个服务器寻找角色..." % len(servers))
    found, errors = [], 0
    for i, (gname, sname, sv) in enumerate(servers):
        if i:
            time.sleep(SCAN_INTERVAL)
        try:
            roles, extras = fetch_roles(cookie, sv)
        except WebBindError as e:
            errors += 1
            if errors <= 3:
                log("[bind] %s" % e)
            continue
        if roles:
            log("[bind] %s-%s: %d 个角色" % (gname, sname, len(roles)))
            found.append({"group": gname, "server": sname, "area": sv,
                          "roles": roles, "extras": extras})
    if errors == len(servers):
        raise WebBindError("ACI 全部请求失败, 网络或凭据有问题")
    return found


def roleauth(cookie, act_url="https://wuxia.qq.com/act/a20240325zzqy/index.html"):
    """角色信息使用授权(浏览器绑定前必调, 幂等); 失败不阻塞, 只记日志"""
    url = ACI_URL + "?" + urllib.parse.urlencode(
        {"sCloudApiName": "ams.gameattr.roleauth", "url": act_url,
         "operation": "auth", "game": "wuxia", "callback": "cb"})
    try:
        _get(url, cookie)
    except (urllib.error.URLError, OSError) as e:
        log("[bind] roleauth 调用失败(忽略): %s" % e)


def submit_bind(cookie, gtk, area, server_name, role, extras):
    """提交绑定到活动, 返回 (ok, 消息)"""
    try:
        resp = _amesvr_post(cookie, gtk, FLOW_BIND, build_bind_body(area, server_name, role, extras))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise WebBindError("提交绑定接口异常: %s" % e)
    iret = ams.is_login_expired(resp)
    msg = (resp.get("flowRet") or {}).get("sMsg") or (resp.get("modRet") or {}).get("sMsg") or iret
    return iret == "0", "iRet=%s %s" % (iret, msg)


def _binding_alive(cookie, gtk, area, roleid):
    """绑定是否真的可用(合服后的陈旧绑定即使参数一致也会 400)"""
    try:
        resp = ams.emit(cookie, gtk, ams.FLOW_INIT, str(area), str(roleid))
    except Exception:
        return False
    return ams.is_login_expired(resp) == "0"


# ---------------------------------------------------------------- 终端交互

def _tty():
    return sys.stdin is not None and sys.stdin.isatty()


def _pick_role_terminal(entries, uin):
    """全服扫描结果里选一个角色; 返回条目或 None(取消)"""
    print("\n[bind] QQ%s 找到 %d 个角色:" % (mask(uin), len(entries)))
    for i, e in enumerate(entries, 1):
        r = e["role"]
        lv = "Lv.%s" % r["level"] if str(r.get("level", "")).isdigit() else ""
        print("    %d. %s  %-7s %s(%s)" % (i, r["name"], lv, e["server"], e["group"]))
    while True:
        try:
            s = input("选择要绑定的角色序号(回车=取消): ").strip()
        except EOFError:
            return None
        if not s:
            return None
        if s.isdigit() and 1 <= int(s) <= len(entries):
            break
        print("    输入无效, 请重新输入")
    e = entries[int(s) - 1]
    try:
        c = input("确认绑定 %s / %s ?(回车=确认, 其他输入=取消): "
                  % (e["server"], e["role"]["name"])).strip()
    except EOFError:
        return None
    return None if c else e


# ---------------------------------------------------------------- 编排

def _write_role(data, uin, area, roleid, name, jar, save):
    """FroleName 按页面行为是 percent 编码的, 先解一遍再写"""
    if "%" in str(name or ""):
        name = urllib.parse.unquote(str(name))
    config.upsert_role(data, str(area), str(roleid), name, jar, uin)
    if save:
        config.save_roles(data)


def _do_submit(cookie, gtk, area, server, role, extras):
    """提交 + checkparam 过期重试一次"""
    try:
        ok, msg = submit_bind(cookie, gtk, area, server, role, extras)
    except WebBindError as e:
        return False, str(e)
    if not ok:
        # checkparam/md5str 带时间戳, 选太久会过期: 原服重拉一次再试
        try:
            roles2, extras2 = fetch_roles(cookie, area)
            same = [r for r in roles2 if r["roleid"] == role["roleid"]]
            if same:
                ok, msg = submit_bind(cookie, gtk, area, server, same[0], extras2)
        except WebBindError:
            pass
    return ok, msg


def _confirm_write(cookie, gtk, jar, uin, area, role, server_name, data, save):
    """提交成功后的收尾: 回查确认(查不到也按已提交的选择写), 返回汇报消息"""
    try:
        ba = query_binding(cookie, gtk)
    except WebBindError:
        ba = None
    if ba and ba.get("Farea"):
        _write_role(data, uin, ba.get("Farea", ""), ba.get("FroleId", ""),
                    ba.get("FroleName", ""), jar, save)
        return "绑定成功: %s区 %s(%s), 已写入" % (ba.get("Farea"), role["name"], server_name)
    _write_role(data, uin, area, role["roleid"], role["name"], jar, save)
    return "绑定成功: %s区 %s(%s), 已写入" % (area, role["name"], server_name)


def _find_server(tree, name):
    """大区表里按服务器名找条目(两侧去空白), 返回 (组, 服务器) 或 (None, None)"""
    target = str(name).strip()
    for g in tree:
        for s in g["servers"]:
            if s["t"].strip() == target:
                return g, s
    return None, None


def _preset_differs(preset, ba):
    """现有绑定与 bind 预设的角色是否不同(绑定里的名字可能带 percent 编码)。

    只比角色名: 名字是账号内最强身份; FroleName 缺失时视为相同(不折腾有效绑定)。
    """
    name = str(ba.get("FroleName") or "")
    if not name:
        return False
    if "%" in name:
        name = urllib.parse.unquote(name)
    return name.strip() != str(preset["role"]).strip()


def _bind_preset(cookie, gtk, jar, uin, preset, data, save):
    """按 config.yaml 的 bind 预设直接绑定: 只查目标服务器, 不做全服扫描、无需交互"""
    server_name, role_name = preset["server"], preset["role"]
    try:
        tree = fetch_area_tree()
    except WebBindError as e:
        return False, str(e)
    group, sv = _find_server(tree, server_name)
    if sv is None:
        return False, "bind 预设里的服务器「%s」不在大区表中(名字写对了吗?)" % server_name
    log("[bind] QQ%s 按 bind 预设绑定: %s(%s-%s)"
        % (mask(uin), role_name, sv["t"], group["t"]))
    try:
        roles, extras = fetch_roles(cookie, sv["v"])
    except WebBindError as e:
        return False, str(e)
    role = next((r for r in roles if str(r["name"]).strip() == str(role_name).strip()), None)
    if role is None:
        got = "、".join(str(r["name"]) for r in roles) or "该服务器上没有角色"
        return False, "「%s」上没有找到角色「%s」(%s)" % (server_name, role_name, got)
    roleauth(cookie)                       # 浏览器绑定前必调的授权, 幂等
    ok, msg = _do_submit(cookie, gtk, sv["v"], sv["t"], role, extras)
    if not ok:
        return False, "提交绑定失败(%s)" % msg
    return True, _confirm_write(cookie, gtk, jar, uin, sv["v"], role, sv["t"], data, save)


def bind_role_http(jar, uin, data=None, pick_role=None, presets=None):
    """纯 HTTP 绑定: 查活动侧绑定 -> bind 预设直绑 / 扫描选角色 -> 提交 -> 回查 -> 写 roles。

    presets 传 None 时自动读 config.yaml 的 bind: 段; 命中该 QQ 时走预设直绑
    (未绑定/已失效/与预设不符都会绑成预设, 不扫描不交互)。
    pick_role 传 None 时非交互(只补齐有效绑定, 供收割链路用);
    交互时传 pick_role(entries, uin), entries = [{"group","server","area","role","extras"}],
    返回选中条目或 None(取消)。
    data 传入时就地 upsert 但不落盘(调用方 save); 不传则自装自存。
    返回 (ok, 消息)。
    """
    own_data = data is None
    if own_data:
        data = config.load_roles() or {"roles": []}
    if presets is None:
        try:
            presets = config.load_bindmap()
        except config.ConfigError as e:
            return False, "config.yaml 的 bind 预设有问题(%s)" % e
    preset = presets.get(str(uin))
    cookie = cookie_header(jar)
    gtk = calc_gtk(jar["skey"]) if jar.get("skey") else 0
    try:
        ba = query_binding(cookie, gtk)
    except WebBindError as e:
        return False, "查询绑定状态失败(%s)" % e

    if ba:
        area, roleid = str(ba.get("Farea", "")), str(ba.get("FroleId", ""))
        if _binding_alive(cookie, gtk, area, roleid):
            if not (preset and _preset_differs(preset, ba)):
                _write_role(data, uin, area, roleid, ba.get("FroleName", ""), jar, own_data)
                return True, "活动已有有效绑定(%s区 角色%s), 已写入" % (area, mask(roleid, 4, 4))
            log("[bind] QQ%s 现有绑定的角色与 bind 预设不符, 按预设重绑" % mask(uin))
        else:
            # 陈旧绑定(合服/删角色): 需要重绑
            if preset is None and pick_role is None:
                return False, ("QQ%s 的绑定已失效(%s区 角色%s, 可能合服过), 请在 config.yaml "
                               "的 bind: 段写预设或在终端跑 wuxia-autoclaim bind --uin %s 重新选择"
                               % (mask(uin), area, mask(roleid, 4, 4), uin))
            if preset is None:
                log("[bind] QQ%s 现有绑定(%s区)已失效, 需要重新选择角色" % (mask(uin), area))

    if preset:
        return _bind_preset(cookie, gtk, jar, uin, preset, data, own_data)

    if pick_role is None:
        return False, ("QQ%s 未绑定大区: 请在 config.yaml 的 bind: 段写预设或在终端跑 "
                       "wuxia-autoclaim bind --uin %s 交互完成" % (mask(uin), uin))
    try:
        tree = fetch_area_tree()
        found = scan_all_roles(cookie, tree)
    except WebBindError as e:
        return False, str(e)
    entries = [{"group": f["group"], "server": f["server"], "area": f["area"],
                "role": r, "extras": f["extras"]}
               for f in found for r in f["roles"]]
    if not entries:
        return False, "所有服务器都没有找到角色(这个 QQ 在天刀建过角色吗?)"

    sel = pick_role(entries, uin)
    if sel is None:
        return False, "已取消选择"
    area, role, extras = sel["area"], sel["role"], sel["extras"]

    roleauth(cookie)                       # 浏览器绑定前必调的授权, 幂等
    ok, msg = _do_submit(cookie, gtk, area, sel["server"], role, extras)
    if not ok:
        return False, "提交绑定失败(%s)" % msg
    return True, _confirm_write(cookie, gtk, jar, uin, area, role, sel["server"], data, own_data)


# ---------------------------------------------------------------- 通道账号发现

def channel_accounts(cfg):
    """探测在线通道账号(qqnt 桥 + napcat 端点), 返回 {uin: {"nick", "mint": ()->jar}}。

    mint 现取现用(clientkey 短时效); 单通道失败只记日志。
    """
    out = {}
    try:
        from wuxia_autoclaim.qqnt import QqntBridge
        br = QqntBridge()
        for uin, info in br.accounts().items():
            out[uin] = {
                "nick": info["nick"],
                "mint": lambda u=uin, p=info["port"]: br.mint(
                    u, br.clientkey(u, p), xlogin_first=False)}
    except Exception as e:
        log("[bind] qqnt 桥不可用(%s)" % e)
    eps = cfg.get("napcat") or []
    if eps:
        from wuxia_autoclaim import napcat
        from wuxia_autoclaim.ptlogin import PtClient
        for ep in eps:
            url, token = (ep.get("url") or "").strip(), (ep.get("token") or "").strip()
            if not url:
                continue
            try:
                info = napcat.call(url, token, "get_login_info", timeout=5)
            except Exception as e:
                log("[bind] napcat %s: %s" % (url, e))
                continue
            uin = str(info.get("user_id", ""))
            if not uin or uin in out:
                continue

            def _mint(u=uin, u2=url, t=token):
                return PtClient().mint(u, napcat.call(u2, t, "get_clientkey")["clientkey"])
            out[uin] = {"nick": info.get("nickname", ""), "mint": _mint}
    return out


# ---------------------------------------------------------------- CLI 入口

def _bind_candidate(cand, data, online, picker):
    """绑定一个候选账号: 现有凭据优先, 失效且通道在线时重新签发"""
    uin, jar = cand["uin"], dict(cand.get("jar") or {})
    if cand.get("role") is None:                      # 新账号: 直接走通道签发
        try:
            jar = online[uin]["mint"]()
        except Exception as e:
            return False, "通道签发凭据失败(%s)" % e
    try:
        ok, msg = bind_role_http(jar, uin, data=data, pick_role=picker)
    except Exception as e:
        return False, "运行异常: %s" % e
    if not ok and "iRet=101" in msg and uin in online:
        log("[bind] QQ%s 现有凭据失效, 经通道重新签发..." % mask(uin))
        try:
            jar = online[uin]["mint"]()
        except Exception as e:
            return False, "通道签发凭据失败(%s)" % e
        try:
            ok, msg = bind_role_http(jar, uin, data=data, pick_role=picker)
        except Exception as e:
            return False, "运行异常: %s" % e
    return ok, msg


def cmd(args):
    """bind 子命令: 绑定大区/角色(纯 HTTP; config.yaml bind 预设的号免交互直绑, 其余交互选角色)"""
    try:
        cfg = config.load_config()
        data = config.load_roles()
    except config.ConfigError as e:
        log("[!] %s" % e)
        return 2
    fresh = data is None
    if fresh:
        data = {"roles": []}
    online = channel_accounts(cfg)
    roles = data.get("roles", [])
    known = {str(r.get("uin", "")) for r in roles}

    cands = []
    if args.uin:
        cands = [{"uin": args.uin, "role": r, "nick": r.get("name", ""),
                  "jar": r.get("cookies", {})}
                 for r in roles if str(r.get("uin", "")) == args.uin]
        if args.uin in online:
            cands.append({"uin": args.uin, "role": None,
                          "nick": online[args.uin]["nick"], "jar": None})
        if not cands:
            log("[!] roles.json 和在线通道里都没有 QQ%s" % args.uin)
            return 2
    else:
        cands = [{"uin": str(r.get("uin", "")), "role": r, "nick": r.get("name", ""),
                  "jar": r.get("cookies", {})}
                 for r in roles if not (r.get("area") and r.get("roleid"))]
        for uin, info in online.items():
            if uin not in known:
                cands.append({"uin": uin, "role": None, "nick": info["nick"], "jar": None})
        if not cands:
            if roles:
                log("[bind] 所有角色都已绑定大区/角色, 无需处理")
                return 0
            log("[!] 没有可绑定的账号: 配置 NapCat(config napcat-add, 见 README)"
                "或打开 QQ 桌面客户端后重跑; 在 config.yaml 的 bind: 段写好 QQ->服务器/角色 预设"
                "即可对这些号全自动绑定; 也可跑 login 用浏览器登录绑定")
            return 2

    if len(cands) > 1 and _tty():
        print("\n[bind] 待绑定账号:")
        for i, c in enumerate(cands, 1):
            kind = "roles.json 缺绑定" if c.get("role") else "新账号(通道在线)"
            print("    %d. QQ%s %s [%s]" % (i, mask(c["uin"]), c["nick"] or "?", kind))
        print("    0. 全部")
        try:
            s = input("选择序号(回车=取消): ").strip()
        except EOFError:
            s = ""
        if not s:
            log("[bind] 已取消")
            return 0
        if s == "0":
            pass                                   # 全部
        elif s.isdigit() and 1 <= int(s) <= len(cands):
            cands = [cands[int(s) - 1]]
        else:
            log("[bind] 输入无效, 已退出(未做任何修改)")
            return 0

    picker = _pick_role_terminal if (_tty() and not args.quiet) else None
    okn = badn = 0
    for c in cands:
        ok, msg = _bind_candidate(c, data, online, picker)
        log("[bind] QQ%s: %s" % (mask(c["uin"]), msg))
        if ok:
            okn += 1
        else:
            badn += 1
    if okn:
        config.save_roles(data)                       # 只有真写入了才落盘(fresh 时不会留空文件)
        log("已回写 %s" % config.roles_file())
    return 0 if not badn else 1
