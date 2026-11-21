FROM python:3.12-slim AS runtime

WORKDIR /app

COPY pyproject.toml .
RUN python -c "import tomllib; open('/tmp/requirements.txt', 'w').write('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY rag ./rag
COPY index ./index

ENV PYTHONUNBUFFERED=1
FROM runtime AS web
RUN python -c "import tomllib; open('/tmp/web-requirements.txt', 'w').write('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['optional-dependencies']['web']))" \
    && pip install --no-cache-dir -r /tmp/web-requirements.txt
CMD ["python", "-m", "uvicorn", "rag.web:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]

FROM runtime AS bot
CMD ["python", "-m", "rag.bot"]
