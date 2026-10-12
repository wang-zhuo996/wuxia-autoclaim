# -*- coding: utf-8 -*-
"""日志: 控制台(GBK 降级) + data/autoclaim.log 追加(带简单轮转)

自 wuxia-autosign harvest.py 移植, 改为可 setup() 的模块级日志文件,
适配 pythonw(无控制台)与 cron/systemd(无 tty)下的静默执行。
"""
import sys
from datetime import datetime
from pathlib import Path

LOG_MAX = 2 * 1024 * 1024      # 超过 2MB 轮转成 .1

_file = None                    # 由 setup() 指定
_quiet = False                  # --quiet: 只输出错误行


def setup(path, quiet=False):
    """指定日志文件与静默开关; 不调用 setup 也能用(只打控制台)"""
    global _file, _quiet
    _file = Path(path) if path else None
    _quiet = quiet


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        if not _quiet:
            print(line)
    except UnicodeEncodeError:   # GBK 控制台打不出某些字符(如昵称里的 †)时降级
        enc = sys.stdout.encoding or "gbk"
        print(line.encode(enc, errors="replace").decode(enc))
    except OSError:
        pass                     # pythonw 无 stdout 时静默
    if _file is None:
        return
    try:
        if _file.exists() and _file.stat().st_size > LOG_MAX:
            try:
                _file.with_suffix(_file.suffix + ".1").write_text(
                    _file.read_text("utf-8"), "utf-8")
            except OSError:
                pass
        with open(_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
