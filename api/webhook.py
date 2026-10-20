import os
import json
import base64
import html
import hmac
import re
import requests

from http.server import BaseHTTPRequestHandler


# =========================================================
# تنظیمات
# =========================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATA_TOKEN = os.environ["SELLER_DATA_TOKEN"]
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]

DATA_REPO = "Booknight20/zarinmah-seller-data"
CHANNEL = "@ZarinMahGold"
GROUP = os.getenv(
    "TELEGRAM_GROUP",
    "@ZarinMahCommunity",
).strip()

GROUP_THREAD_ID = int(
    os.getenv("TELEGRAM_GROUP_THREAD_ID", "17")
)

ADMIN_ID = 8816884849

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GH_API = (
    f"https://api.github.com/repos/"
    f"{DATA_REPO}/contents/state.json"
)

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {DATA_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})


# =========================================================
# قوانین
# =========================================================

RULES_TEXT = (
    "📋 <b>قوانین ثبت آگهی زرین ماه</b>\n\n"
    "🔹 اطلاعات کالا، وزن، عیار و قیمت باید دقیق باشد.\n\n"
    "🔹 فروشنده باید مالک کالا یا مجاز به فروش آن باشد.\n\n"
    "🔹 داشتن فاکتور فروش معتبر الزامی است.\n\n"
    "🔹 آگهی‌ها پیش از انتشار توسط مدیر بررسی می‌شوند.\n\n"
    "🔹 قیمت‌های مبتنی بر تابلو، بر اساس مرجع قیمت مورد توافق "
    "خریدار و فروشنده هستند و لزوماً قیمت لحظه‌ای تأییدشده نیستند.\n\n"
    "🔹 بررسی اصالت کالا و شرایط معامله بر عهده خریدار و فروشنده است.\n\n"
    "🔹 درج اطلاعات خلاف واقع ممنوع است.\n\n"
    "با پذیرش قوانین می‌توانید ثبت آگهی را آغاز کنید."
)


# =========================================================
# انواع کالا
# =========================================================

PRODUCT_TYPES = {
    "ring": "💍 انگشتر",
    "earrings": "✨ گوشواره",
    "chain": "📿 زنجیر و گردنبند",
    "bracelet": "🔗 دستبند",
    "bangle": "⭕ النگو",
    "pendant": "🌟 پلاک و آویز",
    "anklet": "🔸 پابند",
    "coin": "🪙 سکه",
    "melted_gold": "🟡 طلای آب‌شده",
    "other": "📦 سایر موارد",
}


JEWELRY_TYPES = {
    "ring",
    "earrings",
    "chain",
    "bracelet",
    "bangle",
    "pendant",
    "anklet",
}


# =========================================================
# انواع سکه
# =========================================================

COIN_TYPES = {
    "emami": "سکه تمام امامی (طرح جدید)",
    "bahar_azadi": "سکه تمام بهار آزادی (طرح قدیم)",
    "half": "نیم‌سکه",
    "quarter": "ربع‌سکه",
    "gram": "سکه یک‌گرمی بانکی",
    "parsian": "سکه پارسیان",
    "other_coin": "سایر سکه‌ها",
}


# سکه‌های استاندارد که در این ربات وزن آن‌ها پرسیده نمی‌شود.
STANDARD_COIN_TYPES = {
    "emami",
    "bahar_azadi",
    "half",
    "quarter",
    "gram",
}


# =========================================================
# عیار
# =========================================================

PURITY_OPTIONS = {
    "18": "۱۸ عیار",
    "21": "۲۱ عیار",
    "24": "۲۴ عیار",
    "other": "سایر عیارها",
    "unknown": "عیار نامشخص",
}


def needs_purity(product_type_key):
    return (
        product_type_key in JEWELRY_TYPES
        or product_type_key == "melted_gold"
    )


def purity_keyboard():
    rows = [
        [
            {
                "text": "۱۸ عیار",
                "callback_data": "purity:18",
            },
            {
                "text": "۲۱ عیار",
                "callback_data": "purity:21",
            },
        ],
        [
            {
                "text": "۲۴ عیار",
                "callback_data": "purity:24",
            },
            {
                "text": "سایر عیارها",
                "callback_data": "purity:other",
            },
        ],
        [
            {
                "text": "عیار نامشخص",
                "callback_data": "purity:unknown",
            },
        ],
    ]

    return {"inline_keyboard": rows}


def show_purity(chat_id, draft):
    product_type = html.escape(
        draft.get("product_type", "")
    )

    send_message(
        chat_id,
        "🔬 <b>انتخاب عیار</b>\n\n"
        f"نوع کالا: {product_type}\n\n"
        "لطفاً عیار درج‌شده روی کالا یا عیار تأییدشده آن را انتخاب کن.\n"
        "اگر عیار را نمی‌دانی، گزینه «عیار نامشخص» را انتخاب کن.",
        keyboard=purity_keyboard(),
    )


# =========================================================
# ارتباط با تلگرام
# =========================================================

def telegram(method, data=None):
    response = requests.post(
        f"{TG_API}/{method}",
        json=data or {},
        timeout=20,
    )

    response.raise_for_status()
    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error in {method}"
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
    }

    if keyboard:
        data["reply_markup"] = keyboard

    if message_thread_id is not None:
        data["message_thread_id"] = message_thread_id

    return telegram("sendMessage", data)


def answer_callback(callback_id, text="", alert=False):
    return telegram(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text[:180],
            "show_alert": alert,
        },
    )


def show_rules(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{
                "text": "✅ پذیرش قوانین و ادامه",
                "callback_data": "terms_accept",
            }],
            [{
                "text": "❌ انصراف از ثبت آگهی",
                "callback_data": "terms_cancel",
            }],
        ]
    }

    send_message(
        chat_id,
        RULES_TEXT,
        keyboard=keyboard,
    )


# =========================================================
# انتخاب نوع کالا
# =========================================================

def product_type_keyboard():
    items = list(PRODUCT_TYPES.items())
    rows = []

    for index in range(0, len(items), 2):
        row = []

        for key, label in items[index:index + 2]:
            row.append({
                "text": label,
                "callback_data": f"type:{key}",
            })

        rows.append(row)

    rows.append([{
        "text": "❌ انصراف",
        "callback_data": "terms_cancel",
    }])

    return {"inline_keyboard": rows}


def show_product_types(chat_id):
    send_message(
        chat_id,
        "💎 <b>انتخاب نوع کالا</b>\n\n"
        "نوع کالایی را که قصد فروش آن را داری انتخاب کن.\n"
        "هر آگهی فقط برای یک کالا ثبت می‌شود.\n\n"
        "عنوان آگهی بر اساس مشخصات کالا به‌صورت خودکار ساخته می‌شود.\n"
        "اگر سکه انتخاب کنی، نوع سکه نیز مشخص خواهد شد.",
        keyboard=product_type_keyboard(),
    )


# =========================================================
# انتخاب نوع سکه
# =========================================================

def coin_type_keyboard():
    rows = []

    for key, label in COIN_TYPES.items():
        rows.append([{
            "text": label,
            "callback_data": f"coin:{key}",
        }])

    rows.append([{
        "text": "❌ انصراف",
        "callback_data": "terms_cancel",
    }])

    return {"inline_keyboard": rows}


