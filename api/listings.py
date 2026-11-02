import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
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

# آدرس API ربات فروشنده
API_BASE_URL = "https://zarinmah-seller-bot.vercel.app"

GITHUB_API = (
    f"https://api.github.com/repos/"
    f"{DATA_REPO}/contents/{STATE_PATH}"
)

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


# Supabase فقط در سمت سرور استفاده می‌شود؛ کلید هرگز به مرورگر ارسال نمی‌شود.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_TIMEOUT = 25

IRAN_PROVINCES = {
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز",
    "ایلام", "بوشهر", "تهران", "چهارمحال و بختیاری", "خراسان جنوبی",
    "خراسان رضوی", "خراسان شمالی", "خوزستان", "زنجان", "سمنان",
    "سیستان و بلوچستان", "فارس", "قزوین", "قم", "کردستان", "کرمان",
    "کرمانشاه", "کهگیلویه و بویراحمد", "گلستان", "گیلان", "لرستان",
    "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد",
}

def supabase_headers(extra=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("تنظیمات اتصال امن Supabase در سرور کامل نیست.")
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    if extra:
        headers.update(extra)
    return headers

def supabase_request(method, path, *, params=None, json_body=None, content=None,
                     content_type=None, prefer=None):
    headers = supabase_headers()
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    if prefer:
        headers["Prefer"] = prefer
    response = requests.request(
        method,
        f"{SUPABASE_URL}{path}",
        headers=headers,
        params=params,
        json=json_body,
        data=content,
        timeout=SUPABASE_TIMEOUT,
    )
    if not response.ok:
        detail = ""
        try:
            payload = response.json()
            detail = payload.get("message") or payload.get("hint") or payload.get("details") or ""
        except Exception:
            detail = response.text[:300]
        raise RuntimeError(f"خطای Supabase ({response.status_code}): {detail}")
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None

def upload_supabase_file(bucket, upload, object_path):
    content_type = upload["content_type"]
    supabase_request(
        "POST",
        f"/storage/v1/object/{bucket}/{object_path}",
        content=upload["content"],
        content_type=content_type,
        prefer=None,
    )
    if bucket == "listing-images":
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{object_path}"
    return object_path

def delete_supabase_file(bucket, object_path):
    if not object_path:
        return
    try:
        supabase_request(
            "DELETE",
            f"/storage/v1/object/{bucket}",
            json_body={"prefixes": [object_path]},
        )
    except Exception as exc:
        print("SUPABASE STORAGE CLEANUP ERROR:", repr(exc))

def supabase_get_listing(listing_id):
    rows = supabase_request(
        "GET",
        "/rest/v1/listings",
        params={"id": f"eq.{int(listing_id)}", "select": "*", "limit": "1"},
    )
    return rows[0] if rows else None

def supabase_insert_listing(row):
    rows = supabase_request(
        "POST",
        "/rest/v1/listings",
        json_body=row,
        prefer="return=representation",
    )
    if not rows:
        raise RuntimeError("Supabase شناسه آگهی را برنگرداند.")
    return rows[0]

def supabase_insert_purchase(row):
    rows = supabase_request(
        "POST",
        "/rest/v1/purchase_requests",
        json_body=row,
        prefer="return=representation",
    )
    if not rows:
        raise RuntimeError("Supabase درخواست خرید را ذخیره نکرد.")
    return rows[0]

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

    handler.send_header(
        "Cache-Control",
        "no-store"
    )

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

    raw = base64.b64decode(
        item["content"]
    )

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
    """
    تلاش مجدد برای کاهش خطای SHA در GitHub.
    """

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

    digits = re.sub(
        r"\D",
        "",
        value
    )

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


def normalize_listing(data, listing_id, photo_url, invoice_path):
    name = clean_text(data.get("sellerName") or data.get("name"), 100)
    phone = clean_text(data.get("sellerPhone") or data.get("phone"), 30)
    category = clean_text(data.get("sellerItem") or data.get("product_type"), 100)
    province = clean_text(data.get("province"), 100)
    city = clean_text(data.get("city"), 100)
    description = clean_text(data.get("sellerDescription") or data.get("description"), 1000)
    weight_raw = clean_text(data.get("sellerWeight") or data.get("weight"), 40)
    karat = clean_text(data.get("sellerKarat") or data.get("karat"), 50)
    method = clean_text(data.get("pricingMethod") or data.get("pricing_method"), 50)

    if not all([name, phone, category, description, province, city]):
        raise ValueError("نام، شماره تماس، دسته‌بندی، توضیحات، استان و شهر الزامی هستند.")
    if not valid_phone(phone):
        raise ValueError("شماره تماس واردشده معتبر نیست.")
    if province not in IRAN_PROVINCES:
        raise ValueError("استان انتخاب‌شده معتبر نیست.")
    if data.get("invoiceConfirm") not in ("on", "true", "1", "yes", "بله"):
        raise ValueError("تأیید داشتن فاکتور معتبر الزامی است.")

    if category == "jewelry":
        subtype = clean_text(data.get("jewelryType"), 100)
        if subtype not in JEWELRY_MAP:
            raise ValueError("نوع زیورآلات را انتخاب کنید.")
        product_type = subtype
        title = subtype
        product_type_key = JEWELRY_MAP[subtype]
        weight_unit = "gram"
    elif category == "coin":
        subtype = clean_text(data.get("coinType"), 100)
        if subtype not in COIN_MAP:
            raise ValueError("نوع سکه را به‌درستی انتخاب کنید.")
        product_type_key, product_type = COIN_MAP[subtype]
        title = product_type
        weight_unit = "gram"
    elif category == "parsian":
        product_type_key = "coin"
        product_type = "سکه پارسیان"
        title = "سکه پارسیان"
        parsian_weight = clean_text(data.get("parsianWeight"), 30)
        if not parsian_weight.isdigit() or int(parsian_weight) <= 0:
            raise ValueError("وزن سکه پارسیان را برحسب سوت وارد کنید.")
        weight_raw = parsian_weight
        weight_unit = "soot"
    elif category == "melted":
        product_type_key = "melted_gold"
        product_type = "طلای آب‌شده"
        title = product_type
        weight_unit = "gram"
    else:
        raise ValueError("دسته‌بندی انتخاب‌شده معتبر نیست.")

    weight = None
    if weight_raw and weight_raw != "ثبت نشده":
        try:
            weight = float(weight_raw.replace(",", "."))
            if weight <= 0:
                raise ValueError()
        except ValueError:
            raise ValueError("وزن واردشده معتبر نیست.")

    fixed_price = clean_text(data.get("fixedPrice"), 50)
    percent = clean_text(data.get("pricePercent"), 30)
    amount = clean_text(data.get("priceAmount"), 50)

    if method not in {"fixed", "percent", "amount", "negotiable"}:
        raise ValueError("روش قیمت‌گذاری را انتخاب کنید.")

    price = None
    pricing_value = None
    if method == "fixed":
        if not fixed_price.isdigit() or int(fixed_price) <= 0:
            raise ValueError("قیمت ثابت معتبر وارد کنید.")
        price = float(fixed_price)
        price_label = f"{int(fixed_price):,} تومان"
    elif method == "percent":
        try:
            pricing_value = float(percent)
            if pricing_value < 0 or pricing_value > 100:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValueError("درصد قیمت‌گذاری باید بین صفر تا ۱۰۰ باشد.")
        price_label = f"{pricing_value:g} درصد بالاتر از تابلو"
    elif method == "amount":
        if not amount.isdigit() or int(amount) <= 0:
            raise ValueError("مبلغ بالاتر از قیمت تابلو معتبر نیست.")
        pricing_value = float(amount)
        price_label = f"{int(amount):,} تومان بالاتر از تابلو"
    else:
        price_label = "توافقی"

    return {
        "id": listing_id,
        "source": "website",
        "seller_name": name,
        "seller_mobile": phone,
        "title": title,
        "product_type": product_type,
        "weight": weight,
        "weight_unit": weight_unit,
        "purity": karat or None,
        "pricing_method": method,
        "pricing_value": pricing_value,
        "price": price,
        "description": description,
        "image_url": photo_url,
        "invoice_path": invoice_path,
        "province": province,
        "city": city,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "_title": title,
        "_price_label": price_label,
        "_product_type_key": product_type_key,
        "_seller_name": name,
        "_seller_mobile": phone,
        "_weight_label": weight_raw or "ثبت نشده",
        "_weight_unit_label": "سوت" if weight_unit == "soot" else "گرم",
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
        f"🆔 شماره آگهی: <code>{listing_id}</code>\n"
        f"👤 فروشنده: {escape_html(listing['_seller_name'])}\n"
        f"📞 تماس: <code>{escape_html(listing['_seller_mobile'])}</code>\n"
        f"📦 نوع کالا: {escape_html(listing['product_type'])}\n"
        f"⚖️ وزن: {escape_html(listing['_weight_label'])} {listing['_weight_unit_label']}\n"
        f"🔎 عیار: {escape_html(listing.get('purity') or 'ثبت نشده')}\n"
        f"📍 استان و شهر: {escape_html(listing['province'])}، {escape_html(listing['city'])}\n"
        f"💰 روش قیمت‌گذاری: {escape_html(listing['pricing_method'])}\n"
        f"💵 قیمت پیشنهادی: {escape_html(listing['_price_label'])}\n\n"
        f"📝 توضیحات:\n{escape_html(listing['description'])}\n\n"
        "🧾 فاکتور در فضای خصوصی ذخیره شده و نسخه‌ای نیز برای بررسی مدیر ارسال می‌شود.\n"
        "⚠️ آگهی تا تأیید مدیر منتشر نمی‌شود."
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ تأیید و انتشار", "callback_data": f"approve_web:{listing_id}"},
        {"text": "❌ رد آگهی", "callback_data": f"reject_web:{listing_id}"},
    ]]}
    telegram_call("sendMessage", data={
        "chat_id": str(ADMIN_ID),
        "text": message,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard, ensure_ascii=False),
    })


