FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/dnd_bot.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py db.py ./

RUN mkdir -p /data

VOLUME ["/data"]

CMD ["python", "main.py"]
