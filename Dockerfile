FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1


RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 bot \
    && useradd --system --uid 10001 --gid bot \
        --home-dir /app --no-create-home --shell /usr/sbin/nologin bot

WORKDIR /app


COPY requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements.lock \
    && rm -f /tmp/requirements.lock

COPY . /app


RUN mkdir -p /opt/casino/data-seed /app/data \
    && cp -a /app/data/. /opt/casino/data-seed/ \
    && chown -R bot:bot /app \
    && chown -R root:root /opt/casino/data-seed \
    && chmod -R a+rX,go-w /opt/casino/data-seed

COPY docker-entrypoint.sh /usr/local/bin/casino-entrypoint
RUN chmod 0755 /usr/local/bin/casino-entrypoint


VOLUME ["/app/data"]

ENTRYPOINT ["/usr/local/bin/casino-entrypoint"]
CMD ["python", "-u", "main.py"]
