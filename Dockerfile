FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN python -c "import tomllib; open('/tmp/requirements.txt', 'w').write('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
    && pip install --no-cache-dir -r /tmp/requirements.txt

COPY rag ./rag
COPY index ./index

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "rag.bot"]
