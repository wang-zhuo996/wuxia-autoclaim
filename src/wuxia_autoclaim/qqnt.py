# -*- coding: utf-8 -*-
"""官方 QQNT 客户端桥: 本地快速登录端口扫描 + 多账号凭据签发

自 wuxia-autosign sso.py 移植(纯 HTTP)。本机每个在线的 QQNT 实例各占一个
ptlogin2 快速登录桥端口(4301/4303/4305/4307/4309...), 各自公布自己登录的账号:

    1. 扫描所有桥端口 /pt_get_uins     -> 在线账号清单 {uin: (昵称, 端口)}
    2. 对目标账号在其端口 /pt_get_st   -> clientkey
    3. ptlogin.mint(uin, clientkey)    -> 全套 cookie(含 pt4_token 长期正本)

仅适用于装有官方 QQ 桌面客户端的机器(Windows/macOS/Linux 桌面);
服务器场景请走 napcat.py 通道。
"""
import json
import re
import time

from wuxia_autoclaim.log import log
from wuxia_autoclaim.ptlogin import PtClient
from wuxia_autoclaim.util import mask
from wuxia_autoclaim import ams


class QqntBridge(PtClient):
    # 每个在线 QQNT 实例占一个; 4310 部分版本也会出现
    PORTS = (4301, 4303, 4305, 4307, 4309, 4310)

    def __init__(self):
        PtClient.__init__(self, verify=False)   # 桥为自签证书
        self.xlogin()                            # 铺 .ptlogin2.qq.com 域 cookie + tk

    def _uins_on(self, port):
        """问一个桥端口要它登录的账号, 返回 [{uin,nickname}]; 端口不通返回 []"""
        try:
            body = self._uins_get(port)
            m = re.search(r"var_sso_uin_list=(\[.*?\]);", body, re.S)
            if not m:
                # 可能 token 过期了, 刷新后重试一次
                self.xlogin()
                body = self._uins_get(port)
                m = re.search(r"var_sso_uin_list=(\[.*?\]);", body, re.S)
                if not m:
                    return []
        except Exception:
            return []
        try:
            return json.loads(m.group(1))
        except Exception:
            return []

    def _uins_get(self, port):
        return self.get(
            "https://localhost.ptlogin2.qq.com:%d/pt_get_uins?callback=cb&r=%f&pt_local_tk=%s"
            % (port, time.time(), self.tk),
            "https://xui.ptlogin2.qq.com/", timeout=3)

    def accounts(self):
        """扫描所有桥端口, 返回 {uin: {'nick': 昵称, 'port': 端口}}"""
        out = {}
        for port in self.PORTS:
            for u in self._uins_on(port):
                uin = str(u.get("uin", ""))
                if uin and uin not in out:
                    out[uin] = {"nick": u.get("nickname", ""), "port": port}
        return out

    def clientkey(self, uin, port):
        """向桥要指定账号的 clientkey(短时效, 即取即用)"""
        # 实测: 非实例首选账号必须带 pt_local_data=1, 否则 400;
        # 偶发 400 时换新 pt_local_token 重试一次
        for _ in range(2):
            try:
                self.get(
                    "https://localhost.ptlogin2.qq.com:%d/pt_get_st?clientuin=%s"
                    "&callback=cb2&r=%f&pt_local_tk=%s&pt_local_data=1"
                    % (port, uin, time.time(), self.tk),
                    "https://xui.ptlogin2.qq.com/")
            except Exception:
                pass
            key = self.ck().get("clientkey", "")
            if key:
                return key
            self.xlogin()
        raise RuntimeError("账号 %s 在端口 %d 没有拿到 clientkey" % (mask(uin), port))


def update_roles(data, dead_uins=None):
    """扫描桥上所有在线账号, 给 roles 里匹配的角色签发新凭据(就地更新 data)。

    dead_uins 是本轮判定失效的 QQ 号集合; 命中且探测有效才计入"修复"。
    返回 (更新数, 修复的失效账号数)。
    """
    cli = QqntBridge()
    accs = cli.accounts()
    if not accs:
        raise RuntimeError("桥上没有在线账号(QQ 客户端开了吗?)")
    updated = fixed = 0
    for uin, info in accs.items():
        matched = [r for r in data.get("roles", []) if r.get("uin") == uin]
        if not matched:
            log("[qqnt] QQ%s(%s, 端口%d) 不在 roles.json, 跳过"
                % (mask(uin), info["nick"], info["port"]))
            continue
        jar = cli.mint(uin, cli.clientkey(uin, info["port"]), xlogin_first=False)
        ok, desc = ams.probe(jar, matched[0])
        if not ok:
            log("[qqnt] QQ%s 新签凭据探测失败(%s), 不写入" % (mask(uin), desc))
            continue
        if dead_uins is None or uin in dead_uins:
            fixed += 1
        changed = any(r.get("cookies", {}).get("skey") != jar.get("skey") for r in matched)
        for r in matched:
            r["cookies"] = dict(jar)
        if changed:
            updated += len(matched)
            log("[qqnt] QQ%s(%s) 已签发并更新 %d 个角色(%s, 正本=%s)"
                % (mask(uin), info["nick"], len(matched), desc,
                   "有" if jar.get("pt4_token") else "无"))
        else:
            log("[qqnt] QQ%s(%s) 凭据与当前一致(%s)" % (mask(uin), info["nick"], desc))
    return updated, fixed
