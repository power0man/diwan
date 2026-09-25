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
    CAMELTOOLS_DATA=/opt/camel_tools \
    PATH=/opt/diwan-venv/bin:$PATH

RUN pip install --no-cache-dir 'uv==0.8.17'

WORKDIR /app
# الاعتمادياتُ قبل الشيفرة: طبقةٌ تُخزَّن ما لم يتغيّر القفل
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --python /usr/local/bin/python3

COPY . .
# قاعدةُ الصرف (CAMeL) داخل الصورة، في مسارٍ يقرؤه المستخدمُ غيرُ الجذر
RUN mkdir -p "$CAMELTOOLS_DATA" \
    && camel_data -i morphology-db-msa-r13 \
    && useradd --create-home --uid 1000 diwan \
    && chown -R diwan /app

USER diwan
# فحصُ الدخان افتراضيًّا؛ والواجهةُ: docker run --network host diwan python tools/serve_ui.py
CMD ["python", "tools/launch_check.py"]
