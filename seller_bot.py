
import os
import json
import base64
import html
import requests

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATA_TOKEN = os.environ["SELLER_DATA_TOKEN"]
DATA_REPO = "Booknight20/zarinmah-seller-data"
CHANNEL = "@ZarinMahGold"
ADMIN_ID = 8816884849

TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
GH_API = f"https://api.github.com/repos/{DATA_REPO}/contents/state.json"

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
        timeout=30,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    return result.get("result")

def send_message(chat_id, text, keyboard=None):
    data = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        data["reply_markup"] = keyboard
    telegram("sendMessage", data)

def main_keyboard(is_admin=False):
    rows = [
        [{"text": "ثبت آگهی فروش"}],
        [{"text": "راهنمای ثبت آگهی"}],
    ]
    if is_admin:
        rows.insert(1, [{"text": "ثبت محصول مدیر"}])
        rows.insert(2, [{"text": "آگهی‌های در انتظار"}])
    return {
        "keyboard": rows,
        "resize_keyboard": True,
    }

def load_state():
    response = session.get(GH_API, timeout=30)

    if response.status_code == 404:
        return {
            "offset": 0,
            "users": {},
            "listings": [],
            "next_id": 1,
        }, None

    response.raise_for_status()
    item = response.json()
    content = base64.b64decode(item["content"]).decode("utf-8")
    return json.loads(content), item["sha"]

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

    response = session.put(GH_API, json=payload, timeout=30)
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
        "📝 عنوان محصول را بفرست.\n"
        "مثال: انگشتر طلای ۱۸ عیار",
    )

def publish_listing(listing):
    title = html.escape(listing["title"])
    description = html.escape(listing["description"])
    price = html.escape(listing["price"])
    contact = html.escape(listing["contact"])

    if listing["kind"] == "admin":
        label = "محصول مدیر زرین ماه"
    else:
        label = "آگهی فروش کاربر"

    caption = (
        f"🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        f"🏷 <b>{label}</b>\n"
        f"💎 <b>{title}</b>\n\n"
        f"📋 {description}\n\n"
        f"💰 <b>قیمت اعلامی:</b> {price}\n"
        f"📞 <b>راه ارتباطی:</b> {contact}\n\n"
        f"#محصولات_برای_فروش"
    )

    photo = listing.get("photo")
    if photo:
        telegram("sendPhoto", {
            "chat_id": CHANNEL,
            "photo": photo,
            "caption": caption[:1000],
            "parse_mode": "HTML",
        })
    else:
        send_message(CHANNEL, caption)

