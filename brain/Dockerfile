FROM python:3.11-slim
WORKDIR /app
COPY brain/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY brain/app/ app/
COPY brain/tests/ tests/
COPY brain/entrypoint.sh /entrypoint.sh
COPY cli/ cli/

# L8: Run as non-root user
# gosu is the standard Docker privilege-drop helper (used by official images)
RUN apt-get update -qq && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r brain && useradd -r -g brain -d /app brain \
    && mkdir -p /app/data && chown -R brain:brain /app \
    && chmod +x /entrypoint.sh

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7741"]
