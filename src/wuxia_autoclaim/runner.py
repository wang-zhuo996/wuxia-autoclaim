# -*- coding: utf-8 -*-
"""run 闭环: 周期任务的执行体

流程(体检只节流"收割", 不节流签到; 签到节流只认服务器的 isTodaySign):

    1. 装载 roles/config
    2. 体检(check_roles): 全活 -> 跳过收割
    3. 有失效且未 --no-harvest -> harvest_once 按通道顺序收割
    4. 对全部角色执行领取主流程(周末一键领取 + 每日许愿, 已许愿则秒退)
    5. Server酱推送(所有角色都"今日已许愿"时不推, 避免每小时轰炸)

返回退出码: 0=全部成功; 1=有角色失败; 2=致命(没有角色/数据问题)。
"""
from wuxia_autoclaim import config
from wuxia_autoclaim.claim import safe_run_role, select_roles, push_serverchan
from wuxia_autoclaim.harvest import check_roles
from wuxia_autoclaim.log import log


def run(args):
    data = config.load_roles()
    if data is None:
        log("[run] 未找到 %s, 请先跑 login 或从旧项目拷贝 roles.json(见 README 迁移节)"
            % config.roles_file())
        return 2
    cfg = config.load_config()
    defaults = data.setdefault("defaults", {})
    forced = config.apply_env(defaults)
    if "gift_index" in forced:   # 环境变量强制的奖励序号对所有角色生效
        for r in data.get("roles", []):
            r["gift_index"] = defaults["gift_index"]

    roles = select_roles(data, getattr(args, "role", ""))
    if not roles:
        log("[run] roles.json 里没有角色, 退出")
        return 2
    if getattr(args, "index", 0):
        for r in roles:
            r["gift_index"] = args.index

    # 体检: 只决定要不要收割, 不影响后面的签到执行
    dead = check_roles(data)
    if dead and not args.no_harvest:
        from wuxia_autoclaim.harvest import harvest_once
        order = config.channel_order(cfg, getattr(args, "channel", ""))
        harvest_once(data, cfg, order=order, bind_new=args.bind_new, dead=dead)

    results = [safe_run_role(r, defaults, claim_only=args.claim_only,
                             gift_id=args.gift if args.gift else 0)
               for r in roles]
    for _, m in results:
        log(m)
    ok_n = sum(1 for ok, _ in results if ok)

    if args.no_push:
        return 0 if ok_n == len(results) else 1
    if all(ok and "今日已许愿" in m for ok, m in results):
        # 所有角色今天都已签过, 不再推送
        log("[run] 所有角色今日已完成, 无需重复")
        return 0 if ok_n == len(results) else 1
    push_serverchan(defaults.get("push_key", ""),
                    "[天刀签到] %d/%d 成功" % (ok_n, len(results)),
                    "\n\n---\n\n".join(m for _, m in results))
    return 0 if ok_n == len(results) else 1
