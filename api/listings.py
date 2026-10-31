
import base64
import json
import mimetypes
import os
import re
import time
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit, parse_qs

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

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# آدرس عمومی API ربات
PUBLIC_API_URL = (
    "https://zarinmah-seller-bot.vercel.app/api/listings"
)

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/vnd.github+json",
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "X-GitHub-Api-Version": "2022-11-28",
})

MAX_BODY_SIZE = 4 * 1024 * 1024
MAX_FILE_SIZE = 1500 * 1024
MAX_TEXT_LENGTH = 1500
MAX_ATTEMPTS = 4


# =========================================================
# پاسخ HTTP
# =========================================================

def send_json(handler, status, data):

    body = json.dumps(
        data,
        ensure_ascii=False
    ).encode("utf-8")

    handler.send_response(status)

    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8"
    )

    handler.send_header("Cache-Control", "no-store")

    handler.send_header(
        "Access-Control-Allow-Origin",
        "*"
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, OPTIONS"
    )

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type"
    )

    handler.send_header(
        "Content-Length",
        str(len(body))
    )

    handler.end_headers()

    if status != 204:
        handler.wfile.write(body)


def send_image(handler, content, content_type):

    handler.send_response(200)

    handler.send_header(
        "Content-Type",
        content_type
    )

    handler.send_header(
        "Content-Length",
        str(len(content))
    )

    handler.send_header(
        "Cache-Control",
        "public, max-age=3600"
    )

    handler.send_header(
        "Access-Control-Allow-Origin",
        "*"
    )

    handler.end_headers()

    handler.wfile.write(content)


# =========================================================
# GitHub state.json
# =========================================================

def load_state():

    if not GITHUB_TOKEN:
        raise RuntimeError(
            "SELLER_DATA_TOKEN تنظیم نشده است."
        )

    response = SESSION.get(
        GITHUB_API,
        timeout=20
    )

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

    state = json.loads(
        raw.decode("utf-8")
    )

    if not isinstance(state, dict):
        raise ValueError(
            "ساختار state.json معتبر نیست."
        )

    state.setdefault("last_update_id", -1)
    state.setdefault("users", {})
    state.setdefault("listings", [])
    state.setdefault("next_id", 1)

    if not isinstance(state["listings"], list):
        raise ValueError(
            "فهرست آگهی‌ها معتبر نیست."
        )

    ids = []

    for listing in state["listings"]:

        try:
            ids.append(
                int(listing.get("id", 0))
            )

        except (TypeError, ValueError):
            pass

    if ids:

        state["next_id"] = max(
            int(state["next_id"]),
            max(ids) + 1
        )

    return state, item["sha"]


def save_state(state, sha):

    encoded = base64.b64encode(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ).encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": "Register website listing",
        "content": encoded,
    }

    if sha:
        payload["sha"] = sha

    response = SESSION.put(
        GITHUB_API,
        json=payload,
        timeout=20
    )

    response.raise_for_status()

    return response.json()


def append_listing(listing):

    for attempt in range(MAX_ATTEMPTS):

        state, sha = load_state()

        existing = next(
            (
                item for item in state["listings"]
                if str(item.get("id")) == str(listing["id"])
            ),
            None
        )

        if existing:
            return existing

        state["listings"].append(listing)

        state["next_id"] = max(
            int(state.get("next_id", 1)),
            int(listing["id"]) + 1
        )

        try:

            save_state(state, sha)

            return listing

        except requests.HTTPError as exc:

            status = (
                exc.response.status_code
                if exc.response is not None
                else None
            )

            if (
                status in (409, 422)
                and attempt < MAX_ATTEMPTS - 1
            ):

                time.sleep(
                    0.5 * (attempt + 1)
                )

                continue

            raise

    raise RuntimeError(
        "ذخیره آگهی ناموفق بود."
    )


# =========================================================
# ابزارهای عمومی
# =========================================================

def clean_text(value, limit=MAX_TEXT_LENGTH):

    if not isinstance(value, str):
        return ""

    return value.strip()[:limit]


