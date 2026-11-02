import os
import json
import base64
import html
import hmac
import re
import uuid
import mimetypes
import requests

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from urllib.parse import quote


# =========================================================
# تنظیمات
# =========================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATA_TOKEN = os.environ["SELLER_DATA_TOKEN"]
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

DATA_REPO = "Booknight20/zarinmah-seller-data"

CHANNEL = "@ZarinMahGold"
GROUP = os.getenv("TELEGRAM_GROUP", "@ZarinMahCommunity").strip()
GROUP_THREAD_ID = int(os.getenv("TELEGRAM_GROUP_THREAD_ID", "17"))

ADMIN_ID = 8816884849

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GH_API = (
    f"https://api.github.com/repos/"
    f"{DATA_REPO}/contents/state.json"
)

SUPABASE_REST = f"{SUPABASE_URL}/rest/v1"
SUPABASE_STORAGE = f"{SUPABASE_URL}/storage/v1"

LISTING_IMAGES_BUCKET = "listing-images"

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {DATA_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})

supabase = requests.Session()
supabase.headers.update({
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
})


# =========================================================
# استان‌های ایران
# =========================================================

IRAN_PROVINCES = [
    "آذربایجان شرقی",
    "آذربایجان غربی",
    "اردبیل",
    "اصفهان",
    "البرز",
    "ایلام",
    "بوشهر",
    "تهران",
    "چهارمحال و بختیاری",
    "خراسان جنوبی",
    "خراسان رضوی",
    "خراسان شمالی",
    "خوزستان",
    "زنجان",
    "سمنان",
    "سیستان و بلوچستان",
    "فارس",
    "قزوین",
    "قم",
    "کردستان",
    "کرمان",
    "کرمانشاه",
    "کهگیلویه و بویراحمد",
    "گلستان",
    "گیلان",
    "لرستان",
    "مازندران",
    "مرکزی",
    "هرمزگان",
    "همدان",
    "یزد",
]

PRODUCT_TYPES = [
    "زیورآلات",
    "سکه",
    "پارسیان",
    "طلای آب‌شده",
    "سایر",
]


# =========================================================
# ابزارهای عمومی
# =========================================================

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_digits(value):
    value = str(value or "")
    return value.translate(str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬",
        "01234567890123456789..",
    )).replace("٬", "").replace(",", ".").strip()


def parse_number(value):
    normalized = normalize_digits(value)

    if not re.fullmatch(r"\d+(?:\.\d{1,4})?", normalized):
        return None

    try:
        number = float(normalized)
        return number if number > 0 else None
    except ValueError:
        return None


def safe(value):
    return html.escape(str(value if value is not None else "ثبت نشده"))


def telegram(method, data=None):
    response = requests.post(
        f"{TG_API}/{method}",
        json=data or {},
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result.get('description', 'unknown')}"
        )

    return result.get("result")


def send_message(
    chat_id,
    text,
    keyboard=None,
    message_thread_id=None,
):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if keyboard:
        data["reply_markup"] = keyboard

    if message_thread_id is not None:
        data["message_thread_id"] = message_thread_id

    return telegram("sendMessage", data)


def answer_callback(callback_id, text, alert=False):
    telegram("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text,
        "show_alert": alert,
    })


def reply_keyboard(rows, resize=True, one_time=False):
    return {
        "keyboard": rows,
        "resize_keyboard": resize,
        "one_time_keyboard": one_time,
    }


def remove_keyboard():
    return {"remove_keyboard": True}


# =========================================================
# ارتباط با GitHub: فقط state کاربران و جریان ثبت
# =========================================================

def empty_state():
    return {
        "last_update_id": -1,
        "users": {},
    }


def load_state():
    response = session.get(GH_API, timeout=25)

    if response.status_code == 404:
        return empty_state(), None

    response.raise_for_status()
    item = response.json()

    content = base64.b64decode(
        item["content"]
    ).decode("utf-8")

    state = json.loads(content)

    if "last_update_id" not in state:
        state["last_update_id"] = (
            int(state.get("offset", 0)) - 1
        )

    state.setdefault("users", {})

    return state, item["sha"]


def save_state(state, sha):
    content = base64.b64encode(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
    ).decode("ascii")

    payload = {
        "message": "Update ZarinMah seller bot state",
        "content": content,
    }

    if sha:
        payload["sha"] = sha

    response = session.put(
        GH_API,
        json=payload,
        timeout=25,
    )
    response.raise_for_status()

    return response.json()["content"]["sha"]


def get_user(state, user_id):
    key = str(user_id)

    if key not in state["users"]:
        state["users"][key] = {}

    return state["users"][key]


def cancel_flow(user):
    user.pop("flow", None)
    user.pop("draft", None)


# =========================================================
# ارتباط با Supabase
# =========================================================

