FROM python:3.12-slim

LABEL org.opencontainers.image.title="ONVIF Idle Proxy"
LABEL org.opencontainers.image.description="Fixes stuck MoveStatus on Hikvision/Dahua PTZ cameras for Frigate autotracking"
LABEL org.opencontainers.image.version="1.5.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY onvif_idle_proxy.py /app/onvif_idle_proxy.py

RUN useradd --system --uid 10001 --no-create-home proxyuser
USER proxyuser

ENTRYPOINT ["python", "/app/onvif_idle_proxy.py"]