def valid_phone(value):

    digits = re.sub(r"\D", "", value)

    return 8 <= len(digits) <= 15


def escape_html(value):

    value = str(value or "")

    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def telegram_call(method, data=None, files=None):

    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN تنظیم نشده است."
        )

    response = requests.post(
        f"{TG_API}/{method}",
        data=data,
        files=files,
        timeout=60
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):

        raise RuntimeError(
            "درخواست تلگرام ناموفق بود: "
            + str(
                result.get(
                    "description",
                    "خطای نامشخص"
                )
            )
        )

    return result["result"]


# =========================================================
# دریافت عکس امن از تلگرام
# =========================================================

def get_public_photo(listing_id):

    state, _ = load_state()

    listing = next(
        (
            item for item in state.get("listings", [])
            if str(item.get("id")) == str(listing_id)
        ),
        None
    )

    if not listing:
        raise ValueError(
            "آگهی موردنظر پیدا نشد."
        )

    # فقط عکس آگهی‌های منتشرشده و فروخته‌شده عمومی است.
    if listing.get("status") not in (
        "published",
        "sold"
    ):
        raise ValueError(
            "عکس این آگهی در دسترس عمومی نیست."
        )

    file_id = listing.get("photo")

    if not file_id:
        raise ValueError(
            "عکس برای این آگهی ثبت نشده است."
        )

    # دریافت مسیر فایل از تلگرام
    file_info = telegram_call(
        "getFile",
        data={
            "file_id": file_id
        }
    )

    file_path = file_info.get("file_path")

    if not file_path:
        raise RuntimeError(
            "مسیر عکس از تلگرام دریافت نشد."
        )

    # دریافت خود فایل از سرور تلگرام
    file_url = (
        f"https://api.telegram.org/file/"
        f"bot{TELEGRAM_TOKEN}/{file_path}"
    )

    response = requests.get(
        file_url,
        timeout=60
    )

    response.raise_for_status()

    content = response.content

    if not content:
        raise RuntimeError(
            "فایل عکس خالی است."
        )

    content_type = (
        mimetypes.guess_type(file_path)[0]
        or "image/jpeg"
    )

    if not content_type.startswith("image/"):
        content_type = "image/jpeg"

    return content, content_type


# =========================================================
# خواندن multipart/form-data
# =========================================================