def show_coin_types(chat_id):
    send_message(
        chat_id,
        "🪙 <b>انتخاب نوع سکه</b>\n\n"
        "نوع سکه موردنظر را انتخاب کن.\n\n"
        "🔸 برای سکه‌های استاندارد، وزن جداگانه پرسیده نمی‌شود.\n"
        "🔸 برای سکه پارسیان، وزن بر حسب سوت دریافت می‌شود.\n"
        "🔸 برای سایر سکه‌ها، وزن بر حسب گرم دریافت می‌شود.",
        keyboard=coin_type_keyboard(),
    )


# =========================================================
# روش‌های قیمت‌گذاری
# =========================================================

PRICE_METHODS = {
    "fixed": "قیمت ثابت",
    "below_board": "درصد زیر قیمت تابلو",
    "above_board": "مبلغ بالاتر از تابلو",
    "board": "به قیمت تابلو",
    "negotiable": "قیمت توافقی",
}


def price_method_keyboard():
    rows = [
        [
            {
                "text": "💵 قیمت ثابت",
                "callback_data": "price_method:fixed",
            },
        ],
        [
            {
                "text": "📉 درصد زیر قیمت تابلو",
                "callback_data": "price_method:below_board",
            },
        ],
        [
            {
                "text": "📈 مبلغ بالاتر از تابلو",
                "callback_data": "price_method:above_board",
            },
        ],
        [
            {
                "text": "📊 به قیمت تابلو",
                "callback_data": "price_method:board",
            },
        ],
        [
            {
                "text": "🤝 قیمت توافقی",
                "callback_data": "price_method:negotiable",
            },
        ],
    ]

    return {"inline_keyboard": rows}


def show_price_methods(chat_id):
    send_message(
        chat_id,
        "💰 <b>روش قیمت‌گذاری</b>\n\n"
        "یکی از روش‌های زیر را انتخاب کن:\n\n"
        "💵 <b>قیمت ثابت:</b> مبلغ نهایی کل کالا را وارد می‌کنی.\n\n"
        "📉 <b>درصد زیر تابلو:</b> درصد کمتر از قیمت مرجع را مشخص می‌کنی.\n\n"
        "📈 <b>مبلغ بالاتر از تابلو:</b> مبلغ اضافه بر قیمت مرجع را وارد می‌کنی.\n\n"
        "📊 <b>به قیمت تابلو:</b> فروش بر مبنای قیمت مرجع مورد توافق.\n\n"
        "🤝 <b>قیمت توافقی:</b> مبلغ نهایی با خریدار مذاکره می‌شود.\n\n"
        "⚠️ قیمت تابلو در این ربات به‌صورت خودکار محاسبه نمی‌شود. "
        "در روش‌های مبتنی بر تابلو، مرجع قیمت باید برای طرفین روشن باشد.",
        keyboard=price_method_keyboard(),
    )


def price_method_description(method):
    descriptions = {
        "fixed": "قیمت نهایی کل کالا به تومان",
        "below_board": "درصد تخفیف نسبت به قیمت تابلو",
        "above_board": "مبلغ اضافه بر قیمت تابلو به تومان",
        "board": "بر اساس قیمت تابلو",
        "negotiable": "توافق با خریدار",
    }

    return descriptions.get(method, "ثبت نشده")


def format_number(value):
    try:
        number = float(value)

        if number.is_integer():
            return f"{int(number):,}"

        return f"{number:,.4f}".rstrip("0").rstrip(".")

    except (ValueError, TypeError):
        return str(value)


def price_display(listing):
    method = listing.get("price_method")

    # سازگاری با آگهی‌های قدیمی
    if not method:
        return str(listing.get("price", "ثبت نشده"))

    value = listing.get("price_value")

    if method == "fixed":
        return f"{format_number(value)} تومان (قیمت ثابت)"

    if method == "below_board":
        return f"{format_number(value)}٪ زیر قیمت تابلو"

    if method == "above_board":
        return f"{format_number(value)} تومان بالاتر از تابلو"

    if method == "board":
        return "به قیمت تابلو"

    if method == "negotiable":
        return "قیمت توافقی"

    return str(listing.get("price", "ثبت نشده"))


def ask_price_value(chat_id, user):
    draft = user.get("draft") or {}
    method = draft.get("price_method")

    user["flow"] = "price_value"

    if method == "fixed":
        prompt = (
            "💵 <b>قیمت ثابت</b>\n\n"
            "قیمت نهایی کل کالا را به تومان وارد کن.\n"
            "مثال: ۱۵۰۰۰۰۰۰"
        )

    elif method == "below_board":
        prompt = (
            "📉 <b>درصد زیر قیمت تابلو</b>\n\n"
            "چند درصد پایین‌تر از قیمت مرجع می‌فروشی؟\n"
            "فقط عدد درصد را وارد کن.\n"
            "مثال: ۲ یا ۲٫۵\n\n"
            "درصد باید بیشتر از صفر و کمتر از ۱۰۰ باشد."
        )

    elif method == "above_board":
        prompt = (
            "📈 <b>مبلغ بالاتر از تابلو</b>\n\n"
            "چه مبلغی به قیمت مرجع اضافه می‌کنی؟\n"
            "مبلغ اضافه را به تومان وارد کن.\n"
            "مثال: ۵۰۰۰۰۰"
        )

    else:
        return

    send_message(chat_id, prompt)


# =========================================================
# دکمه انتشار ثبت آگهی در گروه
# =========================================================

def send_registration_button_to_group():
    keyboard = {
        "inline_keyboard": [[{
            "text": "🟡 ثبت آگهی با ربات",
            "url": "https://t.me/ZarinMahGoldBot?start=sell",
        }]]
    }

    message = send_message(
        GROUP,
        "📣 <b>آگهی کن، دیده شو، بفروش</b> 📣",
        keyboard=keyboard,
        message_thread_id=GROUP_THREAD_ID,
    )

    telegram("pinChatMessage", {
        "chat_id": GROUP,
        "message_id": message["message_id"],
        "disable_notification": True,
    })

    return message


# =========================================================
# منوی اصلی
# =========================================================

def main_keyboard(is_admin=False):
    rows = [
        [{"text": "ثبت آگهی فروش"}],
        [{"text": "📜 قوانین و مقررات"}],
        [{"text": "راهنمای ثبت آگهی"}],
    ]

    if is_admin:
        rows.insert(2, [{"text": "ثبت محصول مدیر"}])
        rows.insert(3, [{"text": "آگهی‌های در انتظار"}])
        rows.insert(
            4,
            [{"text": "📌 ارسال دکمه ثبت آگهی به گروه"}],
        )

    return {
        "keyboard": rows,
        "resize_keyboard": True,
    }


# =========================================================
# ذخیره و بازیابی اطلاعات در GitHub
# =========================================================

def load_state():
    response = session.get(GH_API, timeout=20)

    if response.status_code == 404:
        return {
            "last_update_id": -1,
            "users": {},
            "listings": [],
            "next_id": 1,
        }, None

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
    state.setdefault("listings", [])
    state.setdefault("next_id", 1)

    existing_ids = [
        int(listing.get("id", 0))
        for listing in state["listings"]
    ]

    if existing_ids:
        state["next_id"] = max(
            int(state["next_id"]),
            max(existing_ids) + 1,
        )

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
        "message": "Update seller bot state",
        "content": content,
    }

    if sha:
        payload["sha"] = sha

    response = session.put(
        GH_API,
        json=payload,
        timeout=20,
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
# ابزارهای وزن
# =========================================================

def weight_unit(listing):
    if listing.get("coin_type_key") == "parsian":
        return "سوت"

    return listing.get("weight_unit") or "گرم"


def formatted_weight(listing):
    weight = listing.get("weight")

    if weight in (None, "", "ثبت نشده"):
        return "ثبت نشده"

    return (
        f"{html.escape(str(weight))} "
        f"{html.escape(weight_unit(listing))}"
    )


def requires_weight(draft):
    if draft.get("product_type_key") != "coin":
        return True

    coin_key = draft.get("coin_type_key")

    if coin_key == "parsian":
        return True

    if coin_key == "other_coin":
        return True

    return coin_key not in STANDARD_COIN_TYPES


def normalize_number(value):
    translation = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬",
        "01234567890123456789..",
    )

    value = str(value).translate(translation)
    value = value.replace(",", ".").strip()

    return value


