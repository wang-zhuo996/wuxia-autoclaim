# -*- coding: utf-8 -*-
"""本地登录助手: 浏览器登录活动页后把角色写入 roles.json

自 wuxia-autosign login.py 移植(Python playwright 直驱), 差异:
    - 不再生成 GitHub Secret 文件(完全本地化)
    - 产物只落 <data>/roles.json
    - ensure_playwright 抛 RuntimeError 而不是 sys.exit(bind 模块也要用)

多角色: 换账号/角色后再跑一次本程序即可追加, roles.json 支持任意多个角色。

依赖(可选 extra): pip install "wuxia-autoclaim[browser]"
    python -m playwright install chromium
"""
import argparse
import re
import sys
import time
import urllib.parse
from pathlib import Path

from wuxia_autoclaim import ams, config
from wuxia_autoclaim.log import log
from wuxia_autoclaim.util import extract_cookies, mask

# 登录成功后, 活动 iframe 的地址里会带上 area / roleid / playername
EXPR = "(function(){var f=document.querySelector('iframe');return f?f.src:'';})()"


class MissingPlaywright(RuntimeError):
    """playwright/chromium 不可用(装 browser extra 可解)"""


def ensure_playwright():
    """确认 Python 版 playwright 和 chromium 内核可用, 缺了给出安装指引"""
    try:
        from playwright.sync_api import sync_playwright    # noqa: F401
    except ImportError:
        raise MissingPlaywright(
            "没有安装 Python 版 playwright。请执行:\n"
            "    pip install \"wuxia-autoclaim[browser]\"   (或 pip install playwright)\n"
            "    python -m playwright install chromium")
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            b.close()
    except Exception as e:
        raise MissingPlaywright("chromium 内核不可用(%s)。请执行: python -m playwright install chromium" % e)


def clean_old_files():
    """清掉上一轮残留的二维码截图"""
    for p in config.data_dir().glob("qr_login*.png"):
        try:
            p.unlink()
        except OSError:
            pass


def choose_mode(args):
    if args.qr:
        return "qr"
    if args.headed:
        return "headed"
    print("请选择登录方式:")
    print("  1) 网页登录【推荐, 首次登录必须用这个】: 弹出浏览器窗口, 自己扫码或点头像登录")
    print("  2) 扫码登录: 程序生成二维码图片(已经成功登录过才用这个)")
    print("")
    print("  注意: 首次登录不要选扫码登录! 因为首次登录后页面上还要手动选一次大区,")
    print("        二维码模式下脚本检测不到这一步, 会卡住。先用网页登录跑通一次以后再考虑。")
    while True:
        try:
            c = input("输入 1 或 2 后回车 (直接回车=1): ").strip() or "1"
        except EOFError:      # 没有交互终端时直接用默认方式
            c = "1"
        if c == "1":
            return "headed"
        if c == "2":
            return "qr"
        print("    输入无效, 请重新输入")


def read_role(page):
    """从页面读取 area / roleid / playername, 未登录则返回空"""
    try:
        src = page.evaluate(EXPR) or ""
    except Exception:
        return "", "", ""
    src = src.replace("\\u0026", "&")

    def pick(key):
        m = re.search(key + r"=([^&\"'\\\s]+)", src)
        return urllib.parse.unquote(m.group(1)) if m else ""

    area, roleid, playername = pick("area"), pick("roleid"), pick("playername")
    if area and roleid:
        return area, roleid, playername
    return "", "", ""


def wait_login(page, mode, timeout, on_qr=None):
    """轮询等待登录成功, 返回 (area, roleid, playername); 超时返回 (None, None, '')"""
    deadline = time.time() + timeout
    last_qr = 0
    waited = 0
    while time.time() < deadline:
        if mode == "qr" and on_qr and time.time() - last_qr > 45:
            on_qr()
            last_qr = time.time()
        area, roleid, playername = read_role(page)
        if roleid:
            return area, roleid, playername
        time.sleep(5)
        waited += 5
        if waited % 30 == 0:
            print("    等待登录中... 已等待 %d 秒" % waited)
            if mode == "headed":
                print("    (如果页面停在让选大区/选角色, 请在窗口里手动选一下, 选完页面会自己刷新)")
    return None, None, ""