# =========================================================
# ثبت آگهی
# =========================================================

def create_listing(data, uploads):
    photo = validate_upload(
        uploads.get("sellerPhoto") or uploads.get("photo"),
        {"image/jpeg", "image/png", "image/webp"},
        "عکس محصول",
    )
    invoice = validate_upload(
        uploads.get("sellerInvoice") or uploads.get("invoice"),
        {"image/jpeg", "image/png", "application/pdf"},
        "فاکتور فروش",
    )

    # امضای فایل‌ها بررسی می‌شود تا صرفاً به Content-Type مرورگر اعتماد نکنیم.
    photo_bytes = photo["content"]
    image_signatures = (
        photo_bytes.startswith(b"\xff\xd8\xff"),
        photo_bytes.startswith(b"\x89PNG\r\n\x1a\n"),
        photo_bytes.startswith(b"RIFF") and photo_bytes[8:12] == b"WEBP",
    )
    if not any(image_signatures):
        raise ValueError("محتوای عکس معتبر نیست.")

    invoice_bytes = invoice["content"]
    invoice_is_pdf = invoice_bytes.startswith(b"%PDF-")
    invoice_is_jpeg = invoice_bytes.startswith(b"\xff\xd8\xff")
    invoice_is_png = invoice_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    if not (invoice_is_pdf or invoice_is_jpeg or invoice_is_png):
        raise ValueError("محتوای فاکتور معتبر نیست.")

    # درج موقت برای دریافت ID؛ در صورت شکست، فایل‌های آپلودشده پاک می‌شوند.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe_photo_ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[photo["content_type"]]
    safe_invoice_ext = {
        "application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png"
    }.get(invoice["content_type"], "bin")
    object_token = re.sub(r"[^a-zA-Z0-9_-]", "", timestamp)
    photo_path = f"website/{object_token}.{safe_photo_ext}"
    invoice_path = f"website/{object_token}.{safe_invoice_ext}"

    uploaded_photo = False
    uploaded_invoice = False
    try:
        photo_url = upload_supabase_file("listing-images", photo, photo_path)
        uploaded_photo = True
        saved_invoice_path = upload_supabase_file("listing-invoices", invoice, invoice_path)
        uploaded_invoice = True

        normalized = normalize_listing(data, None, photo_url, saved_invoice_path)

        # پیش از درج رکورد، ارسال عکس به تلگرام را بررسی می‌کنیم.
        photo_message = send_product_photo(photo)
        photos = photo_message.get("photo") or []
        if not photos:
            raise RuntimeError("تلگرام file_id عکس محصول را برنگرداند.")

        db_row = {key: value for key, value in normalized.items()
                  if not key.startswith("_") and key != "id"}
        inserted = supabase_insert_listing(db_row)
        listing_id = int(inserted["id"])

        invoice_sent = False
        try:
            send_invoice(invoice, listing_id)
            invoice_sent = True
        except Exception as exc:
            print("INVOICE SEND ERROR:", repr(exc))

        normalized["id"] = listing_id
        notified = False
        try:
            notify_admin(normalized)
            notified = True
        except Exception as exc:
            print("ADMIN NOTIFICATION ERROR:", repr(exc))

        return {
            "id": listing_id,
            "status": "pending",
            "admin_notification_sent": notified,
            "invoice_sent": invoice_sent,
            "invoice_send_error": not invoice_sent,
            "message": (
                "آگهی در پایگاه داده ذخیره شد و در انتظار تأیید مدیر است."
                if notified and invoice_sent else
                "آگهی ذخیره شد، اما ارسال پیام یا فاکتور به مدیر کامل نشد. لطفاً با پشتیبانی تماس بگیرید و آگهی را دوباره ثبت نکنید."
            ),
        }
    except Exception:
        if uploaded_invoice:
            delete_supabase_file("listing-invoices", invoice_path)
        if uploaded_photo:
            delete_supabase_file("listing-images", photo_path)
        raise


