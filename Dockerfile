FROM python:3-slim

WORKDIR /app
COPY scrape.py server.py watchdog.py index.html ./

# Baked-in snapshot so `docker run` works with no volume. Mounting a host
# directory at /data (with DATA_DIR=/data) serves live data instead.
COPY data.json ./
COPY images/ ./images/

ENV PORT=80 \
    BIND=0.0.0.0 \
    DATA_DIR=/app

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','80')+'/health',timeout=4).status==200 else 1)"

CMD ["python3", "server.py"]
