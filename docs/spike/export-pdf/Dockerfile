# Spike EX1: the app's base image plus what WeasyPrint needs. Compare its size with the
# python:3.13-slim + ffmpeg image the app builds today.
FROM python:3.13-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libpango-1.0-0 libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir weasyprint python-docx markdown-it-py
WORKDIR /spike
