FROM docker.m.daocloud.io/library/python:3.10-slim

ARG INSTALL_WHISPER=false

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_HOST=0.0.0.0 \
    APP_PORT=5088 \
    DOUYIN2TEXT_CONFIG_PATH=/data/config.json \
    DOUYIN2TEXT_WEB_OUTPUT_DIR=/data/output/web \
    XDG_CACHE_HOME=/data/.cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple

RUN python -m pip install flask requests -i https://pypi.tuna.tsinghua.edu.cn/simple \
    && if [ "$INSTALL_WHISPER" = "true" ]; then python -m pip install openai-whisper -i https://pypi.tuna.tsinghua.edu.cn/simple; fi

COPY . /app

RUN mkdir -p /data/output/web /data/.cache \
    && if [ ! -f /data/config.json ]; then cp /app/config.example.json /data/config.json; fi

EXPOSE 5088

CMD ["python", "app.py"]
