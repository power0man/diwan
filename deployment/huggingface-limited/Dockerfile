FROM python:3.12-slim-bookworm
COPY --from=ollama/ollama:0.35.0 /usr/bin/ollama /usr/bin/ollama
COPY --from=ollama/ollama:0.35.0 /usr/lib/ollama /usr/lib/ollama
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/diwan-venv \
    PATH=/opt/diwan-venv/bin:$PATH CAMELTOOLS_DATA=/opt/camel_tools \
    OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/home/diwan/models
RUN pip install --no-cache-dir uv==0.8.17 \
    && apt-get update && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 diwan
WORKDIR /app
COPY app/pyproject.toml app/uv.lock ./
RUN uv sync --frozen --no-install-project --extra dev --python /usr/local/bin/python3 \
    && mkdir -p "$CAMELTOOLS_DATA" && camel_data -i morphology-db-msa-r13
COPY --chown=diwan:diwan app/ /app/
COPY --chown=diwan:diwan launch.py /app/cloud_launch.py
USER diwan
EXPOSE 7860
CMD ["python", "-u", "/app/cloud_launch.py"]
