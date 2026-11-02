import base64
import hashlib
import html
import hmac
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import quote

import requests

# لاگ کامل خطاها در Runtime Logs پروژه Vercel
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("zarinmah.webhook")

# ZarinMah Telegram webhook — Supabase is the source of truth for listings/orders.
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
GITHUB_TOKEN = os.environ.get("SELLER_DATA_TOKEN", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
DATA_REPO = "Booknight20/zarinmah-seller-data"
STATE_PATH = "state.json"
CHANNEL = "@ZarinMahGold"
GROUP = os.environ.get("TELEGRAM_GROUP", "@ZarinMahCommunity").strip()
GROUP_THREAD_ID = int(os.environ.get("TELEGRAM_GROUP_THREAD_ID", "17"))
ADMIN_ID = 8816884849
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else ""
GH_API = f"https://api.github.com/repos/{DATA_REPO}/contents/{STATE_PATH}"
MAX_UPDATE_SIZE = 1_000_000

PROVINCES = [
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز",
    "ایلام", "بوشهر", "تهران", "چهارمحال و بختیاری", "خراسان جنوبی",
    "خراسان رضوی", "خراسان شمالی", "خوزستان", "زنجان", "سمنان",
    "سیستان و بلوچستان", "فارس", "قزوین", "قم", "کردستان", "کرمان",
    "کرمانشاه", "کهگیلویه و بویراحمد", "گلستان", "گیلان", "لرستان",
    "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد",
]
JEWELRY = {
    "انگشتر": "انگشتر طلا", "گوشواره": "گوشواره طلا", "النگو": "النگو طلا",
    "زنجیر": "زنجیر طلا", "گردنبند": "گردنبند طلا", "دستبند": "دستبند طلا",
    "پابند": "پابند طلا", "آویز": "آویز طلا", "سایر زیورآلات": "زیورآلات طلا",
}
COINS = {
    "تمام سکه امامی": "سکه تمام امامی", "تمام سکه بهار آزادی": "سکه تمام بهار آزادی",
    "نیم سکه": "نیم‌سکه", "ربع سکه": "ربع‌سکه",
    "سکه یک گرمی بانک مرکزی": "سکه یک گرمی بانک مرکزی", "سایر سکه‌ها": "سایر سکه‌ها",
}

# فهرست ۱۴۸۱ شهر واقعی ایران؛ داده‌ها بر اساس تقسیمات کشوری تا پایان ۱۴۰۴.
# در صورت در دسترس نبودن منبع، امکان ورود دستی شهر نیز حفظ شده است.
CITY_DATA_URL = "https://raw.githubusercontent.com/sajaddp/list-of-cities-in-Iran/main/dist/json/cities-filtered.json"
PROVINCE_ID_BY_NAME = {
    "مرکزی": 100, "گیلان": 101, "مازندران": 102, "آذربایجان شرقی": 103,
    "آذربایجان غربی": 104, "کرمانشاه": 105, "خوزستان": 106, "فارس": 107,
    "کرمان": 108, "خراسان رضوی": 109, "اصفهان": 110, "سیستان و بلوچستان": 111,
    "کردستان": 112, "همدان": 113, "چهارمحال و بختیاری": 114, "لرستان": 115,
    "ایلام": 116, "کهگیلویه و بویراحمد": 117, "بوشهر": 118, "زنجان": 119,
    "سمنان": 120, "یزد": 121, "هرمزگان": 122, "تهران": 123, "اردبیل": 124,
    "قم": 125, "قزوین": 126, "گلستان": 127, "خراسان شمالی": 128,
    "خراسان جنوبی": 129, "البرز": 130,
}
CITY_PAGE_SIZE = 20
_CITY_DATA_CACHE = None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def fa_to_en(value):
    return str(value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬", "01234567890123456789.."))


def esc(value):
    return html.escape(str(value or ""), quote=True)


def clean(value, limit=1500):
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e\x0f-\x1f]", "", str(value or ""))
    return value.strip()[:limit]


def sb_headers(extra=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("تنظیمات Supabase در Vercel کامل نیست.")
    headers = {"apikey": SUPABASE_SERVICE_ROLE_KEY, "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}"}
    if extra:
        headers.update(extra)
    return headers


def sb(method, path, *, params=None, payload=None, raw=None, content_type=None, prefer=None):
    headers = sb_headers()
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    if prefer:
        headers["Prefer"] = prefer
    response = requests.request(method, SUPABASE_URL + path, headers=headers, params=params,
                                json=payload, data=raw, timeout=40)
    if not response.ok:
        try:
            body = response.json()
            detail = body.get("message") or body.get("details") or body.get("hint") or str(body)
        except Exception:
            detail = response.text[:300]
        raise RuntimeError(f"Supabase {response.status_code}: {detail}")
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None


def sb_get_listing(listing_id):
    rows = sb("GET", "/rest/v1/listings", params={"id": f"eq.{int(listing_id)}", "select": "*", "limit": "1"}) or []
    return rows[0] if rows else None


def sb_update_listing(listing_id, changes):
    changes["updated_at"] = now_iso()
    rows = sb("PATCH", "/rest/v1/listings", params={"id": f"eq.{int(listing_id)}"},
              payload=changes, prefer="return=representation") or []
    return rows[0] if rows else None


def sb_get_purchase(request_id):
    rows = sb("GET", "/rest/v1/purchase_requests", params={"id": f"eq.{int(request_id)}", "select": "*", "limit": "1"}) or []
    return rows[0] if rows else None


def sb_update_purchase(request_id, changes):
    changes["updated_at"] = now_iso()
    rows = sb("PATCH", "/rest/v1/purchase_requests", params={"id": f"eq.{int(request_id)}"},
              payload=changes, prefer="return=representation") or []
    return rows[0] if rows else None


def telegram(method, data=None):
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN در Vercel تنظیم نشده است.")
    response = requests.post(f"{TG_API}/{method}", json=data or {}, timeout=45)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram API error"))
    return result.get("result")


def send_message(chat_id, text, keyboard=None, message_thread_id=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if keyboard:
        payload["reply_markup"] = keyboard
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    return telegram("sendMessage", payload)


def send_registration_button_to_group():
    keyboard = {"inline_keyboard": [[{"text": "🟡 ثبت آگهی با ربات", "url": "https://t.me/ZarinMahGoldBot?start=sell"}]]}
    message = send_message(
        GROUP,
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n📣 <b>آگهی کن، دیده شو، بفروش! 📣</b>\n\nآگهی پس از بررسی و تأیید مدیر در موضوع خریدوفروش منتشر می‌شود.",
        keyboard, GROUP_THREAD_ID,
    )
    telegram("pinChatMessage", {"chat_id": GROUP, "message_id": message["message_id"], "disable_notification": True})
    return message


def gh_headers():
    if not GITHUB_TOKEN:
        raise RuntimeError("SELLER_DATA_TOKEN در Vercel تنظیم نشده است.")
    return {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def load_state():
    response = requests.get(GH_API, headers=gh_headers(), timeout=25)
    if response.status_code == 404:
        return {"last_update_id": -1, "users": {}}, None
    response.raise_for_status()
    item = response.json()
    raw = base64.b64decode(item["content"])
    state = json.loads(raw.decode("utf-8"))
    if not isinstance(state, dict):
        raise ValueError("state.json معتبر نیست.")
    state.setdefault("last_update_id", int(state.get("offset", 0)) - 1)
    state.setdefault("users", {})
    # هر کلید قدیمی مانند listings و purchase_requests بدون دست‌کاری حفظ می‌شود.
    return state, item["sha"]


def save_state(state, sha):
    content = base64.b64encode(json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
    payload = {"message": "Update ZarinMah Telegram conversation state", "content": content}
    if sha:
        payload["sha"] = sha
    response = requests.put(GH_API, headers=gh_headers(), json=payload, timeout=25)
    response.raise_for_status()
    return response.json()["content"]["sha"]


def get_user(state, user_id):
    key = str(user_id)
    state.setdefault("users", {})
    state["users"].setdefault(key, {})
    return state["users"][key]


def cancel_flow(user):
    user.pop("flow", None)
    user.pop("draft", None)


def main_keyboard(is_admin=False):
    rows = [[{"text": "ثبت آگهی فروش"}], [{"text": "راهنمای ثبت آگهی"}]]
    if is_admin:
        rows = [
            [{"text": "ثبت آگهی فروش"}, {"text": "ثبت محصول مدیر"}],
            [{"text": "آگهی‌های در انتظار"}, {"text": "آگهی‌های منتشرشده"}],
            [{"text": "آگهی‌های حذف‌شده"}],
            [{"text": "📌 ارسال دکمه ثبت آگهی به گروه"}],
            [{"text": "راهنمای ثبت آگهی"}],
        ]
    return {"keyboard": rows, "resize_keyboard": True}


def ask(chat_id, text, keyboard=None):
    send_message(chat_id, text, keyboard)


def start_listing(chat_id, user, kind):
    user["flow"] = "title"
    user["draft"] = {"kind": kind, "photo_file_id": None, "invoice_file_id": None}
    ask(chat_id, "📝 <b>ثبت آگهی زرین ماه</b>\n\nمرحله ۱: عنوان محصول را بفرست.\nمثال: انگشتر طلای ۱۸ عیار\n\nبرای لغو هر زمان /cancel را بفرست.")


def ask_category(chat_id):
    keyboard = {
        "keyboard": [
            [{"text": "زیورآلات طلا"}, {"text": "سکه"}],
            [{"text": "سکه پارسیان"}, {"text": "طلای آب‌شده"}],
            [{"text": "/cancel"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False,
        "is_persistent": True,
    }
    ask(chat_id, "دسته‌بندی محصول را از دکمه‌های زیر انتخاب کن:", keyboard)


def ask_subtype(chat_id, category):
    options = list(JEWELRY.keys()) if category == "jewelry" else list(COINS.keys())
    rows = [[{"text": value}] for value in options]
    rows.append([{"text": "/cancel"}])
    ask(chat_id, "نوع محصول را از گزینه‌های زیر انتخاب کن:", {
        "keyboard": rows, "resize_keyboard": True,
        "one_time_keyboard": False, "is_persistent": True,
    })


def ask_purity(chat_id):
    rows = [
        [{"text": "۱۸ عیار"}, {"text": "۲۱ عیار"}],
        [{"text": "۲۲ عیار"}, {"text": "۲۴ عیار"}],
        [{"text": "۹۰۰ از ۱۰۰۰ (سکه)"}, {"text": "نیازمند بررسی"}],
        [{"text": "ورود دستی عیار"}],
        [{"text": "/cancel"}],
    ]
    ask(chat_id, "عیار محصول را انتخاب کن:", {
        "keyboard": rows, "resize_keyboard": True,
        "one_time_keyboard": False, "is_persistent": True,
    })


def load_city_records():
    global _CITY_DATA_CACHE
    if _CITY_DATA_CACHE is not None:
        return _CITY_DATA_CACHE
    response = requests.get(CITY_DATA_URL, timeout=20)
    response.raise_for_status()
    records = response.json()
    if not isinstance(records, list) or not records:
        raise RuntimeError("فهرست شهرها معتبر نیست.")
    _CITY_DATA_CACHE = records
    return records


def get_cities_for_province(province):
    province_id = PROVINCE_ID_BY_NAME.get(province)
    if province_id is None:
        raise ValueError("استان انتخاب‌شده در فهرست شهرها موجود نیست.")
    records = load_city_records()
    names = sorted({
        clean(item.get("name"), 80)
        for item in records
        if isinstance(item, dict) and int(item.get("province_id", -1)) == province_id and item.get("name")
    })
    if not names:
        raise RuntimeError("برای استان انتخاب‌شده شهری پیدا نشد.")
    return names


def city_keyboard(cities, page):
    total_pages = max(1, (len(cities) + CITY_PAGE_SIZE - 1) // CITY_PAGE_SIZE)
    page = max(0, min(int(page), total_pages - 1))
    start = page * CITY_PAGE_SIZE
    chunk = cities[start:start + CITY_PAGE_SIZE]
    rows = []
    for i in range(0, len(chunk), 2):
        row = []
        for offset, city in enumerate(chunk[i:i + 2]):
            index = start + i + offset
            row.append({"text": city, "callback_data": f"citysel:{index}"})
        rows.append(row)
    nav = []
    if page > 0:
        nav.append({"text": "⬅️ قبلی", "callback_data": f"citypage:{page - 1}"})
    nav.append({"text": f"{page + 1}/{total_pages}", "callback_data": "citynoop"})
    if page + 1 < total_pages:
        nav.append({"text": "بعدی ➡️", "callback_data": f"citypage:{page + 1}"})
    rows.append(nav)
    rows.append([{"text": "✍️ ورود دستی نام شهر", "callback_data": "citymanual"}])
    return {"inline_keyboard": rows}


def ask_city_page(chat_id, province, page=0, message_id=None):
    cities = get_cities_for_province(province)
    markup = city_keyboard(cities, page)
    text = f"📍 شهر محل فروش در استان {esc(province)} را انتخاب کن.\nتعداد شهرهای ثبت‌شده: {len(cities)}"
    if message_id:
        telegram("editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": text, "parse_mode": "HTML", "reply_markup": markup,
        })
    else:
        send_message(chat_id, "استان انتخاب شد؛ حالا شهر را از فهرست انتخاب کن.", {"remove_keyboard": True})
        send_message(chat_id, text, markup)


def ask_pricing(chat_id):
    keyboard = {"keyboard": [[{"text": "قیمت ثابت"}, {"text": "درصد بالاتر از تابلو"}], [{"text": "مبلغ ثابت بالاتر از تابلو"}, {"text": "توافقی"}], [{"text": "/cancel"}]], "resize_keyboard": True, "one_time_keyboard": False, "is_persistent": True}
    ask(chat_id, "روش قیمت‌گذاری را انتخاب کن:", keyboard)


def ask_province(chat_id):
    rows = [[{"text": province}] for province in PROVINCES]
    rows.append([{"text": "/cancel"}])
    ask(chat_id, "استان محل فروش را از فهرست زیر انتخاب کن:", {"keyboard": rows, "resize_keyboard": True, "one_time_keyboard": False, "is_persistent": True})


def get_telegram_file(file_id):
    info = telegram("getFile", {"file_id": file_id})
    file_path = info.get("file_path")
    if not file_path:
        raise RuntimeError("مسیر فایل از تلگرام دریافت نشد.")
    response = requests.get(f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}", timeout=60)
    response.raise_for_status()
    return response.content, file_path


def upload_telegram_file(file_id, bucket, object_path, mime):
    """دانلود فایل از تلگرام و آپلود در باکت مناسب Supabase."""
    if not file_id:
        raise ValueError("شناسه فایل تلگرام خالی است.")
    logger.info("FILE_UPLOAD_START bucket=%s object=%s", bucket, object_path)
    content, telegram_path = get_telegram_file(file_id)
    size = len(content)
    if size <= 0:
        raise ValueError("فایل دریافتی از تلگرام خالی است.")
    if size > 10 * 1024 * 1024:
        raise ValueError("حجم فایل تلگرام بیش از حد مجاز است.")
    encoded = "/".join(quote(part, safe="") for part in object_path.split("/"))
    sb(
        "POST",
        f"/storage/v1/object/{bucket}/{encoded}",
        raw=content,
        content_type=mime,
        prefer="resolution=ignore-duplicates",
    )
    logger.info(
        "FILE_UPLOAD_OK bucket=%s object=%s bytes=%s telegram_path=%s",
        bucket, object_path, size, telegram_path,
    )
    if bucket == "listing-images":
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{encoded}"
    return object_path


def price_label(listing):
    method, value, price = listing.get("pricing_method"), listing.get("pricing_value"), listing.get("price")
    if method == "fixed" and price is not None:
        return f"{int(float(price)):,} تومان"
    if method == "percent" and value is not None:
        return f"{float(value):g}٪ بالاتر از تابلو"
    if method == "amount" and value is not None:
        return f"{int(float(value)):,} تومان بالاتر از تابلو"
    return "توافقی" if method == "negotiable" else "قیمت اعلام نشده"


def listing_caption(listing):
    label = "محصول مدیر زرین ماه" if str(listing.get("owner_telegram_id") or "") == str(ADMIN_ID) else "آگهی فروش کاربر"
    weight = listing.get("weight")
    unit = "سوت" if listing.get("weight_unit") == "soot" else "گرم"
    weight_text = f"{weight} {unit}" if weight is not None else "ثبت نشده"
    location = "، ".join(x for x in [listing.get("province"), listing.get("city")] if x) or "ثبت نشده"
    text = (
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        f"🏷 <b>{label}</b>\n"
        f"💎 <b>{esc(listing.get('title') or listing.get('product_type'))}</b>\n\n"
        f"📋 {esc(listing.get('description') or 'بدون توضیحات')}\n"
        f"⚖️ <b>وزن:</b> {esc(weight_text)}\n"
        f"🔎 <b>عیار:</b> {esc(listing.get('purity') or 'ثبت نشده')}\n"
        f"💰 <b>قیمت:</b> {esc(price_label(listing))}\n"
        f"📍 <b>محل:</b> {esc(location)}\n\n"
        "#محصولات_برای_فروش"
    )
    if listing.get("status") == "sold":
        text += "\n\n✅ <b>فروخته شد</b>"
    return text[:1000]


def purchase_keyboard(listing_id):
    return {"inline_keyboard": [[{"text": "🛒 ثبت درخواست خرید", "callback_data": f"buy:{listing_id}"}]]}


def store_message_ref(listing_id, ref):
    current = sb_get_listing(listing_id)
    if not current:
        return
    messages = current.get("telegram_messages") or []
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    if not isinstance(messages, list):
        messages = []
    if not any(str(x.get("chat_id")) == str(ref["chat_id"]) for x in messages if isinstance(x, dict)):
        messages.append(ref)
        sb_update_listing(listing_id, {"telegram_messages": messages})


def send_listing_destination(listing, chat_id, destination, thread_id=None):
    current = sb_get_listing(listing["id"])
    messages = current.get("telegram_messages") or [] if current else []
    if isinstance(messages, dict):
        messages = messages.get("messages", [])
    for old in messages if isinstance(messages, list) else []:
        if isinstance(old, dict) and str(old.get("chat_id")) == str(chat_id):
            return old
    caption = listing_caption(listing)
    keyboard = purchase_keyboard(listing["id"]) if listing.get("status") != "sold" else None
    photo_url = listing.get("image_url")
    payload = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    if keyboard:
        payload["reply_markup"] = keyboard
    if photo_url:
        payload["photo"] = photo_url
        message = telegram("sendPhoto", payload)
        has_photo = True
    else:
        payload.pop("caption", None)
        payload["text"] = caption
        message = telegram("sendMessage", payload)
        has_photo = False
    ref = {"chat_id": message["chat"]["id"], "message_id": message["message_id"], "has_photo": has_photo, "destination": destination}
    store_message_ref(listing["id"], ref)
    return ref


def publish_listing(listing):
    # Manager-owned inventory is posted to channel + group; user ads only to group topic 17.
    if str(listing.get("owner_telegram_id") or "") == str(ADMIN_ID):
        send_listing_destination(listing, CHANNEL, "channel")
    send_listing_destination(listing, GROUP, "group", GROUP_THREAD_ID)


def notify_admin_pending(listing):
    text = (
        "🔔 <b>آگهی تلگرامی برای بررسی</b>\n\n"
        f"🆔 آگهی: <code>{listing['id']}</code>\n"
        f"💎 کالا: {esc(listing.get('title'))}\n"
        f"👤 فروشنده: {esc(listing.get('seller_name'))}\n"
        f"📞 تماس خصوصی: <code>{esc(listing.get('seller_mobile'))}</code>\n"
        f"⚖️ وزن: {esc(str(listing.get('weight') or 'ثبت نشده'))} {'سوت' if listing.get('weight_unit') == 'soot' else 'گرم'}\n"
        f"💰 قیمت: {esc(price_label(listing))}\n"
        f"📍 محل: {esc(listing.get('province'))}، {esc(listing.get('city'))}\n"
        f"📝 توضیحات: {esc(listing.get('description'))}\n"
        "🧾 فاکتور در فضای خصوصی ذخیره شده است."
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ تأیید و انتشار", "callback_data": f"approve:{listing['id']}"},
        {"text": "❌ رد آگهی", "callback_data": f"reject:{listing['id']}"},
    ]]}
    send_message(ADMIN_ID, text, keyboard)


def finish_listing(state, user_id, chat_id):
    """ثبت آگهی؛ مرحله شکست در لاگ Vercel با traceback کامل ثبت می‌شود."""
    user = get_user(state, user_id)
    draft = user.get("draft") or {}
    if not draft.get("photo_file_id") or not draft.get("invoice_file_id"):
        ask(chat_id, "عکس محصول و فاکتور فروش هر دو الزامی هستند.")
        return

    stage = "prepare_upload"
    listing_id = None
    try:
        # شناسه ثابت باعث می‌شود در تلاش مجدد، فایل عکس دوباره با نام تازه ساخته نشود.
        token = draft.setdefault("storage_token", uuid.uuid4().hex)
        photo_ext = "jpg"
        invoice_ext = draft.get("invoice_ext", "pdf")
        photo_path = f"telegram/{token}.{photo_ext}"
        invoice_path = f"telegram/{token}.{invoice_ext}"

        if not draft.get("photo_url"):
            stage = "storage_upload_image"
            draft["photo_url"] = upload_telegram_file(
                draft["photo_file_id"], "listing-images", photo_path, "image/jpeg"
            )

        if not draft.get("saved_invoice_path"):
            stage = "storage_upload_invoice"
            invoice_mime = draft.get("invoice_mime", "application/pdf")
            draft["saved_invoice_path"] = upload_telegram_file(
                draft["invoice_file_id"], "listing-invoices", invoice_path, invoice_mime
            )

        stage = "prepare_database_row"
        is_admin = str(user_id) == str(ADMIN_ID) and draft.get("kind") == "admin"
        row = {
            "owner_user_id": None,
            "owner_telegram_id": str(user_id),
            "title": clean(draft.get("title"), 150) or clean(draft.get("product_type"), 150),
            "product_type": draft["product_type"],
            "weight": draft.get("weight"),
            "purity": draft.get("purity"),
            "pricing_method": draft["pricing_method"],
            "price": draft.get("price"),
            "pricing_value": draft.get("pricing_value"),
            "description": draft.get("description"),
            "image_url": draft["photo_url"],
            "status": "published" if is_admin else "pending",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "seller_name": "زرین ماه" if is_admin else clean(draft.get("seller_name"), 100),
            "seller_mobile": clean(draft.get("seller_mobile"), 30) or None,
            "weight_unit": draft.get("weight_unit", "gram"),
            "province": draft.get("province"),
            "city": draft.get("city"),
            "invoice_path": draft["saved_invoice_path"],
            "published_at": now_iso() if is_admin else None,
        }

        stage = "supabase_insert_listing"
        rows = sb("POST", "/rest/v1/listings", payload=row, prefer="return=representation") or []
        if not rows:
            raise RuntimeError("ثبت آگهی در Supabase ناموفق بود؛ پاسخ خالی دریافت شد.")
        listing = rows[0]
        listing_id = listing.get("id")

        # از این نقطه آگهی در دیتابیس ثبت شده؛ دیگر خطای پیام‌رسانی نباید
        # به کاربر بگوید آگهی ثبت نشده یا او را به ارسال دوباره فاکتور هدایت کند.
        cancel_flow(user)
        logger.info("LISTING_DATABASE_INSERT_OK listing_id=%s user_id=%s", listing_id, user_id)

    except Exception:
        logger.exception(
            "LISTING_FINISH_FAILED stage=%s user_id=%s", stage, user_id
        )
        raise

    if is_admin:
        try:
            stage = "admin_publish"
            publish_listing(listing)
            send_message(chat_id, f"✅ محصول مدیر با شناسه {listing['id']} در کانال و گروه منتشر شد.", main_keyboard(True))
        except Exception:
            logger.exception("ADMIN_PRODUCT_POST_INSERT_FAILED listing_id=%s", listing_id)
            try:
                send_message(
                    chat_id,
                    f"محصول با شناسه {listing['id']} در پایگاه داده ثبت شد، اما انتشار کامل نشد. از ثبت دوباره خودداری کن و گزارش Vercel را بررسی کن.",
                    main_keyboard(True),
                )
            except Exception:
                logger.exception("ADMIN_PRODUCT_RESULT_MESSAGE_FAILED listing_id=%s", listing_id)
    else:
        try:
            stage = "admin_notify"
            notify_admin_pending(listing)
            result_text = f"✅ آگهی شما با شناسه {listing['id']} برای بررسی مدیر ارسال شد. پس از تأیید در موضوع خریدوفروش گروه منتشر می‌شود."
        except Exception:
            logger.exception("ADMIN_PENDING_NOTIFICATION_FAILED listing_id=%s", listing_id)
            result_text = f"آگهی شما با شناسه {listing['id']} در سامانه ذخیره شد، اما اطلاع‌رسانی به مدیر ناموفق بود. لطفاً آگهی را دوباره ثبت نکن و از طریق پشتیبانی پیگیری کن."
        try:
            send_message(chat_id, result_text, main_keyboard(False))
        except Exception:
            logger.exception("SELLER_RESULT_MESSAGE_FAILED listing_id=%s", listing_id)


def pending_listings(chat_id):
    rows = sb("GET", "/rest/v1/listings", params={"status": "eq.pending", "deleted_at": "is.null", "select": "id,title,product_type,province,city,seller_name,seller_mobile,weight,weight_unit,pricing_method,pricing_value,price,description", "order": "created_at.asc", "limit": "30"}) or []
    if not rows:
        send_message(chat_id, "در حال حاضر آگهی در انتظاری وجود ندارد.")
        return
    for listing in rows:
        text = (f"🆕 <b>آگهی {listing['id']}</b>\n💎 {esc(listing.get('title'))}\n👤 {esc(listing.get('seller_name'))}\n📞 <code>{esc(listing.get('seller_mobile'))}</code>\n📍 {esc(listing.get('province'))}، {esc(listing.get('city'))}\n💰 {esc(price_label(listing))}")
        keyboard = {"inline_keyboard": [[
            {"text": "✅ تأیید", "callback_data": f"approve:{listing['id']}"},
            {"text": "❌ رد", "callback_data": f"reject:{listing['id']}"},
        ]]}
        send_message(chat_id, text, keyboard)


def published_listings(chat_id, deleted=False):
    params = {"select": "id,title,product_type,status,deleted_at", "order": "created_at.desc", "limit": "30"}
    if deleted:
        params["deleted_at"] = "not.is.null"
    else:
        params["deleted_at"] = "is.null"
        params["status"] = "in.(published,sold)"
    rows = sb("GET", "/rest/v1/listings", params=params) or []
    if not rows:
        send_message(chat_id, "موردی برای نمایش وجود ندارد.")
        return
    for listing in rows:
        if deleted:
            keyboard = {"inline_keyboard": [[{"text": "♻️ بازگردانی آگهی", "callback_data": f"restore:{listing['id']}"}]]}
        else:
            keyboard = {"inline_keyboard": [[
                {"text": "✅ فروش رفت", "callback_data": f"sold:{listing['id']}"},
                {"text": "🗑 حذف آگهی", "callback_data": f"delete:{listing['id']}"},
            ]]}
        send_message(chat_id, f"🆔 {listing['id']} — {esc(listing.get('title') or listing.get('product_type'))} — {esc(listing.get('status'))}", keyboard)


def handle_message(state, message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = clean(message.get("text") or message.get("caption"), 2500)
    is_admin = str(user_id) == str(ADMIN_ID)
    user = get_user(state, user_id)

    if text == "/cancel":
        cancel_flow(user)
        send_message(chat_id, "عملیات لغو شد.", main_keyboard(is_admin))
        return

    parts = text.split(maxsplit=1)
    command = parts[0].split("@", 1)[0] if parts else ""
    payload = parts[1].strip().lower() if len(parts) > 1 else ""
    if command == "/start":
        cancel_flow(user)
        if payload == "sell":
            start_listing(chat_id, user, "user")
        else:
            send_message(chat_id, "سلام! به ربات فروشندگان زرین ماه خوش آمدی. 🌙\nبرای ثبت آگهی، گزینه «ثبت آگهی فروش» را انتخاب کن. آگهی پس از تأیید مدیر منتشر می‌شود.", main_keyboard(is_admin))
        return

    if text == "راهنمای ثبت آگهی":
        send_message(chat_id, "برای ثبت آگهی، مشخصات محصول، روش قیمت‌گذاری، استان و شهر، شماره تماس، عکس محصول و فاکتور فروش معتبر را وارد کن. آگهی پس از تأیید مدیر منتشر می‌شود.\nلغو عملیات: /cancel", main_keyboard(is_admin))
        return
    if text == "ثبت آگهی فروش":
        start_listing(chat_id, user, "user")
        return
    if text == "ثبت محصول مدیر" and is_admin:
        start_listing(chat_id, user, "admin")
        return
    if text == "📌 ارسال دکمه ثبت آگهی به گروه" and is_admin:
        try:
            send_registration_button_to_group()
            send_message(chat_id, "✅ پیام ثبت آگهی در موضوع خریدوفروش گروه ارسال و سنجاق شد.", main_keyboard(True))
        except Exception as exc:
            print("PIN REGISTRATION MESSAGE ERROR:", str(exc)[:200])
            send_message(chat_id, "ارسال یا سنجاق پیام انجام نشد. مجوز ارسال و سنجاق‌کردن پیام ربات را بررسی کن.", main_keyboard(True))
        return
    if text == "آگهی‌های در انتظار" and is_admin:
        pending_listings(chat_id)
        return
    if text == "آگهی‌های منتشرشده" and is_admin:
        published_listings(chat_id, False)
        return
    if text == "آگهی‌های حذف‌شده" and is_admin:
        published_listings(chat_id, True)
        return

    flow = user.get("flow")
    draft = user.get("draft") or {}
    if not flow:
        send_message(chat_id, "برای شروع از دکمه‌های منو استفاده کن.", main_keyboard(is_admin))
        return

    if flow == "title":
        if not text:
            ask(chat_id, "عنوان محصول را متنی ارسال کن.")
            return
        draft["title"] = text[:150]
        user["flow"] = "category"
        ask_category(chat_id)
        return

    if flow == "category":
        mapping = {"زیورآلات طلا": "jewelry", "سکه": "coin", "سکه پارسیان": "parsian", "طلای آب‌شده": "melted"}
        if text not in mapping:
            ask_category(chat_id)
            return
        category = mapping[text]
        draft["category"] = category
        if category == "jewelry":
            user["flow"] = "subtype"
            ask_subtype(chat_id, category)
        elif category == "coin":
            user["flow"] = "subtype"
            ask_subtype(chat_id, category)
        elif category == "parsian":
            draft["product_type"] = "سکه پارسیان"
            draft["weight_unit"] = "soot"
            user["flow"] = "weight"
            ask(chat_id, "وزن سکه پارسیان را فقط برحسب سوت وارد کن. مثال: ۵۰۰")
        else:
            draft["product_type"] = "طلای آب‌شده"
            draft["weight_unit"] = "gram"
            user["flow"] = "weight"
            ask(chat_id, "وزن محصول را برحسب گرم وارد کن؛ اگر نمی‌خواهی ثبت کنی بنویس «ندارد».")
        return

    if flow == "subtype":
        if draft.get("category") == "jewelry" and text in JEWELRY:
            draft["product_type"] = JEWELRY[text]
        elif draft.get("category") == "coin" and text in COINS:
            draft["product_type"] = COINS[text]
        else:
            ask(chat_id, "گزینه معتبر نیست. یکی از گزینه‌های فهرست را دقیق ارسال کن.")
            return
        draft["weight_unit"] = "gram"
        user["flow"] = "weight"
        ask(chat_id, "وزن محصول را برحسب گرم وارد کن؛ اگر نمی‌خواهی ثبت کنی بنویس «ندارد».")
        return

    if flow == "weight":
        if text in ("ندارد", "ثبت نمی‌کنم", "نامشخص") and draft.get("weight_unit") != "soot":
            draft["weight"] = None
        else:
            raw = fa_to_en(text).replace(",", ".").replace("٫", ".")
            if not re.fullmatch(r"\d+(?:\.\d{1,4})?", raw):
                ask(chat_id, "وزن معتبر نیست. فقط عدد وارد کن؛ برای وزن نامشخص بنویس «ندارد».")
                return
            value = float(raw)
            if value <= 0:
                ask(chat_id, "وزن باید بیشتر از صفر باشد.")
                return
            draft["weight"] = int(value) if value.is_integer() else value
        user["flow"] = "purity"
        ask_purity(chat_id)
        return

    if flow == "purity":
        allowed_purities = {"۱۸ عیار", "۲۱ عیار", "۲۲ عیار", "۲۴ عیار", "۹۰۰ از ۱۰۰۰ (سکه)", "نیازمند بررسی"}
        if text == "ورود دستی عیار":
            user["flow"] = "purity_custom"
            ask(chat_id, "عیار را به‌صورت متنی وارد کن؛ مثال: ۷۵۰ یا ۱۸ عیار.")
            return
        if text not in allowed_purities:
            ask_purity(chat_id)
            return
        draft["purity"] = text
        user["flow"] = "description"
        ask(chat_id, "توضیحات محصول را بفرست؛ وضعیت، اجرت و جزئیات مهم را بنویس.")
        return

    if flow == "purity_custom":
        if not text or len(text) > 60:
            ask(chat_id, "عیار معتبر وارد کن (حداکثر ۶۰ نویسه).")
            return
        draft["purity"] = text
        user["flow"] = "description"
        ask(chat_id, "توضیحات محصول را بفرست؛ وضعیت، اجرت و جزئیات مهم را بنویس.")
        return

    if flow == "description":
        if not text:
            ask(chat_id, "توضیحات محصول الزامی است.")
            return
        draft["description"] = text[:1500]
        user["flow"] = "pricing_method"
        ask_pricing(chat_id)
        return

    if flow == "pricing_method":
        methods = {"قیمت ثابت": "fixed", "درصد بالاتر از تابلو": "percent", "مبلغ ثابت بالاتر از تابلو": "amount", "توافقی": "negotiable"}
        if text not in methods:
            ask_pricing(chat_id)
            return
        draft["pricing_method"] = methods[text]
        if methods[text] == "fixed":
            user["flow"] = "price_value"
            ask(chat_id, "قیمت نهایی را به تومان وارد کن.")
        elif methods[text] == "percent":
            user["flow"] = "price_value"
            ask(chat_id, "چند درصد بالاتر از قیمت تابلو؟ عددی بین صفر تا ۱۰۰ وارد کن.")
        elif methods[text] == "amount":
            user["flow"] = "price_value"
            ask(chat_id, "چه مبلغی بالاتر از قیمت تابلو (تومان)؟")
        else:
            draft["price"] = None
            draft["pricing_value"] = None
            user["flow"] = "province"
            ask_province(chat_id)
        return

    if flow == "price_value":
        raw = fa_to_en(text).replace(",", "").replace("٫", ".").strip()
        method = draft.get("pricing_method")
        try:
            value = float(raw)
            if value <= 0 and method != "percent":
                raise ValueError
            if method == "percent" and not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            ask(chat_id, "مقدار واردشده معتبر نیست. دوباره عدد را وارد کن.")
            return
        if method == "fixed":
            draft["price"] = int(value)
            draft["pricing_value"] = None
        else:
            draft["price"] = None
            draft["pricing_value"] = value
        user["flow"] = "province"
        ask_province(chat_id)
        return

    if flow == "province":
        if text not in PROVINCES:
            ask(chat_id, "استان را از فهرست انتخاب کن.")
            return
        draft["province"] = text
        user["flow"] = "city"
        try:
            ask_city_page(chat_id, text, 0)
        except Exception as exc:
            print("CITY DATA LOAD ERROR:", str(exc)[:250])
            user["flow"] = "city_manual"
            ask(chat_id, "فهرست شهرها موقتاً در دسترس نیست. نام شهر محل فروش را وارد کن.")
        return

    if flow == "city_manual":
        if len(text) < 2:
            ask(chat_id, "نام شهر معتبر وارد کن.")
            return
        draft["city"] = text[:80]
        if draft.get("kind") == "admin":
            draft["seller_name"] = "زرین ماه"
            draft["seller_mobile"] = None
            user["flow"] = "photo"
            ask(chat_id, "📷 عکس محصول را ارسال کن. از علامت سنجاق/پیوست کنار کادر پیام، عکس را از گالری انتخاب کن. ارسال عکس الزامی است.")
        else:
            user["flow"] = "seller_name"
            ask(chat_id, "نام و نام خانوادگی فروشنده را بفرست.")
        return

    if flow == "seller_name":
        if not text:
            ask(chat_id, "نام فروشنده الزامی است.")
            return
        draft["seller_name"] = text[:100]
        user["flow"] = "seller_mobile"
        ask(chat_id, "شماره تماس فروشنده را برای هماهنگی خصوصی مدیر ارسال کن.")
        return

    if flow == "seller_mobile":
        mobile = re.sub(r"\D", "", fa_to_en(text))
        if not 8 <= len(mobile) <= 15:
            ask(chat_id, "شماره تماس معتبر نیست. دوباره شماره را ارسال کن.")
            return
        draft["seller_mobile"] = mobile
        user["flow"] = "photo"
        ask(chat_id, "📷 عکس محصول را ارسال کن. از علامت سنجاق/پیوست کنار کادر پیام، عکس را از گالری انتخاب کن. عکس برای نمایش آگهی الزامی است.")
        return

    if flow == "photo":
        photos = message.get("photo") or []
        if not photos:
            ask(chat_id, "لطفاً خودِ عکس را با گزینه گالری/پیوست تلگرام ارسال کن؛ پیام متنی یا فایل PDF به‌جای عکس محصول پذیرفته نمی‌شود.")
            return
        draft["photo_file_id"] = photos[-1]["file_id"]
        user["flow"] = "invoice"
        ask(chat_id, "حالا فاکتور فروش معتبر را به‌صورت عکس یا فایل PDF ارسال کن. فاکتور برای عموم نمایش داده نمی‌شود.")
        return

    if flow == "invoice":
        document = message.get("document")
        photos = message.get("photo") or []
        if document:
            mime = document.get("mime_type") or "application/octet-stream"
            filename = document.get("file_name") or "invoice.pdf"
            if mime not in ("application/pdf", "image/jpeg", "image/png", "image/webp"):
                ask(chat_id, "فاکتور باید PDF یا تصویر JPG/PNG/WEBP باشد.")
                return
            draft["invoice_file_id"] = document["file_id"]
            draft["invoice_mime"] = mime
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in ("pdf", "jpg", "jpeg", "png", "webp"):
                ext = {
                    "application/pdf": "pdf",
                    "image/jpeg": "jpg",
                    "image/png": "png",
                    "image/webp": "webp",
                }.get(mime, "pdf")
            draft["invoice_ext"] = ext
        elif photos:
            draft["invoice_file_id"] = photos[-1]["file_id"]
            draft["invoice_mime"] = "image/jpeg"
            draft["invoice_ext"] = "jpg"
        else:
            ask(chat_id, "فاکتور فروش الزامی است. آن را به‌صورت عکس یا PDF ارسال کن.")
            return
        try:
            finish_listing(state, user_id, chat_id)
        except Exception:
            logger.exception("INVOICE_SUBMISSION_FAILED user_id=%s", user_id)
            try:
                ask(
                    chat_id,
                    "ثبت آگهی کامل نشد. اطلاعاتت در ربات نگه داشته شده است. لطفاً فعلاً آگهی جدید نساز؛ پشتیبانی باید گزارش خطای ثبت را بررسی کند. اگر مشکل موقت باشد، می‌توانی همین فاکتور را دوباره ارسال کنی.",
                )
            except Exception:
                logger.exception("INVOICE_ERROR_REPLY_FAILED user_id=%s", user_id)
        return


def admin_callback(callback, data):
    try:
        action, raw_id = data.split(":", 1)
        object_id = int(raw_id)
    except Exception:
        return "درخواست نامعتبر است.", True

    if action in ("approve", "approve_web", "reject", "reject_web", "sold", "delete", "restore"):
        listing = sb_get_listing(object_id)
        if not listing:
            return "آگهی پیدا نشد.", True
        if action in ("approve", "approve_web"):
            if listing.get("deleted_at"):
                return "آگهی حذف‌شده است؛ ابتدا آن را بازیابی کن.", True
            if listing.get("status") != "pending":
                return "این آگهی قبلاً بررسی شده است.", True
            publish_listing(listing)
            sb_update_listing(object_id, {"status": "published", "published_at": now_iso()})
            owner = listing.get("owner_telegram_id")
            if owner and str(owner) != str(ADMIN_ID):
                try:
                    send_message(owner, f"✅ آگهی شماره {object_id} پس از بررسی در موضوع خریدوفروش گروه منتشر شد.")
                except Exception:
                    pass
            return "آگهی تأیید و منتشر شد.", False
        if action in ("reject", "reject_web"):
            if listing.get("status") != "pending" or listing.get("deleted_at"):
                return "این آگهی در وضعیت انتظار نیست.", True
            sb_update_listing(object_id, {"deleted_at": now_iso()})
            owner = listing.get("owner_telegram_id")
            if owner and str(owner) != str(ADMIN_ID):
                try:
                    send_message(owner, "متأسفانه آگهی شما تأیید نشد. برای ثبت مجدد، از منوی ربات اقدام کن.")
                except Exception:
                    pass
            return "آگهی رد و از نمایش عمومی خارج شد.", False
        if action == "sold":
            if listing.get("status") != "published" or listing.get("deleted_at"):
                return "فقط آگهی منتشرشده را می‌توان فروش‌رفته کرد.", True
            sb_update_listing(object_id, {"status": "sold", "sold_at": now_iso()})
            edit_listing_messages(listing, sold=True)
            return "آگهی فروش‌رفته شد.", False
        if action == "delete":
            if listing.get("deleted_at"):
                return "این آگهی قبلاً حذف شده است.", True
            sb_update_listing(object_id, {"deleted_at": now_iso()})
            edit_listing_messages(listing, disabled=True)
            return "آگهی به‌صورت موقت حذف شد.", False
        if action == "restore":
            if not listing.get("deleted_at"):
                return "این آگهی حذف نشده است.", True
            sb_update_listing(object_id, {"deleted_at": None})
            edit_listing_messages(listing, restore=True)
            return "آگهی بازیابی شد.", False

    if action == "forward_web":
        request_row = sb_get_purchase(object_id)
        if not request_row:
            return "درخواست خرید پیدا نشد.", True
        if request_row.get("status") != "pending":
            return "این درخواست قبلاً پردازش شده است.", True
        listing = sb_get_listing(request_row["listing_id"])
        if not listing:
            return "آگهی مربوط به درخواست پیدا نشد.", True
        buyer_mobile = request_row.get("buyer_mobile") or "از تلگرام ثبت نشده"
        buyer_telegram = request_row.get("buyer_telegram_id")
        seller_mobile = listing.get("seller_mobile") or "ثبت نشده"
        seller_telegram = listing.get("owner_telegram_id")
        details = (
            "📨 <b>اطلاعات خصوصی درخواست خرید</b>\n\n"
            f"🆔 درخواست: <code>{object_id}</code>\n"
            f"📦 آگهی: <code>{listing['id']}</code>\n"
            f"💎 کالا: {esc(listing.get('title') or listing.get('product_type'))}\n\n"
            f"👤 خریدار: {esc(request_row.get('buyer_name') or 'ثبت نشده')}\n"
            f"📞 شماره خریدار: <code>{esc(buyer_mobile)}</code>\n"
            f"🆔 تلگرام خریدار: <code>{esc(buyer_telegram or 'ثبت نشده')}</code>\n\n"
            f"👤 فروشنده: {esc(listing.get('seller_name') or 'ثبت نشده')}\n"
            f"📞 شماره فروشنده: <code>{esc(seller_mobile)}</code>\n"
            f"🆔 تلگرام فروشنده: <code>{esc(seller_telegram or 'ثبت نشده')}</code>\n\n"
            "🔒 این اطلاعات فقط برای هماهنگی همین درخواست است."
        )
        if seller_telegram and buyer_telegram:
            try:
                send_message(seller_telegram, details)
            except Exception:
                send_message(ADMIN_ID, details + "\n\n⚠️ ارسال مستقیم به فروشنده ناموفق بود؛ هماهنگی را از طریق شماره‌ها انجام بده.")
        else:
            send_message(ADMIN_ID, details)
        sb_update_purchase(object_id, {"status": "forwarded"})
        return "اطلاعات درخواست به مسیر خصوصی هماهنگی ارسال شد.", False

    return "عملیات ناشناخته است.", True


def edit_listing_messages(listing, sold=False, disabled=False, restore=False):
    refs = listing.get("telegram_messages") or []
    if isinstance(refs, dict):
        refs = refs.get("messages", [])
    for ref in refs if isinstance(refs, list) else []:
        if not isinstance(ref, dict) or not ref.get("chat_id") or not ref.get("message_id"):
            continue
        chat_id, message_id = ref["chat_id"], ref["message_id"]
        if sold:
            text = listing_caption({**listing, "status": "sold"})
            method = "editMessageCaption" if ref.get("has_photo") else "editMessageText"
            field = "caption" if ref.get("has_photo") else "text"
            telegram(method, {"chat_id": chat_id, "message_id": message_id, field: text, "parse_mode": "HTML"})
            telegram("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
        elif disabled:
            telegram("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})
        elif restore and listing.get("status") == "published":
            telegram("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": purchase_keyboard(listing["id"])})


def handle_callback(callback, state):
    callback_id = callback["id"]
    user_id = callback["from"]["id"]
    data = callback.get("data", "")

    def answer(message, alert=False):
        telegram("answerCallbackQuery", {"callback_query_id": callback_id, "text": message, "show_alert": alert})

    # انتخاب شهر با صفحه‌بندی؛ دکمه‌های شهر فقط در مرحله انتخاب شهر فعال‌اند.
    if data == "citynoop":
        answer("برای دیدن شهرها از دکمه‌های شهر استفاده کن.")
        return
    if data == "citymanual":
        user = get_user(state, user_id)
        if user.get("flow") != "city":
            answer("این مرحله دیگر فعال نیست.", True)
            return
        user["flow"] = "city_manual"
        answer("ورود دستی شهر فعال شد.")
        send_message(callback["message"]["chat"]["id"], "نام شهر محل فروش را بنویس:")
        return
    if data.startswith("citypage:"):
        user = get_user(state, user_id)
        if user.get("flow") != "city":
            answer("این مرحله دیگر فعال نیست.", True)
            return
        try:
            page = int(data.split(":", 1)[1])
            province = (user.get("draft") or {}).get("province")
            ask_city_page(callback["message"]["chat"]["id"], province, page, callback["message"]["message_id"])
            answer("فهرست شهرها به‌روزرسانی شد.")
        except Exception as exc:
            print("CITY PAGE ERROR:", str(exc)[:250])
            answer("بارگذاری شهرها ناموفق بود. نام شهر را دستی وارد کن.", True)
            user["flow"] = "city_manual"
            send_message(callback["message"]["chat"]["id"], "نام شهر محل فروش را بنویس:")
        return
    if data.startswith("citysel:"):
        user = get_user(state, user_id)
        if user.get("flow") != "city":
            answer("این مرحله دیگر فعال نیست.", True)
            return
        try:
            index = int(data.split(":", 1)[1])
            draft = user.get("draft") or {}
            province = draft.get("province")
            cities = get_cities_for_province(province)
            if index < 0 or index >= len(cities):
                raise ValueError("invalid city index")
            draft["city"] = cities[index]
            chat_id = callback["message"]["chat"]["id"]
            telegram("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": callback["message"]["message_id"], "reply_markup": {"inline_keyboard": []}})
            if draft.get("kind") == "admin":
                draft["seller_name"] = "زرین ماه"
                draft["seller_mobile"] = None
                user["flow"] = "photo"
                send_message(chat_id, "📷 حالا عکس محصول را ارسال کن. از علامت سنجاق/پیوست کنار کادر پیام، عکس را از گالری انتخاب کن. ارسال عکس الزامی است.")
            else:
                user["flow"] = "seller_name"
                send_message(chat_id, "نام و نام خانوادگی فروشنده را بفرست.")
            answer("شهر انتخاب شد: " + cities[index])
        except Exception as exc:
            print("CITY SELECT ERROR:", str(exc)[:250])
            answer("انتخاب شهر ناموفق بود؛ دوباره تلاش کن.", True)
        return

    if data.startswith("buy:"):
        try:
            listing_id = int(data.split(":", 1)[1])
        except ValueError:
            answer("شناسه آگهی معتبر نیست.", True)
            return
        listing = sb_get_listing(listing_id)
        if not listing or listing.get("status") != "published" or listing.get("deleted_at"):
            answer("این آگهی دیگر برای خرید در دسترس نیست.", True)
            return
        if str(listing.get("owner_telegram_id") or "") == str(user_id):
            answer("نمی‌توانی برای آگهی خودت درخواست خرید ثبت کنی.", True)
            return
        existing = sb("GET", "/rest/v1/purchase_requests", params={
            "listing_id": f"eq.{listing_id}", "buyer_telegram_id": f"eq.{user_id}",
            "status": "in.(pending,forwarded)", "select": "id", "limit": "1",
        }) or []
        if existing:
            answer("درخواست قبلی تو برای این آگهی ثبت شده است.", True)
            return
        buyer = callback.get("from", {})
        buyer_name = " ".join(x for x in [buyer.get("first_name"), buyer.get("last_name")] if x) or "ثبت نشده"
        row = {
            "listing_id": listing_id, "buyer_user_id": None,
            "buyer_telegram_id": str(user_id), "buyer_name": buyer_name,
            "buyer_mobile": None, "message": "درخواست خرید از تلگرام زرین ماه",
            "status": "pending", "created_at": now_iso(), "updated_at": now_iso(),
        }
        inserted = sb("POST", "/rest/v1/purchase_requests", payload=row, prefer="return=representation") or []
        if not inserted:
            answer("ثبت درخواست خرید ناموفق بود. دوباره تلاش کن.", True)
            return
        req = inserted[0]
        admin_text = (
            "🛒 <b>درخواست خرید جدید از تلگرام</b>\n\n"
            f"🆔 درخواست: <code>{req['id']}</code>\n📦 آگهی: <code>{listing_id}</code>\n"
            f"💎 کالا: {esc(listing.get('title') or listing.get('product_type'))}\n"
            f"👤 خریدار: {esc(buyer_name)}\n🆔 شناسه تلگرام: <code>{user_id}</code>\n\n"
            "برای انتقال اطلاعات خصوصی، دکمه زیر را بزن."
        )
        keyboard = {"inline_keyboard": [[{"text": "📨 بررسی و انتقال درخواست", "callback_data": f"forward_web:{req['id']}"}]]}
        try:
            send_message(ADMIN_ID, admin_text, keyboard)
            answer("درخواست خرید برای پشتیبانی زرین ماه ارسال شد.")
        except Exception as exc:
            print("TELEGRAM PURCHASE ADMIN NOTIFICATION FAILED:", str(exc)[:250])
            answer("درخواست ثبت شد، اما اطلاع‌رسانی به مدیر ناموفق بود. با پشتیبانی تماس بگیر.", True)
        return

    if user_id != ADMIN_ID:
        answer("این عملیات فقط برای مدیر مجاز است.", True)
        return
    try:
        message, alert = admin_callback(callback, data)
    except Exception as exc:
        print("ADMIN CALLBACK ERROR:", str(exc)[:350])
        message, alert = "عملیات با خطا مواجه شد؛ گزارش Vercel را بررسی کن.", True
    answer(message, alert)
    if not alert:
        send_message(ADMIN_ID, "✅ " + message)


class handler(BaseHTTPRequestHandler):
    def respond(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond(200, {"ok": True, "message": "ZarinMah Supabase webhook is running."})

    def do_POST(self):
        received = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not WEBHOOK_SECRET or not hmac.compare_digest(received, WEBHOOK_SECRET):
            self.respond(403, {"ok": False, "error": "Forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_UPDATE_SIZE:
                self.respond(400, {"ok": False, "error": "Invalid request size"})
                return
            update = json.loads(self.rfile.read(length))
            if "update_id" not in update:
                self.respond(400, {"ok": False, "error": "Missing update_id"})
                return
            state, sha = load_state()
            update_id = int(update["update_id"])
            if update_id <= int(state.get("last_update_id", -1)):
                self.respond(200, {"ok": True, "message": "Duplicate update ignored"})
                return
            if "callback_query" in update:
                handle_callback(update["callback_query"], state)
            elif "message" in update:
                handle_message(state, update["message"])
            state["last_update_id"] = update_id
            save_state(state, sha)
            self.respond(200, {"ok": True, "message": "OK"})
        except Exception:
            logger.exception("WEBHOOK_PROCESSING_FAILED")
            self.respond(500, {"ok": False, "error": "Webhook processing failed"})

    def log_message(self, fmt, *args):
        print("WEBHOOK HTTP:", fmt % args)
