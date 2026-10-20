import os
import json
import base64
import html
import hmac
import re
import requests

from http.server import BaseHTTPRequestHandler


TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATA_TOKEN = os.environ["SELLER_DATA_TOKEN"]
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]

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

RULES_TEXT = (
    "📜 <b>قوانین و مقررات ثبت آگهی زرین ماه</b>\n\n"
    "۱. اطلاعات کالا، وزن، عیار و قیمت باید دقیق و واقعی باشد.\n\n"
    "۲. فروشنده باید مالک کالا باشد یا اجازه فروش آن را داشته باشد.\n\n"
    "۳. مسئولیت اصالت کالا و صحت اطلاعات آگهی بر عهده فروشنده است.\n\n"
    "۴. مسئولیت بررسی کالا و توافق نهایی بر عهده طرفین معامله است.\n\n"
    "۵. آگهی‌های کاربران قبل از انتشار توسط مدیر بررسی می‌شوند.\n\n"
    "۶. آگهی‌های خلاف واقع یا مغایر قوانین منتشر نخواهند شد.\n\n"
    "با انتخاب «پذیرش قوانین و ادامه»، موافقت خود را با این مقررات اعلام می‌کنی."
)

session = requests.Session()
session.headers.update({
    "Authorization": f"Bearer {DATA_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})


def telegram(method, data=None):
    response = requests.post(
        f"{TG_API}/{method}",
        json=data or {},
        timeout=20,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError("Telegram API request failed")

    return result.get("result")


def send_message(chat_id, text, keyboard=None, message_thread_id=None):
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


def show_rules(chat_id):
    """نمایش قوانین پیش از شروع ثبت آگهی."""
    keyboard = {
        "inline_keyboard": [
            [{
                "text": "✅ پذیرش قوانین و ادامه",
                "callback_data": "terms_accept",
            }],
            [{
                "text": "❌ انصراف",
                "callback_data": "terms_cancel",
            }],
        ]
    }

    send_message(
        chat_id,
        RULES_TEXT,
        keyboard=keyboard,
    )


def send_registration_button_to_group():
    """ارسال و سنجاق دکمه ثبت آگهی در موضوع خریدوفروش."""
    keyboard = {
        "inline_keyboard": [[
            {
                "text": "🟡 ثبت آگهی با ربات",
                "url": "https://t.me/ZarinMahGoldBot?start=sell",
            }
        ]]
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


def send_listing_to_destination(chat_id, caption, photo=None, thread_id=None):
    """ارسال آگهی و برگرداندن شناسه پیام."""
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


def main_keyboard(is_admin=False):
    rows = [
        [{"text": "ثبت آگهی فروش"}],
        [{"text": "📜 قوانین و مقررات"}],
        [{"text": "راهنمای ثبت آگهی"}],
    ]

    if is_admin:
        rows.insert(2, [{"text": "ثبت محصول مدیر"}])
        rows.insert(3, [{"text": "آگهی‌های در انتظار"}])
        rows.insert(4, [{"text": "📌 ارسال دکمه ثبت آگهی به گروه"}])

    return {
        "keyboard": rows,
        "resize_keyboard": True,
    }


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
        int(item.get("id", 0))
        for item in state["listings"]
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


def start_listing(chat_id, user, kind):
    user["flow"] = "title"
    user["draft"] = {
        "kind": kind,
        "photo": None,
    }

    send_message(
        chat_id,
        "📝 ثبت آگهی فروش زرین ماه\n\n"
        "مرحله ۱ از ۷\n"
        "عنوان محصول را بفرست.\n"
        "مثال: انگشتر طلای ۱۸ عیار\n\n"
        "برای لغو عملیات، /cancel را بفرست.",
    )


def publish_listing(listing):
    title = html.escape(str(listing.get("title", "")))
    description = html.escape(str(listing.get("description", "")))
    weight = html.escape(str(listing.get("weight", "ثبت نشده")))
    price = html.escape(str(listing.get("price", "")))
    contact = html.escape(str(listing.get("contact", "")))
    city = html.escape(str(listing.get("city", "ثبت نشده")))

    if listing.get("kind") == "admin":
        label = "محصول مدیر زرین ماه"
    else:
        label = "آگهی فروش کاربر"

    caption = (
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        f"🏷 <b>{label}</b>\n"
        f"💎 <b>{title}</b>\n\n"
        f"📋 {description}\n"
        f"⚖️ <b>وزن طلا:</b> {weight} گرم\n"
        f"💰 <b>قیمت اعلامی:</b> {price}\n"
        f"📍 <b>شهر:</b> {city}\n"
        f"📞 <b>راه ارتباطی:</b> {contact}\n\n"
        "#محصولات_برای_فروش"
    )

    if listing.get("status") == "sold":
        caption += "\n\n✅ <b>فروخته شد</b>"

    photo = listing.get("photo")
    published_messages = []

    # محصولات مدیر در کانال و گروه؛ آگهی کاربران فقط در گروه.
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
    """روی پیام‌های منتشرشده برچسب فروش‌رفته درج می‌کند."""
    for posted in listing.get("published_messages", []):
        chat_id = posted.get("chat_id")
        message_id = posted.get("message_id")

        if not chat_id or not message_id:
            continue

        if posted.get("has_photo"):
            method = "editMessageCaption"
            field = "caption"
        else:
            method = "editMessageText"
            field = "text"

        title = html.escape(str(listing.get("title", "")))
        description = html.escape(str(listing.get("description", "")))
        weight = html.escape(str(listing.get("weight", "ثبت نشده")))
        price = html.escape(str(listing.get("price", "")))
        contact = html.escape(str(listing.get("contact", "")))
        city = html.escape(str(listing.get("city", "ثبت نشده")))

        label = (
            "محصول مدیر زرین ماه"
            if listing.get("kind") == "admin"
            else "آگهی فروش کاربر"
        )

        text = (
            "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
            f"🏷 <b>{label}</b>\n"
            f"💎 <b>{title}</b>\n\n"
            f"📋 {description}\n"
            f"⚖️ <b>وزن طلا:</b> {weight} گرم\n"
            f"💰 <b>قیمت اعلامی:</b> {price}\n"
            f"📍 <b>شهر:</b> {city}\n"
            f"📞 <b>راه ارتباطی:</b> {contact}\n\n"
            "#محصولات_برای_فروش\n\n"
            "✅ <b>فروخته شد</b>"
        )

        telegram(method, {
            "chat_id": chat_id,
            "message_id": message_id,
            field: text[:1000] if posted.get("has_photo") else text,
            "parse_mode": "HTML",
        })


def finish_listing(state, user_id, chat_id):
    user = get_user(state, user_id)
    draft = user["draft"]

    listing = {
        "id": state["next_id"],
        "owner_id": user_id,
        "kind": draft["kind"],
        "title": draft["title"],
        "description": draft["description"],
        "weight": draft["weight"],
        "price": draft["price"],
        "contact": draft["contact"],
        "city": draft.get("city", "ثبت نشده"),
        "photo": draft.get("photo"),
        "status": (
            "pending"
            if draft["kind"] == "user"
            else "publishing"
        ),
    }

    state["next_id"] += 1
    state["listings"].append(listing)

    cancel_flow(user)

    if listing["kind"] == "admin":
        send_message(
            chat_id,
            "✅ اطلاعات محصول مدیر ثبت شد.\n"
            "پس از ذخیره وضعیت، محصول در کانال و موضوع خریدوفروش گروه منتشر می‌شود.",
            main_keyboard(True),
        )
        return listing["id"]

    admin_text = (
        "🔔 <b>آگهی جدید برای بررسی</b>\n\n"
        f"🆔 شماره آگهی: {listing['id']}\n"
        f"💎 {html.escape(listing['title'])}\n"
        f"⚖️ وزن: {html.escape(listing['weight'])} گرم\n"
        f"📋 {html.escape(listing['description'])}\n"
        f"💰 قیمت: {html.escape(listing['price'])}\n"
        f"📍 شهر: {html.escape(listing.get('city', 'ثبت نشده'))}\n"
        f"📞 ارتباط: {html.escape(listing['contact'])}\n\n"
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

    send_message(
        chat_id,
        "✅ آگهی دریافت شد و برای بررسی مدیر ارسال شد.\n"
        "پس از تأیید مدیر، در موضوع خریدوفروش گروه منتشر می‌شود.",
        main_keyboard(user_id == ADMIN_ID),
    )

    return None


def handle_message(state, message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()

    user = get_user(state, user_id)
    is_admin = user_id == ADMIN_ID

    if text == "/cancel":
        cancel_flow(user)
        send_message(
            chat_id,
            "عملیات لغو شد.",
            main_keyboard(is_admin),
        )
        return None

    # پشتیبانی از /start، /start sell و /start@BotUsername sell
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

    if command == "/start":
        cancel_flow(user)

        if payload == "sell":
            show_rules(chat_id)
            return None

        send_message(
            chat_id,
            "سلام! به ربات فروشندگان زرین ماه خوش آمدی. 🌙\n\n"
            "برای ثبت آگهی، گزینهٔ «ثبت آگهی فروش» را بزن.\n"
            "اگر از لینک مستقیم ثبت آگهی وارد شده‌ای، "
            "ابتدا قوانین را مشاهده و تأیید کن.\n\n"
            "آگهی کاربران پس از تأیید مدیر منتشر می‌شود.",
            main_keyboard(is_admin),
        )
        return None

    if text == "راهنمای ثبت آگهی":
        send_message(
            chat_id,
            "برای ثبت آگهی، عنوان، توضیحات، وزن طلا، "
            "قیمت و راه ارتباطی را بفرست.\n"
            "در پایان می‌توانی عکس محصول را هم ارسال کنی.\n"
            "آگهی کاربران قبل از انتشار بررسی می‌شود.\n\n"
            "برای لغو هر زمان /cancel را بفرست.",
            main_keyboard(is_admin),
        )
        return None

    if text == "📜 قوانین و مقررات":
        show_rules(chat_id)
        return None

    if text == "ثبت آگهی فروش":
        cancel_flow(user)
        show_rules(chat_id)
        return None

    if text == "ثبت محصول مدیر" and is_admin:
        start_listing(chat_id, user, "admin")
        return None

    if text == "📌 ارسال دکمه ثبت آگهی به گروه" and is_admin:
        try:
            send_registration_button_to_group()
            send_message(
                chat_id,
                "✅ پیام دکمه‌دار ثبت آگهی در موضوع خریدوفروش ارسال و سنجاق شد.\n\n"
                "اگر ارسال یا سنجاق انجام نشد، بررسی کن ربات در گروه "
                "مجوز ارسال پیام و سنجاق‌کردن پیام را داشته باشد و "
                "موضوع برای ارسال پیام توسط ربات بسته نباشد.",
                main_keyboard(True),
            )
        except Exception:
            send_message(
                chat_id,
                "❌ ارسال یا سنجاق پیام انجام نشد.\n\n"
                "لطفاً بررسی کن ربات مدیر گروه باشد و مجوز ارسال پیام "
                "و سنجاق‌کردن پیام را داشته باشد. اگر موضوع خریدوفروش "
                "بسته است، موقتاً آن را باز کن، دوباره تلاش کن و سپس ببند.",
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

            send_message(
                chat_id,
                f"آگهی شماره {item['id']}\n"
                f"عنوان: {html.escape(item['title'])}\n"
                f"وزن: {html.escape(str(item.get('weight', 'ثبت نشده')))} گرم\n"
                f"قیمت: {html.escape(item['price'])}\n"
                f"شهر: {html.escape(item.get('city', 'ثبت نشده'))}",
                keyboard,
            )

        return None

    flow = user.get("flow")

    if not flow:
        send_message(
            chat_id,
            "برای شروع از دکمه‌های منو استفاده کن.",
            main_keyboard(is_admin),
        )
        return None

    draft = user["draft"]

    if flow == "title":
        if not text:
            send_message(chat_id, "لطفاً عنوان محصول را بفرست.")
            return None

        draft["title"] = text[:150]
        user["flow"] = "description"
        send_message(chat_id, "توضیحات محصول را بفرست.")

    elif flow == "description":
        if not text:
            send_message(chat_id, "لطفاً توضیحات را بفرست.")
            return None

        draft["description"] = text[:700]
        user["flow"] = "weight"
        send_message(
            chat_id,
            "⚖️ وزن طلا را به گرم وارد کن.\n"
            "مثال: 2.500\n"
            "می‌توانی از اعداد فارسی یا انگلیسی استفاده کنی.",
        )

    elif flow == "weight":
        normalized = text.translate(
            str.maketrans(
                "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬",
                "01234567890123456789..",
            )
        ).replace(",", ".").strip()

        if not re.fullmatch(r"\d+(?:\.\d{1,4})?", normalized):
            send_message(
                chat_id,
                "وزن معتبر نیست. فقط عدد مثبت بر حسب گرم وارد کن.\n"
                "مثال: 2.500",
            )
            return None

        try:
            weight_value = float(normalized)
        except ValueError:
            weight_value = 0

        if weight_value <= 0 or weight_value > 100000:
            send_message(
                chat_id,
                "وزن باید بیشتر از صفر و در محدوده معتبر باشد.",
            )
            return None

        draft["weight"] = (
            f"{weight_value:.4f}".rstrip("0").rstrip(".")
        )
        user["flow"] = "price"
        send_message(chat_id, "💰 قیمت پیشنهادی را بفرست.")

    elif flow == "price":
        if not text:
            send_message(chat_id, "لطفاً قیمت را بفرست.")
            return None

        draft["price"] = text[:100]
        user["flow"] = "contact"
        send_message(
            chat_id,
            "راه ارتباطی فروشنده را بفرست؛ "
            "مثلاً آیدی تلگرام یا شماره تماس.",
        )

    elif flow == "contact":
        if not text:
            send_message(chat_id, "لطفاً راه ارتباطی را بفرست.")
            return None

        draft["contact"] = text[:150]
        user["flow"] = "city"
        send_message(
            chat_id,
            "📍 شهر محل فروش را بنویس.\n"
            "مثال: بروجرد، تهران یا اصفهان.",
        )

    elif flow == "city":
        if not text:
            send_message(chat_id, "لطفاً نام شهر را بنویس.")
            return None

        draft["city"] = text[:80]
        user["flow"] = "photo"
        send_message(
            chat_id,
            "حالا عکس محصول را بفرست.\n"
            "اگر عکس نداری، عبارت «بدون عکس» را بفرست.",
        )

    elif flow == "photo":
        photos = message.get("photo")

        if photos:
            draft["photo"] = photos[-1]["file_id"]
        elif text.lower() not in ("بدون عکس", "ندارم", "no"):
            send_message(
                chat_id,
                "لطفاً عکس محصول را بفرست یا بنویس «بدون عکس».",
            )
            return None

        return finish_listing(state, user_id, chat_id)

    return None


def handle_callback(state, callback, sha, update_id):
    user_id = callback["from"]["id"]
    callback_id = callback["id"]
    data = callback.get("data", "")

    # پذیرش قوانین توسط کاربر؛ این دکمه برای همه کاربران است.
    if data == "terms_accept":
        callback_message = callback.get("message") or {}
        chat = callback_message.get("chat") or {}
        chat_id = chat.get("id")

        if chat_id is None:
            telegram("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "خطا در دریافت اطلاعات کاربر.",
                "show_alert": True,
            })
            return sha

        user = get_user(state, user_id)
        cancel_flow(user)

        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "قوانین پذیرفته شد.",
        })

        start_listing(chat_id, user, "user")
        return sha

    # انصراف از ثبت آگهی
    if data == "terms_cancel":
        callback_message = callback.get("message") or {}
        chat = callback_message.get("chat") or {}
        chat_id = chat.get("id")

        user = get_user(state, user_id)
        cancel_flow(user)

        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "ثبت آگهی لغو شد.",
        })

        if chat_id is not None:
            send_message(
                chat_id,
                "عملیات ثبت آگهی لغو شد. هر زمان خواستی می‌توانی دوباره شروع کنی.",
                main_keyboard(user_id == ADMIN_ID),
            )

        return sha

    # سایر دکمه‌های مدیریتی فقط برای مدیر هستند.
    if user_id != ADMIN_ID:
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "این عملیات فقط برای مدیر مجاز است.",
            "show_alert": True,
        })
        return sha

    try:
        action, listing_id = data.split(":", 1)
        listing_id = int(listing_id)
    except (ValueError, TypeError):
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "درخواست نامعتبر است.",
            "show_alert": True,
        })
        return sha

    listing = next(
        (
            item for item in state["listings"]
            if int(item.get("id", -1)) == listing_id
        ),
        None,
    )

    if not listing:
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "آگهی پیدا نشد.",
            "show_alert": True,
        })
        return sha

    if action == "sold":
        if listing.get("status") == "sold":
            telegram("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "این آگهی قبلاً فروش‌رفته علامت خورده است.",
            })
            return sha

        if listing.get("status") != "published":
            telegram("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "فقط آگهی منتشرشده را می‌توان فروش‌رفته کرد.",
                "show_alert": True,
            })
            return sha

        mark_listing_sold(listing)
        listing["status"] = "sold"
        sha = save_state(state, sha)

        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "برچسب فروش‌رفته روی آگهی درج شد.",
        })

        send_message(
            ADMIN_ID,
            f"✅ آگهی شماره {listing_id} به‌عنوان فروش‌رفته ثبت شد.",
        )
        return sha

    if listing.get("status") != "pending":
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "این آگهی قبلاً بررسی شده یا پیدا نشد.",
            "show_alert": True,
        })
        return sha

    if action == "approve":
        listing.setdefault("weight", "ثبت نشده")
        listing.setdefault("city", "ثبت نشده")
        listing["status"] = "publishing"
        state["last_update_id"] = update_id
        sha = save_state(state, sha)

        publish_listing(listing)
        listing["status"] = "published"
        sha = save_state(state, sha)

        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "آگهی منتشر شد.",
        })

        sold_keyboard = {
            "inline_keyboard": [[{
                "text": "✅ فروش رفت",
                "callback_data": f"sold:{listing['id']}",
            }]]
        }

        send_message(
            ADMIN_ID,
            "📣 آگهی منتشر شد.\n"
            f"🆔 شماره آگهی: {listing['id']}\n"
            f"💎 {html.escape(listing.get('title', ''))}\n"
            f"📍 شهر: {html.escape(listing.get('city', 'ثبت نشده'))}\n\n"
            "اگر کالا فروش رفت، دکمه زیر را بزن:",
            sold_keyboard,
        )

        send_message(
            listing["owner_id"],
            "✅ آگهی شما پس از بررسی در موضوع خریدوفروش گروه منتشر شد.",
        )

    elif action == "reject":
        listing["status"] = "rejected"

        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "آگهی رد شد.",
        })

        send_message(
            listing["owner_id"],
            "متأسفانه آگهی شما تأیید نشد. برای ارسال دوباره، "
            "از منوی ربات یک آگهی جدید ثبت کن.",
        )

    else:
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "عملیات ناشناخته است.",
            "show_alert": True,
        })

    return sha


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
        self.respond(200, "Seller webhook endpoint is running.")

    def do_POST(self):
        received_secret = self.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )

        if not hmac.compare_digest(
            received_secret,
            WEBHOOK_SECRET,
        ):
            self.respond(403, "Forbidden")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))

            if length <= 0 or length > 1_000_000:
                self.respond(400, "Invalid request size")
                return

            update = json.loads(self.rfile.read(length))

            if "update_id" not in update:
                self.respond(400, "Missing update ID")
                return

            state, sha = load_state()
            update_id = int(update["update_id"])

            if update_id <= state.get("last_update_id", -1):
                self.respond(200, "Duplicate update ignored")
                return

            admin_listing_id = None

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

                self.respond(200, "OK")
                return

            elif "message" in update:
                admin_listing_id = handle_message(
                    state,
                    update["message"],
                )

            state["last_update_id"] = update_id
            sha = save_state(state, sha)

            if admin_listing_id is not None:
                listing = next(
                    (
                        item for item in state["listings"]
                        if int(item.get("id", -1)) == admin_listing_id
                    ),
                    None,
                )

                if listing and listing.get("status") == "publishing":
                    publish_listing(listing)

                    listing["status"] = "published"
                    sha = save_state(state, sha)

                    sold_keyboard = {
                        "inline_keyboard": [[{
                            "text": "✅ فروش رفت",
                            "callback_data": f"sold:{listing['id']}",
                        }]]
                    }

                    send_message(
                        ADMIN_ID,
                        "✅ محصول مدیر در کانال زرین ماه و موضوع خریدوفروش گروه منتشر شد.\n"
                        f"🆔 شماره آگهی: {listing['id']}\n"
                        "اگر کالا فروش رفت، دکمه زیر را بزن:",
                        sold_keyboard,
                    )

                    send_message(
                        ADMIN_ID,
                        "منوی مدیریت:",
                        main_keyboard(True),
                    )

            self.respond(200, "OK")

        except Exception:
            try:
                self.respond(500, "Temporary processing error")
            except Exception:
                pass

    def log_message(self, format, *args):
        return
