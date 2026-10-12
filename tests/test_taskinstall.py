# -*- coding: utf-8 -*-
"""taskinstall 单测: 包装脚本/触发表达式/幂等装卸的内容生成(不执行系统命令)"""
from wuxia_autoclaim import taskinstall as ti


def test_cron_spec():
    assert ti.cron_spec(60) == "17 */1 * * *"
    assert ti.cron_spec(120) == "17 */2 * * *"
    assert ti.cron_spec(15) == "*/15 * * * *"
    assert ti.cron_spec(45) == "*/45 * * * *"


def test_oncalendar():
    assert ti.oncalendar(60) == ("hourly", [], True)
    assert ti.oncalendar(30) == ("*:0/30", [], True)
    cal, extra, persistent = ti.oncalendar(45)
    assert cal is None
    assert "OnUnitActiveSec=45min" in extra and "OnBootSec=2min" in extra
    assert persistent is False                       # 相对触发没法配 Persistent 补漏


def test_filter_crontab_idempotent():
    lines = ["# crontab header", "0 5 * * * /bin/other",
             "17 */1 * * * /bin/sh /x/task.sh # wuxia-autoclaim:v1"]
    kept = ti.filter_crontab(lines)
    assert kept == ["# crontab header", "0 5 * * * /bin/other"]
    assert ti.filter_crontab(kept) == kept           # 再过滤一遍不变(幂等)


def test_build_task_cmd():
    s = ti.build_task_cmd(r"C:\py\pythonw.exe", r"D:\d data", r"D:\repo\src")
    assert "\r\n" in s
    assert 'set "WUXIA_DATA_DIR=D:\\d data"' in s
    assert 'set "PYTHONPATH=D:\\repo\\src"' in s
    assert '"C:\\py\\pythonw.exe" -m wuxia_autoclaim --quiet run' in s
    # 安装态(无源码树)不写 PYTHONPATH
    s2 = ti.build_task_cmd("/usr/bin/python", "/home/u/.config/wuxia-autoclaim", None)
    assert "PYTHONPATH" not in s2
    assert "exec" not in s2


def test_build_task_sh():
    s = ti.build_task_sh("/opt/venv/bin/python", "/home/u/.config/wuxia-autoclaim",
                         "/srv/wuxia/src")
    assert s.startswith("#!/bin/sh")
    assert "WUXIA_DATA_DIR='/home/u/.config/wuxia-autoclaim'" in s
    assert "export WUXIA_DATA_DIR" in s
    assert "PYTHONPATH='/srv/wuxia/src'" in s
    assert "exec '/opt/venv/bin/python' -m wuxia_autoclaim --quiet run" in s


def test_build_units():
    svc = ti.build_service("/home/u/.config/wuxia-autoclaim/task.sh")
    assert "Type=oneshot" in svc
    assert "ExecStart=/bin/sh /home/u/.config/wuxia-autoclaim/task.sh" in svc
    timer = ti.build_timer(60)
    assert "OnCalendar=hourly" in timer and "Persistent=true" in timer
    assert "Unit=%s.service" % ti.TASK_NAME in timer
    assert "WantedBy=timers.target" in timer
