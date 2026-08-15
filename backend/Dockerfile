FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TMPDIR=/data/tmp

# pymssql ships manylinux wheels, so SQL Server connectivity needs no system
# ODBC driver here. If a future change swaps in pyodbc, this is where
# msodbcsql18 + unixodbc would be installed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY alembic.ini entrypoint.sh ./

RUN chmod +x entrypoint.sh \
 && useradd --uid 10001 --create-home appuser \
 && mkdir -p /data/uploads /data/tmp/duckdb \
 && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
