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


def send_registration_button_to_group():
    """ارسال و سنجاق پیام دکمه‌دار ثبت آگهی در موضوع خریدوفروش."""
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
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        "📣 <b>آگهی کن، دیده شو، بفروش! 📣</b>\n\n"
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


def send_listing_to_destination(chat_id, caption, photo=None, thread_id=None):
    """ارسال آگهی به مقصد؛ در صورت نیاز داخل موضوع مشخص گروه."""
    if photo:
        data = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption[:1000],
            "parse_mode": "HTML",
        }
        if thread_id is not None:
            data["message_thread_id"] = thread_id
        telegram("sendPhoto", data)
    else:
        send_message(
            chat_id,
            caption,
            message_thread_id=thread_id,
        )


def main_keyboard(is_admin=False):
    rows = [
        [{"text": "ثبت آگهی فروش"}],
        [{"text": "راهنمای ثبت آگهی"}],
    ]

    if is_admin:
        rows.insert(1, [{"text": "ثبت محصول مدیر"}])
        rows.insert(2, [{"text": "آگهی‌های در انتظار"}])
        rows.insert(3, [{"text": "📌 ارسال دکمه ثبت آگهی به گروه"}])

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
        "مرحله ۱ از ۶\n"
        "عنوان محصول را بفرست.\n"
        "مثال: انگشتر طلای ۱۸ عیار\n\n"
        "برای لغو عملیات، /cancel را بفرست.",
    )


def publish_listing(listing):
    title = html.escape(str(listing.get("title", "")))
    description = html.escape(
        str(listing.get("description", ""))
    )
    weight = html.escape(
        str(listing.get("weight", "ثبت نشده"))
    )
    price = html.escape(str(listing.get("price", "")))
    contact = html.escape(str(listing.get("contact", "")))

    if listing.get("kind") == "admin":
        label = "محصول مدیر زرین ماه"
    else:
        label = "آگهی فروش کاربر"

    caption = (
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        f"🏷 <b>{label}</b>\n"
        f"💎 <b>{title}</b>\n\n"
        f"📋 {description}\n"
        f"⚖️ <b>وزن طلا:</b> {weight} گرم\n\n"
        f"💰 <b>قیمت اعلامی:</b> {price}\n"
        f"📞 <b>راه ارتباطی:</b> {contact}\n\n"
        "#محصولات_برای_فروش"
    )

    photo = listing.get("photo")

    # آگهی کاربران فقط در موضوع خریدوفروش گروه منتشر می‌شود.
    # محصولات مدیر در کانال و همچنین در موضوع خریدوفروش گروه منتشر می‌شوند.
    if listing.get("kind") == "admin":
        send_listing_to_destination(CHANNEL, caption, photo=photo)

    send_listing_to_destination(
        GROUP,
        caption,
        photo=photo,
        thread_id=GROUP_THREAD_ID,
    )


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
    command = command_parts[0].split("@", 1)[0] if command_parts else ""
    payload = (
        command_parts[1].strip().lower()
        if len(command_parts) > 1
        else ""
    )

    if command == "/start":
        cancel_flow(user)

        # لینک مستقیم ثبت آگهی:
        # https://t.me/ZarinMahGoldBot?start=sell
        if payload == "sell":
            start_listing(chat_id, user, "user")
            return None

        send_message(
            chat_id,
            "سلام! به ربات فروشندگان زرین ماه خوش آمدی. 🌙\n\n"
            "برای ثبت آگهی، گزینهٔ «ثبت آگهی فروش» را بزن.\n"
            "اگر از لینک مستقیم ثبت آگهی وارد شده‌ای، "
            "می‌توانی از لینک ثبت آگهی استفاده کنی.\n\n"
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
        )
        return None

    if text == "ثبت آگهی فروش":
        start_listing(chat_id, user, "user")
        return None
