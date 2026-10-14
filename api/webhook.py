import os
import json
import base64
import html
import hmac
import requests

from http.server import BaseHTTPRequestHandler


# =========================================================
# تنظیمات اصلی
# =========================================================

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATA_TOKEN = os.environ["SELLER_DATA_TOKEN"]
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]

DATA_REPO = "Booknight20/zarinmah-seller-data"
CHANNEL = "@ZarinMahGold"
CHANNEL_URL = "https://t.me/ZarinMahGold"
BOT_URL = "https://t.me/ZarinMahGoldBot"
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
# ارتباط با تلگرام
# =========================================================

def telegram(method, data=None):
    response = requests.post(
        f"{TG_API}/{method}",
        json=data or {},
        timeout=15,
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


# =========================================================
# منوی اصلی ربات
# =========================================================

def section_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📊 قیمت و تحلیل بازار",
                    "callback_data": "section_market",
                }
            ],
            [
                {
                    "text": "💎 محصولات برای فروش",
                    "callback_data": "section_products",
                }
            ],
        ]
    }


def section_back_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📊 قیمت و تحلیل بازار",
                    "callback_data": "section_market",
                }
            ],
            [
                {
                    "text": "💎 محصولات برای فروش",
                    "callback_data": "section_products",
                }
            ],
            [
                {
                    "text": "🏠 بازگشت به منوی اصلی",
                    "callback_data": "section_home",
                }
            ],
        ]
    }


def market_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📊 ورود به کانال زرین ماه",
                    "url": CHANNEL_URL,
                }
            ],
            [
                {
                    "text": "🏠 بازگشت به منو",
                    "callback_data": "section_home",
                }
            ],
        ]
    }


def products_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "📝 ثبت آگهی فروش",
                    "url": f"{BOT_URL}?start=sell",
                }
            ],
            [
                {
                    "text": "💎 مشاهده کانال محصولات",
                    "url": CHANNEL_URL,
                }
            ],
            [
                {
                    "text": "🏠 بازگشت به منو",
                    "callback_data": "section_home",
                }
            ],
        ]
    }


def main_keyboard(is_admin=False):
    rows = [
        [{"text": "انتخاب بخش"}],
        [{"text": "ثبت آگهی فروش"}],
        [{"text": "راهنمای ثبت آگهی"}],
    ]

    if is_admin:
        rows.insert(2, [{"text": "ثبت محصول مدیر"}])
        rows.insert(3, [{"text": "آگهی‌های در انتظار"}])

    return {
        "keyboard": rows,
        "resize_keyboard": True,
    }


def send_main_menu(chat_id, is_admin=False):
    text = (
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        "به زرین ماه خوش آمدید.\n\n"
        "از دکمه‌های زیر بخش موردنظر خود را انتخاب کنید:\n\n"
        "📊 <b>قیمت و تحلیل بازار</b>\n"
        "قیمت‌ها و مطالب تحلیلی بازار طلا و ارز.\n\n"
        "💎 <b>محصولات برای فروش</b>\n"
        "مشاهده کانال محصولات یا ثبت آگهی فروش.\n\n"
        "برای انتخاب بخش، یکی از دکمه‌های زیر را بزنید."
    )

    send_message(
        chat_id,
        text,
        section_keyboard(),
    )

    send_message(
        chat_id,
        "از منوی پایین هم می‌توانی ثبت آگهی را شروع کنی.",
        main_keyboard(is_admin),
    )


# =========================================================
# ذخیره و بازیابی اطلاعات
# اطلاعات قبلی state.json حفظ می‌شود.
# =========================================================

