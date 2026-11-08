FROM python:3.12.14-slim-trixie@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

ARG EXPECTED_PYTHON_VERSION=3.12.14

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCRAPER_STORE_PATH=/data \
    SCRAPER_DB_PATH=/data/scraper.db \
    SCREENER_SESSION_FILE=/run/secrets/screener_session \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN groupadd --system scraper && useradd --system --gid scraper --home /app scraper
WORKDIR /app
COPY scrapper/requirements.lock /app/requirements.lock
RUN test "$(python -c 'import platform; print(platform.python_version())')" = "$EXPECTED_PYTHON_VERSION" \
    && python -m pip install --no-cache-dir --no-compile --require-hashes \
        --requirement /app/requirements.lock \
    && python -m pip check
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /var/cache/fontconfig/* \
        /var/cache/ldconfig/aux-cache /var/log/apt/* \
        /var/log/alternatives.log /var/log/dpkg.log
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg tesseract-ocr \
    && rm -rf /var/lib/apt/lists/* /var/cache/fontconfig/* \
        /var/cache/ldconfig/aux-cache /var/log/apt/* \
        /var/log/alternatives.log /var/log/dpkg.log
RUN chmod -R a+rX /ms-playwright
COPY scrapper /app/scrapper
RUN mkdir -p /data && chown -R scraper:scraper /app /data

USER scraper
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "from scrapper.config import DB_PATH; import sqlite3; c=sqlite3.connect(DB_PATH); c.execute('SELECT 1'); c.close()"]

ENTRYPOINT ["python", "-m", "scrapper.cli"]
CMD ["--help"]
