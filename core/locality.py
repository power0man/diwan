"""المحليّةُ تُشتقّ من الاسم والمضيف، لا من إعلان المزوّد (جديد-is-local-guard، الفجوة core-g1).

كان `OllamaProvider` يضع `is_local = True` لكل نموذج، فوصلت حمولةُ `local_only` إلى
`gpt-oss:120b-cloud` فعلًا: خادمُ Ollama المحليّ يمرّر النموذجَ السحابيّ إلى ollama.com،
والإعلانُ لم يرَ ذلك. فهنا مصدرٌ واحد يقرؤه المزوّد والنواةُ والمواضعُ الخمسة في الجلسات
والواجهة، ولا يُصدَّق فيه إعلانٌ يناقضه الاسمُ أو المضيف.

القاعدة:
- نموذجٌ في اسمه مقطعُ `cloud` بعد بدايةٍ أو `-` أو `:` أو `/` سحابيٌّ. والنمطُ لا يشترط
  نهايةَ المقطع عمدًا: `x:cloudy` يُرفض كذلك، فالخطأ في هذا الاتجاه رفضٌ لا تسرّب.
- المضيفُ محليٌّ إن كان `localhost` أو عنوانَ loopback بعينه (127.0.0.0/8 أو ::1)،
  بمخطّط http أو https. وما لا يُحلَّل عنوانًا يُرفض.
- المزوّدُ محليٌّ إن أعلن `is_local` بـTrue بعينها (لا بما يُقرأ صدقًا)، ولم يناقضه
  اسمُ نموذجه ولا مضيفُه حيث يُعلنهما.

حدودٌ معلنة:
- اسمٌ سحابيٌّ لا يحمل `cloud` (وكيلٌ محليٌّ بلقبٍ مستعار) يفلت: الفحصُ على الاسم لا على
  ما يفعله الخادم. ومزوّدُ م٩ (`providers/local_chat.py`) يسدّ هذا بفحص /api/show.
- مزوّدٌ لا يُعلن `model` ولا `base_url` يُقبل بإعلانه وحده؛ والنواةُ تفحص `req.model`
  فحصًا ثانيًا مستقلًّا لهذا.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

CLOUD_MODEL = re.compile(r"(?:^|[-:/])cloud", re.IGNORECASE)


def is_cloud_model(model) -> bool:
    """اسمُ نموذجٍ سحابيّ، أو ما ليس اسمًا — فالمجهولُ لا يُعدّ محليًّا."""
    return not isinstance(model, str) or bool(CLOUD_MODEL.search(model))


def is_loopback_url(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # منفذٌ غير صالح يرفع ValueError
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_local_provider(provider) -> bool:
    """الإعلانُ `is True` بعينه، ثم لا يناقضه اسمُ النموذج ولا المضيف."""
    if getattr(provider, "is_local", None) is not True:
        return False
    model = getattr(provider, "model", None)
    if model is not None and is_cloud_model(model):
        return False
    base_url = getattr(provider, "base_url", None)
    return base_url is None or is_loopback_url(base_url)