def parse_positive_number(value):
    normalized = normalize_number(value)

    if not re.fullmatch(r"\d+(?:\.\d{1,4})?", normalized):
        return None

    try:
        number = float(normalized)
    except ValueError:
        return None

    if number <= 0:
        return None

    return number


# =========================================================
# عنوان خودکار کالا
# =========================================================

def generate_title(draft):
    product_key = draft.get("product_type_key")

    if product_key == "coin":
        title = draft.get("coin_type") or "سکه"

        if draft.get("coin_type_key") == "parsian":
            weight = draft.get("weight")

            if weight:
                title += f" {weight} سوت"

        elif draft.get("coin_type_key") == "other_coin":
            weight = draft.get("weight")

            if weight:
                title += f" {weight} گرم"

        return title

    if product_key == "melted_gold":
        title = "طلای آب‌شده"

    elif product_key in JEWELRY_TYPES:
        label = PRODUCT_TYPES.get(product_key, "طلا")
        title = label.split(" ", 1)[-1]
        title += " طلا"

    else:
        title = PRODUCT_TYPES.get(
            product_key,
            "سایر موارد",
        ).split(" ", 1)[-1]

    purity_key = draft.get("purity_key")

    if purity_key in ("18", "21", "24"):
        title += f" {purity_key} عیار"

    elif purity_key == "other":
        title += " با عیار دیگر"

    elif purity_key == "unknown":
        title += " با عیار نامشخص"

    return title[:150]


# =========================================================
# نمایش نوع کالا در آگهی
# =========================================================

def get_product_type(listing):
    product_type = listing.get(
        "product_type",
        "📦 سایر موارد",
    )

    coin_type = listing.get("coin_type")

    if coin_type:
        return f"{product_type} — {coin_type}"

    purity = listing.get("purity")

    if purity:
        return f"{product_type} — {purity}"

    return product_type


# =========================================================
# ساخت متن آگهی
# =========================================================

def build_listing_caption(listing, sold=False):
    title = html.escape(
        str(listing.get("title") or generate_title(listing))
    )

    description = html.escape(
        str(listing.get("description") or "بدون توضیحات")
    )

    contact = html.escape(
        str(listing.get("contact", "ثبت نشده"))
    )

    city = html.escape(
        str(listing.get("city", "ثبت نشده"))
    )

    product_type = html.escape(
        get_product_type(listing)
    )

    weight = formatted_weight(listing)
    price = html.escape(price_display(listing))

    if listing.get("kind") == "admin":
        label = "محصول مدیر زرین ماه"
    else:
        label = "آگهی فروش کاربر"

    caption = (
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        f"🏷 <b>{label}</b>\n"
        f"💎 <b>نوع کالا:</b> {product_type}\n"
        f"📝 <b>{title}</b>\n\n"
        f"📋 <b>توضیحات:</b> {description}\n"
        f"⚖️ <b>وزن:</b> {weight}\n"
        f"💰 <b>روش قیمت‌گذاری:</b> {price}\n"
        f"📍 <b>شهر:</b> {city}\n"
        f"📞 <b>راه ارتباطی:</b> {contact}\n\n"
        "#زرین_ماه #خرید_و_فروش_طلا"
    )

    if sold or listing.get("status") == "sold":
        caption += "\n\n✅ <b>فروخته شد</b>"

    return caption


# =========================================================
# ارسال آگهی به مقصد
# =========================================================

def send_listing_to_destination(
    chat_id,
    caption,
    photo=None,
    thread_id=None,
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

        message = telegram("sendPhoto", data)

    else:
        message = send_message(
            chat_id,
            caption,
            message_thread_id=thread_id,
        )

    return {
        "chat_id": message["chat"]["id"],
        "message_id": message["message_id"],
        "has_photo": bool(photo),
    }


def publish_listing(listing):
    caption = build_listing_caption(listing)
    photo = listing.get("photo")
    published_messages = []

    # محصول مدیر در کانال و گروه منتشر می‌شود.
    # آگهی کاربر پس از تأیید فقط در گروه منتشر می‌شود.
    if listing.get("kind") == "admin":
        published_messages.append(
            send_listing_to_destination(
                CHANNEL,
                caption,
                photo=photo,
            )
        )

    published_messages.append(
        send_listing_to_destination(
            GROUP,
            caption,
            photo=photo,
            thread_id=GROUP_THREAD_ID,
        )
    )

    listing["published_messages"] = published_messages
    return published_messages


def mark_listing_sold(listing):
    text = build_listing_caption(listing, sold=True)

    for posted in listing.get("published_messages", []):
        chat_id = posted.get("chat_id")
        message_id = posted.get("message_id")

        if not chat_id or not message_id:
            continue

        if posted.get("has_photo"):
            telegram("editMessageCaption", {
                "chat_id": chat_id,
                "message_id": message_id,
                "caption": text[:1000],
                "parse_mode": "HTML",
            })

        else:
            telegram("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
            })


# =========================================================
# مراحل فرم ثبت آگهی
# =========================================================

def start_listing(chat_id, user, kind):
    user["flow"] = "type"
    user["draft"] = {
        "kind": kind,
        "photo": None,
    }

    show_product_types(chat_id)


def start_weight_or_description(chat_id, user):
    draft = user.get("draft") or {}

    if requires_weight(draft):
        user["flow"] = "weight"

        if draft.get("coin_type_key") == "parsian":
            send_message(
                chat_id,
                "⚖️ <b>وزن سکه پارسیان</b>\n\n"
                "وزن را بر حسب سوت وارد کن.\n"
                "مثال: ۵۰۰، ۷۵۰، ۱۰۰۰ یا ۱۵۰۰\n\n"
                "هر ۱۰۰۰ سوت برابر با ۱ گرم است.\n"
                "وزن باید عدد صحیح باشد.",
            )

        else:
            send_message(
                chat_id,
                "⚖️ <b>وزن کالا</b>\n\n"
                "وزن را بر حسب گرم وارد کن.\n"
                "مثال: ۲٫۵ یا ۳٫۷۵۰\n\n"
                "برای سکه‌های استاندارد وزن پرسیده نمی‌شود.",
            )

    else:
        start_description_step(chat_id, user)


def start_description_step(chat_id, user):
    user["flow"] = "description"

    keyboard = {
        "inline_keyboard": [[{
            "text": "بدون توضیحات",
            "callback_data": "skip:description",
        }]]
    }

    send_message(
        chat_id,
        "📋 <b>توضیحات تکمیلی</b>\n\n"
        "اگر کالا ایراد، خط‌وخش، فاکتور یا نکته خاصی دارد، "
        "اینجا بنویس.\n\n"
        "در صورت نداشتن توضیحات، دکمه زیر را بزن.",
        keyboard=keyboard,
    )


