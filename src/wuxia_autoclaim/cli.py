# -*- coding: utf-8 -*-
"""统一 CLI: wuxia-autoclaim <子命令>

退出码约定: 0=成功/无事可做; 1=业务性失败; 2=致命/环境错误。
日志恒写 <data>/autoclaim.log, --quiet 只影响控制台输出(pythonw/cron 下也安全)。
"""
import argparse
import copy
import json
import sys

from wuxia_autoclaim import __version__, config
from wuxia_autoclaim.log import echo, log, setup


def build_parser():
    # 公共选项同时挂到主解析器和各子命令上(SUPPRESS 默认值保证两种写法等价:
    # wuxia-autoclaim --quiet run  ==  wuxia-autoclaim run --quiet)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS,
                        help="数据目录(默认: 源码树 data/ 或系统配置目录)")
    common.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                        help="静默模式: 不写控制台, 只写日志文件")

    ap = argparse.ArgumentParser(
        prog="wuxia-autoclaim",
        description="天刀「周周载愿」本地自动许愿/领取: NapCat/QQNT 双通道凭据, 跨平台定时任务")
    ap.add_argument("--data-dir", default="", help=argparse.SUPPRESS)
    ap.add_argument("--quiet", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version="wuxia-autoclaim %s" % __version__)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", parents=[common],
                       help="执行一轮: 体检->收割->许愿/领取->推送(周期任务执行体)")
    p.add_argument("--role", default="", help="只跑指定角色: 序号/名称/QQ号")
    p.add_argument("--gift", type=int, default=0, help="指定 giftId")
    p.add_argument("--index", type=int, default=0, help="按序号选奖励(1起)")
    p.add_argument("--claim-only", action="store_true", help="仅领奖, 不做今天的许愿")
    p.add_argument("--no-harvest", action="store_true", help="跳过凭据收割, 只用手头 cookie")
    p.add_argument("--no-push", action="store_true", help="本次禁用 Server酱推送")
    p.add_argument("--channel", default="", help="临时覆盖通道顺序(逗号分隔: napcat,qqnt,game)")
    p.add_argument("--bind-new", action="store_true",
                   help="收割到新 QQ 时自动补齐绑定(活动中心已有绑定或 config.yaml bind 预设的号)")

    p = sub.add_parser("harvest", parents=[common], help="只做凭据体检+收割, 不许愿")
    p.add_argument("--channel", default="", help="临时覆盖通道顺序")
    p.add_argument("--bind-new", action="store_true",
                   help="收割到新 QQ 时自动补齐绑定(活动中心已有绑定或 config.yaml bind 预设的号)")

    p = sub.add_parser("gift", parents=[common], help="查看本周许愿物品并选择(序号持久化到角色)")
    p.add_argument("--role", default="", help="只处理指定角色: 序号/名称/QQ号")
    p.add_argument("--index", type=int, default=0, help="非交互直接设置奖励序号(1起)")

    p = sub.add_parser("status", parents=[common], help="只读体检: 角色/凭据/通道状态")
    p.add_argument("--json", action="store_true", help="以 JSON 输出(便于脚本)")

    p = sub.add_parser("login", parents=[common],
                       help="浏览器交互登录, 绑定角色(兜底用, 需 browser extra)")
    p.add_argument("--qr", action="store_true", help="扫码登录(仅限已成功登录过的情况)")
    p.add_argument("--headed", action="store_true", help="网页登录(默认)")
    p.add_argument("--timeout", type=int, default=300, help="等待登录的秒数(默认300)")

    p = sub.add_parser("bind", parents=[common],
                       help="绑定大区/角色(纯 HTTP, 首选; 需 NapCat 或 QQ 客户端在线; "
                            "config.yaml bind 预设的号免交互直绑)")
    p.add_argument("--uin", default="", help="只处理该 QQ 号(缺省=缺绑定的角色+通道在线的新账号)")

    p = sub.add_parser("task", parents=[common], help="周期性任务安装/卸载/状态")
    t = p.add_subparsers(dest="task_cmd")
    q = t.add_parser("install", help="安装周期任务(Windows: schtasks; Linux: systemd/cron)")
    q.add_argument("--method", default="auto", choices=("auto", "schtasks", "systemd", "cron"))
    q.add_argument("--interval", type=int, default=60, help="间隔分钟(默认60, 最小10)")
    q = t.add_parser("uninstall", help="卸载周期任务")
    q.add_argument("--method", default="auto", choices=("auto", "schtasks", "systemd", "cron"))
    t.add_parser("status", help="查看各形态安装状态与下次触发时间")

    p = sub.add_parser("config", parents=[common], help="查看/修改配置")
    c = p.add_subparsers(dest="config_cmd")
    c.add_parser("show", help="打印生效配置(token 脱敏)")
    q = c.add_parser("set", help="设置配置项")
    q.add_argument("key", choices=("game_dir", "channel_order", "auto_bind",
                                   "gift_index", "push_key"))
    q.add_argument("value", help="新值(channel_order 逗号分隔; auto_bind true/false)")
    q = c.add_parser("napcat-add", help="添加 NapCat OneBot HTTP 端点")
    q.add_argument("url", help="如 http://127.0.0.1:3000")
    q.add_argument("token", nargs="?", default="", help="OneBot access token(建议设置)")
    c.add_parser("napcat-clear", help="清空 NapCat 端点列表")
    return ap