def fetch_gifts(jar, area, roleid):
    """登录后拉取当前活动的奖励列表, 拿到序号->名称对照"""
    try:
        resp = ams.emit(ams.cookie_header(jar), ams.calc_gtk(jar["skey"]),
                        ams.FLOW_INIT, area, roleid)
        return ams.parse_init(resp)[2]
    except Exception:
        return []


def login_once(args):
    """执行一次登录流程, 成功时写入 roles.json, 返回 True"""
    ensure_playwright()
    config.init_data_dir()
    clean_old_files()
    mode = choose_mode(args)

    print("\n[1/2] 正在打开登录页面...")
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=(mode != "headed"))
        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(ams.URL_ACT, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            browser.close()
            log("[!] 页面打开失败: %s" % e)
            return False
        if mode == "headed":
            print("      浏览器窗口已弹出, 请在窗口里完成登录。")
            print("      首次登录的话, 页面上可能让你选大区, 跟着选一下即可。")
            print("      登录成功后不用做别的操作, 本程序会自动检测。")
        else:
            print("      页面已在后台打开, 马上生成二维码给你扫。")

        def shot_qr():
            qr = config.data_dir() / ("qr_login_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
            try:
                page.screenshot(path=str(qr))
                print("\n>>> 请用手机 QQ 扫描这个二维码:\n    %s\n" % qr)
                print("    (二维码约 2 分钟换一张, 请以最新提示的文件为准; 扫完记得在手机上点『确认登录』)")
            except Exception:
                pass

        area, roleid, playername = wait_login(page, mode, args.timeout, on_qr=shot_qr)
        if not roleid:
            browser.close()
            log("[!] 等待超时, 没有检测到登录。建议改用默认方式重来: wuxia-autoclaim login")
            return False
        print("\n[2/2] 登录成功, 正在保存登录态...")
        jar = extract_cookies(ctx.cookies())
        browser.close()

    if not jar.get("skey"):
        log("[!] 登录态里没有 skey, 说明没有真正登录成功, 请重试")
        return False
    uin = jar.get("uin", "").lstrip("o")
    data = config.load_roles() or {"defaults": dict(config.DEFAULTS), "roles": []}
    config.upsert_role(data, area, roleid, playername, jar, uin)
    config.save_roles(data)
    gifts = fetch_gifts(jar, area, roleid)
    print_result(data, uin, area, gifts)
    return True


def print_result(data, uin, area, gifts):
    defaults = data.get("defaults", {})
    roles = data.get("roles", [])
    gift_idx = defaults.get("gift_index", 3)
    print("\n" + "=" * 66)
    print("[登录信息已保存] %s" % config.roles_file())
    print("=" * 66)
    print("本次登录 QQ: %s   大区: %s   角色: %s"
          % (mask(uin), area, mask(roles[-1]["roleid"], 4, 4)))
    print("roles.json 里现在有 %d 个角色:" % len(roles))
    for r in roles:
        print("    - %s (QQ%s %s区)" % (r.get("name", "?"), r.get("uin", "?"), r.get("area", "?")))
    print("")
    print("奖励序号(想换再改, 跳过就用默认第 %d 个):" % gift_idx)
    if gifts:
        for i, g in enumerate(gifts, 1):
            mark = "  <-- 当前默认" if i == gift_idx else ""
            print("    %d = %s%s" % (i, g["name"], mark))
    print("")
    print("接下来:")
    print("    wuxia-autoclaim status        # 体检凭据")
    print("    wuxia-autoclaim run           # 立即许愿/领取一次")
    print("    wuxia-autoclaim task install  # 安装为周期任务(静默自动跑)")
    print("")
    print("[!] roles.json 是你的私人登录凭证, 不要发给任何人(data/ 已被 .gitignore 忽略)。")


def cmd(args):
    """统一 CLI 的入口(args 需带 qr/headed/timeout 属性)"""
    try:
        return 0 if login_once(args) else 1
    except MissingPlaywright as e:
        log("[!] %s" % e)
        return 2


def main(argv=None):
    ap = argparse.ArgumentParser(description="本地登录助手(多角色)")
    ap.add_argument("--qr", action="store_true", help="扫码登录(仅限已成功登录过的情况)")
    ap.add_argument("--headed", action="store_true", help="网页登录(默认, 推荐)")
    ap.add_argument("--timeout", type=int, default=300, help="等待登录的秒数(默认300)")
    args = ap.parse_args(argv)
    return cmd(args)


if __name__ == "__main__":
    sys.exit(main())
