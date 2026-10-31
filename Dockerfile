# CrowdCount: detector + dashboard in one container (CPU inference).
#
#   docker build -t crowdcount .
#   docker run --rm -p 8080:8080 crowdcount                      # demo on the sample video
#   docker run --rm -p 8080:8080 -v crowdcount-data:/data crowdcount run --host 0.0.0.0 --source rtsp://…
#
# State lives in /data (users, settings, logs, zones.json); downloads in /cache.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CROWDCOUNT_DATA_DIR=/data \
    CROWDCOUNT_CACHE_DIR=/cache

# OpenCV runtime libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch first: the default wheel bundles CUDA and is several GB larger.
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && rm -rf /src

RUN useradd --create-home --uid 1000 crowdcount \
    && mkdir -p /data /cache \
    && chown crowdcount:crowdcount /data /cache
USER crowdcount
# Relative paths (zones.json) resolve inside the persistent volume.
WORKDIR /data
VOLUME ["/data", "/cache"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"

ENTRYPOINT ["crowdcount"]
CMD ["demo", "--host", "0.0.0.0"]
