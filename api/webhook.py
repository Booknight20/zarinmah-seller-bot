
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

    if photo:
        telegram("sendPhoto", {
            "chat_id": CHANNEL,
            "photo": photo,
            "caption": caption[:1000],
            "parse_mode": "HTML",
        })
    else:
        send_message(CHANNEL, caption)


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
            "پس از ذخیره وضعیت، انتشار در کانال انجام می‌شود.",
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
        "پس از تأیید، در کانال منتشر می‌شود.",
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

    if text == "ثبت محصول مدیر" and is_admin:
        start_listing(chat_id, user, "admin")
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
                f"قیمت: {html.escape(item['price'])}",
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

    if not listing or listing.get("status") != "pending":
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "این آگهی قبلاً بررسی شده یا پیدا نشد.",
            "show_alert": True,
        })
        return sha

    if action == "approve":
        listing.setdefault("weight", "ثبت نشده")
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
            received_secret, WEBHOOK_SECRET
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

                handle_callback(
                    state, callback, sha, update_id
                )

                # وضعیت نهایی آپدیت ذخیره می‌شود.
                state["last_update_id"] = update_id
                sha = save_state(state, sha)

                self.respond(200, "OK")
                return

            elif "message" in update:
                admin_listing_id = handle_message(
                    state, update["message"]
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

                    send_message(
                        ADMIN_ID,
                        "✅ محصول مدیر با موفقیت در کانال "
                        "زرین ماه منتشر شد.",
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
