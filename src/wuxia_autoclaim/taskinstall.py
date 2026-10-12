# -*- coding: utf-8 -*-
"""周期性任务安装/卸载: Windows schtasks + Linux systemd user timer / cron 兜底

参考 wuxia-autosign harvest.py 的 install_task 设计(schtasks + pythonw 静默),
扩展到 Linux:
    - 优先 systemd user timer(~/.config/systemd/user/, 开机即跑补漏(Persistent)),
      需要登录会话之外也运行时提示/尝试 enable-linger
    - 容器/WSL 等无 systemd user 的环境自动落 crontab(带标记行, 幂等装卸)

包装脚本统一落在数据目录(不落包目录, 安装态包目录可能只读且升级会丢),
内容固定: 绝对路径解释器 + 钉死的 WUXIA_DATA_DIR + `python -m wuxia_autoclaim run --quiet`。
"""
import os
import subprocess
import sys
from pathlib import Path

from wuxia_autoclaim import config
from wuxia_autoclaim.log import log

TASK_NAME = "wuxia-autoclaim"          # systemd/cron 侧名字; schtasks 用大写别名
SCHTASKS_NAME = "WuxiaAutoclaim"
CRON_MARKER = "# wuxia-autoclaim:v1"
MIN_INTERVAL = 10


def resolve_python():
    """任务用的解释器: Windows 优先 pythonw.exe(无控制台窗口, 静默)"""
    py = Path(sys.executable).resolve()
    if os.name == "nt":
        pyw = py.with_name("pythonw.exe")
        if pyw.exists():
            return pyw
    return py


def build_task_cmd(python, data_dir, src_path=None):
    """Windows 包装脚本内容(CRLF); src_path 非空说明是源码树运行, 需要 PYTHONPATH"""
    lines = ["@echo off",
             'set "WUXIA_DATA_DIR=%s"' % data_dir]
    if src_path:
        lines.append('set "PYTHONPATH=%s"' % src_path)
    lines.append('"%s" -m wuxia_autoclaim --quiet run' % python)
    return "\r\n".join(lines) + "\r\n"


def build_task_sh(python, data_dir, src_path=None):
    """POSIX 包装脚本内容"""
    lines = ["#!/bin/sh",
             "WUXIA_DATA_DIR='%s'" % data_dir,
             "export WUXIA_DATA_DIR"]
    if src_path:
        lines += ["PYTHONPATH='%s'" % src_path, "export PYTHONPATH"]
    lines.append("exec '%s' -m wuxia_autoclaim --quiet run" % python)
    return "\n".join(lines) + "\n"


