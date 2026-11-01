
import os
import json
import base64
import requests
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = {"python": "ok"}

        try:
            response = requests.get(
                "https://api.github.com/repos/Booknight20/zarinmah-seller-data/contents/state.json",
                headers={
                    "Authorization": f"Bearer {os.environ.get('SELLER_DATA_TOKEN', '')}",
                    "Accept": "application/vnd.github+json"
                },
                timeout=15
            )

            response.raise_for_status()
            item = response.json()

            raw = base64.b64decode(item["content"])
            state = json.loads(raw.decode("utf-8"))

            result["json_decode"] = "ok"
            result["state_is_dict"] = isinstance(state, dict)
            result["listings_is_list"] = isinstance(state.get("listings"), list)
            result["listings_count"] = len(state.get("listings", []))

        except Exception as exc:
            result["error_type"] = type(exc).__name__
            result["error"] = str(exc)[:300]

        body = json.dumps(result, ensure_ascii=False).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

