FROM python:3-slim

WORKDIR /app
COPY scrape.py server.py watchdog.py index.html data.json ./
COPY images/ ./images/

ENV PORT=80 \
    BIND=0.0.0.0

EXPOSE 80

CMD ["python3", "server.py"]