def cron_spec(minutes):
    """cron 五段式触发表达式(分钟位错峰用 :17, 避开整点)"""
    if minutes >= 60 and minutes % 60 == 0:
        return "17 */%d * * *" % (minutes // 60)
    return "*/%d * * * *" % minutes


def oncalendar(minutes):
    """systemd timer 的触发配置, 返回 (OnCalendar 或 None, 附加行列表, persistent)"""
    if minutes == 60:
        return "hourly", [], True
    if minutes < 60 and 60 % minutes == 0:
        return "*:0/%d" % minutes, [], True
    # 整不掉的间隔(90/45/7...)用相对触发, 没法配 Persistent 补漏
    return None, ["OnUnitActiveSec=%dmin" % minutes,
                  "OnBootSec=2min", "AccuracySec=1min"], False


def filter_crontab(lines):
    """剔除本工具的旧标记行(装卸幂等的关键)"""
    return [l for l in lines if CRON_MARKER not in l]


def build_service(task_sh):
    return ("[Unit]\n"
            "Description=wuxia-autoclaim 天刀周周载愿(许愿+领取)\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            "ExecStart=/bin/sh %s\n" % task_sh)


def build_timer(minutes):
    cal, extra, persistent = oncalendar(minutes)
    trig = ("OnCalendar=%s\n" % cal) if cal else "".join(l + "\n" for l in extra)
    return ("[Unit]\n"
            "Description=wuxia-autoclaim 定时触发\n\n"
            "[Timer]\n"
            + trig
            + ("Persistent=true\n" if persistent else "")
            + "Unit=%s.service\n\n"
              "[Install]\n"
              "WantedBy=timers.target\n" % TASK_NAME)


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)


def _write_wrapper():
    """生成 data/ 下的包装脚本, 返回其路径"""
    config.init_data_dir()
    py = resolve_python()
    repo = config.find_repo_root()
    src_path = str(repo / "src") if repo else None
    ddir = config.data_dir()
    if os.name == "nt":
        p = ddir / "task.cmd"
        p.write_text(build_task_cmd(py, ddir, src_path), "utf-8")
    else:
        p = ddir / "task.sh"
        p.write_text(build_task_sh(py, ddir, src_path), "utf-8")
        try:
            p.chmod(0o700)
        except OSError:
            pass
    log("已生成包装脚本 %s (python=%s)" % (p, py))
    return p


# ---------------- Windows: schtasks ----------------

def install_schtasks(minutes):
    wrapper = _write_wrapper()
    r = _run(["schtasks", "/Create", "/F", "/TN", SCHTASKS_NAME,
              "/TR", '"%s"' % wrapper, "/SC", "MINUTE", "/MO", str(minutes)])
    if r.returncode != 0:
        log("[!] 计划任务创建失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
        return False
    log("已创建计划任务 %s (每 %d 分钟静默执行一次), 日志见 %s"
        % (SCHTASKS_NAME, minutes, config.log_file()))
    return True


def uninstall_schtasks():
    r = _run(["schtasks", "/Delete", "/F", "/TN", SCHTASKS_NAME])
    out = (r.stdout + r.stderr)
    if r.returncode != 0 and ("找不到" not in out and "cannot find" not in out.lower()):
        log("[!] 计划任务删除失败: %s" % out.strip())
        return False
    log("计划任务已删除" if r.returncode == 0 else "计划任务本来就不存在, 视为已卸载")
    try:
        (config.data_dir() / "task.cmd").unlink()
    except OSError:
        pass
    return True


def status_schtasks():
    r = _run(["schtasks", "/Query", "/TN", SCHTASKS_NAME, "/FO", "LIST"])
    if r.returncode != 0:
        log("schtasks: 未安装")
        return False
    for line in r.stdout.splitlines():
        s = line.strip()
        if s.startswith(("任务名", "TaskName", "下次运行时间", "Next Run Time", "状态", "Status")):
            log("schtasks: %s" % s)
    return True


# ---------------- POSIX: systemd user timer ----------------

def systemd_available():
    try:
        return _run(["systemctl", "--user", "show-environment"]).returncode == 0
    except Exception:
        return False


def unit_dir():
    return Path.home() / ".config" / "systemd" / "user"


def install_systemd(minutes):
    wrapper = _write_wrapper()
    d = unit_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / ("%s.service" % TASK_NAME)).write_text(build_service(wrapper), "utf-8")
    (d / ("%s.timer" % TASK_NAME)).write_text(build_timer(minutes), "utf-8")
    r = _run(["systemctl", "--user", "daemon-reload"])
    if r.returncode != 0:
        log("[!] daemon-reload 失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
        return False
    r = _run(["systemctl", "--user", "enable", "--now", "%s.timer" % TASK_NAME])
    if r.returncode != 0:
        log("[!] timer 启用失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
        return False
    log("已启用 systemd user timer %s.timer (每 %d 分钟), 日志: journalctl --user -u %s"
        % (TASK_NAME, minutes, TASK_NAME))
    # 无登录会话也要跑 -> 需要 linger; 对自己的用户通常免 root
    try:
        s = _run(["loginctl", "show-user", os.environ.get("USER") or "", "--property=Linger"])
        if "Linger=no" in s.stdout:
            e = _run(["loginctl", "enable-linger", os.environ.get("USER") or ""])
            if e.returncode == 0:
                log("已开启 linger(服务器注销后任务照跑)")
            else:
                log("[i] 服务器场景建议手动执行: sudo loginctl enable-linger $USER (注销后任务才照跑)")
    except Exception:
        pass
    return True


def uninstall_systemd():
    ok = True
    r = _run(["systemctl", "--user", "disable", "--now", "%s.timer" % TASK_NAME])
    if r.returncode != 0 and "not loaded" not in (r.stdout + r.stderr) \
            and "Loaded: not-found" not in (r.stdout + r.stderr):
        log("[!] timer 停用失败: %s %s" % (r.stdout.strip(), r.stderr.strip()))
        ok = False
    for name in ("%s.timer" % TASK_NAME, "%s.service" % TASK_NAME):
        try:
            (unit_dir() / name).unlink()
        except OSError:
            pass
    _run(["systemctl", "--user", "daemon-reload"])
    try:
        (config.data_dir() / "task.sh").unlink()
    except OSError:
        pass
    log("systemd user timer 已卸载")
    return ok


def status_systemd():
    if not (unit_dir() / ("%s.timer" % TASK_NAME)).exists():
        log("systemd: 未安装")
        return False
    r = _run(["systemctl", "--user", "list-timers", "%s*" % TASK_NAME, "--no-pager"])
    for line in r.stdout.splitlines():
        if line.strip():
            log("systemd: %s" % line.strip())
    return True


# ---------------- POSIX 兜底: crontab ----------------

def current_crontab():
    r = _run(["crontab", "-l"])
    if r.returncode != 0:
        return []               # "no crontab for xxx" 等视为空表
    return r.stdout.splitlines()


def write_crontab(lines):
    r = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n",
                       capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def install_cron(minutes):
    _write_wrapper()
    task_sh = config.data_dir() / "task.sh"
    lines = filter_crontab(current_crontab())
    lines.append("%s /bin/sh %s >> %s 2>&1 %s"
                 % (cron_spec(minutes), task_sh, config.log_file(), CRON_MARKER))
    ok, err = write_crontab(lines)
    if not ok:
        log("[!] crontab 写入失败: %s" % err)
        return False
    log("已安装 crontab 条目(每 %d 分钟), 标记 %s, 日志见 %s"
        % (minutes, CRON_MARKER, config.log_file()))
    return True


def uninstall_cron():
    lines = filter_crontab(current_crontab())
    if len(lines) == len(current_crontab()):
        log("cron: 未安装")
        return True
    ok, err = write_crontab(lines)
    log("cron 条目已卸载" if ok else "[!] crontab 写回失败: %s" % err)
    return ok


def status_cron():
    found = [l for l in current_crontab() if CRON_MARKER in l]
    for l in found:
        log("cron: %s" % l.strip())
    if not found:
        log("cron: 未安装")
    return bool(found)


# ---------------- 对外入口 ----------------

def install(method, minutes):
    if minutes < MIN_INTERVAL:
        log("[!] 间隔最少 %d 分钟" % MIN_INTERVAL)
        return False
    if os.name == "nt":
        if method in ("auto", "schtasks"):
            return install_schtasks(minutes)
        log("[!] Windows 只支持 schtasks")
        return False
    if method == "systemd":
        return install_systemd(minutes)
    if method == "cron":
        return install_cron(minutes)
    if method == "auto":
        if systemd_available():
            return install_systemd(minutes)
        log("[i] systemd user 不可用(容器/WSL?), 转用 crontab")
        return install_cron(minutes)
    log("[!] 未知的方法: %s" % method)
    return False


def uninstall(method):
    if os.name == "nt":
        if method in ("auto", "schtasks"):
            return uninstall_schtasks()
        log("[!] Windows 只支持 schtasks")
        return False
    if method == "auto":
        ok = uninstall_systemd()
        return uninstall_cron() and ok
    if method == "systemd":
        return uninstall_systemd()
    if method == "cron":
        return uninstall_cron()
    log("[!] 未知的方法: %s" % method)
    return False


def status():
    found = False
    if os.name == "nt":
        found = status_schtasks()
    else:
        found = status_systemd() or found
        found = status_cron() or found
    return found
