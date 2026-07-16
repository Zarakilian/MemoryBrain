FROM python:3.11-slim
WORKDIR /app
COPY brain/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY brain/app/ app/
COPY brain/tests/ tests/

# Build stamp (served at /api/ui/version, shown in the UI footer):
# "<12-char content hash of app/>.<UTC build time>". Same code -> cached
# layer -> same stamp; any app/ change -> new stamp. Ends the
# "am-I-even-running-new-code" class of debugging failure.
RUN python -c "import hashlib,pathlib,datetime;h=hashlib.sha256();[(h.update(str(p).encode()),h.update(p.read_bytes())) for p in sorted(pathlib.Path('app').rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')];pathlib.Path('app/BUILD_STAMP').write_text(h.hexdigest()[:12]+'.'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M'))"
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