def start_contact_step(chat_id, user):
    user["flow"] = "contact"

    send_message(
        chat_id,
        "📞 <b>راه ارتباطی فروشنده</b>\n\n"
        "آیدی تلگرام یا شماره تماس قابل استفاده را وارد کن.",
    )


def start_city_step(chat_id, user):
    user["flow"] = "city"

    send_message(
        chat_id,
        "📍 <b>شهر محل فروش</b>\n\n"
        "نام شهر را وارد کن.\n"
        "مثال: بروجرد، تهران یا اصفهان.",
    )


def start_photo_step(chat_id, user):
    user["flow"] = "photo"

    keyboard = {
        "inline_keyboard": [[{
            "text": "بدون عکس",
            "callback_data": "skip:photo",
        }]]
    }

    send_message(
        chat_id,
        "📷 <b>عکس محصول</b>\n\n"
        "اگر امکان دارد، عکس واضح کالا را ارسال کن.\n"
        "عکس خوب به جلب اعتماد خریدار کمک می‌کند.\n\n"
        "اگر عکس نداری، دکمه «بدون عکس» را بزن.",
        keyboard=keyboard,
    )


def start_listing_after_type(chat_id, user, product_type):
    draft = user.get("draft")

    if not draft or user.get("flow") != "type":
        send_message(
            chat_id,
            "این مرحله منقضی شده است. "
            "لطفاً ثبت آگهی را دوباره آغاز کن.",
        )
        return

    if product_type not in PRODUCT_TYPES:
        send_message(chat_id, "نوع کالا معتبر نیست.")
        return

    draft["product_type_key"] = product_type
    draft["product_type"] = PRODUCT_TYPES[product_type]

    # اطلاعات وابسته به نوع کالای قبلی پاک می‌شود.
    for key in (
        "coin_type_key",
        "coin_type",
        "weight",
        "weight_unit",
        "purity_key",
        "purity",
    ):
        draft.pop(key, None)

    if product_type == "coin":
        user["flow"] = "coin_type"
        show_coin_types(chat_id)
        return

    if needs_purity(product_type):
        user["flow"] = "purity"
        show_purity(chat_id, draft)
        return

    start_weight_or_description(chat_id, user)


def start_listing_after_coin(chat_id, user, coin_key):
    draft = user.get("draft")

    if not draft or user.get("flow") != "coin_type":
        send_message(
            chat_id,
            "این مرحله منقضی شده است. "
            "لطفاً ثبت آگهی را دوباره آغاز کن.",
        )
        return

    if coin_key not in COIN_TYPES:
        send_message(chat_id, "نوع سکه معتبر نیست.")
        return

    draft["coin_type_key"] = coin_key
    draft["coin_type"] = COIN_TYPES[coin_key]

    draft.pop("weight", None)

    if coin_key == "parsian":
        draft["weight_unit"] = "سوت"

    else:
        draft["weight_unit"] = "گرم"

    start_weight_or_description(chat_id, user)


# =========================================================
# ادامه فرم پس از ویرایش
# =========================================================

def finish_edit_or_continue(chat_id, user):
    draft = user.get("draft") or {}

    if draft.get("editing"):
        draft.pop("editing", None)
        user["flow"] = "preview"
        show_preview(chat_id, user)
        return

    start_contact_step(chat_id, user)


# =========================================================
# خلاصه نهایی آگهی
# =========================================================

def build_preview_text(draft):
    title = html.escape(generate_title(draft))
    product_type = html.escape(get_product_type(draft))
    description = html.escape(
        str(draft.get("description") or "بدون توضیحات")
    )
    city = html.escape(str(draft.get("city", "ثبت نشده")))
    contact = html.escape(str(draft.get("contact", "ثبت نشده")))
    weight = formatted_weight(draft)
    price = html.escape(price_display(draft))

    photo_status = (
        "عکس دریافت شده"
        if draft.get("photo")
        else "بدون عکس"
    )

    return (
        "🧾 <b>پیش‌نمایش آگهی زرین ماه</b>\n\n"
        "لطفاً اطلاعات را با دقت بررسی کن.\n\n"
        f"💎 <b>نوع کالا:</b> {product_type}\n"
        f"📝 <b>عنوان خودکار:</b> {title}\n"
        f"⚖️ <b>وزن:</b> {weight}\n"
        f"📋 <b>توضیحات:</b> {description}\n"
        f"💰 <b>روش قیمت‌گذاری:</b> {price}\n"
        f"📍 <b>شهر:</b> {city}\n"
        f"📞 <b>راه ارتباطی:</b> {contact}\n"
        f"📷 <b>تصویر:</b> {photo_status}\n\n"
        "اگر اطلاعات درست است، دکمه تأیید را بزن.\n"
        "در غیر این صورت، بخش موردنظر را اصلاح کن."
    )


def preview_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "💎 اصلاح نوع کالا",
                    "callback_data": "edit:product",
                },
                {
                    "text": "⚖️ اصلاح وزن",
                    "callback_data": "edit:weight",
                },
            ],
            [
                {
                    "text": "📋 اصلاح توضیحات",
                    "callback_data": "edit:description",
                },
                {
                    "text": "💰 اصلاح قیمت",
                    "callback_data": "edit:price",
                },
            ],
            [
                {
                    "text": "📍 اصلاح شهر",
                    "callback_data": "edit:city",
                },
                {
                    "text": "📞 اصلاح ارتباط",
                    "callback_data": "edit:contact",
                },
            ],
            [
                {
                    "text": "📷 اصلاح عکس",
                    "callback_data": "edit:photo",
                },
            ],
            [
                {
                    "text": "✅ تأیید و ارسال",
                    "callback_data": "preview:confirm",
                },
            ],
            [
                {
                    "text": "❌ لغو آگهی",
                    "callback_data": "terms_cancel",
                },
            ],
        ]
    }


def show_preview(chat_id, user):
    draft = user.get("draft") or {}

    if not draft:
        send_message(chat_id, "پیش‌نویس آگهی پیدا نشد.")
        return

    draft["title"] = generate_title(draft)

    send_message(
        chat_id,
        build_preview_text(draft),
        keyboard=preview_keyboard(),
    )


# =========================================================
# ذخیره نهایی آگهی
# =========================================================

