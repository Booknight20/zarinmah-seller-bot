
import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler

import requests


# =========================================================
# تنظیمات
# =========================================================

DATA_REPO = "Booknight20/zarinmah-seller-data"
STATE_PATH = "state.json"

GITHUB_TOKEN = os.environ.get("SELLER_DATA_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

ADMIN_ID = 8816884849

GITHUB_API = (
    f"https://api.github.com/repos/"
    f"{DATA_REPO}/contents/{STATE_PATH}"
)

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
})

MAX_BODY_SIZE = 20_000
MAX_TEXT_LENGTH = 1500


# =========================================================
# پاسخ HTTP و CORS
# =========================================================

def send_json(handler, status, data):
    body = json.dumps(
        data, ensure_ascii=False
    ).encode("utf-8")

    handler.send_response(status)
    handler.send_header(
        "Content-Type", "application/json; charset=utf-8"
    )
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header(
        "Access-Control-Allow-Headers", "Content-Type"
    )
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


# =========================================================
# خواندن و ذخیره state.json
# =========================================================

def load_state():
    response = SESSION.get(GITHUB_API, timeout=15)

    if response.status_code == 404:
        return {
            "last_update_id": -1,
            "users": {},
            "listings": [],
            "next_id": 1,
        }, None

    response.raise_for_status()
    item = response.json()

    raw = base64.b64decode(item["content"])
    state = json.loads(raw.decode("utf-8"))

    if not isinstance(state, dict):
        raise ValueError("ساختار state.json معتبر نیست.")

    state.setdefault("last_update_id", -1)
    state.setdefault("users", {})
    state.setdefault("listings", [])
    state.setdefault("next_id", 1)

    if not isinstance(state["listings"], list):
        raise ValueError("فهرست آگهی‌ها معتبر نیست.")

    ids = []
    for listing in state["listings"]:
        try:
            ids.append(int(listing.get("id", 0)))
        except (TypeError, ValueError):
            continue

    if ids:
        state["next_id"] = max(
            int(state["next_id"]), max(ids) + 1
        )

    return state, item["sha"]