def parse_request(handler):

    content_type = handler.headers.get(
        "Content-Type",
        ""
    )

    length_values = (
        handler.headers.get_all("Content-Length") or []
    )

    print(
        "REQUEST CONTENT-LENGTH:",
        repr(length_values)
    )

    print(
        "REQUEST CONTENT-TYPE:",
        repr(content_type)
    )

    print(
        "REQUEST TRANSFER-ENCODING:",
        repr(handler.headers.get("Transfer-Encoding"))
    )

    if not length_values:
        raise ValueError(
            "سرور طول درخواست را دریافت نکرده است."
        )

    parsed_lengths = []

    for value in length_values:

        for part in value.split(","):

            part = part.strip()

            if not part.isdigit():
                raise ValueError(
                    "مقدار طول درخواست در سرور معتبر نیست."
                )

            parsed_lengths.append(
                int(part)
            )

    if (
        not parsed_lengths
        or len(set(parsed_lengths)) != 1
    ):
        raise ValueError(
            "مقادیر طول درخواست با یکدیگر مطابقت ندارند."
        )

    length = parsed_lengths[0]

    if length <= 0:
        raise ValueError(
            "اطلاعات فرم دریافت نشد."
        )

    if length > MAX_BODY_SIZE:
        raise ValueError(
            "حجم کل درخواست بیشتر از ۴ مگابایت است."
        )

    raw = handler.rfile.read(length)

    if len(raw) != length:
        raise ValueError(
            "دریافت اطلاعات فرم ناقص انجام شد."
        )

    if "multipart/form-data" in content_type.lower():

        try:

            message = BytesParser(
                policy=policy.default
            ).parsebytes(
                (
                    f"Content-Type: {content_type}\r\n"
                    "MIME-Version: 1.0\r\n\r\n"
                ).encode("ascii") + raw
            )

        except Exception:

            raise ValueError(
                "ساختار اطلاعات و فایل‌های ارسالی معتبر نیست."
            )

        fields = {}
        uploads = {}

        if not message.is_multipart():
            raise ValueError(
                "ساختار فایل‌های ارسالی معتبر نیست."
            )

        for part in message.iter_parts():

            name = part.get_param(
                "name",
                header="content-disposition"
            )

            if not name:
                continue

            filename = part.get_filename()

            if filename:

                content = (
                    part.get_payload(decode=True) or b""
                )

                uploads[name] = {
                    "filename": os.path.basename(filename),
                    "content_type": (
                        part.get_content_type()
                        or "application/octet-stream"
                    ),
                    "content": content,
                }

            else:

                content = (
                    part.get_payload(decode=True) or b""
                )

                charset = (
                    part.get_content_charset()
                    or "utf-8"
                )

                fields[name] = content.decode(
                    charset,
                    errors="replace"
                ).strip()

        print(
            "REQUEST FIELDS:",
            list(fields.keys())
        )

        print(
            "REQUEST UPLOADS:",
            {
                key: len(value["content"])
                for key, value in uploads.items()
            }
        )

        return fields, uploads

    if "application/json" in content_type.lower():

        data = json.loads(
            raw.decode("utf-8")
        )

        if not isinstance(data, dict):
            raise ValueError(
                "ساختار اطلاعات فرم معتبر نیست."
            )

        return data, {}

    raise ValueError(
        "نوع درخواست پشتیبانی نمی‌شود."
    )


def validate_upload(upload, allowed_types, label):

    if not upload or not upload.get("content"):
        raise ValueError(
            f"ارسال {label} الزامی است."
        )

    if len(upload["content"]) > MAX_FILE_SIZE:
        raise ValueError(
            f"حجم {label} باید حداکثر ۱٫۵ مگابایت باشد."
        )

    if upload["content_type"] not in allowed_types:
        raise ValueError(
            f"فرمت {label} مجاز نیست."
        )

    return upload


# =========================================================
# تبدیل اطلاعات فرم سایت به ساختار ربات
# =========================================================

JEWELRY_MAP = {
    "انگشتر": "ring",
    "گوشواره": "earrings",
    "النگو": "bangle",
    "زنجیر": "chain",
    "گردنبند": "pendant",
    "دستبند": "bracelet",
    "پابند": "anklet",
    "آویز": "pendant",
    "سایر زیورآلات": "other",
}

COIN_MAP = {
    "تمام سکه امامی": (
        "emami",
        "سکه تمام امامی"
    ),
    "تمام سکه بهار آزادی": (
        "bahar_azadi",
        "سکه تمام بهار آزادی"
    ),
    "نیم سکه": (
        "half",
        "نیم‌سکه"
    ),
    "ربع سکه": (
        "quarter",
        "ربع‌سکه"
    ),
    "سکه یک گرمی بانک مرکزی": (
        "gram",
        "سکه یک گرمی بانک مرکزی"
    ),
    "سایر سکه‌ها": (
        "other_coin",
        "سایر سکه‌ها"
    ),
}


