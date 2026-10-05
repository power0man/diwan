# ج٦ (#36): ديوان في حاوية. التثبيتُ أمرٌ واحد: `docker build -t diwan .`
# الاعتمادياتُ من uv.lock المجمَّد وحده (لا حلَّ جديدًا)، وبايثون 3.12 لأن عجلاتِ editdistance
# (عبر camel-tools) موجودةٌ لها فلا يلزم مترجم (قيس على Nitro في #72). والمحرّكُ خارج الحاوية
# (Ollama على المضيف)، والمتونُ لا تدخلها (ق٥٨)؛ ففحصُ الدخان فيها يخرج 3 بحدٍّ معلن.
# الصورتان ببصمة فهرسهما لا بوسمٍ يتحرّك (#285)؛ البصمتان قُرئتا من Docker Hub في ٤ أكتوبر ٢٠٢٦ للوسمين نفسيهما.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/diwan-venv \
    UV_CACHE_DIR=/opt/uv-cache \
    DIWAN_DATA_HOME=/app/var \
    CAMELTOOLS_DATA=/opt/camel_tools \
    PATH=/opt/diwan-venv/bin:$PATH

# uv بإصداره وبصمات عجلاته من PyPI (لينكس glibc على x86_64 وaarch64)، فلا يُثبَّت ما لم يُقرأ (#285)
RUN printf '%s\n' 'uv==0.8.17 \
      --hash=sha256:b6d30d02fb65193309fc12a20f9e1a9fab67f469d3e487a254ca1145fd06788f \
      --hash=sha256:84d56ae50ca71aec032577adf9737974554a82a94e52cee57722745656c1d383 \
      --hash=sha256:3941cecd9a6a46d3d4505753912c9cf3e8ae5eea30b9d0813f3656210f8c5d01' > /tmp/uv-requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/uv-requirements.txt \
    && rm /tmp/uv-requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory /app
COPY --from=node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6 /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY pyproject.toml uv.lock ./

# مرحلةُ العجلة: الإضافةُ dev (hatchling وpytest) تلزم بناءَ العجلة وحده، فتُثبَّت هنا ولا تدخل طبقاتِ صورة التشغيل.
# فحذفُها في طبقةٍ لاحقة يُخفيها ولا يُخرج بايتاتِها من الطبقة السابقة (ملاحظة Codex على #294، #285).
FROM base AS wheel
RUN uv sync --frozen --no-install-project --extra dev --python /usr/local/bin/python3
COPY . .
RUN python -m hatchling build -t wheel -d /opt/diwan-wheel

# صورةُ التشغيل: الاعتمادياتُ قبل الشيفرة (طبقةٌ تُخزَّن ما لم يتغيّر القفل)، بلا dev؛ واختباراتُ CI تثبّتها فوقها من
# القفل ببصماتها (container-smoke.yml)
FROM base
RUN uv sync --frozen --no-install-project --python /usr/local/bin/python3

# قاعدةُ الصرف (CAMeL) داخل الصورة، في مسارٍ يقرؤه المستخدمُ غيرُ الجذر
RUN mkdir -p "$CAMELTOOLS_DATA" \
    && camel_data -i morphology-db-msa-r13 \
    && useradd --create-home --uid 1000 diwan \
    && mkdir -p /app/var \
    && chown diwan /app /app/var \
    && chown -R diwan /opt/uv-cache

COPY --chown=diwan . .
COPY --from=wheel /opt/diwan-wheel /opt/diwan-wheel
RUN uv pip install --no-cache --python /opt/diwan-venv/bin/python --no-deps /opt/diwan-wheel/*.whl

USER diwan
VOLUME ["/app/var"]
EXPOSE 8765
# انشر منفذ الواجهة على المضيف بـ -p 127.0.0.1:8765:8765؛ الدليل في docs/INSTALL.md.
# بلا محرّك ولا متون: تعذّرٌ معلن برمز 3، وليس إعلان جاهزية.
CMD ["diwan", "check"]
