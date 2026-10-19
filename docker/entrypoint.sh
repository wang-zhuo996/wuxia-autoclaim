#!/bin/sh
# wuxia-autoclaim 容器入口
#   无参数: 首启种子 -> WUXIA_* 环境变量写入 config.yaml -> 循环执行周期任务
#   有参数: 同样先落环境变量, 再把参数原样交给 CLI 一次性执行
#           (docker compose run --rm wuxia status / bind / gift / config show ...)
set -u

: "${WUXIA_DATA_DIR:=/data}"
export WUXIA_DATA_DIR
mkdir -p "$WUXIA_DATA_DIR" 2>/dev/null || true

# ---- 首启种子: 空数据目录给出可编辑的配置模板与空角色表
if [ ! -f "$WUXIA_DATA_DIR/config.yaml" ]; then
    cp /app/config.example.yaml "$WUXIA_DATA_DIR/config.yaml"
    echo "[entrypoint] 已生成初始 $WUXIA_DATA_DIR/config.yaml(带注释模板)"
fi
if [ ! -f "$WUXIA_DATA_DIR/roles.json" ]; then
    printf '{"roles": []}\n' > "$WUXIA_DATA_DIR/roles.json"
    echo "[entrypoint] 已生成空 $WUXIA_DATA_DIR/roles.json"
fi

# ---- compose 环境变量 -> config.yaml(幂等: 变量非空才写, 写则以变量为准)
python -m wuxia_autoclaim config from-env \
    || echo "[entrypoint] config from-env 有告警, 按文件现有配置继续"

# ---- 一次性命令模式
if [ "$#" -gt 0 ]; then
    exec python -m wuxia_autoclaim "$@"
fi

# ---- 周期循环: 默认每 60 分钟一轮 run(WUXIA_INTERVAL 可调, 最小 10)
INTERVAL="${WUXIA_INTERVAL:-60}"
case "$INTERVAL" in ''|*[!0-9]*) INTERVAL=60 ;; esac
[ "$INTERVAL" -ge 10 ] || INTERVAL=10

# PID1 不 trap 信号等于收不到; 子进程/sleep 一律后台起 + wait,
# TERM 到达时先转发给当前子进程(python/sleep)再退出, docker stop 秒级完成
CHILD=""
_on_term() {
    [ -n "$CHILD" ] && kill -TERM "$CHILD" 2>/dev/null
    exit 0
}
trap _on_term TERM INT

echo "[entrypoint] 周期任务启动: 每 ${INTERVAL} 分钟一轮 (TZ=${TZ:-未设置})"
while :; do
    # roles.json 还没有角色时先跑 harvest --bind-new 纳新(配好 bind 预设/NapCat
    # 即全自动起步), 有了角色就回归常规 run; 每轮现查, 修好配置下一轮自愈
    if python -c 'import json,os
d = json.load(open(os.path.join(os.environ.get("WUXIA_DATA_DIR", "/data"), "roles.json")))
raise SystemExit(0 if d.get("roles") else 1)' 2>/dev/null; then
        python -m wuxia_autoclaim run &
    else
        echo "[entrypoint] roles.json 还没有角色, 本轮先 harvest --bind-new 纳新"
        python -m wuxia_autoclaim harvest --bind-new &
    fi
    CHILD=$!
    wait "$CHILD"; RC=$?
    CHILD=""
    echo "[entrypoint] 本轮退出码 ${RC}, ${INTERVAL} 分钟后再跑"
    sleep "$((INTERVAL * 60))" &
    CHILD=$!
    wait "$CHILD"
    CHILD=""
done