def normalize_listing(data, listing_id, photo_file_id):

    name = clean_text(
        data.get("sellerName") or data.get("name"),
        100
    )

    phone = clean_text(
        data.get("sellerPhone") or data.get("phone"),
        30
    )

    category = clean_text(
        data.get("sellerItem") or data.get("product_type"),
        100
    )

    city = clean_text(
        data.get("city") or "ثبت نشده",
        100
    )

    description = clean_text(
        data.get("sellerDescription") or data.get("description"),
        1000
    )

    weight = clean_text(
        data.get("sellerWeight") or data.get("weight"),
        40
    )

    karat = clean_text(
        data.get("sellerKarat") or data.get("karat"),
        50
    )

    method = clean_text(
        data.get("pricingMethod") or data.get("pricing_method"),
        50
    )

    if not all([
        name,
        phone,
        category,
        description
    ]):
        raise ValueError(
            "نام، شماره تماس، دسته‌بندی و توضیحات الزامی هستند."
        )

    if not valid_phone(phone):
        raise ValueError(
            "شماره تماس واردشده معتبر نیست."
        )

    if data.get("invoiceConfirm") not in (
        "on",
        "true",
        "1",
        "yes",
        "بله"
    ):
        raise ValueError(
            "تأیید داشتن فاکتور معتبر الزامی است."
        )

    if category == "jewelry":

        subtype = clean_text(
            data.get("jewelryType"),
            100
        )

        if not subtype:
            raise ValueError(
                "نوع زیورآلات را انتخاب کنید."
            )

        product_type_key = JEWELRY_MAP.get(
            subtype,
            "other"
        )

        product_type = subtype
        title = subtype
        weight_unit = "گرم"

    elif category == "coin":

        subtype = clean_text(
            data.get("coinType"),
            100
        )

        if subtype not in COIN_MAP:
            raise ValueError(
                "نوع سکه را به‌درستی انتخاب کنید."
            )

        product_type_key, product_type = COIN_MAP[subtype]

        title = product_type
        weight_unit = "گرم"

    elif category == "parsian":

        product_type_key = "coin"
        product_type = "سکه پارسیان"
        title = "سکه پارسیان"

        parsian_weight = clean_text(
            data.get("parsianWeight"),
            30
        )

        if (
            not parsian_weight
            or not parsian_weight.isdigit()
        ):
            raise ValueError(
                "وزن سکه پارسیان را برحسب سوت وارد کنید."
            )

        weight = parsian_weight
        weight_unit = "سوت"

    elif category == "melted":

        product_type_key = "melted_gold"
        product_type = "طلای آب‌شده"
        title = "طلای آب‌شده"
        weight_unit = "گرم"

    else:

        raise ValueError(
            "دسته‌بندی انتخاب‌شده معتبر نیست."
        )

    if not weight:
        weight = "ثبت نشده"

    fixed_price = clean_text(
        data.get("fixedPrice"),
        50
    )

    percent = clean_text(
        data.get("pricePercent"),
        30
    )

    amount = clean_text(
        data.get("priceAmount"),
        50
    )

    price_method_map = {
        "fixed": "fixed",
        "percent": "above_board_percent",
        "amount": "above_board",
        "negotiable": "negotiable",
    }

    if method not in price_method_map:
        raise ValueError(
            "روش قیمت‌گذاری را انتخاب کنید."
        )

    price_method = price_method_map[method]

    if method == "fixed":

        if (
            not fixed_price.isdigit()
            or int(fixed_price) <= 0
        ):
            raise ValueError(
                "قیمت ثابت معتبر وارد کنید."
            )

        price_value = fixed_price
        price = fixed_price + " تومان"

    elif method == "percent":

        if not percent:
            raise ValueError(
                "درصد بالاتر از قیمت تابلو را وارد کنید."
            )

        try:

            if float(percent) < 0:
                raise ValueError()

        except ValueError:

            raise ValueError(
                "درصد قیمت‌گذاری معتبر نیست."
            )

        price_value = percent
        price = percent + " درصد بالاتر از تابلو"

    elif method == "amount":

        if not amount.isdigit():
            raise ValueError(
                "مبلغ بالاتر از قیمت تابلو را وارد کنید."
            )

        price_value = amount
        price = amount + " تومان بالاتر از تابلو"

    else:

        price_value = ""
        price = "توافقی"

    return {
        "id": listing_id,
        "source": "website",
        "owner_id": ADMIN_ID,
        "kind": "user",

        "seller_name": name,
        "contact": phone,

        "product_type_key": product_type_key,
        "product_type": product_type,
        "item_type": product_type,

        "coin_type_key": (
            product_type_key
            if category == "coin"
            else ""
        ),

        "coin_type": (
            product_type
            if category == "coin"
            else ""
        ),

        "purity_key": karat,
        "purity": karat,
        "karat": karat,

        "weight": weight,
        "weight_unit": weight_unit,

        "title": title,
        "description": description,

        "price_method": price_method,
        "price_value": price_value,
        "pricing_method": method,
        "price": price,

        "photo": photo_file_id,
        "photo_url": None,

        "city": city,
        "status": "pending",

        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# =========================================================
# ارسال فایل و اطلاع‌رسانی به مدیر
# =========================================================

def send_product_photo(upload):

    return telegram_call(
        "sendPhoto",
        data={
            "chat_id": str(ADMIN_ID),
            "caption": (
                "📷 عکس محصول آگهی جدید سایت زرین ماه"
            ),
        },
        files={
            "photo": (
                upload["filename"],
                upload["content"],
                upload["content_type"],
            )
        },
    )


def send_invoice(upload, listing_id):

    return telegram_call(
        "sendDocument",
        data={
            "chat_id": str(ADMIN_ID),
            "caption": (
                f"🧾 فاکتور فروش آگهی شماره {listing_id}\n"
                "این سند فقط برای بررسی مدیر ارسال شده است."
            ),
        },
        files={
            "document": (
                upload["filename"],
                upload["content"],
                upload["content_type"],
            )
        },
    )


def notify_admin(listing):

    listing_id = listing["id"]

    message = (
        "🆕 <b>آگهی جدید از سایت زرین ماه</b>\n\n"

        f"🆔 شماره آگهی: "
        f"<code>{listing_id}</code>\n"

        f"👤 فروشنده: "
        f"{escape_html(listing['seller_name'])}\n"

        f"📞 تماس: "
        f"{escape_html(listing['contact'])}\n"

        f"📦 نوع کالا: "
        f"{escape_html(listing['product_type'])}\n"

        f"⚖️ وزن: "
        f"{escape_html(listing['weight'])} "
        f"{escape_html(listing['weight_unit'])}\n"

        f"🔎 عیار: "
        f"{escape_html(listing.get('karat') or 'ثبت نشده')}\n"

        f"📍 شهر: "
        f"{escape_html(listing['city'])}\n"

        f"💰 روش قیمت‌گذاری: "
        f"{escape_html(listing['pricing_method'])}\n"

        f"💵 قیمت پیشنهادی: "
        f"{escape_html(listing['price'])}\n\n"

        f"📝 توضیحات:\n"
        f"{escape_html(listing['description'])}\n\n"

        "🧾 فاکتور باید بررسی شود.\n"
        "⚠️ آگهی تا تأیید مدیر منتشر نمی‌شود."
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

    telegram_call(
        "sendMessage",
        data={
            "chat_id": str(ADMIN_ID),
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(
                keyboard,
                ensure_ascii=False
            ),
        },
    )


# =========================================================
# ثبت آگهی
# =========================================================

def create_listing(data, uploads):

    photo = validate_upload(
        uploads.get("sellerPhoto")
        or uploads.get("photo"),

        {
            "image/jpeg",
            "image/png",
            "image/webp"
        },

        "عکس محصول",
    )

    invoice = validate_upload(
        uploads.get("sellerInvoice")
        or uploads.get("invoice"),

        {
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/pdf",
        },

        "فاکتور فروش",
    )

    photo_message = send_product_photo(photo)

    photos = photo_message.get("photo") or []

    if not photos:
        raise RuntimeError(
            "تلگرام file_id عکس محصول را برنگرداند."
        )

    photo_file_id = photos[-1]["file_id"]

    state, _ = load_state()

    listing_id = int(
        state.get("next_id", 1)
    )

    listing = normalize_listing(
        data,
        listing_id,
        photo_file_id
    )

    append_listing(listing)

    invoice_sent = False
    invoice_error = False

    try:

        send_invoice(
            invoice,
            listing_id
        )

        invoice_sent = True

    except Exception as exc:

        print(
            "INVOICE SEND ERROR:",
            repr(exc)
        )

        invoice_error = True

    try:

        notify_admin(listing)

        notified = True

    except Exception as exc:

        print(
            "ADMIN NOTIFICATION ERROR:",
            repr(exc)
        )

        notified = False

    return {
        "id": listing_id,
        "status": "pending",

        "admin_notification_sent": notified,
        "invoice_sent": invoice_sent,
        "invoice_send_error": invoice_error,

        "message": (
            "آگهی ذخیره شد و در انتظار تأیید مدیر است."
            if notified and invoice_sent
            else
            "آگهی ذخیره شد، اما ارسال پیام یا فاکتور به مدیر کامل نشد. "
            "لطفاً تنظیمات ربات و دسترسی مدیر بررسی شود."
        ),
    }


# =========================================================
# خروجی عمومی آگهی‌ها
# =========================================================

def public_listing(item):

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
        "status",
    )

    result = {
        key: item[key]
        for key in allowed
        if key in item
    }

    # حفظ وضعیت واقعی آگهی
    result["status"] = item.get("status", "pending")

    # آدرس عکس از مسیر امن API
    if item.get("photo"):

        result["photo_url"] = (
            f"{PUBLIC_API_URL}?photo={item['id']}"
        )

    else:

        result["photo_url"] = item.get("photo_url")

    return result


# =========================================================
# Vercel handler
# =========================================================

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):

        send_json(
            self,
            204,
            {}
        )

    def do_GET(self):

        try:

            query = parse_qs(
                urlsplit(self.path).query
            )

            # درخواست دریافت عکس
            photo_id = query.get("photo", [None])[0]

            if photo_id is not None:

                if not str(photo_id).isdigit():

                    send_json(
                        self,
                        400,
                        {
                            "ok": False,
                            "error": "شناسه عکس معتبر نیست."
                        }
                    )

                    return

                content, content_type = get_public_photo(
                    photo_id
                )

                send_image(
                    self,
                    content,
                    content_type
                )

                return

            # درخواست فهرست آگهی‌ها
            state, _ = load_state()

            published = [
                public_listing(item)
                for item in state.get("listings", [])
                if item.get("status") in (
                    "published",
                    "sold"
                )
            ]

            send_json(
                self,
                200,
                {
                    "ok": True,
                    "listings": published,
                }
            )

        except ValueError as exc:

            print(
                "GET VALIDATION ERROR:",
                repr(exc)
            )

            send_json(
                self,
                404,
                {
                    "ok": False,
                    "error": str(exc),
                }
            )

        except Exception as exc:

            print(
                "GET ERROR:",
                repr(exc)
            )

            send_json(
                self,
                500,
                {
                    "ok": False,
                    "error": "خواندن آگهی‌ها یا دریافت عکس ناموفق بود.",
                }
            )

    def do_POST(self):

        try:

            data, uploads = parse_request(self)

            result = create_listing(
                data,
                uploads
            )

            send_json(
                self,
                201 if result["admin_notification_sent"] else 202,
                {
                    "ok": True,
                    **result
                }
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError
        ) as exc:

            print(
                "REQUEST DECODE ERROR:",
                repr(exc)
            )

            send_json(
                self,
                400,
                {
                    "ok": False,
                    "error": "اطلاعات ارسالی معتبر نیست.",
                }
            )

        except ValueError as exc:

            print(
                "VALIDATION ERROR:",
                repr(exc)
            )

            send_json(
                self,
                400,
                {
                    "ok": False,
                    "error": str(exc),
                }
            )

        except Exception as exc:

            print(
                "POST ERROR:",
                repr(exc)
            )

            send_json(
                self,
                500,
                {
                    "ok": False,
                    "error": (
                        "ثبت آگهی با خطا مواجه شد. "
                        "توکن‌های سرور، دسترسی ربات و مخزن داده را بررسی کنید."
                    ),
                }
            )

    def log_message(self, format, *args):

        print(
            "HTTP LOG:",
            format % args
        )
