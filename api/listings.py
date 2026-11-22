import json
import os
import re
import uuid
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote

import requests

# ZarinMah Listings API — Supabase backend
ADMIN_ID = "8816884849"
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}" if TELEGRAM_TOKEN else ""
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_PUBLISHABLE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
STAGING_SUPABASE_URL = "https://qijahxokldkaqmjinfvy.supabase.co"
SUPABASE_TIMEOUT = 30
PREVIEW_DRY_RUN = os.environ.get("ZARINMAH_PREVIEW_DRY_RUN", "").strip().lower() == "true"
MAX_BODY_SIZE = 4 * 1024 * 1024
MAX_FILE_SIZE = 1536 * 1024

PROVINCES = {
    "آذربایجان شرقی", "آذربایجان غربی", "اردبیل", "اصفهان", "البرز",
    "ایلام", "بوشهر", "تهران", "چهارمحال و بختیاری", "خراسان جنوبی",
    "خراسان رضوی", "خراسان شمالی", "خوزستان", "زنجان", "سمنان",
    "سیستان و بلوچستان", "فارس", "قزوین", "قم", "کردستان", "کرمان",
    "کرمانشاه", "کهگیلویه و بویراحمد", "گلستان", "گیلان", "لرستان",
    "مازندران", "مرکزی", "هرمزگان", "همدان", "یزد",
}
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


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def clean(value, limit=1500):
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value or ""))
    return value.strip()[:limit]


def digits(value):
    return str(value or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


def phone_digits(value):
    return re.sub(r"\D", "", digits(value))


def valid_phone(value):
    return 8 <= len(phone_digits(value)) <= 15


def html(value):
    value = str(value or "")
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def sb_headers(extra=None, public=False, user_token=None):
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL در تنظیمات Vercel وجود ندارد.")
    if user_token and SUPABASE_PUBLISHABLE_KEY:
        result = {
            "apikey": SUPABASE_PUBLISHABLE_KEY,
            "Authorization": f"Bearer {user_token}",
        }
    elif public and SUPABASE_PUBLISHABLE_KEY:
        result = {"apikey": SUPABASE_PUBLISHABLE_KEY}
    else:
        if not SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY در تنظیمات Vercel وجود ندارد.")
        result = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        }
    if extra:
        result.update(extra)
    return result


def sb_request(method, path, *, params=None, payload=None, raw=None, content_type=None, prefer=None, public=False, user_token=None):
    headers = sb_headers(public=public, user_token=user_token)
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    if prefer:
        headers["Prefer"] = prefer
    response = requests.request(
        method, SUPABASE_URL + path, headers=headers, params=params,
        json=payload, data=raw, timeout=SUPABASE_TIMEOUT,
    )
    if not response.ok:
        try:
            body = response.json()
            detail = body.get("message") or body.get("details") or body.get("hint") or str(body)
        except Exception:
            detail = response.text[:400]
        if response.status_code == 409 and "profiles_username_lower_uidx" in response.text:
            raise ValueError("این نام کاربری قبلاً انتخاب شده است؛ نام دیگری وارد کنید.")
        raise RuntimeError(f"Supabase {response.status_code}: {detail}")
    if response.status_code == 204 or not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return None


class AuthError(Exception):
    pass


class ProfileIncompleteError(Exception):
    pass


def authenticate_user(handler):
    authorization = clean(handler.headers.get("Authorization", ""), 2048)
    if not authorization.startswith("Bearer "):
        raise AuthError("برای ادامه ابتدا وارد حساب کاربری شوید.")
    auth_api_key = SUPABASE_PUBLISHABLE_KEY or SUPABASE_SERVICE_ROLE_KEY
    if not SUPABASE_URL or not auth_api_key:
        raise RuntimeError("تنظیمات احراز هویت Supabase کامل نیست.")
    try:
        response = requests.get(
            SUPABASE_URL + "/auth/v1/user",
            headers={
                "apikey": auth_api_key,
                "Authorization": authorization,
            },
            timeout=15,
        )
    except requests.RequestException:
        raise RuntimeError("بررسی حساب کاربری موقتاً ناموفق بود.")
    if response.status_code != 200:
        raise AuthError("نشست ورود معتبر نیست یا منقضی شده است؛ دوباره وارد شوید.")
    try:
        user = response.json()
    except ValueError:
        raise AuthError("پاسخ احراز هویت معتبر نیست.")
    user_id = clean(user.get("id"), 80)
    phone = clean(user.get("phone"), 30)
    email = clean(user.get("email"), 254).lower()
    if not user_id:
        raise AuthError("شناسه حساب کاربری معتبر نیست.")
    access_token = authorization[7:].strip()
    if valid_phone(phone):
        return {"id": user_id, "phone": phone, "email": email, "access_token": access_token}
    if SUPABASE_URL == STAGING_SUPABASE_URL and email:
        return {"id": user_id, "phone": "", "email": email, "access_token": access_token}
    raise AuthError("حساب کاربری باید شماره موبایل تأییدشده داشته باشد.")


def get_user_profile(user, access_token=None):
    rows = sb_request(
        "GET", "/rest/v1/profiles",
        params={
            "id": f"eq.{user['id']}",
            "select": "id,full_name,mobile,email,username,province,city,address,national_id,is_active",
            "limit": "1",
        },
        user_token=access_token,
    ) or []
    return rows[0] if rows else None


def require_complete_profile(user, access_token=None):
    profile = get_user_profile(user, access_token=access_token)
    if not profile:
        raise ProfileIncompleteError("پروفایل حساب شما هنوز ایجاد نشده است. صفحه را تازه‌سازی کنید یا با پشتیبانی تماس بگیرید.")
    if profile.get("is_active") is not True:
        raise AuthError("حساب کاربری شما غیرفعال است. برای پیگیری با پشتیبانی تماس بگیرید.")
    full_name = clean(profile.get("full_name"), 100)
    if not full_name:
        raise ProfileIncompleteError("برای ادامه، ابتدا نام و نام خانوادگی خود را در حساب کاربری تکمیل کنید.")
    username = clean(profile.get("username"), 24)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,23}", username):
        raise ProfileIncompleteError("برای ادامه، یک نام کاربری معتبر و یکتا در حساب خود ثبت کنید.")
    if not clean(profile.get("address"), 500) or len(clean(profile.get("address"), 500)) < 10:
        raise ProfileIncompleteError("برای ادامه، ابتدا نشانی محل سکونت خود را در حساب کاربری تکمیل کنید.")
    if not valid_national_id(profile.get("national_id")):
        raise ProfileIncompleteError("برای ادامه، ابتدا کد ملی معتبر خود را در حساب کاربری ثبت کنید.")
    if clean(profile.get("province"), 80) not in PROVINCES or not clean(profile.get("city"), 100):
        raise ProfileIncompleteError("برای ادامه، استان و شهر محل سکونت را در حساب کاربری تکمیل کنید.")
    return profile


def valid_national_id(value):
    value = digits(value)
    if not re.fullmatch(r"\d{10}", value) or len(set(value)) == 1:
        return False
    checksum = int(value[9])
    total = sum(int(value[i]) * (10 - i) for i in range(9))
    remainder = total % 11
    return checksum == (remainder if remainder < 2 else 11 - remainder)


def update_user_profile(user, data, access_token=None):
    full_name = clean(data.get("full_name"), 100)
    username = clean(data.get("username"), 24).lower()
    email = clean(data.get("email"), 254).lower()
    province = clean(data.get("province"), 80)
    city = clean(data.get("city"), 100)
    address = clean(data.get("address"), 500)
    national_id = digits(data.get("national_id"))

    if len(full_name.split()) < 2 or len(full_name) < 5:
        raise ValueError("نام و نام خانوادگی کامل را وارد کنید.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,23}", username):
        raise ValueError("نام کاربری باید ۳ تا ۲۴ نویسه و شامل حروف انگلیسی، عدد، نقطه، خط تیره یا زیرخط باشد.")
    if email and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("نشانی ایمیل معتبر وارد کنید یا کادر آن را خالی بگذارید.")
    if province not in PROVINCES or not city:
        raise ValueError("استان و شهر محل سکونت را از فهرست انتخاب کنید.")
    if len(address) < 10:
        raise ValueError("آدرس کامل محل سکونت را وارد کنید.")
    if not valid_national_id(national_id):
        raise ValueError("کد ملی معتبر ۱۰ رقمی وارد کنید.")

    existing = get_user_profile(user, access_token=access_token)
    if existing and existing.get("is_active") is not True:
        raise AuthError("حساب کاربری شما غیرفعال است. برای پیگیری با پشتیبانی تماس بگیرید.")
    is_staging_email = SUPABASE_URL == STAGING_SUPABASE_URL and not user.get("phone") and bool(user.get("email"))
    if is_staging_email:
        mobile = clean(data.get("mobile"), 30)
        if not valid_phone(mobile):
            raise ValueError("برای حساب آزمایشی، شماره تماس معتبر وارد کنید.")
    else:
        mobile = clean(user.get("phone"), 30)
    profile_payload = {
        "full_name": full_name,
        "username": username,
        "email": email or None,
        "mobile": mobile or None,
        "province": province,
        "city": city,
        "address": address,
        "national_id": national_id,
        "updated_at": now_iso(),
    }
    if existing:
        rows = sb_request(
            "PATCH", "/rest/v1/profiles",
            params={"id": f"eq.{user['id']}"},
            payload=profile_payload,
            prefer="return=representation",
            user_token=access_token,
        ) or []
    else:
        rows = sb_request(
            "POST", "/rest/v1/profiles",
            payload={
                "id": user["id"],
                **profile_payload,
            },
            prefer="return=representation",
            user_token=access_token,
        ) or []
    if not rows:
        raise ProfileIncompleteError("ذخیره پروفایل انجام نشد. کمی بعد دوباره تلاش کنید.")
    return rows[0]




def get_user_role(user, access_token=None):
    rows = sb_request(
        "GET", "/rest/v1/user_roles",
        params={"user_id": f"eq.{user['id']}", "select": "role", "limit": "1"},
        user_token=access_token,
    ) or []
    return rows[0].get("role", "user") if rows else "user"


def require_roles(user, allowed_roles, message="این عملیات برای نقش کاربری شما مجاز نیست."):
    access_token = user.get("access_token")
    profile = require_complete_profile(user, access_token=access_token)
    if get_user_role(user, access_token=access_token) not in set(allowed_roles):
        raise AuthError(message)
    return profile