def supabase_request(method, table, params=None, payload=None, prefer=None):
    headers = {}

    if prefer:
        headers["Prefer"] = prefer

    response = supabase.request(
        method,
        f"{SUPABASE_REST}/{table}",
        params=params,
        json=payload,
        headers=headers,
        timeout=30,
    )

    if not response.ok:
        raise RuntimeError(
            f"Supabase {table} error: "
            f"{response.status_code} {response.text[:1000]}"
        )

    if not response.text:
        return None

    return response.json()


def get_listing(listing_id, include_deleted=False):
    params = {
        "id": f"eq.{int(listing_id)}",
        "select": "*",
        "limit": "1",
    }

    if not include_deleted:
        params["deleted_at"] = "is.null"

    rows = supabase_request(
        "GET",
        "listings",
        params=params,
    )

    return rows[0] if rows else None


def insert_listing(payload):
    rows = supabase_request(
        "POST",
        "listings",
        payload=payload,
        prefer="return=representation",
    )

    if not rows:
        raise RuntimeError("Supabase listing insert returned no row")

    return rows[0]


def update_listing(listing_id, payload):
    return supabase_request(
        "PATCH",
        "listings",
        params={"id": f"eq.{int(listing_id)}"},
        payload=payload,
        prefer="return=representation",
    )


def get_purchase_request(request_id):
    rows = supabase_request(
        "GET",
        "purchase_requests",
        params={
            "id": f"eq.{int(request_id)}",
            "select": "*",
            "limit": "1",
        },
    )

    return rows[0] if rows else None


def insert_purchase_request(payload):
    rows = supabase_request(
        "POST",
        "purchase_requests",
        payload=payload,
        prefer="return=representation",
    )

    if not rows:
        raise RuntimeError("Supabase purchase insert returned no row")

    return rows[0]


def update_purchase_request(request_id, payload):
    return supabase_request(
        "PATCH",
        "purchase_requests",
        params={"id": f"eq.{int(request_id)}"},
        payload=payload,
        prefer="return=representation",
    )


def find_duplicate_purchase(listing_id, buyer_telegram_id):
    rows = supabase_request(
        "GET",
        "purchase_requests",
        params={
            "listing_id": f"eq.{int(listing_id)}",
            "buyer_telegram_id": f"eq.{buyer_telegram_id}",
            "status": "in.(pending,forwarded)",
            "select": "id,status",
            "limit": "1",
        },
    )

    return rows[0] if rows else None


# =========================================================
# ذخیره عکس تلگرام در Supabase Storage
# =========================================================

def upload_telegram_photo(file_id):
    file_info = telegram("getFile", {"file_id": file_id})
    file_path = file_info.get("file_path")

    if not file_path:
        raise RuntimeError("Telegram photo path not found")

    download_url = (
        f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/"
        f"{file_path}"
    )

    response = requests.get(download_url, timeout=40)
    response.raise_for_status()

    content_type = response.headers.get(
        "Content-Type",
        "image/jpeg",
    )

    extension = mimetypes.guess_extension(content_type) or ".jpg"
    object_path = f"telegram/{uuid.uuid4().hex}{extension}"

    upload_url = (
        f"{SUPABASE_STORAGE}/object/"
        f"{LISTING_IMAGES_BUCKET}/{object_path}"
    )

    upload_headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "false",
    }

    uploaded = requests.post(
        upload_url,
        data=response.content,
        headers=upload_headers,
        timeout=40,
    )

    if not uploaded.ok:
        raise RuntimeError(
            f"Supabase image upload failed: "
            f"{uploaded.status_code} {uploaded.text[:500]}"
        )

    return (
        f"{SUPABASE_STORAGE}/object/public/"
        f"{LISTING_IMAGES_BUCKET}/{object_path}"
    )


# =========================================================
# منوها و پیام ثبت آگهی
# =========================================================

def main_keyboard(is_admin=False):
    rows = [
        [{"text": "ثبت آگهی فروش"}],
        [{"text": "راهنمای ثبت آگهی"}],
    ]

    if is_admin:
        rows.insert(1, [{"text": "ثبت محصول مدیر"}])
        rows.insert(2, [{"text": "آگهی‌های در انتظار"}])
        rows.insert(3, [{
            "text": "📌 ارسال دکمه ثبت آگهی به گروه"
        }])

    return {
        "keyboard": rows,
        "resize_keyboard": True,
    }


def send_registration_button_to_group():
    keyboard = {
        "inline_keyboard": [[{
            "text": "🟡 ثبت آگهی با ربات",
            "url": "https://t.me/ZarinMahGoldBot?start=sell",
        }]]
    }

    message = send_message(
        GROUP,
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        "📣 <b>آگهی کن، دیده شو، بفروش!</b>\n\n"
        "آگهی پس از بررسی و تأیید مدیر در موضوع خریدوفروش منتشر می‌شود.",
        keyboard=keyboard,
        message_thread_id=GROUP_THREAD_ID,
    )

    telegram("pinChatMessage", {
        "chat_id": GROUP,
        "message_id": message["message_id"],
        "disable_notification": True,
    })

    return message


