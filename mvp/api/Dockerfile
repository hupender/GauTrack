FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Non-root runtime user (spec §1.12).
RUN useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin gautrack \
 && mkdir -p /data/photos \
 && chown -R gautrack:gautrack /app /data/photos \
 && chmod +x /app/entrypoint.sh
USER gautrack

EXPOSE 8000
CMD ["/app/entrypoint.sh"]