def require_admin(user):
    return require_roles(user, {"admin"}, "این عملیات فقط برای مدیر کل سامانه مجاز است.")


def require_listing_manager(user):
    return require_roles(user, {"admin", "listing_manager"}, "این عملیات فقط برای مدیر آگهی‌ها یا مدیر کل مجاز است.")


def require_member_manager(user):
    return require_roles(user, {"admin", "member_manager"}, "این عملیات فقط برای مدیر اعضا یا مدیر کل مجاز است.")



def sb_request_counted(path, params, access_token=None):
    headers = sb_headers({"Prefer": "count=exact"}, user_token=access_token)
    response = requests.get(
        SUPABASE_URL + path, headers=headers, params=params, timeout=SUPABASE_TIMEOUT
    )
    if not response.ok:
        try:
            body = response.json()
            detail = body.get("message") or body.get("details") or body.get("hint") or str(body)
        except Exception:
            detail = response.text[:400]
        raise RuntimeError(f"Supabase {response.status_code}: {detail}")
    try:
        rows = response.json() if response.content else []
    except ValueError:
        rows = []
    content_range = response.headers.get("Content-Range", "")
    try:
        total = int(content_range.rsplit("/", 1)[1])
    except (IndexError, ValueError):
        total = len(rows)
    return rows, total


def account_page(value):
    try:
        return max(1, min(int(value), 100000))
    except (TypeError, ValueError):
        return 1


def account_search(value):
    value = clean(value, 80)
    return re.sub(r"[^0-9A-Za-z\u0600-\u06FF\s@._+\-]", "", value).strip()