# =========================================================
# ثبت درخواست خرید از سایت
# =========================================================

def normalize_phone(value):
    value = str(value or '').strip()
    return re.sub(r"\D", "", value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"
    )))


def save_purchase_request(purchase_request):
    listing = supabase_get_listing(purchase_request["listing_id"])
    if not listing or listing.get("status") != "published" or listing.get("deleted_at"):
        raise ValueError("این آگهی دیگر برای خرید در دسترس نیست.")

    buyer_phone = normalize_phone(purchase_request["buyer_phone"])
    seller_phone = normalize_phone(listing.get("seller_mobile", ""))
    if buyer_phone and seller_phone and buyer_phone == seller_phone:
        raise ValueError("شماره تماس خریدار و فروشنده نمی‌تواند یکسان باشد.")

    # جلوگیری از ثبت درخواست تکراری فعال برای یک آگهی و شماره تماس.
    existing = supabase_request(
        "GET",
        "/rest/v1/purchase_requests",
        params={
            "listing_id": f"eq.{int(purchase_request['listing_id'])}",
            "buyer_mobile": f"eq.{purchase_request['buyer_phone']}",
            "status": "in.(pending,forwarded)",
            "select": "id",
            "limit": "1",
        },
    )
    if existing:
        raise ValueError("درخواست قبلی شما برای این آگهی ثبت شده است.")

    row = {
        "listing_id": int(purchase_request["listing_id"]),
        "buyer_user_id": None,
        "buyer_telegram_id": None,
        "buyer_name": purchase_request["buyer_name"],
        "buyer_mobile": purchase_request["buyer_phone"],
        "message": purchase_request.get("message") or "درخواست از سایت زرین ماه",
        "status": "pending",
    }
    record = supabase_insert_purchase(row)
    return record, listing