def finish_listing(state, user_id, chat_id, sha):
    user = get_user(state, user_id)
    draft = user["draft"]

    listing = {
        "id": state["next_id"],
        "owner_id": user_id,
        "kind": draft["kind"],
        "title": draft["title"],
        "description": draft["description"],
        "price": draft["price"],
        "contact": draft["contact"],
        "photo": draft.get("photo"),
        "status": "pending" if draft["kind"] == "user" else "published",
    }
    state["next_id"] += 1

    if listing["kind"] == "admin":
        publish_listing(listing)
        state["listings"].append(listing)
        send_message(chat_id, "✅ محصول مدیر در کانال منتشر شد.")
    else:
        state["listings"].append(listing)
        admin_text = (
            "🔔 <b>آگهی جدید برای بررسی</b>\n\n"
            f"🆔 شماره آگهی: {listing['id']}\n"
            f"💎 {html.escape(listing['title'])}\n"
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
            "پس از تأیید، در کانال منتشر می‌شود.",
        )

    cancel_flow(user)

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
        return

    if text == "/start":
        cancel_flow(user)
        send_message(
            chat_id,
            "سلام! به ربات فروشندگان زرین ماه خوش آمدی. 🌙\n\n"
            "برای ثبت آگهی، گزینهٔ «ثبت آگهی فروش» را بزن.\n"
            "آگهی کاربران پس از تأیید مدیر منتشر می‌شود.",
            main_keyboard(is_admin),
        )
        return

    if text == "راهنمای ثبت آگهی":
        send_message(
            chat_id,
            "برای ثبت آگهی، عنوان، توضیحات، قیمت و راه ارتباطی را بفرست.\n"
            "در پایان می‌توانی عکس محصول را هم ارسال کنی.\n"
            "آگهی کاربران قبل از انتشار بررسی می‌شود.\n\n"
            "برای لغو هر زمان /cancel را بفرست.",
        )
        return

    if text == "ثبت آگهی فروش":
        start_listing(chat_id, user, "user")
        return

    if text == "ثبت محصول مدیر" and is_admin:
        start_listing(chat_id, user, "admin")
        return

    if text == "آگهی‌های در انتظار" and is_admin:
        pending = [
            item for item in state["listings"]
            if item["status"] == "pending"
        ]
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
                f"آگهی شماره {item['id']}\n"
                f"عنوان: {html.escape(item['title'])}\n"
                f"قیمت: {html.escape(item['price'])}",
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
            send_message(chat_id, "لطفاً عنوان محصول را به صورت متن بفرست.")
            return
        draft["title"] = text[:150]
        user["flow"] = "description"
        send_message(chat_id, "توضیحات محصول را بفرست.")
    elif flow == "description":
        if not text:
            send_message(chat_id, "لطفاً توضیحات را به صورت متن بفرست.")
            return
        draft["description"] = text[:700]
        user["flow"] = "price"
        send_message(chat_id, "قیمت پیشنهادی را بفرست.")
    elif flow == "price":
        if not text:
            send_message(chat_id, "لطفاً قیمت را به صورت متن بفرست.")
            return
        draft["price"] = text[:100]
        user["flow"] = "contact"
        send_message(
            chat_id,
            "راه ارتباطی فروشنده را بفرست؛ مثلاً آیدی تلگرام یا شماره تماس.",
        )
    elif flow == "contact":
        if not text:
            send_message(chat_id, "لطفاً راه ارتباطی را به صورت متن بفرست.")
            return
        draft["contact"] = text[:150]
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
            return
        finish_listing(state, user_id, chat_id, None)

def handle_callback(state, callback):
    user_id = callback["from"]["id"]
    callback_id = callback["id"]
    data = callback.get("data", "")

    if user_id != ADMIN_ID:
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "این عملیات فقط برای مدیر مجاز است.",
            "show_alert": True,
        })
        return

    try:
        action, listing_id = data.split(":", 1)
        listing_id = int(listing_id)
    except (ValueError, TypeError):
        return

    listing = next(
        (item for item in state["listings"]
         if item["id"] == listing_id),
        None,
    )

    if not listing or listing["status"] != "pending":
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "این آگهی قبلاً بررسی شده یا پیدا نشد.",
        })
        return

    if action == "approve":
        publish_listing(listing)
        listing["status"] = "published"
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "آگهی منتشر شد.",
        })
        send_message(
            listing["owner_id"],
            "✅ آگهی شما پس از بررسی تأیید و منتشر شد.",
        )
    elif action == "reject":
        listing["status"] = "rejected"
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "آگهی رد شد.",
        })
        send_message(
            listing["owner_id"],
            "متأسفانه آگهی شما تأیید نشد. برای اصلاح و ارسال دوباره، "
            "از منوی ربات یک آگهی جدید ثبت کن.",
        )

def main():
    state, sha = load_state()

    # این برنامه از polling استفاده می‌کند؛ اگر webhook فعال باشد،
    # Telegram خطا می‌دهد. عمداً webhook را خودکار حذف نمی‌کنیم.
    updates = telegram("getUpdates", {
        "offset": state.get("offset", 0),
        "timeout": 5,
        "allowed_updates": ["message", "callback_query"],
    })

    for update in updates:
        if "message" in update:
            handle_message(state, update["message"])
        elif "callback_query" in update:
            handle_callback(state, update["callback_query"])

        state["offset"] = update["update_id"] + 1
        sha = save_state(state, sha)

if __name__ == "__main__":
    main()
