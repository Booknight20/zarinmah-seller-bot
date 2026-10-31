python
import base64
import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import requests


# =========================================================
# تنظیمات
# =========================================================

DATA_REPO = "Booknight20/zarinmah-seller-data"
STATE_PATH = "state.json"

GITHUB_TOKEN = os.environ.get("SELLER_DATA_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

GITHUB_API = (
    f"https://api.github.com/repos/"
    f"{DATA_REPO}/contents/{STATE_PATH}"
)

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
})


# =========================================================
# پاسخ خطا
# =========================================================

def send_error(handler, status, message):

    body = message.encode("utf-8")

    handler.send_response(status)

    handler.send_header(
        "Content-Type",
        "text/plain; charset=utf-8"
    )

    handler.send_header(
        "Cache-Control",
        "no-store"
    )

    handler.send_header(
        "Access-Control-Allow-Origin",
        "*"
    )

    handler.send_header(
        "Content-Length",
        str(len(body))
    )

    handler.end_headers()
    handler.wfile.write(body)


# =========================================================
# دریافت اطلاعات آگهی
# =========================================================

def load_listing(listing_id):

    if not GITHUB_TOKEN:
        raise RuntimeError(
            "SELLER_DATA_TOKEN تنظیم نشده است."
        )

    response = SESSION.get(
        GITHUB_API,
        timeout=20
    )

    response.raise_for_status()

    item = response.json()

    raw = base64.b64decode(
        item["content"]
    )

    state = json.loads(
        raw.decode("utf-8")
    )

    listings = state.get("listings", [])

    for listing in listings:

        if str(listing.get("id")) == str(listing_id):

            # فقط آگهی‌های منتشرشده یا فروخته‌شده
            if listing.get("status") not in (
                "published",
                "sold"
            ):
                return None

            return listing

    return None


# =========================================================
# دریافت عکس از تلگرام
# =========================================================

def get_telegram_photo(file_id):

    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN تنظیم نشده است."
        )

    response = requests.get(
        f"{TG_API}/getFile",
        params={
            "file_id": file_id
        },
        timeout=20
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            "دریافت مسیر عکس از تلگرام ناموفق بود."
        )

    file_path = result["result"].get("file_path")

    if not file_path:
        raise RuntimeError(
            "مسیر عکس در تلگرام پیدا نشد."
        )

    download_url = (
        f"https://api.telegram.org/file/"
        f"bot{TELEGRAM_TOKEN}/{file_path}"
    )

    photo_response = requests.get(
        download_url,
        timeout=30
    )

    photo_response.raise_for_status()

    content_type = photo_response.headers.get(
        "Content-Type",
        "image/jpeg"
    )

    if not content_type.startswith("image/"):
        raise RuntimeError(
            "فایل دریافتی تصویر معتبر نیست."
        )

    return (
        photo_response.content,
        content_type
    )


# =========================================================
# Vercel handler
# =========================================================

class handler(BaseHTTPRequestHandler):

    def do_GET(self):

        try:

            parsed = urlparse(self.path)

            params = parse_qs(
                parsed.query
            )

            listing_id = (
                params.get("id", [""])[0]
            )

            if not listing_id.isdigit():

                send_error(
                    self,
                    400,
                    "شناسه آگهی معتبر نیست."
                )

                return

            listing = load_listing(
                listing_id
            )

            if not listing:

                send_error(
                    self,
                    404,
                    "عکس آگهی پیدا نشد."
                )

                return

            file_id = listing.get("photo")

            if not file_id:

                send_error(
                    self,
                    404,
                    "برای این آگهی عکسی ثبت نشده است."
                )

                return

            photo_content, content_type = get_telegram_photo(
                file_id
            )

            self.send_response(200)

            self.send_header(
                "Content-Type",
                content_type
            )

            self.send_header(
                "Content-Length",
                str(len(photo_content))
            )

            self.send_header(
                "Cache-Control",
                "public, max-age=3600"
            )

            self.send_header(
                "Access-Control-Allow-Origin",
                "*"
            )

            self.end_headers()

            self.wfile.write(
                photo_content
            )

        except requests.HTTPError as exc:

            print(
                "PHOTO HTTP ERROR:",
                repr(exc)
            )

            send_error(
                self,
                502,
                "دریافت عکس از سرویس خارجی ناموفق بود."
            )

        except Exception as exc:

            print(
                "PHOTO ERROR:",
                repr(exc)
            )

            send_error(
                self,
                500,
                "نمایش عکس با خطا مواجه شد."
            )

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, OPTIONS"
        )

        self.end_headers()

    def log_message(self, format, *args):

        print(
            "PHOTO LOG:",
            format % args
        )
