FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir . \
    && useradd --system --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /srv/data/audio \
    && chown -R app:app /srv/data

CMD ["alembic", "upgrade", "head"]
