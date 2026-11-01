
import json
import traceback
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = {}

        try:
            import api.listings as listings

            result["import_listings"] = "ok"

            state, sha = listings.load_state()

            result["load_state"] = "ok"
            result["listing_count"] = len(state.get("listings", []))

            visible = [
                listings.public_listing(item)
                for item in state.get("listings", [])
                if item.get("status") in ("published", "sold")
            ]

            result["public_filter"] = "ok"
            result["visible_count"] = len(visible)

        except Exception as exc:
            result["error_type"] = type(exc).__name__
            result["error"] = str(exc)[:500]
            result["traceback"] = traceback.format_exc()[-2500:]

        body = json.dumps(
            result,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
