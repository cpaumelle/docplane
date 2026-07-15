FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py common.py ./
COPY tools/ ./tools/

RUN useradd -r -u 10001 -d /app docsmcp
USER docsmcp

EXPOSE 8049
CMD ["python", "server.py"]
