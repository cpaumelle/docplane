FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 docplane
COPY . ./dashboard
USER 10001:10001
EXPOSE 8051
CMD ["uvicorn", "dashboard.combined_app:app", "--host", "0.0.0.0", "--port", "8051"]
