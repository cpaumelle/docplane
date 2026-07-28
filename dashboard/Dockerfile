FROM node:22-alpine AS assets
WORKDIR /src
COPY package.json esbuild.config.js ./
RUN npm install --ignore-scripts --no-audit --no-fund
COPY editor-src ./editor-src
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 docplane
COPY . ./dashboard
COPY --from=assets /src/static/ ./dashboard/static/
USER 10001:10001
EXPOSE 8051
CMD ["uvicorn", "dashboard.combined_app:app", "--host", "0.0.0.0", "--port", "8051"]
