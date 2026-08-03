FROM python:3.11-slim

ARG GIT_SHA=unknown
ARG BUILD_TIMESTAMP=unknown
ENV GIT_SHA=${GIT_SHA} \
    BUILD_TIMESTAMP=${BUILD_TIMESTAMP} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Standard OCI provenance so image metadata answers "what commit is this"
# without a running container; /healthz git_sha remains the runtime check.
LABEL org.opencontainers.image.revision=${GIT_SHA} \
      org.opencontainers.image.created=${BUILD_TIMESTAMP} \
      org.opencontainers.image.title="docplane-api" \
      org.opencontainers.image.source="https://github.com/cpaumelle/docplane"

WORKDIR /app
COPY docs-api/requirements.txt ./requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends rsync curl \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 docplane \
    && rm -rf /var/lib/apt/lists/*

COPY docs-api/app ./app
# Canonical sanitisation transform (policy issue #60): the model API imports
# migration.redaction as its secret-shape DETECTOR (reject, never rewrite).
COPY migration/__init__.py ./migration/__init__.py
COPY migration/redaction.py ./migration/redaction.py
COPY migration/links.py ./migration/links.py
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
