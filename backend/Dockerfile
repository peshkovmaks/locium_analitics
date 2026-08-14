FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y gcc libpq-dev && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user used by Celery services in docker-compose.yml
RUN groupadd -g 1000 appgroup && useradd -u 1000 -g appgroup -d /app -s /bin/bash app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
