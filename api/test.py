
import os
import json
import requests
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        token = os.environ.get("SELLER_DATA_TOKEN", "")

        result = {
            "python": "ok",
            "github_token_exists": bool(token),
            "github_connection": "not_tested"
        }

        if not token:
            result["github_connection"] = "missing_token"
        else:
            try:
                response = requests.get(
                    "https://api.github.com/repos/Booknight20/zarinmah-seller-data/contents/state.json",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28"
                    },
                    timeout=15
                )

                result["github_http_status"] = response.status_code

                if response.status_code == 200:
                    result["github_connection"] = "ok"
                elif response.status_code == 404:
                    result["github_connection"] = "file_not_found"
                elif response.status_code == 401:
                    result["github_connection"] = "unauthorized"
                elif response.status_code == 403:
                    result["github_connection"] = "forbidden"
                else:
                    result["github_connection"] = "error"

            except Exception as exc:
                result["github_connection"] = "request_failed"
                result["error_type"] = type(exc).__name__

        body = json.dumps(result).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
