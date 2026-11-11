# Replacement application image; building never activates acquisition.
FROM trading-ai-postgresql-base:20261110
USER root
COPY scrapper/requirements.lock /tmp/native-requirements.lock
COPY scrapper/requirements.postgresql.txt /tmp/native-postgresql.txt
RUN python -m pip install --no-cache-dir --no-compile --require-hashes -r /tmp/native-requirements.lock \
    && python -m pip install --no-cache-dir --no-compile -r /tmp/native-postgresql.txt \
    && python -m pip check
COPY --chown=999:999 scrapper /app/scrapper
ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision=$SOURCE_REVISION
USER scraper
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD ["python", "-c", "from scrapper.db.database import connect,init_db; c=connect(read_only=True); init_db(c); c.execute('SELECT 1'); c.close()"]
