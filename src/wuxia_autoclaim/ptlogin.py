# -*- coding: utf-8 -*-
"""ptlogin2 凭据签发核心: clientkey -> jump x2 -> 全套 cookie

自 wuxia-autosign sso.py 重构。链路(2026-10-07 实测打通, 见旧项目 wuxia-login-guide.md §8):

    1. xui.ptlogin2 xlogin          -> 铺 .ptlogin2.qq.com 域 cookie(含 pt_local_token)
    2. ssl.ptlogin2/jump appid=21000501 -> .qq.com 的 uin/skey/RK/ptcz
    3. ssl.ptlogin2/jump appid=716027609 -> .game.qq.com 的 p_skey/p_uin/pt4_token 长期正本

clientkey 的来源有两个(互相独立):
    - 本地 QQNT 客户端桥(见 qqnt.py, 需要客户端在线, 桥为自签证书 -> verify=False)
    - NapCat OneBot 的 get_clientkey action(见 napcat.py, 公网接口 -> verify=True)

坑(实测): 桥请求必须带 .ptlogin2.qq.com 域 cookie + 正确 Referer, 否则 400 空响应;
chromium 因 PNA 走不通, 必须脚本层直连。
"""
import http.cookiejar
import ssl
import time
import urllib.parse
import urllib.request

from wuxia_autoclaim.util import extract_cookies

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
APPID_WUXIA = "21000501"     # wuxia.qq.com 活动中心
APPID_GAME = "716027609"     # game.qq.com 域(p_skey/pt4_token 的签发方)
URL_ACT = "https://wuxia.qq.com/cp/a20230309_98549/index.html"
URL_PROXY = "https://game.qq.com/comm-htdocs/milo/proxy.html"


def xlogin_url():
    """xlogin 请求地址: 拿 pt_local_token(防CSRF)并铺 .ptlogin2.qq.com 域 cookie"""
    return ("https://xui.ptlogin2.qq.com/cgi-bin/xlogin?appid=%s"
            "&proxy_url=%s&target=self&s_url=%s"
            % (APPID_WUXIA, urllib.parse.quote(URL_PROXY, safe=""),
               urllib.parse.quote(URL_ACT, safe="")))


def jump_url(uin, clientkey, appid, u1):
    """用 clientkey 换目标站点登录态的 jump 请求地址"""
    return ("https://ssl.ptlogin2.qq.com/jump?clientuin=%s&clientkey=%s&u1=%s"
            "&appid=%s&daid=8&keyindex=19"
            % (uin, urllib.parse.quote(str(clientkey), safe=""),
               urllib.parse.quote(u1, safe=""), appid))


class PtClient:
    """带 cookiejar 的 urllib 会话。

    verify=False 仅给本地桥用(qqnt.py): 桥是自签证书且必须直连;
    公网路径(xlogin/jump)默认走证书校验。
    """

    def __init__(self, verify=True):
        self.ctx = ssl.create_default_context()
        if not verify:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self.ctx),
            urllib.request.HTTPCookieProcessor(self.jar))
        self.op.addheaders = [("User-Agent", UA)]
        self.tk = ""

    def get(self, url, referer, timeout=10):
        req = urllib.request.Request(url)
        req.add_header("Referer", referer)
        r = self.op.open(req, timeout=timeout)
        try:
            return r.read().decode("utf-8", "replace")
        except Exception:
            return ""

    def ck(self):
        return {c.name: c.value for c in self.jar}

    def xlogin(self):
        """第 1 步: xlogin 铺 cookie, 记下 pt_local_token"""
        self.get(xlogin_url(), "https://wuxia.qq.com/")
        self.tk = self.ck().get("pt_local_token", "")
        if not self.tk:
            raise RuntimeError("xlogin 没有下发 pt_local_token")

    def mint(self, uin, clientkey, xlogin_first=True):
        """完整签发链: 返回 7 键 cookie dict。

        xlogin_first=False 用于桥路径(构造时已经 xlogin 过, 避免重复请求)。
        """
        if xlogin_first and not self.tk:
            self.xlogin()
        # jump #1: wuxia appid -> .qq.com 全域登录态
        self.get(jump_url(uin, clientkey, APPID_WUXIA, URL_ACT),
                 "https://xui.ptlogin2.qq.com/")
        # jump #2: game 域 appid -> p_skey/p_uin/pt4_token 长期正本
        self.get(jump_url(uin, clientkey, APPID_GAME, URL_PROXY),
                 "https://xui.ptlogin2.qq.com/")
        jar = extract_cookies(
            {"name": c.name, "value": c.value, "domain": c.domain} for c in self.jar)
        if not jar.get("skey"):
            raise RuntimeError("jump 后没有 skey, 链路异常")
        jar["uin"] = jar.get("uin", "o" + uin)
        return jar