def start_listing(chat_id, user, kind):
    user["flow"] = "title"
    user["draft"] = {
        "kind": kind,
        "photo": None,
    }

    send_message(
        chat_id,
        "📝 <b>ثبت آگهی زرین ماه</b>\n\n"
        "مرحله ۱: عنوان محصول را بفرست.\n"
        "مثال: انگشتر طلای ۱۸ عیار\n\n"
        "برای لغو عملیات /cancel را بفرست.",
    )


def province_keyboard():
    rows = []

    for index in range(0, len(IRAN_PROVINCES), 2):
        row = [{"text": IRAN_PROVINCES[index]}]

        if index + 1 < len(IRAN_PROVINCES):
            row.append({"text": IRAN_PROVINCES[index + 1]})

        rows.append(row)

    rows.append([{"text": "لغو"}])

    return reply_keyboard(rows, one_time=True)


def product_type_keyboard():
    rows = [
        [{"text": PRODUCT_TYPES[0]}, {"text": PRODUCT_TYPES[1]}],
        [{"text": PRODUCT_TYPES[2]}, {"text": PRODUCT_TYPES[3]}],
        [{"text": PRODUCT_TYPES[4]}],
    ]

    return reply_keyboard(rows, one_time=True)


# =========================================================
# قالب آگهی و انتشار تلگرام
# =========================================================

def listing_caption(listing, sold=False):
    title = safe(listing.get("title"))
    description = safe(listing.get("description"))
    product_type = safe(listing.get("product_type"))
    weight = safe(listing.get("weight"))
    weight_unit = safe(listing.get("weight_unit") or "گرم")
    purity = safe(listing.get("purity"))
    price = safe(listing.get("price"))
    province = safe(listing.get("province"))
    city = safe(listing.get("city"))

    label = (
        "محصول مدیر زرین ماه"
        if listing.get("owner_telegram_id") is None
        else "آگهی فروش کاربر"
    )

    caption = (
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        f"🏷 <b>{label}</b>\n"
        f"💎 <b>{title}</b>\n"
        f"📂 نوع محصول: {product_type}\n"
        f"⚖️ وزن: {weight} {weight_unit}\n"
        f"✨ عیار: {purity}\n"
        f"💰 قیمت اعلامی: {price}\n"
        f"📍 استان: {province}\n"
        f"🏙 شهر: {city}\n\n"
        f"📋 {description}\n\n"
        "#زرین_ماه #خرید_و_فروش_طلا"
    )

    if sold:
        caption += "\n\n✅ <b>فروخته شد</b>"

    return caption


def purchase_keyboard(listing_id):
    return {
        "inline_keyboard": [[{
            "text": "🛒 ثبت درخواست خرید",
            "callback_data": f"buy:{listing_id}",
        }]]
    }


def send_listing_to_destination(
    chat_id,
    caption,
    photo=None,
    thread_id=None,
    keyboard=None,
):
    if photo:
        data = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption[:1000],
            "parse_mode": "HTML",
        }

        if thread_id is not None:
            data["message_thread_id"] = thread_id

        if keyboard:
            data["reply_markup"] = keyboard

        message = telegram("sendPhoto", data)

        return {
            "chat_id": message["chat"]["id"],
            "message_id": message["message_id"],
            "has_photo": True,
        }

    message = send_message(
        chat_id,
        caption,
        keyboard=keyboard,
        message_thread_id=thread_id,
    )

    return {
        "chat_id": message["chat"]["id"],
        "message_id": message["message_id"],
        "has_photo": False,
    }


def publish_listing(listing):
    caption = listing_caption(listing)
    keyboard = purchase_keyboard(listing["id"])

    published_messages = []

    # محصول مدیر در کانال و گروه منتشر می‌شود.
    # آگهی کاربران فقط در موضوع خریدوفروش گروه.
    if not listing.get("owner_telegram_id"):
        published_messages.append(
            send_listing_to_destination(
                CHANNEL,
                caption,
                photo=listing.get("image_url"),
                keyboard=keyboard,
            )
        )

    published_messages.append(
        send_listing_to_destination(
            GROUP,
            caption,
            photo=listing.get("image_url"),
            thread_id=GROUP_THREAD_ID,
            keyboard=keyboard,
        )
    )

    update_listing(
        listing["id"],
        {
            "telegram_messages": published_messages,
            "published_at": now_iso(),
            "status": "published",
        },
    )

    return published_messages


