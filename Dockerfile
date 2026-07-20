FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-service.txt ./requirements-service.txt
RUN pip install --no-cache-dir -r requirements-service.txt \
    && useradd --system --uid 10001 --create-home --home-dir /home/spiritkin spiritkin \
    && mkdir -p /data/control_plane /app/state

COPY scripts ./scripts
COPY backend ./backend
COPY config ./config
COPY docs ./docs
COPY mobile-link-bridge ./mobile-link-bridge

RUN chown -R spiritkin:spiritkin /data /app \
    && python -m py_compile \
        backend/orchestrator/runtime_host.py \
        scripts/control_plane_store.py \
        scripts/control_plane_worker.py \
        scripts/mobile_link_receiver.py

USER spiritkin

EXPOSE 8791
VOLUME ["/data/control_plane"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8791/android/health', timeout=3).read()"

CMD ["python", "scripts/mobile_link_receiver.py", "--host", "0.0.0.0", "--port", "8791", "--state-dir", "/data/control_plane"]
