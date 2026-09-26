# ج٨ (#44): صورةُ المحلّل. تُبنى من هذا الملف وقفلِه وحدهما بـ`analysis/prepare.py`، وهويّتُها بإيصال
# (بصمةُ الصورة وبصمةُ القفل ونسخةُ بايثون) يتحقّق منه `core/execution.py` عند كل نداءٍ قبل أن يُرسل كود.
# لا مصدرَ ولا أسرارَ ولا مقبسَ Docker في سياق البناء؛ والتشغيلُ بلا شبكة، ونظامُ الملفّات فيه للقراءة وحدها.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
FROM ${PYTHON_IMAGE}
ARG LOCK_SHA256
LABEL diwan.lock-sha256="${LOCK_SHA256}"
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin analyst \
    && mkdir /workspace && chown 1000:1000 /workspace
COPY requirements.lock /opt/analysis.lock
# --no-deps: القفلُ كاملٌ بعابرها، فلا يُحلّ عند البناء ما لم يُراجع. وسرُّ `pip-ca` الاختياريّ شهادةُ وكيلٍ
# يعيد توقيع TLS (كجلسات السحابة)؛ لا يدخل طبقةً، والبصماتُ تحفظ السلامةَ أيًّا كان موقّعُ الاتصال.
RUN --mount=type=secret,id=pip-ca,required=false \
    echo "${LOCK_SHA256}  /opt/analysis.lock" | sha256sum --check --status \
    && python -m venv /opt/venv \
    && if [ -s /run/secrets/pip-ca ]; then export PIP_CERT=/run/secrets/pip-ca; fi \
    && /opt/venv/bin/python -m pip install --no-cache-dir --no-deps --require-hashes -r /opt/analysis.lock \
    && /opt/venv/bin/python -m pip check
COPY diwan_ar.py /opt/diwan/diwan_ar.py
# ذاكرةُ الخطوط تُبنى مرّةً هنا؛ والحاويةُ تنسخها إلى /tmp فلا تُعاد في كل نداء
RUN /opt/venv/bin/python -c "import pathlib, site; pathlib.Path(site.getsitepackages()[0], 'diwan.pth').write_text('/opt/diwan\n')" \
    && MPLCONFIGDIR=/opt/mplconfig MPLBACKEND=Agg /opt/venv/bin/python -c "import matplotlib.pyplot as plt; plt.figure(); plt.close('all')" \
    && chmod -R a+rX /opt/mplconfig /opt/diwan
ENV PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    HOME="/tmp/home" PYTHONDONTWRITEBYTECODE="1" MPLBACKEND="Agg"
USER 1000:1000
WORKDIR /workspace
