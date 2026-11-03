FROM python:3.13-slim

# Pango is what the PDF export (WeasyPrint) draws text with. ffmpeg already pulls it in; naming it
# keeps the export working if ffmpeg's dependencies ever change.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libpango-1.0-0 libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
# Operator scripts run with `docker compose exec web python scripts/<name>.py` (delete_user,
# backfill_extraction, and the Phase C key re-encryption later).
COPY scripts ./scripts
RUN pip install --no-cache-dir . \
    && useradd --system --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /srv/data/audio \
    && chown -R app:app /srv/data

CMD ["alembic", "upgrade", "head"]
