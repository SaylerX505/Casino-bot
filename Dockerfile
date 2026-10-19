# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# --- RUNTIME USER ---
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 bot \
    && useradd --system --uid 10001 --gid bot \
        --home-dir /app --no-create-home --shell /usr/sbin/nologin bot

WORKDIR /app

# --- DEPENDENCY LAYERS ---
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY . /app

# --- DATA SEED (DB EXCLUDED) ---
RUN mkdir -p /opt/casino/data-seed /app/data \
    && cp -a /app/data/. /opt/casino/data-seed/ \
    && chown -R bot:bot /app \
    && chown -R root:root /opt/casino/data-seed \
    && chmod -R a+rX,go-w /opt/casino/data-seed

COPY docker-entrypoint.sh /usr/local/bin/casino-entrypoint
RUN chmod 0755 /usr/local/bin/casino-entrypoint

# --- PERSISTENT DATA VOLUME ---
VOLUME ["/app/data"]

ENTRYPOINT ["/usr/local/bin/casino-entrypoint"]
CMD ["python", "-u", "main.py"]
