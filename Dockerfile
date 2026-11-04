# Optional container image. Native installation is recommended on a Raspberry Pi.
FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg iproute2 openssl iputils-ping openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[vision,mqtt,web]" && rm -rf /src

RUN useradd --system --uid 10001 --home-dir /data pi-guard \
    && install -d -o pi-guard -g pi-guard -m 0700 /data /recordings

ENV PI_GUARD_DATA_DIR=/data \
    PI_GUARD_RECORDINGS_DIR=/recordings \
    PI_GUARD_CONFIG_FILE=/config/config.yaml \
    PI_GUARD_RUNTIME_DIR=/run/pi-guard \
    PYTHONUNBUFFERED=1

USER pi-guard
WORKDIR /data
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 CMD ["pi-guard", "health"]
CMD ["pi-guard", "run"]