def mark_listing_sold(listing):
    messages = listing.get("telegram_messages") or []
    caption = listing_caption(listing, sold=True)

    for posted in messages:
        chat_id = posted.get("chat_id")
        message_id = posted.get("message_id")

        if not chat_id or not message_id:
            continue

        if posted.get("has_photo"):
            telegram("editMessageCaption", {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": caption[:1000],
                "parse_mode": "HTML",
            })
        else:
            telegram("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": caption,
                "parse_mode": "HTML",
            })

        telegram("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        })

    update_listing(
        listing["id"],
        {
            "status": "sold",
            "sold_at": now_iso(),
        },
    )


# =========================================================
# ایجاد آگهی در Supabase
# =========================================================

def create_supabase_listing(user_id, draft):
    image_url = None

    if draft.get("photo"):
        image_url = upload_telegram_photo(draft["photo"])

    is_admin_product = draft.get("kind") == "admin"

    payload = {
        "owner_telegram_id": (
            None if is_admin_product else str(user_id)
        ),
        "title": draft["title"],
        "product_type": draft["product_type"],
        "weight": draft["weight"],
        "weight_unit": "گرم",
        "purity": draft.get("purity") or "۱۸ عیار",
        "pricing_method": "fixed",
        "pricing_value": draft["price"],
        "price": draft["price"],
        "description": draft["description"],
        "image_url": image_url,
        "seller_name": (
            "زرین ماه" if is_admin_product
            else draft.get("seller_name")
        ),
        "seller_mobile": (
            None if is_admin_product
            else draft.get("contact")
        ),
        "province": draft["province"],
        "city": draft["city"],
        "status": (
            "published" if is_admin_product else "pending"
        ),
    }

    return insert_listing(payload)


def finish_listing(user_id, chat_id, user):
    draft = user["draft"]

    listing = create_supabase_listing(user_id, draft)

    cancel_flow(user)

    if listing.get("status") == "published":
        publish_listing(listing)

        send_message(
            chat_id,
            "✅ محصول مدیر در کانال زرین ماه و موضوع خریدوفروش گروه منتشر شد.",
            main_keyboard(True),
        )

        send_message(
            ADMIN_ID,
            f"محصول مدیر منتشر شد.\nشناسه آگهی: {listing['id']}",
        )

        return

    admin_text = (
        "🔔 <b>آگهی جدید برای بررسی</b>\n\n"
        f"🆔 شماره آگهی: <code>{listing['id']}</code>\n"
        f"💎 عنوان: {safe(listing.get('title'))}\n"
        f"📂 نوع: {safe(listing.get('product_type'))}\n"
        f"⚖️ وزن: {safe(listing.get('weight'))} گرم\n"
        f"✨ عیار: {safe(listing.get('purity'))}\n"
        f"💰 قیمت: {safe(listing.get('price'))}\n"
        f"📍 استان: {safe(listing.get('province'))}\n"
        f"🏙 شهر: {safe(listing.get('city'))}\n"
        f"📋 توضیحات: {safe(listing.get('description'))}\n"
        f"📞 تماس فروشنده: <code>{safe(draft.get('contact'))}</code>\n\n"
        "برای انتشار، آگهی را تأیید کن."
    )

    keyboard = {
        "inline_keyboard": [[
            {
                "text": "✅ تأیید و انتشار",
                "callback_data": f"approve:{listing['id']}",
            },
            {
                "text": "❌ رد آگهی",
                "callback_data": f"reject:{listing['id']}",
            },
        ]]
    }

    send_message(ADMIN_ID, admin_text, keyboard)

    if listing.get("image_url"):
        telegram("sendPhoto", {
            "chat_id": ADMIN_ID,
            "photo": listing["image_url"],
            "caption": f"تصویر آگهی شماره {listing['id']}",
        })

    send_message(
        chat_id,
        "✅ آگهی دریافت شد و برای بررسی مدیر ارسال شد.\n"
        "پس از تأیید، در موضوع خریدوفروش گروه منتشر می‌شود.",
        main_keyboard(user_id == ADMIN_ID),
    )


# =========================================================
# راهنمای ثبت آگهی
# =========================================================

def send_help(chat_id):
    send_message(
        chat_id,
        "📋 <b>راهنمای ثبت آگهی زرین ماه</b>\n\n"
        "برای ثبت آگهی، اطلاعات محصول را مرحله‌به‌مرحله وارد کن:\n"
        "۱. عنوان محصول\n"
        "۲. نوع محصول\n"
        "۳. توضیحات\n"
        "۴. وزن به گرم\n"
        "۵. عیار\n"
        "۶. قیمت به تومان\n"
        "۷. راه ارتباطی\n"
        "۸. استان و شهر\n"
        "۹. عکس محصول\n\n"
        "آگهی کاربران قبل از انتشار توسط مدیر بررسی می‌شود.\n"
        "برای لغو هر مرحله /cancel را بفرست.",
    )


# =========================================================
# پردازش پیام‌های ورودی
# =========================================================

def handle_message(state, message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()

    user = get_user(state, user_id)
    is_admin = user_id == ADMIN_ID

    if text == "/cancel" or text == "لغو":
        cancel_flow(user)
        send_message(
            chat_id,
            "عملیات لغو شد.",
            main_keyboard(is_admin),
        )
        return

    command_parts = text.split(maxsplit=1)
    command = (
        command_parts[0].split("@", 1)[0]
        if command_parts else ""
    )
    payload = (
        command_parts[1].strip().lower()
        if len(command_parts) > 1 else ""
    )

    if command == "/start":
        cancel_flow(user)

        if payload == "sell":
            start_listing(chat_id, user, "user")
            return

        send_message(
            chat_id,
            "سلام! به ربات فروشندگان زرین ماه خوش آمدی. 🌙\n\n"
            "برای ثبت آگهی، گزینه «ثبت آگهی فروش» را انتخاب کن.\n"
            "آگهی کاربران پس از تأیید مدیر منتشر می‌شود.",
            main_keyboard(is_admin),
        )
        return

    if text == "راهنمای ثبت آگهی":
        send_help(chat_id)
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
            send_message(
                chat_id,
                "✅ پیام ثبت آگهی در موضوع خریدوفروش ارسال و سنجاق شد.",
                main_keyboard(True),
            )
        except Exception:
            send_message(
                chat_id,
                "❌ ارسال یا سنجاق پیام ناموفق بود. "
                "مجوزهای ربات در گروه و موضوع خریدوفروش را بررسی کن.",
                main_keyboard(True),
            )
        return

    if text == "آگهی‌های در انتظار" and is_admin:
        pending = supabase_request(
            "GET",
            "listings",
            params={
                "status": "eq.pending",
                "deleted_at": "is.null",
                "select": (
                    "id,title,product_type,weight,price,"
                    "province,city,created_at"
                ),
                "order": "created_at.asc",
                "limit": "30",
            },
        )

        if not pending:
            send_message(chat_id, "در حال حاضر آگهی در انتظاری وجود ندارد.")
            return

        for item in pending:
            keyboard = {
                "inline_keyboard": [[
                    {
                        "text": "✅ تأیید و انتشار",
                        "callback_data": f"approve:{item['id']}",
                    },
                    {
                        "text": "❌ رد آگهی",
                        "callback_data": f"reject:{item['id']}",
                    },
                ]]
            }

            send_message(
                chat_id,
                f"🆔 آگهی: <code>{item['id']}</code>\n"
                f"💎 عنوان: {safe(item.get('title'))}\n"
                f"📂 نوع: {safe(item.get('product_type'))}\n"
                f"⚖️ وزن: {safe(item.get('weight'))} گرم\n"
                f"💰 قیمت: {safe(item.get('price'))}\n"
                f"📍 استان: {safe(item.get('province'))}\n"
                f"🏙 شهر: {safe(item.get('city'))}",
                keyboard,
            )

        return

    flow = user.get("flow")

    if not flow:
        send_message(
            chat_id,
            "برای شروع از دکمه‌های منو استفاده کن.",
            main_keyboard(is_admin),
        )
        return

    draft = user["draft"]

    if flow == "title":
        if not text:
            send_message(chat_id, "لطفاً عنوان محصول را بفرست.")
            return

        draft["title"] = text[:150]
        user["flow"] = "product_type"

        send_message(
            chat_id,
            "نوع محصول را انتخاب کن:",
            product_type_keyboard(),
        )
        return

    if flow == "product_type":
        if text not in PRODUCT_TYPES:
            send_message(
                chat_id,
                "لطفاً یکی از گزینه‌های نوع محصول را انتخاب کن.",
                product_type_keyboard(),
            )
            return

        draft["product_type"] = text
        user["flow"] = "description"

        send_message(
            chat_id,
            "توضیحات محصول را بفرست.",
            remove_keyboard(),
        )
        return

    if flow == "description":
        if not text:
            send_message(chat_id, "لطفاً توضیحات محصول را بفرست.")
            return

        draft["description"] = text[:700]
        user["flow"] = "weight"

        send_message(
            chat_id,
            "⚖️ وزن محصول را به گرم وارد کن.\n"
            "مثال: 2.500",
        )
        return

    if flow == "weight":
        weight = parse_number(text)

        if weight is None or weight > 100000:
            send_message(
                chat_id,
                "وزن معتبر نیست. عدد مثبت برحسب گرم وارد کن.\n"
                "مثال: 2.500",
            )
            return

        draft["weight"] = weight
        user["flow"] = "purity"

        send_message(
            chat_id,
            "عیار محصول را وارد کن.\n"
            "مثال: ۱۸ عیار، ۲۴ عیار یا ۹۰۰",
        )
        return

    if flow == "purity":
        if not text:
            send_message(chat_id, "لطفاً عیار محصول را وارد کن.")
            return

        draft["purity"] = text[:40]
        user["flow"] = "price"

        send_message(
            chat_id,
            "💰 قیمت پیشنهادی را به تومان وارد کن.\n"
            "فقط عدد وارد کن؛ مثال: 25000000",
        )
        return

    if flow == "price":
        price = parse_number(text)

        if price is None or price > 100000000000000:
            send_message(
                chat_id,
                "قیمت معتبر نیست. مبلغ را فقط به‌صورت عدد وارد کن.",
            )
            return

        draft["price"] = price
        user["flow"] = "contact"

        send_message(
            chat_id,
            "راه ارتباطی فروشنده را بفرست؛ "
            "مثلاً شماره موبایل یا آیدی تلگرام.",
        )
        return

    if flow == "contact":
        if not text:
            send_message(chat_id, "لطفاً راه ارتباطی را بفرست.")
            return

        draft["contact"] = text[:150]
        draft["seller_name"] = (
            message.get("from", {}).get("first_name") or "فروشنده"
        )[:100]

        user["flow"] = "province"

        send_message(
            chat_id,
            "📍 استان محل فروش را از فهرست زیر انتخاب کن:",
            province_keyboard(),
        )
        return

    if flow == "province":
        if text not in IRAN_PROVINCES:
            send_message(
                chat_id,
                "لطفاً استان را از فهرست انتخاب کن.",
                province_keyboard(),
            )
            return

        draft["province"] = text
        user["flow"] = "city"

        send_message(
            chat_id,
            f"استان انتخاب‌شده: <b>{safe(text)}</b>\n\n"
            "حالا نام شهر را بنویس.",
            remove_keyboard(),
        )
        return

    if flow == "city":
        if not text or len(text) > 80:
            send_message(
                chat_id,
                "نام شهر معتبر نیست. نام شهر را وارد کن.",
            )
            return

        draft["city"] = text
        user["flow"] = "photo"

        send_message(
            chat_id,
            "📷 حالا عکس محصول را بفرست.\n"
            "اگر عکس نداری، عبارت «بدون عکس» را بفرست.",
        )
        return

    if flow == "photo":
        photos = message.get("photo")

        if photos:
            draft["photo"] = photos[-1]["file_id"]

        elif text.lower() not in (
            "بدون عکس",
            "ندارم",
            "no",
        ):
            send_message(
                chat_id,
                "لطفاً عکس محصول را بفرست یا بنویس «بدون عکس».",
            )
            return

        finish_listing(user_id, chat_id, user)
        return


# =========================================================
# مدیریت درخواست خرید
# =========================================================

def handle_buy(callback, listing_id):
    buyer = callback.get("from", {})
    buyer_id = str(buyer.get("id"))

    listing = get_listing(listing_id)

    if not listing or listing.get("status") != "published":
        answer_callback(
            callback["id"],
            "این آگهی در دسترس خرید نیست.",
            True,
        )
        return

    if str(listing.get("owner_telegram_id") or "") == buyer_id:
        answer_callback(
            callback["id"],
            "نمی‌توانی برای آگهی خودت درخواست خرید ثبت کنی.",
            True,
        )
        return

    duplicate = find_duplicate_purchase(listing_id, buyer_id)

    if duplicate:
        answer_callback(
            callback["id"],
            "درخواست خرید قبلی تو برای این آگهی ثبت شده است.",
            True,
        )
        return

    buyer_name = " ".join(filter(None, [
        buyer.get("first_name"),
        buyer.get("last_name"),
    ])) or "ثبت‌نشده"

    purchase = insert_purchase_request({
        "listing_id": listing_id,
        "buyer_telegram_id": buyer_id,
        "buyer_name": buyer_name,
        "message": (
            f"username: @{buyer.get('username')}"
            if buyer.get("username") else None
        ),
        "status": "pending",
    })

    request_id = purchase["id"]

    admin_text = (
        "🛒 <b>درخواست خرید جدید</b>\n\n"
        f"🆔 درخواست: <code>{request_id}</code>\n"
        f"📦 آگهی: <code>{listing_id}</code>\n"
        f"💎 کالا: {safe(listing.get('title'))}\n"
        f"💰 قیمت: {safe(listing.get('price'))}\n"
        f"👤 خریدار: {safe(buyer_name)}\n"
        f"🔗 نام کاربری: @{safe(buyer.get('username') or 'ندارد')}\n"
        f"🆔 شناسه تلگرام: <code>{buyer_id}</code>\n"
        f"📞 تماس فروشنده: <code>{safe(listing.get('seller_mobile'))}</code>"
    )

    keyboard = {
        "inline_keyboard": [[{
            "text": "📨 ارسال درخواست و اطلاعات به فروشنده",
            "callback_data": f"forward:{request_id}",
        }]]
    }

    send_message(ADMIN_ID, admin_text, keyboard)

    answer_callback(
        callback["id"],
        "درخواست خرید برای پشتیبانی زرین ماه ثبت شد.",
    )


def handle_forward(callback, request_id):
    purchase = get_purchase_request(request_id)

    if not purchase or purchase.get("status") != "pending":
        answer_callback(
            callback["id"],
            "این درخواست قبلاً پردازش شده یا وجود ندارد.",
            True,
        )
        return

    listing = get_listing(purchase["listing_id"])

    if not listing or listing.get("status") != "published":
        answer_callback(
            callback["id"],
            "آگهی دیگر منتشرشده نیست.",
            True,
        )
        return

    buyer_telegram_id = purchase.get("buyer_telegram_id")
    buyer_mobile = purchase.get("buyer_mobile")
    seller_mobile = listing.get("seller_mobile")
    seller_telegram_id = listing.get("owner_telegram_id")

    # اطلاعات محرمانه فقط برای مدیر نمایش داده می‌شود.
    if not buyer_telegram_id or buyer_mobile:
        send_message(
            ADMIN_ID,
            "📨 <b>اطلاعات درخواست خرید سایت</b>\n\n"
            f"🆔 درخواست: <code>{request_id}</code>\n"
            f"📦 آگهی: <code>{listing['id']}</code>\n"
            f"💎 کالا: {safe(listing.get('title'))}\n\n"
            f"👤 خریدار: {safe(purchase.get('buyer_name'))}\n"
            f"📞 شماره خریدار: <code>{safe(buyer_mobile)}</code>\n\n"
            f"👤 فروشنده: {safe(listing.get('seller_name'))}\n"
            f"📞 شماره فروشنده: <code>{safe(seller_mobile)}</code>\n\n"
            "🔒 این اطلاعات محرمانه است و فقط برای هماهنگی معامله است.",
        )

        update_purchase_request(
            request_id,
            {"status": "forwarded"},
        )

        answer_callback(
            callback["id"],
            "اطلاعات درخواست برای هماهنگی در اختیار مدیر قرار گرفت.",
        )
        return

    if not seller_telegram_id:
        send_message(
            ADMIN_ID,
            "📨 <b>اطلاعات درخواست خرید</b>\n\n"
            f"درخواست: <code>{request_id}</code>\n"
            f"آگهی: <code>{listing['id']}</code>\n"
            f"خریدار: {safe(purchase.get('buyer_name'))}\n"
            f"شماره فروشنده: <code>{safe(seller_mobile)}</code>\n\n"
            "فروشنده حساب تلگرام متصل‌شده ندارد؛ "
            "هماهنگی از طریق شماره ثبت‌شده انجام شود.",
        )

        update_purchase_request(
            request_id,
            {"status": "forwarded"},
        )

        answer_callback(
            callback["id"],
            "اطلاعات برای هماهنگی در اختیار مدیر قرار گرفت.",
        )
        return

    seller_text = (
        "📨 <b>درخواست خرید برای آگهی شما</b>\n\n"
        f"📦 آگهی شماره: <code>{listing['id']}</code>\n"
        f"💎 کالا: {safe(listing.get('title'))}\n"
        f"💰 قیمت: {safe(listing.get('price'))}\n\n"
        f"👤 نام خریدار: {safe(purchase.get('buyer_name'))}\n"
        f"🆔 شناسه تلگرام خریدار: "
        f"<code>{safe(buyer_telegram_id)}</code>\n\n"
        "برای هماهنگی معامله از مسیر پشتیبانی زرین ماه اقدام کن."
    )

    try:
        send_message(seller_telegram_id, seller_text)
    except Exception:
        answer_callback(
            callback["id"],
            "ارسال به فروشنده ناموفق بود؛ احتمالاً ربات را Start نکرده است.",
            True,
        )
        return

    update_purchase_request(
        request_id,
        {"status": "forwarded"},
    )

    answer_callback(
        callback["id"],
        "درخواست برای فروشنده ارسال شد.",
    )


# =========================================================
# callbackهای مدیریتی و خرید
# =========================================================

def handle_callback(callback):
    user_id = callback["from"]["id"]
    data = callback.get("data", "")
    is_admin = user_id == ADMIN_ID

    if data.startswith("buy:"):
        try:
            listing_id = int(data.split(":", 1)[1])
        except ValueError:
            answer_callback(callback["id"], "درخواست نامعتبر است.", True)
            return

        handle_buy(callback, listing_id)
        return

    if data.startswith("forward:"):
        if not is_admin:
            answer_callback(
                callback["id"],
                "این عملیات فقط برای مدیر مجاز است.",
                True,
            )
            return

        try:
            request_id = int(data.split(":", 1)[1])
        except ValueError:
            answer_callback(callback["id"], "درخواست نامعتبر است.", True)
            return

        handle_forward(callback, request_id)
        return

    if not is_admin:
        answer_callback(
            callback["id"],
            "این عملیات فقط برای مدیر مجاز است.",
            True,
        )
        return

    try:
        action, raw_id = data.split(":", 1)
        listing_id = int(raw_id)
    except (ValueError, TypeError):
        answer_callback(callback["id"], "درخواست نامعتبر است.", True)
        return

    listing = get_listing(listing_id)

    if not listing:
        answer_callback(
            callback["id"],
            "آگهی پیدا نشد یا حذف شده است.",
            True,
        )
        return

    if action == "approve":
        if listing.get("status") != "pending":
            answer_callback(
                callback["id"],
                "این آگهی قبلاً بررسی شده است.",
                True,
            )
            return

        # ابتدا وضعیت در دیتابیس ثبت می‌شود تا در صورت خطای ارسال
        # بتوان وضعیت انتشار را تشخیص داد.
        update_listing(
            listing_id,
            {"status": "publishing"},
        )

        listing["status"] = "publishing"

        try:
            publish_listing(listing)
        except Exception:
            # وضعیت publishing باقی می‌ماند تا مدیر بتواند خطا را پیگیری کند.
            answer_callback(
                callback["id"],
                "ثبت تأیید انجام شد اما انتشار تلگرام ناموفق بود.",
                True,
            )
            send_message(
                ADMIN_ID,
                f"⚠️ آگهی {listing_id} تأیید شد اما انتشار تلگرام خطا داشت. "
                "وضعیت آن publishing است.",
            )
            return

        answer_callback(callback["id"], "آگهی منتشر شد.")

        sold_keyboard = {
            "inline_keyboard": [[{
                "text": "✅ فروش رفت",
                "callback_data": f"sold:{listing_id}",
            }]]
        }

        send_message(
            ADMIN_ID,
            "📣 آگهی منتشر شد.\n"
            f"🆔 شماره آگهی: <code>{listing_id}</code>\n"
            f"💎 {safe(listing.get('title'))}\n"
            f"📍 {safe(listing.get('province'))}، "
            f"{safe(listing.get('city'))}\n\n"
            "اگر کالا فروش رفت، دکمه زیر را بزن:",
            sold_keyboard,
        )

        if listing.get("owner_telegram_id"):
            try:
                send_message(
                    listing["owner_telegram_id"],
                    "✅ آگهی شما پس از بررسی در موضوع خریدوفروش گروه منتشر شد.",
                )
            except Exception:
                pass

        return

    if action == "reject":
        if listing.get("status") != "pending":
            answer_callback(
                callback["id"],
                "این آگهی قبلاً بررسی شده است.",
                True,
            )
            return

        update_listing(
            listing_id,
            {"status": "rejected"},
        )

        answer_callback(callback["id"], "آگهی رد شد.")

        if listing.get("owner_telegram_id"):
            try:
                send_message(
                    listing["owner_telegram_id"],
                    "متأسفانه آگهی شما تأیید نشد. "
                    "برای ارسال دوباره، یک آگهی جدید ثبت کن.",
                )
            except Exception:
                pass

        send_message(
            ADMIN_ID,
            f"❌ آگهی شماره {listing_id} رد شد.",
        )
        return

    if action == "sold":
        if listing.get("status") != "published":
            answer_callback(
                callback["id"],
                "فقط آگهی منتشرشده را می‌توان فروش‌رفته کرد.",
                True,
            )
            return

        mark_listing_sold(listing)

        answer_callback(
            callback["id"],
            "آگهی به‌عنوان فروش‌رفته ثبت شد.",
        )

        send_message(
            ADMIN_ID,
            f"✅ آگهی شماره {listing_id} فروش‌رفته ثبت شد.",
        )
        return

    answer_callback(
        callback["id"],
        "عملیات ناشناخته است.",
        True,
    )


# =========================================================
# وب‌هوک
# =========================================================

class handler(BaseHTTPRequestHandler):

    def respond(self, status, message):
        body = json.dumps({
            "ok": status == 200,
            "message": message,
        }).encode("utf-8")

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.respond(
            200,
            "Seller webhook endpoint is running.",
        )

    def do_POST(self):
        received_secret = self.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            "",
        )

        if not hmac.compare_digest(
            received_secret,
            WEBHOOK_SECRET,
        ):
            self.respond(403, "Forbidden")
            return

        try:
            length = int(
                self.headers.get("Content-Length", "0")
            )

            if length <= 0 or length > 1_000_000:
                self.respond(400, "Invalid request size")
                return

            raw_body = self.rfile.read(length)
            update = json.loads(raw_body)

            if "update_id" not in update:
                self.respond(400, "Missing update ID")
                return

            state, sha = load_state()
            update_id = int(update["update_id"])

            if update_id <= int(state.get("last_update_id", -1)):
                self.respond(200, "Duplicate update ignored")
                return

            if "callback_query" in update:
                handle_callback(update["callback_query"])

            elif "message" in update:
                handle_message(
                    state,
                    update["message"],
                )

            state["last_update_id"] = update_id
            save_state(state, sha)

            self.respond(200, "OK")

        except Exception:
            try:
                self.respond(
                    500,
                    "Temporary processing error",
                )
            except Exception:
                pass

    def log_message(self, format, *args):
        return
