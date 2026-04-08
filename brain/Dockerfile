FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/

# L8: Run as non-root user
RUN groupadd -r brain && useradd -r -g brain -d /app brain \
    && mkdir -p /app/data && chown -R brain:brain /app
USER brain

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7741"]
