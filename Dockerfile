FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SCRAPER_STORE_PATH=/data \
    SCRAPER_DB_PATH=/data/scraper.db \
    SCREENER_SESSION_FILE=/run/secrets/screener_session

RUN groupadd --system scraper && useradd --system --gid scraper --home /app scraper
WORKDIR /app
COPY scrapper/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY scrapper /app/scrapper
RUN mkdir -p /data && chown -R scraper:scraper /app /data

USER scraper
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD ["python", "-c", "from scrapper.config import DB_PATH; import sqlite3; c=sqlite3.connect(DB_PATH); c.execute('SELECT 1'); c.close()"]

ENTRYPOINT ["python", "-m", "scrapper.cli"]
CMD ["--help"]
