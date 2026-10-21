# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# gosu lets the entrypoint fix mounted-volume ownership, then drop privileges.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 bot \
    && useradd --system --uid 10001 --gid bot \
        --home-dir /app --no-create-home --shell /usr/sbin/nologin bot

WORKDIR /app

# Install dependencies separately so source-only edits can reuse this layer.
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

COPY . /app

# Keep a read-only seed snapshot outside the mount point. This lets a new,
# empty persistent volume be initialized without overwriting existing data.
# Database files are excluded by .dockerignore and must be migrated separately.
RUN mkdir -p /opt/casino/data-seed /app/data \
    && cp -a /app/data/. /opt/casino/data-seed/ \
    && chown -R bot:bot /app \
    && chown -R root:root /opt/casino/data-seed \
    && chmod -R a+rX,go-w /opt/casino/data-seed

COPY docker-entrypoint.sh /usr/local/bin/casino-entrypoint
RUN chmod 0755 /usr/local/bin/casino-entrypoint

# Configure a JustRunMy.App persistent volume mounted at this exact path.
VOLUME ["/app/data"]

ENTRYPOINT ["/usr/local/bin/casino-entrypoint"]
CMD ["python", "-u", "main.py"]