def finish_listing(state, user_id, chat_id):
    user = get_user(state, user_id)
    draft = user.get("draft")

    if not draft:
        send_message(
            chat_id,
            "پیش‌نویس آگهی پیدا نشد. دوباره شروع کن.",
        )
        return None

    listing = {
        "id": state["next_id"],
        "owner_id": user_id,
        "kind": draft.get("kind", "user"),
        "product_type_key": draft.get("product_type_key"),
        "product_type": draft.get(
            "product_type",
            "📦 سایر موارد",
        ),
        "coin_type_key": draft.get("coin_type_key"),
        "coin_type": draft.get("coin_type"),
        "purity_key": draft.get("purity_key"),
        "purity": draft.get("purity"),
        "weight_unit": draft.get("weight_unit", "گرم"),
        "title": generate_title(draft),
        "description": draft.get("description", ""),
        "weight": draft.get("weight", "ثبت نشده"),
        "price_method": draft.get("price_method"),
        "price_value": draft.get("price_value"),
        "price": price_display(draft),
        "contact": draft.get("contact", ""),
        "city": draft.get("city", "ثبت نشده"),
        "photo": draft.get("photo"),
        "status": (
            "pending"
            if draft.get("kind") == "user"
            else "publishing"
        ),
    }

    state["next_id"] += 1
    state["listings"].append(listing)

    cancel_flow(user)

    if listing["kind"] == "admin":
        send_message(
            chat_id,
            "✅ محصول مدیر ثبت شد.\n\n"
            "پس از ذخیره وضعیت، انتشار آن در کانال و "
            "موضوع خریدوفروش گروه انجام می‌شود.",
            main_keyboard(True),
        )
        return listing["id"]

    admin_text = (
        "🔔 <b>آگهی جدید برای بررسی</b>\n\n"
        f"🆔 <b>شماره آگهی:</b> {listing['id']}\n"
        f"💎 <b>نوع کالا:</b> {html.escape(get_product_type(listing))}\n"
        f"📝 <b>عنوان:</b> {html.escape(listing['title'])}\n"
        f"⚖️ <b>وزن:</b> {formatted_weight(listing)}\n"
        f"📋 <b>توضیحات:</b> "
        f"{html.escape(listing['description'] or 'بدون توضیحات')}\n"
        f"💰 <b>قیمت:</b> {html.escape(price_display(listing))}\n"
        f"📍 <b>شهر:</b> {html.escape(listing['city'])}\n"
        f"📞 <b>ارتباط:</b> {html.escape(listing['contact'])}\n\n"
        "برای انتشار، آگهی را بررسی و تأیید کن."
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

    send_message(
        chat_id,
        "✅ آگهی شما برای بررسی مدیر ارسال شد.\n\n"
        "پس از تأیید، آگهی در موضوع خریدوفروش گروه منتشر می‌شود.",
        main_keyboard(user_id == ADMIN_ID),
    )

    return None


# =========================================================
# منوی اصلاح اطلاعات
# =========================================================

def start_edit_field(chat_id, user, field):
    draft = user.get("draft") or {}

    if not draft:
        send_message(chat_id, "پیش‌نویس آگهی پیدا نشد.")
        return

    draft["editing"] = True

    if field == "product":
        # نوع کالا دوباره انتخاب می‌شود.
        draft.pop("product_type_key", None)
        draft.pop("product_type", None)
        draft.pop("coin_type_key", None)
        draft.pop("coin_type", None)
        draft.pop("purity_key", None)
        draft.pop("purity", None)
        draft.pop("weight", None)
        draft.pop("weight_unit", None)

        user["flow"] = "type"
        show_product_types(chat_id)
        return

    if field == "weight":
        if not requires_weight(draft):
            answer = (
                "برای این نوع سکه وزن جداگانه ثبت نمی‌شود."
            )
            draft.pop("editing", None)
            send_message(chat_id, answer)
            show_preview(chat_id, user)
            return

        user["flow"] = "weight"

        if draft.get("coin_type_key") == "parsian":
            send_message(
                chat_id,
                "وزن جدید سکه پارسیان را بر حسب سوت وارد کن.",
            )
        else:
            send_message(
                chat_id,
                "وزن جدید کالا را بر حسب گرم وارد کن.",
            )

        return

    if field == "description":
        user["flow"] = "description"

        keyboard = {
            "inline_keyboard": [[{
                "text": "بدون توضیحات",
                "callback_data": "skip:description",
            }]]
        }

        send_message(
            chat_id,
            "توضیحات جدید را بفرست یا «بدون توضیحات» را انتخاب کن.",
            keyboard,
        )
        return

    if field == "price":
        user["flow"] = "price_method"
        show_price_methods(chat_id)
        return

    if field == "contact":
        user["flow"] = "contact"
        send_message(chat_id, "راه ارتباطی جدید را وارد کن.")
        return

    if field == "city":
        user["flow"] = "city"
        send_message(chat_id, "نام شهر جدید را وارد کن.")
        return

    if field == "photo":
        user["flow"] = "photo"

        keyboard = {
            "inline_keyboard": [[{
                "text": "حذف عکس",
                "callback_data": "skip:photo",
            }]]
        }

        send_message(
            chat_id,
            "عکس جدید را ارسال کن.\n"
            "برای حذف عکس قبلی، دکمه زیر را بزن.",
            keyboard,
        )
        return


# =========================================================
# مدیریت پیام‌های متنی و عکس
# =========================================================

def handle_message(state, message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()

    user = get_user(state, user_id)
    is_admin = user_id == ADMIN_ID

    # -----------------------------------------------------
    # لغو
    # -----------------------------------------------------

    if text == "/cancel":
        cancel_flow(user)

        send_message(
            chat_id,
            "عملیات لغو شد.",
            main_keyboard(is_admin),
        )
        return None

    command_parts = text.split(maxsplit=1)

    command = (
        command_parts[0].split("@", 1)[0]
        if command_parts
        else ""
    )

    payload = (
        command_parts[1].strip().lower()
        if len(command_parts) > 1
        else ""
    )

    # -----------------------------------------------------
    # شروع ربات
    # -----------------------------------------------------

    if command == "/start":
        cancel_flow(user)

        if payload == "sell":
            show_rules(chat_id)
            return None

        send_message(
            chat_id,
            "سلام! به ربات فروشندگان زرین ماه خوش آمدی. 🌙\n\n"
            "برای ثبت آگهی، گزینه «ثبت آگهی فروش» را بزن.\n"
            "عنوان کالا به‌صورت خودکار ساخته می‌شود و "
            "آگهی کاربران قبل از انتشار بررسی خواهد شد.",
            main_keyboard(is_admin),
        )
        return None

    # -----------------------------------------------------
    # منوها
    # -----------------------------------------------------

    if text == "📜 قوانین و مقررات":
        show_rules(chat_id)
        return None

    if text == "راهنمای ثبت آگهی":
        send_message(
            chat_id,
            "📋 <b>راهنمای ثبت آگهی زرین ماه</b>\n\n"
            "۱. نوع کالا را انتخاب کن.\n"
            "۲. در صورت نیاز، نوع سکه یا عیار را مشخص کن.\n"
            "۳. وزن کالا را وارد کن؛ برای سکه‌های استاندارد "
            "وزن پرسیده نمی‌شود.\n"
            "۴. توضیحات تکمیلی را وارد کن یا رد کن.\n"
            "۵. یکی از پنج روش قیمت‌گذاری را انتخاب کن.\n"
            "۶. راه ارتباطی و شهر را وارد کن.\n"
            "۷. در صورت تمایل عکس بفرست.\n"
            "۸. پیش‌نمایش را بررسی و تأیید کن.\n\n"
            "آگهی کاربران پس از تأیید مدیر منتشر می‌شود.\n"
            "برای لغو هر مرحله /cancel را بفرست.",
            main_keyboard(is_admin),
        )
        return None

    if text == "ثبت آگهی فروش":
        cancel_flow(user)
        show_rules(chat_id)
        return None

    if text == "ثبت محصول مدیر" and is_admin:
        cancel_flow(user)
        start_listing(chat_id, user, "admin")
        return None

    if text == "📌 ارسال دکمه ثبت آگهی به گروه" and is_admin:
        try:
            send_registration_button_to_group()

            send_message(
                chat_id,
                "✅ دکمه ثبت آگهی در موضوع خریدوفروش گروه "
                "ارسال و سنجاق شد.",
                main_keyboard(True),
            )

        except Exception:
            send_message(
                chat_id,
                "❌ ارسال یا سنجاق پیام انجام نشد.\n\n"
                "مجوز ارسال پیام و سنجاق‌کردن پیام ربات را "
                "در گروه بررسی کن.",
                main_keyboard(True),
            )

        return None

    if text == "آگهی‌های در انتظار" and is_admin:
        pending = [
            item for item in state["listings"]
            if item.get("status") == "pending"
        ]

        if not pending:
            send_message(
                chat_id,
                "در حال حاضر آگهی در انتظاری وجود ندارد.",
                main_keyboard(True),
            )
            return None

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

            text_item = (
                f"🆔 <b>آگهی شماره {item['id']}</b>\n"
                f"نوع کالا: {html.escape(get_product_type(item))}\n"
                f"عنوان: {html.escape(item.get('title', ''))}\n"
                f"وزن: {formatted_weight(item)}\n"
                f"قیمت: {html.escape(price_display(item))}\n"
                f"شهر: {html.escape(str(item.get('city', 'ثبت نشده')))}"
            )

            send_message(chat_id, text_item, keyboard)

        return None

    # -----------------------------------------------------
    # کنترل جریان
    # -----------------------------------------------------

    flow = user.get("flow")

    if not flow:
        send_message(
            chat_id,
            "برای شروع از دکمه‌های منو استفاده کن.",
            main_keyboard(is_admin),
        )
        return None

    draft = user.get("draft")

    if not draft:
        cancel_flow(user)

        send_message(
            chat_id,
            "اطلاعات مرحله قبل پیدا نشد. "
            "لطفاً ثبت آگهی را دوباره آغاز کن.",
            main_keyboard(is_admin),
        )
        return None

    # -----------------------------------------------------
    # انتظار انتخاب نوع کالا
    # -----------------------------------------------------

    if flow == "type":
        send_message(
            chat_id,
            "لطفاً نوع کالا را از دکمه‌های نمایش‌داده‌شده انتخاب کن.",
            keyboard=product_type_keyboard(),
        )
        return None

    if flow == "coin_type":
        send_message(
            chat_id,
            "لطفاً نوع سکه را از دکمه‌های نمایش‌داده‌شده انتخاب کن.",
            keyboard=coin_type_keyboard(),
        )
        return None

    if flow == "purity":
        send_message(
            chat_id,
            "لطفاً عیار را از دکمه‌های نمایش‌داده‌شده انتخاب کن.",
            keyboard=purity_keyboard(),
        )
        return None

    if flow == "price_method":
        send_message(
            chat_id,
            "لطفاً یکی از پنج روش قیمت‌گذاری را از دکمه‌ها انتخاب کن.",
            keyboard=price_method_keyboard(),
        )
        return None

    if flow == "preview":
        send_message(
            chat_id,
            "برای تأیید یا اصلاح آگهی از دکمه‌های پیش‌نمایش استفاده کن.",
        )
        return None

    # -----------------------------------------------------
    # توضیحات
    # -----------------------------------------------------

    if flow == "description":
        if not text:
            send_message(
                chat_id,
                "توضیحات را بنویس یا دکمه «بدون توضیحات» را انتخاب کن.",
            )
            return None

        draft["description"] = text[:700]
        finish_edit_or_continue(chat_id, user)
        return None

    # -----------------------------------------------------
    # وزن
    # -----------------------------------------------------

    if flow == "weight":
        normalized = normalize_number(text)

        if not re.fullmatch(
            r"\d+(?:\.\d{1,4})?",
            normalized,
        ):
            unit = weight_unit(draft)

            send_message(
                chat_id,
                f"وزن معتبر نیست. لطفاً عدد مثبت بر حسب {unit} وارد کن.",
            )
            return None

        try:
            weight_value = float(normalized)
        except ValueError:
            weight_value = 0

        max_weight = (
            1_000_000
            if draft.get("coin_type_key") == "parsian"
            else 100_000
        )

        if weight_value <= 0 or weight_value > max_weight:
            send_message(
                chat_id,
                "وزن باید بیشتر از صفر و در محدوده معتبر باشد.",
            )
            return None

        if draft.get("coin_type_key") == "parsian":
            if not weight_value.is_integer():
                send_message(
                    chat_id,
                    "وزن سکه پارسیان باید عدد صحیح سوت باشد.\n"
                    "مثال: ۵۰۰ یا ۱۰۰۰",
                )
                return None

            draft["weight"] = str(int(weight_value))
            draft["weight_unit"] = "سوت"

        else:
            draft["weight"] = (
                f"{weight_value:.4f}".rstrip("0").rstrip(".")
            )
            draft["weight_unit"] = "گرم"

        finish_edit_or_continue(chat_id, user)
        return None

    # -----------------------------------------------------
    # قیمت
    # -----------------------------------------------------

    if flow == "price_value":
        normalized = normalize_number(text)

        if not re.fullmatch(
            r"\d+(?:\.\d{1,4})?",
            normalized,
        ):
            send_message(
                chat_id,
                "لطفاً فقط عدد معتبر وارد کن.",
            )
            return None

        try:
            value = float(normalized)
        except ValueError:
            value = 0

        method = draft.get("price_method")

        if method == "below_board":
            if value <= 0 or value >= 100:
                send_message(
                    chat_id,
                    "درصد باید بیشتر از صفر و کمتر از ۱۰۰ باشد.",
                )
                return None

        elif value <= 0:
            send_message(
                chat_id,
                "مبلغ باید بیشتر از صفر باشد.",
            )
            return None

        if method in ("fixed", "above_board"):
            if not value.is_integer():
                send_message(
                    chat_id,
                    "مبلغ را به تومان و به‌صورت عدد صحیح وارد کن.",
                )
                return None

            draft["price_value"] = str(int(value))

        else:
            draft["price_value"] = (
                f"{value:.4f}".rstrip("0").rstrip(".")
            )

        draft["price"] = price_display(draft)
        finish_edit_or_continue(chat_id, user)
        return None

    # -----------------------------------------------------
    # ارتباط
    # -----------------------------------------------------

    if flow == "contact":
        if not text:
            send_message(
                chat_id,
                "لطفاً آیدی تلگرام یا شماره تماس را وارد کن.",
            )
            return None

        draft["contact"] = text[:150]

        if draft.get("editing"):
            finish_edit_or_continue(chat_id, user)
        else:
            start_city_step(chat_id, user)

        return None

    # -----------------------------------------------------
    # شهر
    # -----------------------------------------------------

    if flow == "city":
        if not text:
            send_message(
                chat_id,
                "لطفاً نام شهر را وارد کن.",
            )
            return None

        draft["city"] = text[:80]

        if draft.get("editing"):
            finish_edit_or_continue(chat_id, user)
        else:
            start_photo_step(chat_id, user)

        return None

    # -----------------------------------------------------
    # عکس
    # -----------------------------------------------------

    if flow == "photo":
        photos = message.get("photo")

        if photos:
            draft["photo"] = photos[-1]["file_id"]

        elif text.lower() in (
            "بدون عکس",
            "ندارم",
            "no",
        ):
            draft["photo"] = None

        else:
            send_message(
                chat_id,
                "لطفاً عکس محصول را بفرست یا دکمه «بدون عکس» را انتخاب کن.",
            )
            return None

        if draft.get("editing"):
            finish_edit_or_continue(chat_id, user)
            return None

        user["flow"] = "preview"
        show_preview(chat_id, user)
        return None

    send_message(
        chat_id,
        "این مرحله شناخته نشد. لطفاً از /cancel استفاده کن و دوباره شروع کن.",
    )

    return None


# =========================================================
# پردازش دکمه‌های تلگرام
# =========================================================

def handle_callback(state, callback, sha, update_id):
    user_id = callback["from"]["id"]
    callback_id = callback["id"]
    data = callback.get("data", "")

    callback_message = callback.get("message") or {}
    chat = callback_message.get("chat") or {}
    chat_id = chat.get("id")

    user = get_user(state, user_id)

    # -----------------------------------------------------
    # قوانین
    # -----------------------------------------------------

    if data == "terms_accept":
        if chat_id is None:
            answer_callback(
                callback_id,
                "خطا در دریافت اطلاعات کاربر.",
                True,
            )
            return sha

        cancel_flow(user)

        answer_callback(
            callback_id,
            "قوانین پذیرفته شد.",
        )

        start_listing(
            chat_id,
            user,
            "user",
        )

        return sha

    if data == "terms_cancel":
        cancel_flow(user)

        answer_callback(
            callback_id,
            "ثبت آگهی لغو شد.",
        )

        if chat_id is not None:
            send_message(
                chat_id,
                "عملیات ثبت آگهی لغو شد. "
                "هر زمان خواستی می‌توانی دوباره شروع کنی.",
                main_keyboard(user_id == ADMIN_ID),
            )

        return sha

    # -----------------------------------------------------
    # انتخاب نوع کالا
    # -----------------------------------------------------

    if data.startswith("type:"):
        product_type = data.split(":", 1)[1]

        if (
            chat_id is None
            or user.get("flow") != "type"
            or not user.get("draft")
        ):
            answer_callback(
                callback_id,
                "این انتخاب منقضی شده است؛ ثبت آگهی را دوباره آغاز کن.",
                True,
            )
            return sha

        if product_type not in PRODUCT_TYPES:
            answer_callback(
                callback_id,
                "نوع کالا معتبر نیست.",
                True,
            )
            return sha

        answer_callback(
            callback_id,
            f"انتخاب شد: {PRODUCT_TYPES[product_type]}",
        )

        start_listing_after_type(
            chat_id,
            user,
            product_type,
        )

        return sha

    # -----------------------------------------------------
    # انتخاب سکه
    # -----------------------------------------------------

    if data.startswith("coin:"):
        coin_key = data.split(":", 1)[1]

        if (
            chat_id is None
            or user.get("flow") != "coin_type"
            or not user.get("draft")
        ):
            answer_callback(
                callback_id,
                "این انتخاب منقضی شده است.",
                True,
            )
            return sha

        if coin_key not in COIN_TYPES:
            answer_callback(
                callback_id,
                "نوع سکه معتبر نیست.",
                True,
            )
            return sha

        answer_callback(
            callback_id,
            f"انتخاب شد: {COIN_TYPES[coin_key]}",
        )

        start_listing_after_coin(
            chat_id,
            user,
            coin_key,
        )

        return sha

    # -----------------------------------------------------
    # انتخاب عیار
    # -----------------------------------------------------

    if data.startswith("purity:"):
        purity_key = data.split(":", 1)[1]

        if (
            chat_id is None
            or user.get("flow") != "purity"
            or not user.get("draft")
        ):
            answer_callback(
                callback_id,
                "این انتخاب منقضی شده است.",
                True,
            )
            return sha

        if purity_key not in PURITY_OPTIONS:
            answer_callback(
                callback_id,
                "گزینه عیار معتبر نیست.",
                True,
            )
            return sha

        draft = user["draft"]
        draft["purity_key"] = purity_key
        draft["purity"] = PURITY_OPTIONS[purity_key]

        answer_callback(
            callback_id,
            f"عیار انتخاب شد: {PURITY_OPTIONS[purity_key]}",
        )

        start_weight_or_description(
            chat_id,
            user,
        )

        return sha

    # -----------------------------------------------------
    # انتخاب روش قیمت‌گذاری
    # -----------------------------------------------------

    if data.startswith("price_method:"):
        method = data.split(":", 1)[1]

        if (
            chat_id is None
            or user.get("flow") != "price_method"
            or not user.get("draft")
        ):
            answer_callback(
                callback_id,
                "این مرحله منقضی شده است.",
                True,
            )
            return sha

        if method not in PRICE_METHODS:
            answer_callback(
                callback_id,
                "روش قیمت‌گذاری معتبر نیست.",
                True,
            )
            return sha

        draft = user["draft"]
        draft["price_method"] = method
        draft.pop("price_value", None)

        if method == "board":
            draft["price"] = "به قیمت تابلو"

        elif method == "negotiable":
            draft["price"] = "قیمت توافقی"

        answer_callback(
            callback_id,
            f"روش انتخاب شد: {PRICE_METHODS[method]}",
        )

        if method in ("board", "negotiable"):
            finish_edit_or_continue(
                chat_id,
                user,
            )

        else:
            ask_price_value(chat_id, user)

        return sha

    # -----------------------------------------------------
    # حذف توضیحات یا عکس
    # -----------------------------------------------------

    if data == "skip:description":
        if (
            chat_id is None
            or user.get("flow") != "description"
            or not user.get("draft")
        ):
            answer_callback(
                callback_id,
                "این مرحله منقضی شده است.",
                True,
            )
            return sha

        user["draft"]["description"] = ""

        answer_callback(
            callback_id,
            "توضیحات رد شد.",
        )

        if user["draft"].get("editing"):
            finish_edit_or_continue(chat_id, user)
        else:
            user["flow"] = "price_method"
            show_price_methods(chat_id)

        return sha

    if data == "skip:photo":
        if (
            chat_id is None
            or user.get("flow") != "photo"
            or not user.get("draft")
        ):
            answer_callback(
                callback_id,
                "این مرحله منقضی شده است.",
                True,
            )
            return sha

        user["draft"]["photo"] = None

        answer_callback(
            callback_id,
            "عکس حذف شد.",
        )

        if user["draft"].get("editing"):
            finish_edit_or_continue(chat_id, user)
        else:
            user["flow"] = "preview"
            show_preview(chat_id, user)

        return sha

    # -----------------------------------------------------
    # اصلاح اطلاعات پیش‌نویس
    # -----------------------------------------------------

    if data.startswith("edit:"):
        if chat_id is None or not user.get("draft"):
            answer_callback(
                callback_id,
                "پیش‌نویس پیدا نشد.",
                True,
            )
            return sha

        if user.get("flow") != "preview":
            answer_callback(
                callback_id,
                "ابتدا پیش‌نمایش آگهی را باز کن.",
                True,
            )
            return sha

        field = data.split(":", 1)[1]

        allowed_fields = {
            "product",
            "weight",
            "description",
            "price",
            "contact",
            "city",
            "photo",
        }

        if field not in allowed_fields:
            answer_callback(
                callback_id,
                "بخش انتخاب‌شده معتبر نیست.",
                True,
            )
            return sha

        answer_callback(
            callback_id,
            "بخش انتخاب‌شده برای اصلاح باز شد.",
        )

        start_edit_field(
            chat_id,
            user,
            field,
        )

        return sha

    # -----------------------------------------------------
    # تأیید نهایی پیش‌نویس
    # -----------------------------------------------------

    if data == "preview:confirm":
        if (
            chat_id is None
            or user.get("flow") != "preview"
            or not user.get("draft")
        ):
            answer_callback(
                callback_id,
                "پیش‌نویس منقضی شده است.",
                True,
            )
            return sha

        draft = user["draft"]

        if (
            not draft.get("product_type_key")
            or not draft.get("contact")
            or not draft.get("city")
            or not draft.get("price_method")
        ):
            answer_callback(
                callback_id,
                "اطلاعات ضروری کامل نیست.",
                True,
            )
            return sha

        if (
            draft.get("price_method")
            in ("fixed", "below_board", "above_board")
            and not draft.get("price_value")
        ):
            answer_callback(
                callback_id,
                "مقدار قیمت وارد نشده است.",
                True,
            )
            return sha

        if requires_weight(draft) and not draft.get("weight"):
            answer_callback(
                callback_id,
                "وزن کالا ثبت نشده است.",
                True,
            )
            return sha

        if (
            draft.get("product_type_key") == "coin"
            and not draft.get("coin_type_key")
        ):
            answer_callback(
                callback_id,
                "نوع سکه مشخص نشده است.",
                True,
            )
            return sha

        answer_callback(
            callback_id,
            "آگهی تأیید شد.",
        )

        finish_listing(
            state,
            user_id,
            chat_id,
        )

        return sha

    # -----------------------------------------------------
    # از اینجا به بعد عملیات فقط مخصوص مدیر است
    # -----------------------------------------------------

    if user_id != ADMIN_ID:
        answer_callback(
            callback_id,
            "این عملیات فقط برای مدیر مجاز است.",
            True,
        )
        return sha

    try:
        action, listing_id_text = data.split(":", 1)
        listing_id = int(listing_id_text)

    except (ValueError, TypeError):
        answer_callback(
            callback_id,
            "درخواست نامعتبر است.",
            True,
        )
        return sha

    listing = next(
        (
            item for item in state["listings"]
            if int(item.get("id", -1)) == listing_id
        ),
        None,
    )

    if not listing:
        answer_callback(
            callback_id,
            "آگهی پیدا نشد.",
            True,
        )
        return sha

    # -----------------------------------------------------
    # فروش‌رفته
    # -----------------------------------------------------

    if action == "sold":
        if listing.get("status") == "sold":
            answer_callback(
                callback_id,
                "این آگهی قبلاً فروش‌رفته علامت خورده است.",
            )
            return sha

        if listing.get("status") != "published":
            answer_callback(
                callback_id,
                "فقط آگهی منتشرشده را می‌توان فروش‌رفته کرد.",
                True,
            )
            return sha

        mark_listing_sold(listing)
        listing["status"] = "sold"
        sha = save_state(state, sha)

        answer_callback(
            callback_id,
            "برچسب فروش‌رفته ثبت شد.",
        )

        send_message(
            ADMIN_ID,
            f"✅ آگهی شماره {listing_id} فروش‌رفته ثبت شد.",
        )

        return sha

    # -----------------------------------------------------
    # جلوگیری از بررسی مجدد
    # -----------------------------------------------------

    if listing.get("status") != "pending":
        answer_callback(
            callback_id,
            "این آگهی قبلاً بررسی شده یا در دسترس نیست.",
            True,
        )
        return sha

    # -----------------------------------------------------
    # تأیید آگهی کاربر
    # -----------------------------------------------------

    if action == "approve":
        listing.setdefault("weight", "ثبت نشده")
        listing.setdefault("city", "ثبت نشده")
        listing.setdefault("product_type", "📦 سایر موارد")
        listing.setdefault("weight_unit", "گرم")

        listing["status"] = "publishing"

        answer_callback(
            callback_id,
            "آگهی برای انتشار تأیید شد.",
        )

        return sha

    # -----------------------------------------------------
    # رد آگهی کاربر
    # -----------------------------------------------------

    if action == "reject":
        listing["status"] = "rejected"

        answer_callback(
            callback_id,
            "آگهی رد شد.",
        )

        send_message(
            listing["owner_id"],
            "متأسفانه آگهی شما تأیید نشد.\n"
            "برای ارسال دوباره، یک آگهی جدید ثبت کن.",
        )

        return sha

    answer_callback(
        callback_id,
        "عملیات ناشناخته است.",
        True,
    )

    return sha


# =========================================================
# تکمیل انتشار محصولات در حال انتشار
# =========================================================

def process_pending_publications(state, sha):
    """
    پس از ذخیره وضعیت، انتشار آگهی‌های تأییدشده و محصولات
    مدیر انجام می‌شود. این بخش در درخواست‌های بعدی نیز
    وضعیت‌های نیمه‌تمام را بررسی می‌کند.
    """

    changed = False

    for listing in state.get("listings", []):
        if listing.get("status") != "publishing":
            continue

        # اگر اطلاعات پیام‌های منتشرشده موجود است، دوباره ارسال نکن.
        if listing.get("published_messages"):
            listing["status"] = "published"
            changed = True
            continue

        publish_listing(listing)
        listing["status"] = "published"
        changed = True

        sold_keyboard = {
            "inline_keyboard": [[{
                "text": "✅ فروش رفت",
                "callback_data": f"sold:{listing['id']}",
            }]]
        }

        if listing.get("kind") == "admin":
            send_message(
                ADMIN_ID,
                "✅ محصول مدیر منتشر شد.\n\n"
                f"🆔 شماره آگهی: {listing['id']}\n"
                f"💎 نوع کالا: {html.escape(get_product_type(listing))}\n"
                f"📝 عنوان: {html.escape(listing.get('title', ''))}\n"
                f"💰 قیمت: {html.escape(price_display(listing))}\n"
                "برای علامت‌گذاری فروش‌رفته از دکمه زیر استفاده کن.",
                sold_keyboard,
            )

            send_message(
                ADMIN_ID,
                "منوی مدیریت:",
                main_keyboard(True),
            )

        else:
            send_message(
                ADMIN_ID,
                "📣 آگهی کاربر منتشر شد.\n\n"
                f"🆔 شماره آگهی: {listing['id']}\n"
                f"💎 نوع کالا: {html.escape(get_product_type(listing))}\n"
                f"📝 عنوان: {html.escape(listing.get('title', ''))}\n"
                f"💰 قیمت: {html.escape(price_display(listing))}\n"
                "برای علامت‌گذاری فروش‌رفته از دکمه زیر استفاده کن.",
                sold_keyboard,
            )

            send_message(
                listing["owner_id"],
                "✅ آگهی شما پس از بررسی در موضوع خریدوفروش گروه منتشر شد.",
            )

    if changed:
        sha = save_state(state, sha)

    return sha


# =========================================================
# وبهوک
# =========================================================

class handler(BaseHTTPRequestHandler):

    def respond(self, status, message):
        body = json.dumps({
            "ok": status == 200,
            "message": message,
        }).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
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

            update = json.loads(
                self.rfile.read(length)
            )

            if "update_id" not in update:
                self.respond(400, "Missing update ID")
                return

            state, sha = load_state()
            update_id = int(update["update_id"])

            # ابتدا وضعیت‌های نیمه‌تمام انتشار بررسی می‌شوند.
            sha = process_pending_publications(
                state,
                sha,
            )

            if update_id <= state.get("last_update_id", -1):
                self.respond(
                    200,
                    "Duplicate update ignored",
                )
                return

            if "callback_query" in update:
                callback = update["callback_query"]

                sha = handle_callback(
                    state,
                    callback,
                    sha,
                    update_id,
                )

                state["last_update_id"] = update_id
                sha = save_state(state, sha)

                # انتشار فقط بعد از ذخیره تغییر وضعیت انجام می‌شود.
                sha = process_pending_publications(
                    state,
                    sha,
                )

                self.respond(200, "OK")
                return

            if "message" in update:
                handle_message(
                    state,
                    update["message"],
                )

            state["last_update_id"] = update_id
            sha = save_state(state, sha)

            # محصول مدیر و آگهی‌های تأییدشده در این مرحله منتشر می‌شوند.
            sha = process_pending_publications(
                state,
                sha,
            )

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
