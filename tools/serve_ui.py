#!/usr/bin/env python3
"""تشغيل واجهة ديوان على 127.0.0.1؛ لا خدمة دائمة ولا تنزيل نموذج."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from providers.local_chat import LocalChatProvider
from providers.ollama import DEFAULT_MODEL
from providers.local_media import LocalMediaProvider
from providers.local_tools import LocalToolProvider
from agent.web_search import SearxngBackend
from webui.server import LocalApp, Server


def _discover_ollama() -> tuple[str | None, str | None, str | None, str | None]:
    """اكتشاف تلقائي للنماذج المحلية النشطة على Ollama (127.0.0.1:11434)."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=1.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = {m.get("name"): m.get("digest") for m in data.get("models", [])
                      if m.get("name") and m.get("digest") and type(m.get("size")) is int
                      and m["size"] > 0 and not m.get("remote_model") and not m.get("remote_host")
                      and "cloud" not in m["name"].lower()}
            chat_m = chat_v = media_m = media_v = None
            for pref in (DEFAULT_MODEL, "qwen3:14b", "qwen2.5:3b", "llama3.1:8b"):   # المعتمَدُ أولًا (ق٥٤)
                if pref in models:
                    chat_m, chat_v = pref, models[pref]
                    break
            if not chat_m:
                for name, digest in models.items():
                    if "cloud" not in name and "embed" not in name:
                        chat_m, chat_v = name, digest
                        break
            for pref in ("minicpm-v4.6:latest", "gemma4:latest"):
                if pref in models:
                    media_m, media_v = pref, models[pref]
                    break
            return chat_m, chat_v, media_m, media_v
    except Exception:
        return None, None, None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "var/daily-ui")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--provider", choices=("local", "mlx"), default=os.environ.get("DIWAN_PROVIDER", "local"))
    parser.add_argument("--runtime-receipt", type=Path,
                        help="إيصال bootstrap صريح لتفعيل أدوات الحاوية؛ لا تشغيل Docker عند فتح الواجهة")
    parser.add_argument("--web-search-url",
                        help="عنوانُ SearXNG (مثل http://127.0.0.1:8080) لتفعيل أداة البحث في الويب (ج٢)؛ بدونه لا تُعلَن")
    args = parser.parse_args()
    media_model, media_version = os.environ.get("DIWAN_MEDIA_MODEL"), os.environ.get("DIWAN_MEDIA_DIGEST")
    if args.provider == "mlx":
        agent_factory = None
        from providers.mlx_provider import MLXProvider
        model = os.environ.get("DIWAN_MLX_MODEL", "mlx-community/Qwen2.5-7B-Instruct-4bit")
        version = os.environ.get("DIWAN_MLX_DIGEST", "mlx-local-v1")
        factory = lambda: MLXProvider(model_name=model)
        factory()
    else:
        model, version = os.environ.get("DIWAN_CHAT_MODEL"), os.environ.get("DIWAN_CHAT_DIGEST")
        if not model or not version:
            auto_cm, auto_cv, auto_mm, auto_mv = _discover_ollama()
            if not model and auto_cm:
                model, version = auto_cm, auto_cv
                print(f"تم اكتشاف نموذج الحوار المحلي تلقائيًا: {model} ({version[:12]}...)", flush=True)
            if not media_model and auto_mm:
                media_model, media_version = auto_mm, auto_mv
                print(f"تم تفعيل وسائط م١٢ بنموذج الرؤية المحلي: {media_model}", flush=True)
        if not model or not version:
            parser.error("يلزم متغيرا DIWAN_CHAT_MODEL وDIWAN_CHAT_DIGEST المحليان أو تشغيل خادم Ollama محليًا")
        factory = lambda: LocalChatProvider(model, version)
        factory()  # تحقق إعداد المزود دون شبكة قبل فتح المنفذ.
        agent_factory = lambda: LocalToolProvider(model, version)
        agent_factory()
    if not 0 <= args.port <= 65535:
        parser.error("منفذ غير صالح")
    if bool(media_model) != bool(media_version):
        parser.error("إعداد الوسائط يتطلب DIWAN_MEDIA_MODEL وDIWAN_MEDIA_DIGEST معًا")
    app = None
    server = None
    try:
        media_factory = (lambda: LocalMediaProvider(media_model, media_version)) if media_model else None
        if media_factory:
            media_factory()
        app = LocalApp(args.root, model=model, model_version=version, provider_factory=factory,
                       media_model=media_model, media_model_version=media_version,
                       media_provider_factory=media_factory,
                       agent_provider_factory=agent_factory, runtime_receipt=args.runtime_receipt,
                       web_search=(SearxngBackend(args.web_search_url) if args.web_search_url else None))
        server = Server(app, args.port)
        print(f"ديوان المحلي: {server.origin}", flush=True)
        print("Ctrl+C للإغلاق؛ تُحفظ الجولات التي انتهت. انتظار النداء الجاري محدود بمهلته.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(getattr(exc, "code", "startup_failed"), file=sys.stderr)
        return 2
    finally:
        if server is not None:
            server.server_close()
        if app is not None:
            app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