def load_state():
    response = session.get(GH_API, timeout=15)

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
        timeout=15,
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
# ثبت آگهی
# =========================================================

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
        "🌙 <b>زرین ماه | طلای کم‌اجرت</b>\n\n"
        f"🏷 <b>{label}</b>\n"
        f"💎 <b>{title}</b>\n\n"
        f"📋 {description}\n\n"
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
        "price": draft["price"],
        "contact": draft["contact"],
        "photo": draft.get("photo"),
        "status": (
            "pending"
            if draft["kind"] == "user"
            else "published"
        ),
    }

    state["next_id"] += 1
    state["listings"].append(listing)

    if listing["kind"] == "admin":
        publish_listing(listing)

        send_message(
            chat_id,
            "✅ محصول مدیر در کانال منتشر شد.",
        )

    else:
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


# =========================================================
# دریافت پیام‌های کاربر
# =========================================================

def handle_message(state, message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]
    text = (message.get("text") or "").strip()

    user = get_user(state, user_id)
    is_admin = user_id == ADMIN_ID

    # لغو عملیات
    if text == "/cancel":
        cancel_flow(user)

        send_message(
            chat_id,
            "عملیات لغو شد.",
            main_keyboard(is_admin),
        )
        return

    # شروع ربات و بررسی پارامتر start
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)

        command = parts[0].split("@", 1)[0]
        payload = (
            parts[1].strip().lower()
            if len(parts) > 1
            else ""
        )

        if command == "/start":
            cancel_flow(user)

            # لینک مستقیم ثبت آگهی
            if payload == "sell":
                start_listing(chat_id, user, "user")
                return

            # منوی دو‌بخشی
            send_main_menu(chat_id, is_admin)
            return

    # انتخاب بخش از منوی پایین
    if text == "انتخاب بخش":
        send_message(
            chat_id,
            "🌙 <b>انتخاب بخش زرین ماه</b>\n\n"
            "کدام بخش را می‌خواهی؟",
            section_keyboard(),
        )
        return

    # راهنمای ثبت آگهی
    if text == "راهنمای ثبت آگهی":
        send_message(
            chat_id,
            "💎 <b>راهنمای ثبت آگهی در زرین ماه</b>\n\n"
            "۱. عنوان محصول را بفرست.\n"
            "۲. توضیحات محصول را وارد کن.\n"
            "۳. قیمت پیشنهادی را بفرست.\n"
            "۴. راه ارتباطی فروشنده را وارد کن.\n"
            "۵. عکس محصول را بفرست یا بنویس «بدون عکس».\n\n"
            "آگهی کاربران قبل از انتشار توسط مدیر بررسی می‌شود.\n"
            "برای لغو هر زمان /cancel را بفرست.",
        )
        return

    # شروع ثبت آگهی
    if text == "ثبت آگهی فروش":
        start_listing(chat_id, user, "user")
        return

    # ثبت محصول مدیر
    if text == "ثبت محصول مدیر" and is_admin:
        start_listing(chat_id, user, "admin")
        return

    # آگهی‌های در انتظار مدیر
    if text == "آگهی‌های در انتظار" and is_admin:
        pending = [
            item for item in state["listings"]
            if item["status"] == "pending"
        ]

        if not pending:
            send_message(
                chat_id,
                "در حال حاضر آگهی در انتظاری وجود ندارد.",
            )
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

    # مراحل ثبت آگهی
    flow = user.get("flow")

    if not flow:
        send_message(
            chat_id,
            "برای شروع، از منوی ربات استفاده کن.",
            main_keyboard(is_admin),
        )
        return

    draft = user["draft"]

    if flow == "title":
        if not text:
            send_message(
                chat_id,
                "لطفاً عنوان محصول را به صورت متن بفرست.",
            )
            return

        draft["title"] = text[:150]
        user["flow"] = "description"

        send_message(chat_id, "توضیحات محصول را بفرست.")

    elif flow == "description":
        if not text:
            send_message(
                chat_id,
                "لطفاً توضیحات را به صورت متن بفرست.",
            )
            return

        draft["description"] = text[:700]
        user["flow"] = "price"

        send_message(chat_id, "قیمت پیشنهادی را بفرست.")

    elif flow == "price":
        if not text:
            send_message(
                chat_id,
                "لطفاً قیمت را به صورت متن بفرست.",
            )
            return

        draft["price"] = text[:100]
        user["flow"] = "contact"

        send_message(
            chat_id,
            "راه ارتباطی فروشنده را بفرست؛ "
            "مثلاً آیدی تلگرام یا شماره تماس.",
        )

    elif flow == "contact":
        if not text:
            send_message(
                chat_id,
                "لطفاً راه ارتباطی را به صورت متن بفرست.",
            )
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

        finish_listing(state, user_id, chat_id)


