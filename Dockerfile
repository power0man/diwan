# ج٦ (#36): ديوان في حاوية. التثبيتُ أمرٌ واحد: `docker build -t diwan .`
# الاعتمادياتُ من uv.lock المجمَّد وحده (لا حلَّ جديدًا)، وبايثون 3.12 لأن عجلاتِ editdistance
# (عبر camel-tools) موجودةٌ لها فلا يلزم مترجم (قيس على Nitro في #72). والمحرّكُ خارج الحاوية
# (Ollama على المضيف)، والمتونُ لا تدخلها (ق٥٨)؛ ففحصُ الدخان فيها يخرج 3 بحدٍّ معلن.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/diwan-venv \
    UV_CACHE_DIR=/opt/uv-cache \
    DIWAN_DATA_HOME=/app/var \
    CAMELTOOLS_DATA=/opt/camel_tools \
    PATH=/opt/diwan-venv/bin:$PATH

RUN pip install --no-cache-dir 'uv==0.8.17'
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /app
COPY --from=node:24-bookworm-slim /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
# الاعتمادياتُ قبل الشيفرة: طبقةٌ تُخزَّن ما لم يتغيّر القفل
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra dev --python /usr/local/bin/python3

# قاعدةُ الصرف (CAMeL) داخل الصورة، في مسارٍ يقرؤه المستخدمُ غيرُ الجذر
RUN mkdir -p "$CAMELTOOLS_DATA" \
    && camel_data -i morphology-db-msa-r13 \
    && useradd --create-home --uid 1000 diwan \
    && mkdir -p /app/var \
    && chown diwan /app /app/var \
    && chown -R diwan /opt/uv-cache

COPY --chown=diwan . .
RUN python -m hatchling build -t wheel -d /opt/diwan-wheel \
    && uv pip install --no-cache --python /opt/diwan-venv/bin/python --no-deps /opt/diwan-wheel/*.whl

USER diwan
VOLUME ["/app/var"]
EXPOSE 8765
# انشر منفذ الواجهة على المضيف بـ -p 127.0.0.1:8765:8765؛ الدليل في docs/INSTALL.md.
# بلا محرّك ولا متون: تعذّرٌ معلن برمز 3، وليس إعلان جاهزية.
CMD ["diwan", "check"]
