"""HF private CPU deployment: real local engine, explicit limited capabilities."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

MODEL = "qwen3.5:9b"
DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"
host = os.environ.get("SPACE_HOST", "")
if not re.fullmatch(r"[a-z0-9-]+\.hf\.space", host):
    raise SystemExit("cloud_space_host_invalid")
root = Path("/home/diwan/ephemeral-workspace")
root.mkdir(mode=0o700, exist_ok=True)
os.umask(0o077)
children = []


def spawn(argv):
    process = subprocess.Popen(argv)
    children.append(process)
    return process


try:
    spawn(["ollama", "serve"])
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=1).close()
            break
        except Exception:
            time.sleep(1)
    subprocess.run(["ollama", "pull", MODEL], check=True, timeout=900)
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
        models = json.load(response)["models"]
    if not any(m.get("name") == MODEL and m.get("digest") == DIGEST for m in models):
        raise SystemExit("cloud_model_digest_mismatch")
    # Nginx is the only public listener. Diwan keeps its loopback trust boundary,
    # exact public Host/Origin and CSRF checks. HF private visibility authenticates
    # before ingress; never change this Space to protected/public.
    config = Path("/home/diwan/nginx.conf")
    config.write_text('''pid /home/diwan/nginx.pid;
error_log stderr warn;
events { worker_connections 128; }
http {
  access_log off;
  client_body_temp_path /home/diwan/nginx-body;
  proxy_temp_path /home/diwan/nginx-proxy;
  fastcgi_temp_path /home/diwan/nginx-fastcgi;
  uwsgi_temp_path /home/diwan/nginx-uwsgi;
  scgi_temp_path /home/diwan/nginx-scgi;
  server {
    listen 7860;
    client_max_body_size 2m;
    location / {
      proxy_pass http://127.0.0.1:8765;
      proxy_set_header Host $host;
      proxy_set_header Authorization "";
      proxy_http_version 1.1;
      proxy_read_timeout 330s;
      proxy_send_timeout 330s;
      proxy_buffering off;
    }
  }
}
''')
    env = os.environ.copy()
    env.update(DIWAN_CHAT_MODEL=MODEL, DIWAN_CHAT_DIGEST=DIGEST)
    app = subprocess.Popen([sys.executable, "tools/serve_ui.py", "--root", str(root),
                            "--port", "8765", "--public-origin", "https://" + host], env=env)
    children.append(app)
    spawn(["nginx", "-c", str(config), "-g", "daemon off;"])
    while all(p.poll() is None for p in children):
        time.sleep(1)
    raise SystemExit("cloud_child_stopped")
finally:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