# =========================================================
# دکمه‌های شیشه‌ای
# =========================================================

def handle_callback(state, callback):
    user_id = callback["from"]["id"]
    callback_id = callback["id"]
    data = callback.get("data", "")

    # دکمه‌های منوی دو‌بخشی برای همه کاربران
    if data == "section_home":
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
        })

        send_main_menu(
            callback["message"]["chat"]["id"],
            user_id == ADMIN_ID,
        )
        return

    if data == "section_market":
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "بخش قیمت و تحلیل بازار",
        })

        send_message(
            callback["message"]["chat"]["id"],
            "📊 <b>قیمت و تحلیل بازار</b>\n\n"
            "برای مشاهده قیمت‌ها و مطالب بازار، وارد کانال "
            "زرین ماه شو.\n\n"
            "هشتگ مطالب این بخش:\n"
            "#قیمت_و_تحلیل\n\n"
            "توجه: این دکمه کانال را باز می‌کند؛ "
            "فهرست خودکار و فیلترشده همه مطالب هشتگ نیست.",
            market_keyboard(),
        )
        return

    if data == "section_products":
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "بخش محصولات برای فروش",
        })

        send_message(
            callback["message"]["chat"]["id"],
            "💎 <b>محصولات برای فروش</b>\n\n"
            "در این بخش آگهی محصولات طلا منتشر می‌شود.\n\n"
            "هشتگ این بخش:\n"
            "#محصولات_برای_فروش\n\n"
            "اگر طلایی برای فروش داری، می‌توانی آگهی‌ات را "
            "از طریق دکمه زیر ارسال کنی. آگهی پس از تأیید "
            "مدیر در کانال منتشر می‌شود.",
            products_keyboard(),
        )
        return

    # از این قسمت به بعد، فقط مدیر مجاز است
    # آگهی‌ها را تأیید یا رد کند.
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
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "دستور معتبر نیست.",
        })
        return

    if action not in ("approve", "reject"):
        telegram("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "دستور معتبر نیست.",
        })
        return

    listing = next(
        (
            item for item in state["listings"]
            if item["id"] == listing_id
        ),
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
            "متأسفانه آگهی شما تأیید نشد. "
            "برای اصلاح و ارسال دوباره، از منوی ربات "
            "یک آگهی جدید ثبت کن.",
        )


# =========================================================
# وب‌هوک
# =========================================================

class handler(BaseHTTPRequestHandler):

    def respond(self, status, message):
        body = json.dumps(
            {
                "ok": status == 200,
                "message": message,
            },
            ensure_ascii=False,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
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

            update = json.loads(self.rfile.read(length))

            if "update_id" not in update:
                self.respond(400, "Missing update ID")
                return

            state, sha = load_state()
            update_id = int(update["update_id"])

            # جلوگیری از پردازش دوباره یک رویداد
            if update_id <= state.get("last_update_id", -1):
                self.respond(200, "Duplicate update ignored")
                return

            if "message" in update:
                handle_message(state, update["message"])

            elif "callback_query" in update:
                handle_callback(
                    state,
                    update["callback_query"],
                )

            state["last_update_id"] = update_id
            save_state(state, sha)

            self.respond(200, "OK")

        except Exception:
            # برای جلوگیری از افشای اطلاعات محرمانه،
            # جزئیات خطا به پاسخ عمومی ارسال نمی‌شود.
            try:
                self.respond(500, "Temporary processing error")
            except Exception:
                pass