def get_account_data(user, query=None):
    query = query or {}
    access_token = user.get("access_token")
    profile = require_complete_profile(user, access_token=access_token)
    role = get_user_role(user, access_token=access_token)
    admin = role == "admin"
    per_page = 20
    listings_page = account_page((query.get("listings_page") or ["1"])[0])
    requests_page = account_page((query.get("requests_page") or ["1"])[0])
    listings_status = clean((query.get("listings_status") or ["all"])[0], 20)
    requests_status = clean((query.get("requests_status") or ["all"])[0], 20)
    listings_search = account_search((query.get("listings_search") or [""])[0])
    requests_search = account_search((query.get("requests_search") or [""])[0])

    listing_params = {
        "select": "id,title,product_type,weight,weight_unit,purity,province,city,description,pricing_method,pricing_value,price,image_url,status,created_at,updated_at,deleted_at,seller_name,seller_mobile,owner_user_id",
        "order": "created_at.desc",
        "limit": str(per_page),
        "offset": str((listings_page - 1) * per_page),
    }
    request_params = {
        "select": "id,listing_id,buyer_name,buyer_mobile,message,status,created_at,updated_at,buyer_user_id",
        "order": "created_at.desc",
        "limit": str(per_page),
        "offset": str((requests_page - 1) * per_page),
    }
    if not admin:
        listing_params["owner_user_id"] = f"eq.{user['id']}"
        request_params["buyer_user_id"] = f"eq.{user['id']}"

    if listings_status == "deleted":
        listing_params["deleted_at"] = "not.is.null"
    else:
        listing_params["deleted_at"] = "is.null"
        if listings_status in ("pending", "published", "sold"):
            listing_params["status"] = f"eq.{listings_status}"
        elif listings_status != "all":
            listings_status = "all"

    if requests_status in ("pending", "forwarded"):
        request_params["status"] = f"eq.{requests_status}"
    elif requests_status != "all":
        requests_status = "all"

    if listings_search:
        term = f"*{listings_search}*"
        listing_params["or"] = (
            f"(title.ilike.{term},product_type.ilike.{term},"
            f"seller_name.ilike.{term},seller_mobile.ilike.{term},city.ilike.{term})"
        )
    if requests_search:
        term = f"*{requests_search}*"
        request_params["or"] = (
            f"(buyer_name.ilike.{term},buyer_mobile.ilike.{term},message.ilike.{term})"
        )

    listings, listings_total = sb_request_counted("/rest/v1/listings", listing_params, access_token=access_token)
    requests_rows, requests_total = sb_request_counted("/rest/v1/purchase_requests", request_params, access_token=access_token)

    titles = {
        str(item.get("id")): item.get("title") or item.get("product_type") or "آگهی"
        for item in listings
    }
    for request_row in requests_rows:
        request_row["listing_title"] = titles.get(
            str(request_row.get("listing_id")),
            f"آگهی شماره {request_row.get('listing_id')}",
        )
        if not admin:
            request_row.pop("buyer_mobile", None)

    payment_orders = sb_request("GET", "/rest/v1/seller_payment_orders", params={
        "select": "id,purpose,listing_id,package_name,amount_toman,payment_method,status,payer_name,tracking_code,rejection_reason,created_at,updated_at,reviewed_at",
        "user_id": "eq." + str(user["id"]), "order": "created_at.desc", "limit": "50",
    }, user_token=access_token) or []

    return {
        "ok": True,
        "profile": profile,
        "role": role,
        "listings": listings,
        "purchase_requests": requests_rows,
        "payment_orders": payment_orders,
        "pagination": {
            "per_page": per_page,
            "listings": {
                "page": listings_page,
                "total": listings_total,
                "total_pages": max(1, (listings_total + per_page - 1) // per_page),
                "status": listings_status,
                "search": listings_search,
            },
            "requests": {
                "page": requests_page,
                "total": requests_total,
                "total_pages": max(1, (requests_total + per_page - 1) // per_page),
                "status": requests_status,
                "search": requests_search,
            },
        },
    }


def record_site_visit(data):
    visitor_id = clean(data.get("visitor_id"), 64)
    session_id = clean(data.get("session_id"), 64)
    try:
        visitor_id = str(uuid.UUID(visitor_id))
        session_id = str(uuid.UUID(session_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("شناسه ناشناس بازدید معتبر نیست.")
    path = clean(data.get("path") or "/", 200).split("?", 1)[0].split("#", 1)[0]
    if not re.fullmatch(r"/[A-Za-z0-9/_-]{0,180}", path) or path.startswith("//"):
        path = "/"
    sb_request(
        "POST", "/rest/v1/site_visit_events",
        payload={"visitor_id": visitor_id, "session_id": session_id, "path": path},
        prefer="resolution=ignore-duplicates,return=minimal",
        public=True,
    )
    return {"ok": True}


def get_admin_dashboard_stats(user):
    require_admin(user)
    result = sb_request(
        "POST", "/rest/v1/rpc/zarinmah_admin_dashboard_stats",
        payload={},
        user_token=user.get("access_token"),
    )
    if not isinstance(result, dict):
        raise RuntimeError("گزارش مدیریتی از پایگاه داده دریافت نشد.")
    return {"ok": True, "stats": result}



def get_admin_members(user, query=None):
    require_admin(user)
    query = query or {}
    access_token = user.get("access_token")
    page = account_page((query.get("members_page") or ["1"])[0])
    search = account_search((query.get("members_search") or [""])[0])
    status_filter = clean((query.get("members_status") or ["all"])[0], 20)
    role_filter = clean((query.get("members_role") or ["all"])[0], 40)
    listing_filter = clean((query.get("members_listings") or ["all"])[0], 20)
    sort_by = clean((query.get("members_sort") or ["newest"])[0], 20)
    if status_filter not in {"all", "active", "inactive"}: status_filter = "all"
    if role_filter not in {"all", "user", "listing_manager", "member_manager", "admin"}: role_filter = "all"
    if listing_filter not in {"all", "with", "without"}: listing_filter = "all"
    if sort_by not in {"newest", "oldest", "most_listings"}: sort_by = "newest"
    params = {
        "select": "id,full_name,mobile,province,city,address,national_id,is_active,created_at,updated_at",
        "order": "created_at.desc", "limit": "10000", "offset": "0",
    }
    if search:
        term = "*" + search + "*"
        params["or"] = "(full_name.ilike." + term + ",mobile.ilike." + term + ")"
    if status_filter == "active": params["is_active"] = "eq.true"
    elif status_filter == "inactive": params["is_active"] = "eq.false"
    members = sb_request("GET", "/rest/v1/profiles", params=params, user_token=access_token) or []
    all_ids = [str(item.get("id")) for item in members if item.get("id")]
    role_map, listing_counts = {}, {}
    if all_ids:
        role_rows = sb_request("GET", "/rest/v1/user_roles", params={
            "select": "user_id,role", "user_id": "in.(" + ",".join(all_ids) + ")", "limit": "10000",
        }, user_token=access_token) or []
        role_map = {str(row.get("user_id")): row.get("role") or "user" for row in role_rows}
        listing_rows = sb_request("GET", "/rest/v1/listings", params={
            "select": "owner_user_id,status,deleted_at", "owner_user_id": "in.(" + ",".join(all_ids) + ")", "limit": "10000",
        }, user_token=access_token) or []
        for row in listing_rows:
            owner = str(row.get("owner_user_id") or "")
            if owner not in all_ids: continue
            item = listing_counts.setdefault(owner, {"total": 0, "active": 0, "pending": 0, "published": 0, "sold": 0, "rejected": 0, "deleted": 0})
            item["total"] += 1
            if row.get("deleted_at") is not None: item["deleted"] += 1
            else:
                item["active"] += 1
                if row.get("status") in ("pending", "published", "sold", "rejected"): item[row["status"]] += 1
    filtered = []
    for member in members:
        mid = str(member.get("id"))
        member["role"] = role_map.get(mid, "user")
        member["listing_counts"] = listing_counts.get(mid, {"total": 0, "active": 0, "pending": 0, "published": 0, "sold": 0, "rejected": 0, "deleted": 0})
        if role_filter != "all" and member["role"] != role_filter: continue
        has_listings = member["listing_counts"]["total"] > 0
        if listing_filter == "with" and not has_listings: continue
        if listing_filter == "without" and has_listings: continue
        filtered.append(member)
    if sort_by == "most_listings": filtered.sort(key=lambda m: (m["listing_counts"]["total"], m.get("created_at") or ""), reverse=True)
    elif sort_by == "oldest": filtered.sort(key=lambda m: m.get("created_at") or "")
    else: filtered.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    total, per_page = len(filtered), 16
    page_members = filtered[(page - 1) * per_page:page * per_page]
    return {"ok": True, "members": page_members, "pagination": {
        "page": page, "per_page": per_page, "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
        "search": search, "status": status_filter, "role": role_filter,
        "listings": listing_filter, "sort": sort_by,
    }}


def record_admin_audit(user, target_user_id, action, old_values=None, new_values=None):
    access_token = user.get("access_token")
    sb_request("POST", "/rest/v1/admin_audit_logs", payload={
        "actor_user_id": str(user.get("id")), "target_user_id": str(target_user_id),
        "action": clean(action, 60), "old_values": old_values or {},
        "new_values": new_values or {}, "created_at": now_iso(),
    }, prefer="return=minimal", user_token=access_token)


def get_admin_member_audit(user, query=None):
    require_admin(user)
    query = query or {}
    member_id = clean((query.get("member_id") or [""])[0], 80)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", member_id):
        raise ValueError("شناسه عضو معتبر نیست.")
    rows = sb_request("GET", "/rest/v1/admin_audit_logs", params={
        "select": "id,actor_user_id,target_user_id,action,old_values,new_values,created_at",
        "target_user_id": "eq." + member_id, "order": "created_at.desc", "limit": "50",
    }, user_token=user.get("access_token")) or []
    return {"ok": True, "audit": rows}


def get_admin_member_listings(user, query=None):
    require_admin(user)
    query = query or {}
    member_id = clean((query.get("member_id") or [""])[0], 80)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", member_id):
        raise ValueError("شناسه عضو معتبر نیست.")
    page = account_page((query.get("member_listings_page") or ["1"])[0])
    per_page = 10
    params = {
        "select": "id,title,product_type,weight,weight_unit,purity,province,city,description,pricing_method,pricing_value,price,image_url,image_url_2,status,created_at,updated_at,deleted_at,invoice_path,rejection_reason_code,rejection_reason_label,rejection_reason_text,rejected_at",
        "owner_user_id": "eq." + member_id, "order": "created_at.desc",
        "limit": str(per_page), "offset": str((page - 1) * per_page),
    }
    params["deleted_at"] = "is.null"
    rows, total = sb_request_counted("/rest/v1/listings", params, access_token=user.get("access_token"))
    listings = []
    for row in rows:
        item = public_listing(row)
        item["updated_at"] = row.get("updated_at")
        item["deleted_at"] = row.get("deleted_at")
        item["invoice_available"] = bool(row.get("invoice_path"))
        item["image_url_2"] = row.get("image_url_2")
        listings.append(item)
    return {"ok": True, "member_id": member_id, "listings": listings, "pagination": {
        "page": page, "per_page": per_page, "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }}


def admin_member_update(user, data):
    require_admin(user)
    access_token = user.get("access_token")
    member_id = clean(data.get("member_id"), 80)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", member_id):
        raise ValueError("شناسه عضو معتبر نیست.")
    if member_id == str(user["id"]) and data.get("is_active") is False:
        raise ValueError("برای جلوگیری از قفل‌شدن پنل، نمی‌توانید حساب خودتان را غیرفعال کنید.")
    full_name = clean(data.get("full_name"), 100)
    if len(full_name.split()) < 2 or len(full_name) < 5:
        raise ValueError("نام و نام خانوادگی معتبر وارد کنید.")
    active_value = data.get("is_active")
    if not isinstance(active_value, bool):
        raise ValueError("وضعیت فعال‌بودن حساب معتبر نیست.")
    old_rows = sb_request("GET", "/rest/v1/profiles", params={
        "id": "eq." + member_id, "select": "id,full_name,is_active", "limit": "1",
    }, user_token=access_token) or []
    if not old_rows: raise ValueError("عضو پیدا نشد.")
    rows = sb_request("PATCH", "/rest/v1/profiles", params={"id": "eq." + member_id},
        payload={"full_name": full_name, "updated_at": now_iso()},
        prefer="return=representation", user_token=access_token) or []
    if not rows: raise ValueError("عضو پیدا نشد یا اطلاعات او به‌روزرسانی نشد.")
    previous_active = bool(old_rows[0].get("is_active"))
    if previous_active != active_value:
        sb_request("POST", "/rest/v1/rpc/zarinmah_admin_set_member_active",
            payload={"p_member_id": member_id, "p_is_active": active_value},
            user_token=access_token)
    record_admin_audit(user, member_id, "member_profile_update",
        {"full_name": old_rows[0].get("full_name"), "is_active": previous_active},
        {"full_name": full_name, "is_active": active_value})
    return {"ok": True, "member": rows[0], "message": "اطلاعات عضو به‌روزرسانی شد."}


def admin_member_role_update(user, data):
    require_admin(user)
    access_token = user.get("access_token")
    member_id = clean(data.get("member_id"), 80)
    new_role = clean(data.get("role"), 40)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", member_id):
        raise ValueError("شناسه عضو معتبر نیست.")
    if member_id == str(user.get("id") or ""):
        raise ValueError("برای جلوگیری از قفل‌شدن دسترسی، نقش حساب خودتان قابل تغییر نیست.")
    if new_role not in {"user", "listing_manager", "member_manager"}:
        raise ValueError("ایجاد یا واگذاری مدیر کل از این بخش امکان‌پذیر نیست.")
    profiles = sb_request("GET", "/rest/v1/profiles", params={
        "id": "eq." + member_id, "select": "id", "limit": "1",
    }, user_token=access_token) or []
    if not profiles: raise ValueError("عضو موردنظر پیدا نشد.")
    existing = sb_request("GET", "/rest/v1/user_roles", params={
        "user_id": "eq." + member_id, "select": "role", "limit": "1",
    }, user_token=access_token) or []
    old_role = (existing[0].get("role") or "user") if existing else "user"
    if old_role == "admin": raise ValueError("نقش مدیر کل قابل تغییر نیست.")
    if old_role == new_role:
        return {"ok": True, "changed": False, "old_role": old_role, "new_role": new_role, "message": "این عضو از قبل همین نقش را دارد."}
    if existing:
        rows = sb_request("PATCH", "/rest/v1/user_roles",
            params={"user_id": "eq." + member_id, "role": "neq.admin"},
            payload={"role": new_role}, prefer="return=representation", user_token=access_token) or []
    else:
        rows = sb_request("POST", "/rest/v1/user_roles",
            payload={"user_id": member_id, "role": new_role},
            prefer="return=representation", user_token=access_token) or []
    if not rows: raise ValueError("تغییر نقش ذخیره نشد؛ فهرست را تازه‌سازی کنید.")
    record_admin_audit(user, member_id, "member_role_update", {"role": old_role}, {"role": new_role})
    return {"ok": True, "changed": True, "old_role": old_role, "new_role": new_role, "message": "نقش عضو با موفقیت به‌روزرسانی شد."}


def get_admin_storage_usage(user):
    require_admin(user)
    access_token = user.get("access_token")
    quota_bytes = 1024 ** 3
    result = []
    for bucket in ("listing-images", "listing-invoices"):
        total_bytes = file_count = unknown_size_files = processed = 0
        truncated, diagnostics = False, []
        pending_prefixes, visited_prefixes = [""], set()
        while pending_prefixes:
            prefix = pending_prefixes.pop(0)
            if prefix in visited_prefixes: continue
            visited_prefixes.add(prefix)
            offset = 0
            while True:
                objects = sb_request("POST", f"/storage/v1/object/list/{bucket}",
                    payload={"prefix": prefix, "limit": 1000, "offset": offset},
                    user_token=access_token) or []
                if not isinstance(objects, list): raise RuntimeError("پاسخ فهرست فایل‌های Storage معتبر نیست.")
                for item in objects:
                    processed += 1
                    if processed > 10000:
                        truncated = True
                        break
                    name = clean(item.get("name"), 1000)
                    if item.get("id") is None:
                        if name and not name.startswith("/") and all(part not in ("", ".", "..") for part in name.split("/")):
                            child = prefix + name.strip("/") + "/"
                            if child not in visited_prefixes: pending_prefixes.append(child)
                        continue
                    file_count += 1
                    metadata = item.get("metadata") or {}
                    size = next((metadata.get(k) for k in ("size", "contentLength", "content_length") if metadata.get(k) is not None), item.get("size"))
                    try: size = int(size) if size is not None else None
                    except (TypeError, ValueError): size = None
                    if size is None or size < 0:
                        unknown_size_files += 1
                        if len(diagnostics) < 5: diagnostics.append({"stage": "file_size_missing", "has_file_id": True})
                    else: total_bytes += size
                if truncated or len(objects) < 1000: break
                offset += 1000
                if offset >= 10000:
                    truncated = True
                    break
            if truncated: break
        result.append({"bucket": bucket, "bytes": total_bytes, "files": file_count,
            "unknown_size_files": unknown_size_files, "quota_bytes": quota_bytes,
            "used_percent": round(total_bytes / quota_bytes * 100, 2),
            "truncated": truncated, "diagnostics": diagnostics})
    return {"ok": True, "storage": result, "quota_bytes": quota_bytes}


def create_admin_invoice_link(user, listing_id):
    require_admin(user)
    access_token = user.get("access_token")
    try: listing_id = int(listing_id)
    except (TypeError, ValueError): raise ValueError("شناسه آگهی معتبر نیست.")
    rows = sb_request("GET", "/rest/v1/listings", params={
        "id": "eq." + str(listing_id), "select": "id,invoice_path", "limit": "1",
    }, user_token=access_token) or []
    if not rows: raise ValueError("آگهی پیدا نشد.")
    path = clean(rows[0].get("invoice_path"), 500)
    parts = path.split("/")
    if len(parts) != 2 or parts[0] != "website" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", parts[1]) or not re.search(r"\.(?:jpg|jpeg|png|pdf)$", parts[1], re.I):
        raise ValueError("فاکتور خصوصی برای این آگهی ثبت نشده است.")
    encoded = "/".join(quote(part, safe="") for part in parts)
    result = sb_request("POST", f"/storage/v1/object/sign/listing-invoices/{encoded}",
        payload={"expiresIn": 90}, user_token=access_token) or {}
    signed = result.get("signedURL") or result.get("signedUrl")
    if not signed: raise RuntimeError("ساخت لینک موقت فاکتور ناموفق بود.")
    if signed.startswith("http://") or signed.startswith("https://"): pass
    elif signed.startswith("/storage/v1/"): signed = SUPABASE_URL + signed
    elif signed.startswith("/"): signed = SUPABASE_URL + "/storage/v1" + signed
    else: signed = SUPABASE_URL + "/storage/v1/" + signed.lstrip("/")
    return {"ok": True, "url": signed, "expires_in": 90}


def update_listing_status(listing_id, payload, filters):
    rows = sb_request(
        "PATCH", "/rest/v1/listings",
        params=filters,
        payload={**payload, "updated_at": now_iso()},
        prefer="return=representation",
    ) or []
    if not rows:
        raise ValueError("آگهی پیدا نشد یا اجازه تغییر آن را ندارید.")
    return rows[0]


def admin_listing_action(user, data):
    require_admin(user)
    try:
        listing_id = int(data.get("listing_id", 0))
    except (TypeError, ValueError):
        raise ValueError("شناسه آگهی معتبر نیست.")
    action = clean(data.get("listing_action"), 30)
    listing = get_listing(listing_id)
    if not listing:
        raise ValueError("آگهی پیدا نشد.")
    deleted = bool(listing.get("deleted_at"))
    status = listing.get("status")
    if action == "publish":
        if listing.get("payment_status") == "awaiting_payment":
            raise ValueError("ابتدا باید پرداخت آگهی تأیید شود؛ سپس امکان انتشار وجود دارد.")
        if deleted or status != "pending":
            raise ValueError("فقط آگهی در انتظار و حذف‌نشده قابل انتشار است.")
        patch, filters = {"status": "published", "published_at": now_iso()}, {"id": f"eq.{listing_id}", "status": "eq.pending", "deleted_at": "is.null"}
    elif action == "reject":
        if deleted or status != "pending":
            raise ValueError("فقط آگهی در انتظار بررسی قابل رد است.")
        patch, filters = {"deleted_at": now_iso()}, {"id": f"eq.{listing_id}", "status": "eq.pending", "deleted_at": "is.null"}
    elif action == "sold":
        if deleted or status != "published":
            raise ValueError("فقط آگهی منتشرشده را می‌توان فروش‌رفته کرد.")
        patch, filters = {"status": "sold", "sold_at": now_iso()}, {"id": f"eq.{listing_id}", "status": "eq.published", "deleted_at": "is.null"}
    elif action == "delete":
        if deleted:
            raise ValueError("این آگهی قبلاً حذف شده است.")
        patch, filters = {"deleted_at": now_iso()}, {"id": f"eq.{listing_id}", "deleted_at": "is.null"}
    elif action == "restore":
        if not deleted:
            raise ValueError("این آگهی حذف نشده است.")
        patch, filters = {"deleted_at": None}, {"id": f"eq.{listing_id}", "deleted_at": "not.is.null"}
    else:
        raise ValueError("عملیات مدیریتی آگهی معتبر نیست.")
    row = update_listing_status(listing_id, patch, filters)
    return {"ok": True, "listing": row, "message": "وضعیت آگهی به‌روزرسانی شد."}


def owner_listing_action(user, data):
    require_complete_profile(user)
    try:
        listing_id = int(data.get("listing_id", 0))
    except (TypeError, ValueError):
        raise ValueError("شناسه آگهی معتبر نیست.")
    action = clean(data.get("listing_action"), 30)
    listing = get_listing(listing_id)
    if not listing or str(listing.get("owner_user_id") or "") != str(user.get("id") or ""):
        raise ValueError("آگهی پیدا نشد یا متعلق به حساب شما نیست.")
    if listing.get("deleted_at"):
        raise ValueError("آگهی حذف‌شده قابل تغییر نیست.")
    if action == "sold":
        if listing.get("status") != "published":
            raise ValueError("فقط آگهی منتشرشده را می‌توانید فروش‌رفته کنید.")
        patch, filters = {"status": "sold", "sold_at": now_iso()}, {"id": f"eq.{listing_id}", "owner_user_id": f"eq.{user['id']}", "status": "eq.published", "deleted_at": "is.null"}
    elif action == "delete":
        patch, filters = {"deleted_at": now_iso()}, {"id": f"eq.{listing_id}", "owner_user_id": f"eq.{user['id']}", "deleted_at": "is.null"}
    else:
        raise ValueError("عملیات آگهی معتبر نیست.")
    row = update_listing_status(listing_id, patch, filters)
    return {"ok": True, "listing": row, "message": "آگهی شما به‌روزرسانی شد."}


def admin_purchase_action(user, data):
    require_admin(user)
    try:
        request_id = int(data.get("request_id", 0))
    except (TypeError, ValueError):
        raise ValueError("شناسه درخواست خرید معتبر نیست.")
    action = clean(data.get("request_action"), 30)
    if action != "forward":
        raise ValueError("عملیات درخواست خرید معتبر نیست.")
    rows = sb_request(
        "PATCH", "/rest/v1/purchase_requests",
        params={"id": f"eq.{request_id}", "status": "eq.pending"},
        payload={"status": "forwarded", "updated_at": now_iso()},
        prefer="return=representation",
    ) or []
    if not rows:
        raise ValueError("درخواست خرید پیدا نشد یا قبلاً بررسی شده است.")
    return {"ok": True, "request": rows[0], "message": "درخواست به وضعیت بررسی‌شده تغییر کرد."}

def upload_storage(bucket, path, content, mime, user_token=None):
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    sb_request(
        "POST", f"/storage/v1/object/{bucket}/{encoded_path}",
        raw=content, content_type=mime,
        prefer="resolution=ignore-duplicates", user_token=user_token,
    )
    if bucket == "listing-images":
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{encoded_path}"
    return path


def delete_storage(bucket, path, user_token=None):
    if not path:
        return
    try:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        sb_request("DELETE", f"/storage/v1/object/{bucket}/{encoded_path}", user_token=user_token)
    except Exception as exc:
        print("STORAGE CLEANUP FAILED:", str(exc)[:200])


def normalize_banner_titles(rows):
    for row in rows or []:
        if str(row.get("title") or "").strip() in ("بنر زرین ماه", "بنر زرین‌ماه"):
            row["title"] = ""
    return rows or []

DEFAULT_SITE_SLOGANS = [
    {"first": "درخشش طلا،", "second": "با خیال آسوده"},
    {"first": "زیبایی ماندگار،", "second": "برای انتخابی هوشمندانه"},
    {"first": "خرید و فروش طلا،", "second": "شفاف و ساده"},
    {"first": "هر انتخابی،", "second": "به درخشش زرین ماه"},
    {"first": "طلای دلخواهت،", "second": "با آگاهی انتخاب کن"},
]
DEFAULT_SITE_SLOGAN_SETTINGS = {
    "slogans": DEFAULT_SITE_SLOGANS,
    "topline": "زرین ماه؛ انتخابی درخشان برای خرید و فروش طلا",
    "duration": 5,
    "mode": "rotate",
}

def get_site_slogan_settings(user_token=None):
    rows = sb_request("GET", "/rest/v1/zarinmah_site_settings", params={
        "setting_key": "eq.homepage_slogans",
        "select": "setting_value",
        "limit": "1",
    }, public=not bool(user_token), user_token=user_token) or []
    if not rows:
        return DEFAULT_SITE_SLOGAN_SETTINGS
    value = rows[0].get("setting_value") or {}
    if not isinstance(value, dict):
        return DEFAULT_SITE_SLOGAN_SETTINGS
    result = dict(DEFAULT_SITE_SLOGAN_SETTINGS)
    result.update(value)
    if not isinstance(result.get("slogans"), list) or not result["slogans"]:
        result["slogans"] = DEFAULT_SITE_SLOGAN_SETTINGS["slogans"]
    return result

def get_admin_site_slogans(user):
    require_admin(user)
    return {"ok": True, "settings": get_site_slogan_settings(user_token=user.get("access_token"))}

def save_admin_site_slogans(user, fields):
    require_admin(user)
    try:
        slogans = json.loads(fields.get("slogans_json") or "[]")
    except (TypeError, ValueError):
        raise ValueError("ساختار شعارها معتبر نیست.")
    if not isinstance(slogans, list) or not 1 <= len(slogans) <= 20:
        raise ValueError("تعداد شعارها باید بین ۱ تا ۲۰ مورد باشد.")
    normalized = []
    for item in slogans:
        if not isinstance(item, dict):
            raise ValueError("ساختار یکی از شعارها معتبر نیست.")
        first = clean(item.get("first"), 70)
        second = clean(item.get("second"), 70)
        if not first or not second:
            raise ValueError("هر شعار باید شامل دو خط کامل باشد.")
        normalized.append({"first": first, "second": second})
    topline = clean(fields.get("topline"), 110)
    if not topline:
        raise ValueError("متن نوار بالایی نمی‌تواند خالی باشد.")
    try:
        duration = int(fields.get("duration") or 5)
    except (TypeError, ValueError):
        raise ValueError("مدت نمایش شعار معتبر نیست.")
    if duration not in (4, 5, 6, 8, 10):
        raise ValueError("مدت نمایش باید یکی از گزینه‌های موجود باشد.")
    mode = clean(fields.get("mode"), 10)
    if mode not in ("rotate", "fixed", "off"):
        raise ValueError("حالت نمایش شعار معتبر نیست.")
    settings = {"slogans": normalized, "topline": topline, "duration": duration, "mode": mode}
    sb_request(
        "POST", "/rest/v1/zarinmah_site_settings",
        params={"on_conflict": "setting_key"},
        payload={
            "setting_key": "homepage_slogans",
            "setting_value": settings,
            "updated_by": user["id"],
            "updated_at": now_iso(),
        },
        prefer="resolution=merge-duplicates,return=representation",
        user_token=user.get("access_token"),
    )
    return {"ok": True, "settings": settings, "message": "تنظیمات شعارها ذخیره شد."}

def get_public_banners():
    rows = sb_request("GET", "/rest/v1/zarinmah_banners", params={
        "select": "id,title,image_url,target_url,sort_order,is_active",
        "is_active": "eq.true", "order": "sort_order.asc,created_at.desc",
    }, public=True) or []
    return {"ok": True, "banners": normalize_banner_titles(rows)}

def get_admin_banners(user):
    require_admin(user)
    rows = sb_request("GET", "/rest/v1/zarinmah_banners", params={
        "select": "id,title,image_url,storage_path,target_url,sort_order,is_active,created_at",
        "order": "sort_order.asc,created_at.desc",
    }, user_token=user.get("access_token")) or []
    return {"ok": True, "banners": normalize_banner_titles(rows)}

def save_admin_banner(user, fields, uploads):
    require_admin(user)
    banner_id = clean(fields.get("banner_id"), 40)
    title = clean(fields.get("title"), 120)
    target_url = clean(fields.get("target_url"), 500)
    try:
        sort_order = max(0, min(9999, int(fields.get("sort_order") or 0)))
    except (TypeError, ValueError):
        raise ValueError("ترتیب نمایش معتبر نیست.")
    is_active = str(fields.get("is_active", "true")).lower() in ("1", "true", "yes", "on")
    existing = []
    if banner_id:
        existing = sb_request("GET", "/rest/v1/zarinmah_banners", params={"id": "eq." + banner_id, "select": "id,image_url,storage_path", "limit": "1"}, user_token=user.get("access_token")) or []
        if not existing:
            raise ValueError("بنر موردنظر پیدا نشد.")
    old = existing[0] if existing else {}
    title = title or ""
    upload = uploads.get("banner_image")
    image_url = old.get("image_url", "")
    storage_path = old.get("storage_path", "")
    if upload and upload.get("data"):
        upload = check_file(upload, {"image/jpeg", "image/png", "image/webp"}, "تصویر بنر", max_size=MAX_FILE_SIZE)
        if not sniff_photo(upload["data"]):
            raise ValueError("محتوای تصویر بنر معتبر نیست.")
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[upload["content_type"]]
        storage_path = "banners/" + uuid.uuid4().hex + "." + ext
        image_url = upload_storage("listing-images", storage_path, upload["data"], upload["content_type"], user_token=user.get("access_token"))
    if not image_url:
        raise ValueError("برای بنر جدید، انتخاب تصویر الزامی است.")
    payload = {"title": title, "image_url": image_url, "storage_path": storage_path, "target_url": target_url or None, "sort_order": sort_order, "is_active": is_active, "updated_at": now_iso()}
    if banner_id:
        rows = sb_request("PATCH", "/rest/v1/zarinmah_banners", params={"id": "eq." + banner_id}, payload=payload, prefer="return=representation", user_token=user.get("access_token")) or []
    else:
        payload["created_at"] = now_iso()
        rows = sb_request("POST", "/rest/v1/zarinmah_banners", payload=payload, prefer="return=representation", user_token=user.get("access_token")) or []
    if upload and old.get("storage_path") and old.get("storage_path") != storage_path:
        delete_storage("listing-images", old["storage_path"], user_token=user.get("access_token"))
    return {"ok": True, "banner": rows[0] if rows else payload, "message": "بنر با موفقیت ذخیره شد."}

def delete_admin_banner(user, fields):
    require_admin(user)
    banner_id = clean(fields.get("banner_id"), 40)
    if not banner_id:
        raise ValueError("شناسه بنر معتبر نیست.")
    rows = sb_request("GET", "/rest/v1/zarinmah_banners", params={"id": "eq." + banner_id, "select": "id,storage_path", "limit": "1"}, user_token=user.get("access_token")) or []
    if not rows:
        raise ValueError("بنر موردنظر پیدا نشد.")
    sb_request("DELETE", "/rest/v1/zarinmah_banners", params={"id": "eq." + banner_id}, user_token=user.get("access_token"))
    delete_storage("listing-images", rows[0].get("storage_path"), user_token=user.get("access_token"))
    return {"ok": True, "message": "بنر حذف شد."}

def parse_body(handler):
    raw_length = handler.headers.get("Content-Length", "")
    if not raw_length.isdigit():
        raise ValueError("طول درخواست معتبر نیست.")
    length = int(raw_length)
    if length <= 0 or length > MAX_BODY_SIZE:
        raise ValueError("حجم درخواست باید کمتر از ۴ مگابایت باشد.")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("دریافت اطلاعات فرم ناقص بود.")
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" in content_type.lower():
        envelope = (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n").encode() + body
        message = BytesParser(policy=policy.default).parsebytes(envelope)
        if not message.is_multipart():
            raise ValueError("ساختار فرم ارسالی معتبر نیست.")
        fields, files = {}, {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if not name:
                continue
            filename = part.get_filename()
            data = part.get_payload(decode=True) or b""
            if filename:
                files[name] = {
                    "filename": os.path.basename(filename),
                    "content_type": (part.get_content_type() or "application/octet-stream").lower(),
                    "data": data,
                }
            else:
                charset = part.get_content_charset() or "utf-8"
                fields[name] = data.decode(charset, errors="replace").strip()
        return fields, files
    if "application/json" in content_type.lower():
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("ساختار JSON معتبر نیست.")
        return data, {}
    raise ValueError("نوع درخواست پشتیبانی نمی‌شود.")


def check_file(upload, allowed, label, max_size=MAX_FILE_SIZE):
    if not upload or not upload.get("data"):
        raise ValueError(f"ارسال {label} الزامی است.")
    if len(upload["data"]) > max_size:
        if max_size == MAX_FILE_SIZE:
            raise ValueError(f"حجم {label} نباید بیشتر از ۱٫۵ مگابایت باشد.")
        raise ValueError(f"حجم {label} نباید بیشتر از ۲ مگابایت باشد.")
    if upload["content_type"] not in allowed:
        raise ValueError(f"فرمت {label} مجاز نیست.")
    return upload


def sniff_photo(data):
    return (
        data.startswith(b"\xff\xd8\xff") or
        data.startswith(b"\x89PNG\r\n\x1a\n") or
        (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def sniff_invoice(data):
    return (
        data.startswith(b"%PDF-") or data.startswith(b"\xff\xd8\xff") or
        data.startswith(b"\x89PNG\r\n\x1a\n") or
        (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    )


def product_details(fields):
    category = clean(fields.get("sellerItem"), 40)
    if category == "jewelry":
        subtype = clean(fields.get("jewelryType"), 80)
        if subtype not in JEWELRY:
            raise ValueError("نوع زیورآلات را انتخاب کنید.")
        product_type, weight_unit = JEWELRY[subtype], "gram"
        weight_raw = clean(fields.get("sellerWeight"), 40)
    elif category == "coin":
        subtype = clean(fields.get("coinType"), 80)
        if subtype not in COINS:
            raise ValueError("نوع سکه را انتخاب کنید.")
        product_type, weight_unit = COINS[subtype], "gram"
        weight_raw = clean(fields.get("sellerWeight"), 40)
    elif category == "parsian":
        product_type, weight_unit = "سکه پارسیان", "soot"
        weight_raw = digits(clean(fields.get("parsianWeight"), 30))
        if not weight_raw.isdigit() or int(weight_raw) <= 0:
            raise ValueError("وزن سکه پارسیان را برحسب سوت وارد کنید.")
    elif category == "melted":
        product_type, weight_unit = "طلای آب‌شده", "gram"
        weight_raw = clean(fields.get("sellerWeight"), 40)
    else:
        raise ValueError("دسته‌بندی محصول معتبر نیست.")

    weight = None
    if weight_raw:
        try:
            weight = float(digits(weight_raw).replace("٫", ".").replace(",", ".").replace("٬", ""))
            if weight <= 0 or weight > 10000000:
                raise ValueError
        except ValueError:
            raise ValueError("وزن واردشده معتبر نیست.")

    purity = clean(fields.get("sellerKarat"), 60) or None
    if weight_unit == "soot":
        purity = purity or "عیار درج‌شده روی فاکتور"
    return product_type, weight, weight_unit, purity


def pricing_details(fields):
    method = clean(fields.get("pricingMethod"), 30)
    if method not in {"fixed", "percent", "amount", "negotiable"}:
        raise ValueError("روش قیمت‌گذاری را انتخاب کنید.")
    price = None
    value = None
    if method == "fixed":
        raw = digits(clean(fields.get("fixedPrice"), 40)).replace(",", "")
        if not raw.isdigit() or int(raw) <= 0:
            raise ValueError("قیمت ثابت معتبر وارد کنید.")
        price = int(raw)
        label = f"{price:,} تومان"
    elif method == "percent":
        raw = digits(clean(fields.get("pricePercent"), 30)).replace("٫", ".").replace(",", ".")
        try:
            value = float(raw)
            if not 0 <= value <= 100:
                raise ValueError
        except ValueError:
            raise ValueError("درصد باید بین صفر تا ۱۰۰ باشد.")
        label = f"{value:g}٪ بالاتر از تابلو"
    elif method == "amount":
        raw = digits(clean(fields.get("priceAmount"), 40)).replace(",", "")
        if not raw.isdigit() or int(raw) <= 0:
            raise ValueError("مبلغ بالاتر از تابلو معتبر نیست.")
        value = int(raw)
        label = f"{value:,} تومان بالاتر از تابلو"
    else:
        label = "توافقی"
    return method, value, price, label


def notify_admin(listing, invoice_sent):
    method_labels = {
        "fixed": "قیمت ثابت", "percent": "درصد بالاتر از تابلو",
        "amount": "مبلغ بالاتر از تابلو", "negotiable": "توافقی",
    }
    message = (
        "🆕 <b>آگهی جدید از سایت زرین ماه</b>\n\n"
        f"🆔 آگهی: <code>{listing['id']}</code>\n"
        f"👤 فروشنده: {html(listing['seller_name'])}\n"
        f"📞 تماس: <code>{html(listing['seller_mobile'])}</code>\n"
        f"💎 کالا: {html(listing['product_type'])}\n"
        f"⚖️ وزن: {html(listing.get('_weight_label') or 'ثبت نشده')} {html(listing['_weight_unit_label'])}\n"
        f"🔎 عیار: {html(listing.get('purity') or 'ثبت نشده')}\n"
        f"📍 محل: {html(listing['province'])}، {html(listing['city'])}\n"
        f"💰 روش قیمت‌گذاری: {method_labels.get(listing['pricing_method'], listing['pricing_method'])}\n"
        f"💵 قیمت: {html(listing['_price_label'])}\n\n"
        f"📝 توضیحات: {html(listing['description'])}\n\n"
        f"🧾 فاکتور خصوصی ذخیره شد؛ ارسال نسخه به مدیر: {'موفق' if invoice_sent else 'ناموفق'}\n"
        "⚠️ تا تأیید مدیر، آگهی عمومی نمی‌شود."
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ تأیید و انتشار", "callback_data": f"approve_web:{listing['id']}"},
        {"text": "❌ رد آگهی", "callback_data": f"reject_web:{listing['id']}"},
    ]]}
    photo_url = listing.get("image_url")
    if photo_url:
        try:
            return telegram("sendPhoto", {
                "chat_id": ADMIN_ID, "photo": photo_url,
                "caption": message[:1000], "parse_mode": "HTML",
                "reply_markup": keyboard,
            })
        except Exception as exc:
            print("ADMIN PHOTO MESSAGE FAILED; FALLING BACK TO TEXT:", str(exc)[:200])
    return telegram("sendMessage", {
        "chat_id": ADMIN_ID, "text": message, "parse_mode": "HTML",
        "reply_markup": keyboard, "disable_web_page_preview": True,
    })


def telegram(method, payload):
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN در Vercel تنظیم نشده است.")
    response = requests.post(f"{TG_API}/{method}", json=payload, timeout=45)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "خطای تلگرام"))
    return result.get("result")


def send_invoice_to_admin(upload, listing_id):
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN تنظیم نشده است.")
    response = requests.post(
        f"{TG_API}/sendDocument",
        data={"chat_id": ADMIN_ID, "caption": f"🧾 فاکتور خصوصی آگهی {listing_id}"},
        files={"document": (upload["filename"], upload["data"], upload["content_type"])},
        timeout=60,
    )
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "ارسال فاکتور ناموفق بود"))
    return result["result"]



def get_site_setting(setting_key, default=None):
    rows = sb_request("GET", "/rest/v1/zarinmah_site_settings", params={
        "setting_key": "eq." + clean(setting_key, 80), "select": "setting_value", "limit": "1",
    }) or []
    return rows[0].get("setting_value") if rows else (default or {})


DEFAULT_SELLER_PAYMENT_CONFIG = {
    "method": "manual_card_transfer", "enabled": False, "card_number": "",
    "card_owner": "", "instructions": "پس از واریز وجه، تصویر رسید و شماره پیگیری را ثبت کنید. فعال‌سازی پس از بررسی مدیر انجام می‌شود.",
    "receipt_deadline_hours": 24, "gateway_enabled": False, "gateway_provider": None,
}
DEFAULT_SELLER_TARIFF_CONFIG = {"free_listing_quota": 3, "listing_price_toman": 25000, "listing_duration_days": 30, "packages": []}


def get_seller_payment_config():
    value = get_site_setting("seller_payment_config", DEFAULT_SELLER_PAYMENT_CONFIG)
    return {**DEFAULT_SELLER_PAYMENT_CONFIG, **(value if isinstance(value, dict) else {})}


def get_seller_tariff_config():
    value = get_site_setting("seller_tariff_config", DEFAULT_SELLER_TARIFF_CONFIG)
    return {**DEFAULT_SELLER_TARIFF_CONFIG, **(value if isinstance(value, dict) else {})}


def admin_get_payment_settings(user):
    require_admin(user)
    return {"ok": True, "payment": get_seller_payment_config(), "tariff": get_seller_tariff_config()}


def admin_save_setting(user, key, value, action):
    require_admin(user)
    old_value = get_site_setting(key, {})
    sb_request("POST", "/rest/v1/zarinmah_site_settings",
        params={"on_conflict": "setting_key"},
        payload={"setting_key": key, "setting_value": value, "updated_by": user["id"], "updated_at": now_iso()},
        prefer="resolution=merge-duplicates,return=representation", user_token=user.get("access_token"))
    record_admin_audit(user, user["id"], action, {"setting": key, "value": old_value}, {"setting": key, "value": value})
    return {"ok": True, "message": "تنظیمات با موفقیت ذخیره شد."}


def admin_save_payment_settings(user, fields):
    card_number = phone_digits(fields.get("card_number"))
    card_owner = clean(fields.get("card_owner"), 100)
    instructions = clean(fields.get("instructions"), 700)
    try:
        deadline = int(digits(fields.get("receipt_deadline_hours") or 24))
    except (TypeError, ValueError):
        raise ValueError("مهلت ارسال رسید معتبر نیست.")
    enabled = str(fields.get("enabled") or "").lower() in ("1", "true", "on", "yes")
    if card_number and not re.fullmatch(r"\d{16}", card_number):
        raise ValueError("شماره کارت باید ۱۶ رقم باشد.")
    if not 1 <= deadline <= 168:
        raise ValueError("مهلت ارسال رسید باید بین ۱ تا ۱۶۸ ساعت باشد.")
    if enabled and (not re.fullmatch(r"\\d{16}", card_number) or len(card_owner) < 3):
        raise ValueError("برای فعال‌کردن کارت‌به‌کارت، شماره کارت ۱۶ رقمی و نام صاحب کارت را وارد کنید.")
    value = {
        "method": "manual_card_transfer", "enabled": enabled,
        "card_number": card_number, "card_owner": card_owner,
        "instructions": instructions or DEFAULT_SELLER_PAYMENT_CONFIG["instructions"],
        "receipt_deadline_hours": deadline, "gateway_enabled": False, "gateway_provider": None,
    }
    return admin_save_setting(user, "seller_payment_config", value, "seller_payment_config_update")


def admin_save_tariff_settings(user, fields):
    try:
        quota = int(digits(fields.get("free_listing_quota")))
        price = int(digits(fields.get("listing_price_toman")))
        days = int(digits(fields.get("listing_duration_days")))
    except (TypeError, ValueError):
        raise ValueError("مقادیر تعرفه باید عددی باشند.")
    if not 0 <= quota <= 100: raise ValueError("سهمیه رایگان باید بین صفر تا ۱۰۰ آگهی باشد.")
    if not 0 <= price <= 1000000000: raise ValueError("مبلغ تعرفه معتبر نیست.")
    if not 1 <= days <= 365: raise ValueError("مدت نمایش باید بین ۱ تا ۳۶۵ روز باشد.")
    value = get_seller_tariff_config()
    value.update({"free_listing_quota": quota, "listing_price_toman": price, "listing_duration_days": days})
    return admin_save_setting(user, "seller_tariff_config", value, "seller_tariff_config_update")


def get_admin_payment_orders(user):
    require_admin(user)
    rows = sb_request("GET", "/rest/v1/seller_payment_orders", params={
        "select": "*", "order": "created_at.desc", "limit": "100",
    }, user_token=user.get("access_token")) or []
    user_ids = list(dict.fromkeys(str(row.get("user_id")) for row in rows if row.get("user_id")))
    profiles = {}
    if user_ids:
        profile_rows = sb_request("GET", "/rest/v1/profiles", params={
            "id": "in.(" + ",".join(user_ids) + ")", "select": "id,full_name,mobile,username",
        }, user_token=user.get("access_token")) or []
        profiles = {str(row["id"]): row for row in profile_rows}
    for row in rows:
        row["customer"] = profiles.get(str(row.get("user_id")), {})
        path = clean(row.get("receipt_path"), 600)
        if path:
            try:
                encoded = "/".join(quote(part, safe="") for part in path.split("/"))
                signed = sb_request("POST", f"/storage/v1/object/sign/payment-receipts/{encoded}",
                    payload={"expiresIn": 900}, user_token=user.get("access_token")) or {}
                url = signed.get("signedURL") or signed.get("signedUrl")
                if url:
                    row["receipt_url"] = SUPABASE_URL + url if url.startswith("/") else url
            except Exception as exc:
                print("PAYMENT_RECEIPT_SIGN_FAILED:", str(exc)[:180])
    return {"ok": True, "payments": rows}


def submit_payment_receipt(user, fields, uploads):
    require_complete_profile(user, access_token=user.get("access_token"))
    try:
        order_id = int(digits(fields.get("payment_order_id") or 0))
        amount = int(digits(fields.get("amount_toman") or 0))
    except (TypeError, ValueError):
        raise ValueError("شناسه سفارش یا مبلغ واریزی معتبر نیست.")
    payer_name = clean(fields.get("payer_name"), 100)
    tracking_code = clean(fields.get("tracking_code"), 100)
    if len(payer_name) < 3 or len(tracking_code) < 4:
        raise ValueError("نام پرداخت‌کننده و شماره پیگیری معتبر را وارد کنید.")
    receipt = check_file(uploads.get("payment_receipt"), {
        "image/jpeg", "image/png", "image/webp", "application/pdf"
    }, "تصویر رسید پرداخت")
    if not sniff_invoice(receipt["data"]):
        raise ValueError("محتوای فایل رسید معتبر نیست.")
    orders = sb_request("GET", "/rest/v1/seller_payment_orders", params={
        "id": "eq." + str(order_id), "user_id": "eq." + str(user["id"]),
        "select": "id,user_id,amount_toman,status,receipt_path", "limit": "1",
    }) or []
    if not orders: raise ValueError("سفارش پرداخت پیدا نشد یا متعلق به حساب شما نیست.")
    order = orders[0]
    if order.get("status") not in ("awaiting_receipt", "rejected"):
        raise ValueError("این سفارش در وضعیت دریافت رسید نیست.")
    if amount != int(order.get("amount_toman") or 0):
        raise ValueError("مبلغ واریزی با مبلغ سفارش مطابقت ندارد.")
    ext = {"application/pdf":"pdf","image/jpeg":"jpg","image/png":"png","image/webp":"webp"}[receipt["content_type"]]
    path = f"{user['id']}/{order_id}-{uuid.uuid4().hex}.{ext}"
    upload_storage("payment-receipts", path, receipt["data"], receipt["content_type"])
    updated = sb_request("PATCH", "/rest/v1/seller_payment_orders",
        params={"id": "eq." + str(order_id), "user_id": "eq." + str(user["id"])},
        payload={"payer_name": payer_name, "tracking_code": tracking_code, "receipt_path": path,
                 "status": "under_review", "rejection_reason": None, "updated_at": now_iso()},
        prefer="return=representation") or []
    if not updated:
        delete_storage("payment-receipts", path)
        raise RuntimeError("ثبت رسید در پایگاه داده ناموفق بود.")
    old_path = clean(order.get("receipt_path"), 600)
    if old_path and old_path != path: delete_storage("payment-receipts", old_path)
    return {"ok": True, "payment_order": updated[0], "message": "رسید دریافت شد و پس از بررسی مدیر نتیجه اعلام می‌شود."}


def admin_review_payment(user, fields):
    require_admin(user)
    try: order_id = int(digits(fields.get("payment_order_id") or 0))
    except (TypeError, ValueError): raise ValueError("شناسه پرداخت معتبر نیست.")
    action = clean(fields.get("review_action"), 20)
    reason = clean(fields.get("rejection_reason"), 500)
    if action not in ("approve", "reject"): raise ValueError("عملیات بررسی پرداخت معتبر نیست.")
    rows = sb_request("GET", "/rest/v1/seller_payment_orders", params={
        "id": "eq." + str(order_id), "select": "*", "limit": "1",
    }) or []
    if not rows: raise ValueError("سفارش پرداخت پیدا نشد.")
    order = rows[0]
    if order.get("status") != "under_review" or not order.get("receipt_path"):
        raise ValueError("فقط رسیدهای ارسال‌شده و در انتظار بررسی قابل تصمیم‌گیری هستند.")
    if action == "reject" and len(reason) < 4: raise ValueError("برای رد رسید، دلیل کوتاهی وارد کنید.")
    new_status = "approved" if action == "approve" else "rejected"
    updated = sb_request("PATCH", "/rest/v1/seller_payment_orders",
        params={"id": "eq." + str(order_id), "status": "eq.under_review"},
        payload={"status": new_status, "reviewed_by": user["id"], "reviewed_at": now_iso(),
                 "updated_at": now_iso(), "rejection_reason": None if action == "approve" else reason},
        prefer="return=representation") or []
    if not updated: raise ValueError("وضعیت سفارش تغییر کرده است؛ فهرست را تازه‌سازی کنید.")
    if order.get("purpose") == "listing_fee" and order.get("listing_id"):
        sb_request("PATCH", "/rest/v1/listings",
            params={"id": "eq." + str(order["listing_id"]), "owner_user_id": "eq." + str(order["user_id"]),
                    "payment_status": "eq.awaiting_payment"},
            payload={"payment_status": "paid", "updated_at": now_iso()},
            prefer="return=representation")
    record_admin_audit(user, order.get("user_id"), "seller_payment_" + action,
        {"payment_order_id": order_id, "status": order.get("status")},
        {"payment_order_id": order_id, "status": new_status, "reason": reason if action == "reject" else None})
    return {"ok": True, "payment_order": updated[0], "message": "پرداخت تأیید شد." if action == "approve" else "رسید رد شد."}


def create_listing(fields, uploads, user):
    profile = require_complete_profile(user)
    photo = check_file(uploads.get("sellerPhoto") or uploads.get("photo"), {"image/jpeg", "image/png", "image/webp"}, "عکس محصول")
    invoice = check_file(uploads.get("sellerInvoice") or uploads.get("invoice"), {"image/jpeg", "image/png", "image/webp", "application/pdf"}, "فاکتور فروش")
    if not sniff_photo(photo["data"]):
        raise ValueError("محتوای عکس محصول معتبر نیست.")
    if not sniff_invoice(invoice["data"]):
        raise ValueError("محتوای فاکتور معتبر نیست.")

    seller_name = clean(profile.get("full_name"), 100)
    seller_mobile = clean(user.get("phone"), 30)
    submitted_phone = clean(fields.get("sellerPhone"), 30)
    description = clean(fields.get("sellerDescription"), 2000)
    province = clean(fields.get("province"), 100)
    city = clean(fields.get("city"), 100)
    if not all((seller_name, seller_mobile, description, province, city)):
        raise ValueError("اطلاعات پروفایل، شماره تماس، توضیحات، استان و شهر الزامی هستند.")
    if not valid_phone(seller_mobile):
        raise ValueError("شماره موبایل تأییدشده حساب معتبر نیست.")
    if submitted_phone and phone_digits(submitted_phone) != phone_digits(seller_mobile):
        raise ValueError("شماره تماس آگهی باید با شماره تأییدشده حساب یکسان باشد.")
    if province not in PROVINCES:
        raise ValueError("استان انتخاب‌شده معتبر نیست.")
    if fields.get("invoiceConfirm") not in ("on", "true", "1", "yes", "بله"):
        raise ValueError("تأیید داشتن فاکتور معتبر الزامی است.")

    product_type, weight, weight_unit, purity = product_details(fields)
    pricing_method, pricing_value, price, price_label = pricing_details(fields)

    tariff = get_seller_tariff_config()
    payment_config = get_seller_payment_config()
    try:
        free_quota = max(0, min(100, int(tariff.get("free_listing_quota", 3))))
        listing_fee = max(0, int(tariff.get("listing_price_toman", 25000)))
        listing_duration = max(1, min(365, int(tariff.get("listing_duration_days", 30))))
    except (TypeError, ValueError):
        raise RuntimeError("تنظیمات تعرفه فروشندگان معتبر نیست.")
    _, seller_listing_count = sb_request_counted("/rest/v1/listings", {
        "owner_user_id": "eq." + str(user["id"]), "deleted_at": "is.null",
        "status": "in.(pending,published,sold)", "select": "id", "limit": "1",
    }, access_token=user.get("access_token"))
    payment_required = seller_listing_count >= free_quota and listing_fee > 0
    if payment_required and not (
        payment_config.get("enabled") is True
        and re.fullmatch(r"\d{16}", phone_digits(payment_config.get("card_number")))
        and clean(payment_config.get("card_owner"), 100)
    ):
        raise ValueError("سهمیه آگهی رایگان شما به پایان رسیده است. دریافت پرداخت فعلاً از طرف مدیر غیرفعال است؛ لطفاً بعداً مراجعه کنید.")
    title = clean(fields.get("title"), 150) or product_type
    token = uuid.uuid4().hex
    photo_ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[photo["content_type"]]
    invoice_ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[invoice["content_type"]]
    photo_path = f"website/{token}.{photo_ext}"
    invoice_path = f"website/{token}.{invoice_ext}"
    photo_uploaded = invoice_uploaded = False
    created_listing_id = None
    try:
        image_url = upload_storage("listing-images", photo_path, photo["data"], photo["content_type"])
        photo_uploaded = True
        saved_invoice_path = upload_storage("listing-invoices", invoice_path, invoice["data"], invoice["content_type"])
        invoice_uploaded = True
        row = {
            "owner_user_id": user["id"],
            "owner_telegram_id": None,
            "title": title,
            "product_type": product_type,
            "weight": weight,
            "purity": purity,
            "pricing_method": pricing_method,
            "price": price,
            "pricing_value": pricing_value,
            "description": description,
            "image_url": image_url,
            "status": "pending",
            "payment_status": "awaiting_payment" if payment_required else "free",
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "seller_name": seller_name,
            "seller_mobile": seller_mobile,
            "weight_unit": weight_unit,
            "province": province,
            "city": city,
            "invoice_path": saved_invoice_path,
        }
        inserted = sb_request("POST", "/rest/v1/listings", payload=row, prefer="return=representation")
        if not inserted:
            raise RuntimeError("شناسه آگهی از Supabase دریافت نشد.")
        record = inserted[0]
        listing_id = int(record["id"])
        created_listing_id = listing_id
        payment_order = None
        if payment_required:
            order_rows = sb_request("POST", "/rest/v1/seller_payment_orders", payload={
                "user_id": user["id"], "purpose": "listing_fee", "listing_id": listing_id,
                "amount_toman": listing_fee, "payment_method": "manual_card_transfer",
                "provider": "manual_card_transfer", "status": "awaiting_receipt",
                "metadata": {"listing_title": title, "display_duration_days": listing_duration},
            }, prefer="return=representation")
            if not order_rows: raise RuntimeError("ساخت سفارش پرداخت ناموفق بود.")
            payment_order = order_rows[0]
            sb_request("PATCH", "/rest/v1/listings", params={"id": "eq." + str(listing_id)},
                payload={"payment_order_id": payment_order["id"]}, prefer="return=minimal")
        listing = {
            "id": listing_id, "seller_name": seller_name, "seller_mobile": seller_mobile,
            "product_type": product_type, "weight": weight, "weight_unit": weight_unit,
            "purity": purity, "province": province, "city": city,
            "pricing_method": pricing_method, "description": description,
            "_weight_label": clean(fields.get("parsianWeight") if weight_unit == "soot" else fields.get("sellerWeight")) or "ثبت نشده",
            "_weight_unit_label": "سوت" if weight_unit == "soot" else "گرم",
            "_price_label": price_label,
            "image_url": image_url,
        }
        if payment_required:
            return {
                "id": listing_id, "status": "pending", "payment_required": True,
                "payment_order": {"id": payment_order["id"], "amount_toman": listing_fee,
                                  "purpose": "listing_fee", "status": "awaiting_receipt"},
                "payment_config": {"method": "manual_card_transfer",
                    "card_number": payment_config.get("card_number"),
                    "card_owner": payment_config.get("card_owner"),
                    "instructions": payment_config.get("instructions"),
                    "receipt_deadline_hours": payment_config.get("receipt_deadline_hours", 24)},
                "admin_notification_sent": False, "invoice_sent": False,
                "message": "آگهی ذخیره شد اما تا تأیید پرداخت منتشر نمی‌شود. ابتدا مبلغ را کارت‌به‌کارت و رسید را ثبت کنید.",
            }
        invoice_sent = False
        try:
            send_invoice_to_admin(invoice, listing_id)
            invoice_sent = True
        except Exception as exc:
            print("INVOICE TELEGRAM DELIVERY FAILED:", str(exc)[:250])
        notified = False
        try:
            notify_admin(listing, invoice_sent)
            notified = True
        except Exception as exc:
            print("ADMIN NOTIFICATION FAILED:", str(exc)[:250])
        return {
            "id": listing_id, "status": "pending",
            "admin_notification_sent": notified, "invoice_sent": invoice_sent,
            "message": "آگهی در پایگاه داده ذخیره شد و در انتظار بررسی مدیر است. اگر پیام مدیر ارسال نشد، آگهی را دوباره ثبت نکنید.",
        }
    except Exception:
        if created_listing_id:
            try:
                sb_request("DELETE", "/rest/v1/listings", params={"id": "eq." + str(created_listing_id)})
            except Exception as cleanup_error:
                print("LISTING CLEANUP FAILED:", str(cleanup_error)[:180])
        if invoice_uploaded:
            delete_storage("listing-invoices", invoice_path)
        if photo_uploaded:
            delete_storage("listing-images", photo_path)
        raise


def get_listing(listing_id):
    rows = sb_request("GET", "/rest/v1/listings", params={
        "id": f"eq.{int(listing_id)}", "select": "*", "limit": "1",
    }) or []
    return rows[0] if rows else None


def public_listing(item):
    method = item.get("pricing_method")
    value = item.get("pricing_value")
    if method == "fixed" and item.get("price") is not None:
        price_label = f"{int(float(item['price'])):,} تومان"
    elif method == "percent" and value is not None:
        price_label = f"{float(value):g}٪ بالاتر از تابلو"
    elif method == "amount" and value is not None:
        price_label = f"{int(float(value)):,} تومان بالاتر از تابلو"
    else:
        price_label = "توافقی" if method == "negotiable" else "قیمت اعلام نشده"
    return {
        "id": item.get("id"), "title": item.get("title"),
        "product_type": item.get("product_type"), "item_type": item.get("product_type"),
        "weight": item.get("weight"),
        "weight_unit": "سوت" if item.get("weight_unit") == "soot" else "گرم",
        "karat": item.get("purity"), "purity": item.get("purity"),
        "province": item.get("province"), "city": item.get("city"),
        "description": item.get("description"), "pricing_method": method,
        "pricing_value": value, "price": price_label,
        "photo_url": item.get("image_url"), "image_url": item.get("image_url"),
        "created_at": item.get("created_at"), "status": item.get("status"),
    }


def save_purchase_request(data, user):
    profile = require_complete_profile(user)
    try:
        listing_id = int(data.get("listing_id", 0))
    except (TypeError, ValueError):
        raise ValueError("شناسه آگهی معتبر نیست.")
    listing = get_listing(listing_id)
    if not listing or listing.get("status") != "published" or listing.get("deleted_at"):
        raise ValueError("این آگهی دیگر برای خرید در دسترس نیست.")
    buyer_name = clean(profile.get("full_name"), 100)
    buyer_mobile = clean(user.get("phone"), 30)
    if not buyer_name or not buyer_mobile:
        raise ValueError("نام و شماره موبایل تأییدشده حساب برای ثبت درخواست الزامی است.")
    if not valid_phone(buyer_mobile):
        raise ValueError("شماره تماس معتبر نیست.")
    if str(listing.get("owner_user_id") or "") == str(user.get("id") or ""):
        raise ValueError("برای آگهی خودتان نمی‌توانید درخواست خرید ثبت کنید.")
    if phone_digits(buyer_mobile) == phone_digits(listing.get("seller_mobile")):
        raise ValueError("شماره تماس خریدار و فروشنده نمی‌تواند یکسان باشد.")
    existing = sb_request("GET", "/rest/v1/purchase_requests", params={
        "listing_id": f"eq.{listing_id}",
        "buyer_mobile": f"eq.{phone_digits(buyer_mobile)}",
        "status": "in.(pending,forwarded)", "select": "id", "limit": "1",
    }) or []
    if existing:
        raise ValueError("درخواست قبلی شما برای این آگهی ثبت شده است.")
    row = {
        "listing_id": listing_id, "buyer_user_id": user["id"],
        "buyer_telegram_id": None, "buyer_name": buyer_name,
        "buyer_mobile": phone_digits(buyer_mobile),
        "message": clean(data.get("message") or "درخواست خرید از سایت زرین ماه", 1000),
        "status": "pending", "created_at": now_iso(), "updated_at": now_iso(),
    }
    result = sb_request("POST", "/rest/v1/purchase_requests", payload=row, prefer="return=representation")
    if not result:
        raise RuntimeError("ثبت درخواست خرید در Supabase ناموفق بود.")
    return result[0], listing


def notify_purchase_admin(request_row, listing):
    text = (
        "🛒 <b>درخواست خرید جدید از سایت زرین ماه</b>\n\n"
        f"🆔 درخواست: <code>{request_row['id']}</code>\n"
        f"📦 آگهی: <code>{listing['id']}</code>\n"
        f"💎 کالا: {html(listing.get('title') or listing.get('product_type'))}\n"
        f"👤 خریدار: {html(request_row.get('buyer_name'))}\n"
        f"📞 شماره خریدار: <code>{html(request_row.get('buyer_mobile'))}</code>\n\n"
        "🔒 اطلاعات تماس فقط برای هماهنگی خصوصی در اختیار مدیر قرار می‌گیرد."
    )
    keyboard = {"inline_keyboard": [[{
        "text": "📨 بررسی و انتقال درخواست", "callback_data": f"forward_web:{request_row['id']}"
    }]]}
    return telegram("sendMessage", {"chat_id": ADMIN_ID, "text": text, "parse_mode": "HTML", "reply_markup": keyboard})


def respond(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Authorization")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if status != 204:
        handler.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        respond(self, 204, {})

    def do_GET(self):
        try:
            query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            action = query.get("action", [""])[0]
            if action == "admin_payment_settings":
                user = authenticate_user(self)
                respond(self, 200, admin_get_payment_settings(user))
                return
            if action == "admin_payments":
                user = authenticate_user(self)
                respond(self, 200, get_admin_payment_orders(user))
                return
            if action == "admin_dashboard_stats":
                user = authenticate_user(self)
                respond(self, 200, get_admin_dashboard_stats(user))
                return
            if action == "site_slogans":
                respond(self, 200, {"ok": True, "settings": get_site_slogan_settings()})
                return
            if action == "admin_site_slogans":
                user = authenticate_user(self)
                respond(self, 200, get_admin_site_slogans(user))
                return
            if action == "banners":
                respond(self, 200, get_public_banners())
                return
            if action == "admin_banners":
                user = authenticate_user(self)
                respond(self, 200, get_admin_banners(user))
                return
            if action == "admin_members":
                user = authenticate_user(self)
                respond(self, 200, get_admin_members(user, query))
                return
            if action == "admin_member_audit":
                user = authenticate_user(self)
                respond(self, 200, get_admin_member_audit(user, query))
                return
            if action == "admin_member_listings":
                user = authenticate_user(self)
                respond(self, 200, get_admin_member_listings(user, query))
                return
            if action == "admin_storage_usage":
                user = authenticate_user(self)
                respond(self, 200, get_admin_storage_usage(user))
                return
            if action == "admin_invoice":
                user = authenticate_user(self)
                respond(self, 200, create_admin_invoice_link(user, (query.get("listing_id") or [""])[0]))
                return
            if action == "profile":
                user = authenticate_user(self)
                profile = get_user_profile(user, access_token=user.get("access_token"))
                role = get_user_role(user, access_token=user.get("access_token")) if profile else "user"
                respond(self, 200, {"ok": True, "profile": profile, "role": role})
                return
            if action == "account":
                user = authenticate_user(self)
                respond(self, 200, get_account_data(user, query))
                return
            # Read directly from listings so deleted_at filtering does not depend on an old RPC.
            rows = sb_request(
                "GET",
                "/rest/v1/listings",
                params={
                    "select": "id,title,product_type,weight,weight_unit,purity,province,city,description,pricing_method,pricing_value,price,image_url,created_at,status,deleted_at",
                    "status": "in.(published,sold)",
                    "deleted_at": "is.null",
                    "order": "created_at.desc",
                },
                public=True,
            ) or []
            listings = [
                public_listing(row)
                for row in rows
                if row.get("status") in ("published", "sold") and row.get("deleted_at") is None
            ]
            respond(self, 200, {"ok": True, "listings": listings})
        except AuthError as exc:
            respond(self, 401, {"ok": False, "error": str(exc)})
        except ProfileIncompleteError as exc:
            respond(self, 409, {"ok": False, "code": "PROFILE_INCOMPLETE", "error": str(exc)})
        except Exception as exc:
            print("LISTINGS GET ERROR:", str(exc)[:300])
            respond(self, 500, {"ok": False, "error": "دریافت اطلاعات از پایگاه داده ناموفق بود. گزارش Vercel را بررسی کنید."})

    def do_POST(self):
        try:
            fields, uploads = parse_body(self)
            if fields.get("action") == "track_site_visit":
                respond(self, 202, record_site_visit(fields))
                return
            user = authenticate_user(self)
            if fields.get("action") == "admin_payment_settings_save":
                respond(self, 200, admin_save_payment_settings(user, fields))
                return
            if fields.get("action") == "admin_tariff_settings_save":
                respond(self, 200, admin_save_tariff_settings(user, fields))
                return
            if fields.get("action") == "submit_payment_receipt":
                respond(self, 200, submit_payment_receipt(user, fields, uploads))
                return
            if fields.get("action") == "admin_payment_review":
                respond(self, 200, admin_review_payment(user, fields))
                return
            if fields.get("action") == "admin_site_slogans_save":
                respond(self, 200, save_admin_site_slogans(user, fields))
                return
            if fields.get("action") == "admin_banner_save":
                respond(self, 200, save_admin_banner(user, fields, uploads))
                return
            if fields.get("action") == "admin_banner_delete":
                respond(self, 200, delete_admin_banner(user, fields))
                return
            if fields.get("action") == "admin_member_update":
                respond(self, 200, admin_member_update(user, fields))
                return
            if fields.get("action") == "admin_member_role_update":
                respond(self, 200, admin_member_role_update(user, fields))
                return
            if fields.get("action") == "complete_profile":
                profile = update_user_profile(user, fields, access_token=user.get("access_token"))
                respond(self, 200, {"ok": True, "profile": profile, "message": "پروفایل شما با موفقیت تکمیل شد."})
                return
            if fields.get("action") in ("admin_listing_action", "owner_listing_action", "admin_purchase_action"):
                if PREVIEW_DRY_RUN:
                    respond(self, 200, {"ok": True, "preview": True, "message": "اعتبارسنجی عملیات پنل در حالت آزمایشی انجام شد؛ تغییری ذخیره نشد."})
                    return
                if fields.get("action") == "admin_listing_action":
                    result = admin_listing_action(user, fields)
                elif fields.get("action") == "owner_listing_action":
                    result = owner_listing_action(user, fields)
                else:
                    result = admin_purchase_action(user, fields)
                respond(self, 200, result)
                return
            if PREVIEW_DRY_RUN:
                respond(self, 200, {
                    "ok": True, "preview": True,
                    "message": "ورود و ارسال آزمایشی تأیید شد؛ در این نسخه هیچ آگهی، درخواست خرید یا پیام تلگرامی ذخیره و ارسال نشد.",
                })
                return
            if not uploads and fields.get("action") == "purchase":
                request_row, listing = save_purchase_request(fields, user)
                notified = False
                try:
                    notify_purchase_admin(request_row, listing)
                    notified = True
                except Exception as exc:
                    print("PURCHASE NOTIFICATION FAILED:", str(exc)[:250])
                respond(self, 201 if notified else 202, {
                    "ok": True, "request_id": request_row["id"],
                    "admin_notification_sent": notified,
                    "message": "درخواست خرید ثبت شد. از ثبت دوباره خودداری کنید.",
                })
                return
            result = create_listing(fields, uploads, user)
            respond(self, 201 if result["admin_notification_sent"] else 202, {"ok": True, **result})
        except AuthError as exc:
            respond(self, 401, {"ok": False, "error": str(exc)})
        except ProfileIncompleteError as exc:
            respond(self, 409, {"ok": False, "code": "PROFILE_INCOMPLETE", "error": str(exc)})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            respond(self, 400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            print("LISTINGS POST ERROR:", str(exc)[:400])
            respond(self, 500, {"ok": False, "error": "عملیات در سرور انجام نشد. گزارش Vercel و تنظیمات Supabase را بررسی کنید."})

    def log_message(self, fmt, *args):
        print("HTTP:", fmt % args)