def notify_purchase_admin(purchase_request, listing):
    request_id = purchase_request["id"]
    message = (
        "🛒 <b>درخواست خرید جدید از سایت زرین ماه</b>\n\n"
        f"🆔 شماره درخواست: <code>{request_id}</code>\n"
        f"📦 شماره آگهی: <code>{listing.get('id')}</code>\n"
        f"💎 کالا: {escape_html(listing.get('title') or listing.get('product_type') or '')}\n"
        f"💰 قیمت: {escape_html(listing.get('price') or 'توافقی')}\n\n"
        f"👤 نام خریدار: {escape_html(purchase_request.get('buyer_name'))}\n"
        f"📞 شماره خریدار: <code>{escape_html(purchase_request.get('buyer_mobile'))}</code>\n\n"
        "🔒 اطلاعات فروشنده پس از بررسی درخواست از دیتابیس دریافت می‌شود."
    )
    keyboard = {"inline_keyboard": [[{
        "text": "📨 بررسی و انتقال درخواست",
        "callback_data": f"forward_web:{request_id}",
    }]]}
    return telegram_call("sendMessage", data={
        "chat_id": str(ADMIN_ID),
        "text": message,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard, ensure_ascii=False),
    })


# =========================================================
# خروجی عمومی آگهی‌ها
# =========================================================

