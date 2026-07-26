FROM python:3.11-slim
WORKDIR /app
# rsync: the deploy applies a staged mkdocs build into the live served dir atomically
# per file (no mass-404 rebuild window). See app/generator.py _build_site.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rsync \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# Ship unit tests so they can run in-image: python -m pytest tests/ -q (text-only, tiny).
COPY tests ./tests
ARG GIT_SHA=unknown
ARG BUILD_TIMESTAMP=unknown
ENV GIT_SHA=$GIT_SHA
ENV BUILD_TIMESTAMP=$BUILD_TIMESTAMP
ARG DEPLOY_ID=unknown
ENV DEPLOY_ID=$DEPLOY_ID
CMD ["uvicorn", "app.application:app", "--host", "0.0.0.0", "--port", "8010"]
