# wuxia-autoclaim 容器镜像: 纯标准库零依赖, 直接 PYTHONPATH 挂源码运行(构建无需出网)
# 调度由 entrypoint 的循环承担(等价宿主机的 task install), 见 docker/entrypoint.sh
FROM python:3.12-slim-bookworm

# slim 基础镜像自带 tzdata/ca-certificates; TZ 只影响日志时间戳(业务周几来自服务端)
ENV PYTHONPATH=/app/src \
    WUXIA_DATA_DIR=/data \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY src/ src/
COPY data/config.example.yaml config.example.yaml
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 数据目录由运行时挂载(compose volumes / docker run -v), 不声明匿名 VOLUME
ENTRYPOINT ["/entrypoint.sh"]