def public_listing(item):
    # فقط ستون‌های عمومی برگردانده می‌شوند؛ شماره فروشنده و مسیر فاکتور محرمانه‌اند.
    return {
        "id": item.get("id"),
        "product_type": item.get("product_type"),
        "item_type": item.get("product_type"),
        "weight": item.get("weight"),
        "weight_unit": "سوت" if item.get("weight_unit") == "soot" else "گرم",
        "karat": item.get("purity"),
        "purity": item.get("purity"),
        "province": item.get("province"),
        "city": item.get("city"),
        "description": item.get("description"),
        "pricing_method": item.get("pricing_method"),
        "pricing_value": item.get("pricing_value"),
        "price": item.get("price"),
        "photo_url": item.get("image_url"),
        "created_at": item.get("created_at"),
        "status": item.get("status"),
    }


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
            rows = supabase_request(
                "POST",
                "/rest/v1/rpc/get_public_listings",
                json_body={},
            ) or []
            published = [
                public_listing(item)
                for item in rows
                if item.get("status") in {"published", "sold"}
            ]
            send_json(self, 200, {"ok": True, "listings": published})
        except Exception as exc:
            print("GET SUPABASE ERROR:", repr(exc))
            send_json(self, 500, {
                "ok": False,
                "error": "خواندن آگهی‌ها ناموفق بود. تنظیمات Supabase و تابع get_public_listings را بررسی کنید."
            })

    def do_POST(self):
        try:
            data, uploads = parse_request(self)

            if not uploads and isinstance(data, dict) and data.get("action") == "purchase":
                try:
                    listing_id = int(data.get("listing_id", 0))
                except (TypeError, ValueError):
                    raise ValueError("شناسه آگهی معتبر نیست.")

                buyer_name = clean_text(data.get("buyer_name"), 100)
                buyer_phone = clean_text(data.get("buyer_phone"), 30)
                if not buyer_name or not buyer_phone:
                    raise ValueError("نام و شماره تماس برای ثبت درخواست الزامی است.")
                if not valid_phone(buyer_phone):
                    raise ValueError("شماره تماس واردشده معتبر نیست.")

                purchase_request, listing = save_purchase_request({
                    "source": "website",
                    "listing_id": listing_id,
                    "buyer_name": buyer_name,
                    "buyer_phone": buyer_phone,
                })
                notified = True
                try:
                    notify_purchase_admin(purchase_request, listing)
                except Exception as exc:
                    print("PURCHASE ADMIN NOTIFICATION ERROR:", repr(exc))
                    notified = False

                send_json(self, 201 if notified else 202, {
                    "ok": True,
                    "request_id": purchase_request["id"],
                    "admin_notification_sent": notified,
                    "message": (
                        "درخواست خرید برای پشتیبانی زرین ماه ارسال شد."
                        if notified else
                        "درخواست ثبت شد، اما اطلاع‌رسانی به مدیر کامل نشد. لطفاً با پشتیبانی تماس بگیرید و دوباره ثبت نکنید."
                    )
                })
                return

            result = create_listing(data, uploads)
            send_json(self, 201 if result["admin_notification_sent"] else 202, {
                "ok": True,
                **result,
            })

        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            print("REQUEST DECODE ERROR:", repr(exc))
            send_json(self, 400, {"ok": False, "error": "اطلاعات ارسالی معتبر نیست."})

        except ValueError as exc:
            print("VALIDATION ERROR:", repr(exc))
            send_json(self, 400, {"ok": False, "error": str(exc)})

        except Exception as exc:
            print("POST ERROR:", repr(exc))
            send_json(self, 500, {
                "ok": False,
                "error": "عملیات با خطا مواجه شد. تنظیمات Supabase، دسترسی ربات و گزارش‌های Vercel را بررسی کنید."
            })

    def log_message(self, format, *args):

        print(
            "HTTP LOG:",
            format % args
        )