def save_state(state, sha):
    encoded = base64.b64encode(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": "Update listings through website API",
        "content": encoded,
    }

    if sha:
        payload["sha"] = sha

    response = SESSION.put(
        GITHUB_API,
        json=payload,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


# =========================================================
# ابزارهای اعتبارسنجی
# =========================================================

def clean_text(value, limit=MAX_TEXT_LENGTH):
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def valid_phone(value):
    digits = re.sub(r"\D", "", value)
    return 8 <= len(digits) <= 15


def public_listing(item):
    # فقط فیلدهای مجاز عمومی؛ اطلاعات مدیریتی و خصوصی حذف می‌شوند.
    allowed = (
        "id",
        "product_type",
        "item_type",
        "weight",
        "weight_unit",
        "karat",
        "city",
        "description",
        "pricing_method",
        "price",
        "created_at",
        "photo_url",
        "status",
    )

    result = {
        key: item[key]
        for key in allowed
        if key in item
    }

    result["status"] = "published"
    return result


# =========================================================
# اطلاع‌رسانی به مدیر تلگرام
# =========================================================

def notify_admin(listing):
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN تنظیم نشده است.")

    product_type = listing.get("product_type", "ثبت نشده")
    weight = listing.get("weight", "ثبت نشده")
    unit = listing.get("weight_unit", "گرم")
    city = listing.get("city", "ثبت نشده")
    description = listing.get("description", "بدون توضیح")
    price = listing.get("price") or "توافقی"
    method = listing.get("pricing_method", "ثبت نشده")
    listing_id = listing["id"]

    message = (
        "🆕 <b>آگهی جدید از سایت زرین ماه</b>\n\n"
        f"🆔 شماره آگهی: <code>{listing_id}</code>\n"
        f"📦 نوع کالا: {product_type}\n"
        f"⚖️ وزن: {weight} {unit}\n"
        f"📍 شهر: {city}\n"
        f"💰 روش قیمت‌گذاری: {method}\n"
        f"💵 قیمت پیشنهادی: {price}\n\n"
        f"📝 توضیحات:\n{description}\n\n"
        "⚠️ این آگهی تا زمان تأیید مدیر منتشر نمی‌شود."
    )

    keyboard = {
        "inline_keyboard": [[
            {
                "text": "✅ تأیید و انتشار",
                "callback_data": f"approve:{listing_id}",
            },
            {
                "text": "❌ رد آگهی",
                "callback_data": f"reject:{listing_id}",
            },
        ]]
    }

    response = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": ADMIN_ID,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        },
        timeout=15,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("ارسال پیام مدیر در تلگرام ناموفق بود.")

    return True


# =========================================================
# ساخت آگهی سایت
# =========================================================

def create_listing(data):
    name = clean_text(data.get("name"), 100)
    phone = clean_text(data.get("phone"), 30)
    product_type = clean_text(data.get("product_type"), 100)
    weight = clean_text(data.get("weight"), 40)
    weight_unit = clean_text(data.get("weight_unit"), 20) or "گرم"
    city = clean_text(data.get("city"), 100)
    description = clean_text(data.get("description"), 1000)
    pricing_method = clean_text(data.get("pricing_method"), 100)
    price = clean_text(data.get("price"), 50)
    photo_url = clean_text(data.get("photo_url"), 500)

    if not all([name, phone, product_type, city, description]):
        raise ValueError("لطفاً نام، شماره تماس، نوع کالا، شهر و توضیحات را تکمیل کنید.")

    if not valid_phone(phone):
        raise ValueError("شماره تماس واردشده معتبر نیست.")

    if not weight:
        weight = "ثبت نشده"

    if photo_url and not photo_url.startswith("https://"):
        raise ValueError("نشانی تصویر باید با https:// شروع شود.")

    # فیلد contact برای سازگاری احتمالی با ساختار ربات نگه داشته می‌شود.
    # این فیلد هرگز در پاسخ عمومی API نمایش داده نمی‌شود.
    for attempt in range(4):
        state, sha = load_state()

        listing_id = int(state.get("next_id", 1))

        listing = {
            "id": listing_id,
            "source": "website",
            "owner_id": None,
            "seller_name": name,
            "contact": phone,
            "product_type": product_type,
            "item_type": product_type,
            "weight": weight,
            "weight_unit": weight_unit,
            "city": city,
            "description": description,
            "pricing_method": pricing_method or "توافقی",
            "price": price or None,
            "photo_url": photo_url or None,
            "status": "pending",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        state["listings"].append(listing)
        state["next_id"] = listing_id + 1

        try:
            save_state(state, sha)
            break
        except requests.HTTPError as exc:
            status = (
                exc.response.status_code
                if exc.response is not None else None
            )
            if status in (409, 422) and attempt < 3:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise
    else:
        raise RuntimeError("ذخیره آگهی پس از چند تلاش ناموفق بود.")

    # ابتدا آگهی ذخیره می‌شود؛ خطای پیام تلگرام نباید آن را حذف کند.
    try:
        notify_admin(listing)
    except Exception:
        # وضعیت pending باقی می‌ماند؛ برای جلوگیری از انتشار خودکار.
        # خطا در پاسخ گزارش می‌شود تا ارسال مدیر بررسی شود.
        return {
            "id": listing_id,
            "status": "pending",
            "admin_notification_sent": False,
            "message": (
                "آگهی ذخیره شد اما اطلاع‌رسانی مدیر ناموفق بود. "
                "لازم است ارسال پیام بررسی شود."
            ),
        }

    return {
        "id": listing_id,
        "status": "pending",
        "admin_notification_sent": True,
        "message": "آگهی ثبت شد و برای تأیید مدیر ارسال شد.",
    }


# =========================================================
# Vercel Python handler
# =========================================================

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        send_json(self, 204, {})

    def do_GET(self):
        try:
            state, _ = load_state()

            published = [
                public_listing(item)
                for item in state.get("listings", [])
                if item.get("status") == "published"
            ]

            send_json(self, 200, {
                "ok": True,
                "listings": published,
            })

        except Exception:
            send_json(self, 500, {
                "ok": False,
                "error": "خواندن آگهی‌ها ناموفق بود.",
            })

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))

            if length <= 0 or length > MAX_BODY_SIZE:
                send_json(self, 413, {
                    "ok": False,
                    "error": "حجم درخواست نامعتبر است.",
                })
                return

            raw = self.rfile.read(length)
            data = json.loads(raw.decode("utf-8"))

            if not isinstance(data, dict):
                send_json(self, 400, {
                    "ok": False,
                    "error": "ساختار درخواست نامعتبر است.",
                })
                return

            result = create_listing(data)

            status_code = (
                201 if result["admin_notification_sent"] else 202
            )

            send_json(self, status_code, {
                "ok": True,
                **result,
            })

        except (UnicodeDecodeError, json.JSONDecodeError):
            send_json(self, 400, {
                "ok": False,
                "error": "JSON درخواست معتبر نیست.",
            })

        except ValueError as exc:
            send_json(self, 400, {
                "ok": False,
                "error": str(exc),
            })

        except Exception:
            send_json(self, 500, {
                "ok": False,
                "error": (
                    "ثبت آگهی با خطا مواجه شد. "
                    "تنظیمات سرور و مخزن داده بررسی شود."
                ),
            })

    def log_message(self, format, *args):
        # از ثبت اطلاعات درخواست در لاگ پیش‌فرض جلوگیری می‌کنیم.
        return

