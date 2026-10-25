# ---- stage 1: build wheels for every dependency -----------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# ---- stage 2: runtime image, no compilers or pip cache ----------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links /wheels -r /wheels/requirements.txt \
    && rm -rf /wheels

COPY engine.py main.py locations.csv ./
COPY tools ./tools

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

ENV PORT=8000 \
    LOCATIONS_CSV=/app/locations.csv \
    WORKERS=1 \
    LOG_LEVEL=info

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers ${WORKERS} --log-level ${LOG_LEVEL}"]