def cmd_run(args):
    if args.index:
        args.gift = 0
    from wuxia_autoclaim import runner
    return runner.run(args)


def cmd_harvest(args):
    data = config.load_roles()
    if data is None:
        log("[!] 未找到 %s, 请先跑 bind 绑定角色(或 login/从旧项目拷 roles.json, 见 README)"
            % config.roles_file())
        return 2
    cfg = config.load_config()
    from wuxia_autoclaim.harvest import harvest_once
    return 0 if harvest_once(data, cfg, order=config.channel_order(cfg, args.channel),
                              bind_new=args.bind_new) else 1


def cmd_status(args):
    from wuxia_autoclaim import ams
    from wuxia_autoclaim.util import cookie_header, calc_gtk, mask
    cfg = config.load_config()
    data = config.load_roles()
    out = {"data_dir": str(config.data_dir()),
           "roles_file": str(config.roles_file()), "roles": [], "channels": {}}

    if data is None:
        out["roles"] = None
    else:
        for r in data.get("roles", []):
            name = r.get("name") or ("QQ%s" % r.get("uin", "?"))
            jar = r.get("cookies", {})
            item = {"name": name, "uin": mask(r.get("uin", "")), "area": r.get("area", "")}
            if not jar.get("skey"):
                item.update(ok=False, why="no skey")
            elif not (r.get("area") and r.get("roleid")):
                item.update(ok=False, why="missing area/roleid (跑 bind)")
            else:
                try:
                    resp = ams.emit(cookie_header(jar), calc_gtk(jar["skey"]),
                                    ams.FLOW_INIT, r.get("area", ""), r.get("roleid", ""))
                    iret = ams.is_login_expired(resp)
                    item["iRet"] = iret
                    if iret == "0":
                        week, signed, gifts, toget = ams.parse_init(resp)
                        item.update(ok=True, week=week, signed=bool(signed), to_get=toget)
                    else:
                        item.update(ok=iret == "99998", why="iRet=%s" % iret)
                except Exception as e:
                    item.update(ok=False, why="接口异常: %s" % e)
            out["roles"].append(item)

    # 通道侧只读探测
    eps = cfg.get("napcat") or []
    if eps:
        from wuxia_autoclaim.napcat import call, NapCatError
        ch = []
        for ep in eps:
            try:
                info = call(ep.get("url", ""), ep.get("token", ""), "get_login_info", timeout=5)
                ch.append({"url": ep.get("url"), "ok": True,
                           "uin": mask(info.get("user_id", "")), "nick": info.get("nickname", "")})
            except NapCatError as e:
                ch.append({"url": ep.get("url"), "ok": False, "error": str(e)})
        out["channels"]["napcat"] = ch
    try:
        from wuxia_autoclaim.qqnt import QqntBridge
        accs = QqntBridge().accounts()
        out["channels"]["qqnt"] = [{"uin": mask(u), "nick": i["nick"], "port": i["port"]}
                                   for u, i in accs.items()] or "无在线账号"
    except Exception as e:
        out["channels"]["qqnt"] = str(e)
    from wuxia_autoclaim.gamecache import cache_dir
    out["channels"]["game"] = str(cache_dir(cfg))

    if args.json:
        echo(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        log("数据目录: %s" % out["data_dir"])
        if out["roles"] is None:
            log("roles: 未初始化(先跑 login)")
        else:
            for item in out["roles"]:
                if item.get("ok"):
                    log("角色 %-12s QQ%s %s区  有效 已许愿=%s 待领奖=%s"
                        % (item["name"], item["uin"], item.get("area", "?"),
                           item.get("signed"), item.get("to_get")))
                else:
                    log("角色 %-12s QQ%s  失效(%s)" % (item["name"], item["uin"], item.get("why")))
        for line in json.dumps(out["channels"], ensure_ascii=False, indent=2).splitlines():
            echo(line)
    roles_ok = out["roles"] is not None and bool(out["roles"]) and \
        all(i.get("ok") for i in out["roles"])
    return 0 if roles_ok else 1


def cmd_gift(args):
    """查看/选择许愿物品: 拉取活动当前物品列表, 交互或 --index 选择后写进角色配置"""
    from wuxia_autoclaim import ams
    from wuxia_autoclaim.claim import select_roles
    from wuxia_autoclaim.util import cookie_header, calc_gtk
    data = config.load_roles()
    if data is None:
        log("[!] 未找到 %s, 请先跑 login 或 harvest" % config.roles_file())
        return 2
    cfg = config.load_config()
    config.apply_env(cfg)
    roles = select_roles(data, args.role)
    if not roles:
        log("[gift] roles.json 里没有角色, 退出")
        return 2

    changed = bad = 0
    for r in roles:
        name = r.get("name") or ("QQ%s" % r.get("uin", "?"))
        jar = r.get("cookies", {})
        if not jar.get("skey") or not (r.get("area") and r.get("roleid")):
            log("[gift] %s 凭据/绑定信息不全, 先跑 harvest/bind" % name)
            bad += 1
            continue
        try:
            resp = ams.emit(cookie_header(jar), calc_gtk(jar["skey"]),
                            ams.FLOW_INIT, r.get("area", ""), r.get("roleid", ""))
        except Exception as e:
            log("[gift] %s 拉取物品列表失败: %s" % (name, e))
            bad += 1
            continue
        iret = ams.is_login_expired(resp)
        if iret != "0":
            log("[gift] %s 登录态异常(iRet=%s), 先跑 harvest" % (name, iret))
            bad += 1
            continue
        week, signed, gifts, toget = ams.parse_init(resp)
        cur = config.gift_index_for(cfg, r.get("uin"))

        if args.index:
            if not (1 <= args.index <= len(gifts)):
                log("[gift] %s 序号 %d 超出范围(1-%d)" % (name, args.index, len(gifts)))
                bad += 1
                continue
            cfg.setdefault("gift_override", {})[str(r.get("uin", ""))] = args.index
            changed += 1
            log("[gift] %s 奖励序号 -> %d (%s)" % (name, args.index, gifts[args.index - 1]["name"]))
            continue

        print("\n[%s] 今日周%s, 本周物品(当前选第 %d 个):" % (name, "一二三四五六日"[week - 1], cur))
        for i, g in enumerate(gifts, 1):
            mark = "   <-- 当前" if i == cur else ""
            print("    %d. %s%s" % (i, g.get("name", "?"), mark))
        while True:
            try:
                s = input("选择序号(回车=保持 %d): " % cur).strip()
            except EOFError:      # 无终端(管道/计划任务)时视为只看不改
                break
            if not s:
                break
            if s.isdigit() and 1 <= int(s) <= len(gifts):
                cfg.setdefault("gift_override", {})[str(r.get("uin", ""))] = int(s)
                changed += 1
                log("[gift] %s 奖励序号 -> %s (%s)" % (name, s, gifts[int(s) - 1]["name"]))
                break
            print("    输入无效, 请重新输入")

    if changed:
        config.save_config(cfg)
        log("已回写 %s" % config.config_file())
    return 0 if not bad else 1


def cmd_task(args):
    from wuxia_autoclaim import taskinstall
    if args.task_cmd == "install":
        return 0 if taskinstall.install(args.method, args.interval) else 1
    if args.task_cmd == "uninstall":
        return 0 if taskinstall.uninstall(args.method) else 1
    if args.task_cmd == "status":
        return 0 if taskinstall.status() else 1
    print("用法: wuxia-autoclaim task {install|uninstall|status}")
    return 2


def cmd_config(args):
    cfg = config.load_config()
    if args.config_cmd == "show":
        shown = copy.deepcopy(cfg)                 # token 脱敏后按 yaml 形态打印
        for ep in shown.get("napcat", []):
            if ep.get("token"):
                ep["token"] = ep["token"][:4] + "..."
        log("数据目录: %s" % config.data_dir())
        log("配置文件: %s" % config.config_file())
        log("roles:     %s" % config.roles_file())
        log("日志:      %s" % config.log_file())
        print(config.dump_config(shown), end="")
        data = config.load_roles()
        if data is not None:
            print(json.dumps({"roles": ["%s (QQ%s %s区)" % (r.get("name", "?"), r.get("uin", "?"),
                                                          r.get("area", "?"))
                                        for r in data.get("roles", [])]},
                             ensure_ascii=False, indent=2))
        return 0
    if args.config_cmd == "set":
        v = args.value
        if args.key == "auto_bind":
            v = v.lower() in ("1", "true", "yes", "on")
        elif args.key == "gift_index":
            if not v.isdigit():
                log("[!] gift_index 要是数字")
                return 2
            v = int(v)
        elif args.key == "channel_order":
            v = config.channel_order(cfg, v)
        cfg[args.key] = v
        config.save_config(cfg)
        log("已设置 %s = %s (回写 %s)" % (args.key, v, config.config_file()))
        return 0
    if args.config_cmd == "napcat-add":
        eps = cfg.setdefault("napcat", [])
        eps[:] = [e for e in eps if e.get("url") != args.url]     # 同址覆盖
        eps.append({"url": args.url, "token": args.token})
        config.save_config(cfg)
        log("已添加 napcat 端点 %s%s (共 %d 个)" % (args.url, "(带token)" if args.token else "", len(eps)))
        return 0
    if args.config_cmd == "napcat-clear":
        cfg["napcat"] = []
        config.save_config(cfg)
        log("已清空 napcat 端点")
        return 0
    print("用法: wuxia-autoclaim config {show|set|napcat-add|napcat-clear}")
    return 2


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        sys.exit(2)
    if args.data_dir:
        config.set_data_dir(args.data_dir)
    try:
        config.init_data_dir()
    except OSError as e:
        print("[!] 数据目录不可写(%s): %s" % (config.data_dir(), e))
        sys.exit(2)
    setup(config.log_file(), quiet=args.quiet)

    handlers = {"run": cmd_run, "harvest": cmd_harvest, "gift": cmd_gift,
                "status": cmd_status, "task": cmd_task, "config": cmd_config}
    try:
        if args.cmd == "login":
            from wuxia_autoclaim import login
            code = login.cmd(args)
        elif args.cmd == "bind":
            from wuxia_autoclaim import webbind
            code = webbind.cmd(args)
        else:
            code = handlers[args.cmd](args)
    except config.ConfigError as e:     # 配置/数据文件损坏: 干净报错而非裸堆栈
        log("[!] %s" % e)
        code = 2
    sys.exit(code)


if __name__ == "__main__":
    main()
