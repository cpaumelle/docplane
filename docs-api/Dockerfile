FROM python:3.11-slim

ARG GIT_SHA=unknown
ARG BUILD_TIMESTAMP=unknown
ENV GIT_SHA=${GIT_SHA} \
    BUILD_TIMESTAMP=${BUILD_TIMESTAMP} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY docs-api/requirements.txt ./requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends rsync curl \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 docplane \
    && rm -rf /var/lib/apt/lists/*

COPY docs-api/app ./app
COPY docs-api/migrate.py ./migrate.py
COPY docs-api/entrypoint.sh ./entrypoint.sh
COPY db/migrations ./db/migrations
COPY mkdocs ./mkdocs
RUN mkdir -p /data/docs /data/mkdocs /data/site \
    && chmod 0755 ./entrypoint.sh \
    && chown -R docplane:docplane /app /data

USER 10001:10001
EXPOSE 8010
ENTRYPOINT ["/app/entrypoint.sh"]
