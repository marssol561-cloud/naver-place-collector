FROM python:3.11-slim

WORKDIR /app

ENV PYTHONIOENCODING=utf-8

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && \
    apt-get install -y --no-install-recommends xvfb && \
    playwright install --with-deps chromium && \
    rm -rf /var/lib/apt/lists/*

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "xvfb-run -a uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
