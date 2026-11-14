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
SUPABASE_TIMEOUT = 30
PREVIEW_DRY_RUN = os.environ.get("ZARINMAH_PREVIEW_DRY_RUN", "").strip().lower() == "true"
# Enable only after the image_url_2 database migration has been applied.
SECOND_IMAGE_ENABLED = os.environ.get("ZARINMAH_SECOND_IMAGE_ENABLED", "").strip().lower() == "true"
MAX_BODY_SIZE = 4 * 1024 * 1024
MAX_FILE_SIZE = 1536 * 1024
MAX_PHOTO_SIZE = 500 * 1024
STORAGE_QUOTA_BYTES = 1024 * 1024 * 1024

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


def sb_headers(extra=None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL یا SUPABASE_SERVICE_ROLE_KEY در تنظیمات Vercel وجود ندارد.")
    result = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    }
    if extra:
        result.update(extra)
    return result


def sb_request(method, path, *, params=None, payload=None, raw=None, content_type=None, prefer=None):
    headers = sb_headers()
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


STAGING_SUPABASE_URL = "https://qijahxokldkaqmjinfvy.supabase.co"


def is_staging_email_user(user):
    return (
        SUPABASE_URL == STAGING_SUPABASE_URL
        and bool(clean(user.get("email"), 254))
        and not clean(user.get("phone"), 30)
    )


def authenticate_user(handler):
    authorization = clean(handler.headers.get("Authorization", ""), 2048)
    if not authorization.startswith("Bearer "):
        raise AuthError("برای ادامه ابتدا وارد حساب کاربری شوید.")
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("تنظیمات اتصال به Supabase کامل نیست.")
    try:
        response = requests.get(
            SUPABASE_URL + "/auth/v1/user",
            headers={
                "apikey": SUPABASE_SERVICE_ROLE_KEY,
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
    if not user_id:
        raise AuthError("شناسه حساب کاربری معتبر نیست.")
    if valid_phone(phone):
        return {"id": user_id, "phone": phone, "email": clean(user.get("email"), 254)}
    if is_staging_email_user(user):
        return {"id": user_id, "phone": "", "email": clean(user.get("email"), 254)}
    raise AuthError("حساب کاربری باید شماره موبایل تأییدشده داشته باشد.")


def get_user_profile(user):
    rows = sb_request(
        "GET", "/rest/v1/profiles",
        params={
            "id": f"eq.{user['id']}",
            "select": "id,full_name,mobile,is_active",
            "limit": "1",
        },
    ) or []
    return rows[0] if rows else None


def require_complete_profile(user):
    profile = get_user_profile(user)
    if not profile:
        raise ProfileIncompleteError("پروفایل حساب شما هنوز ایجاد نشده است. صفحه را تازه‌سازی کنید یا با پشتیبانی تماس بگیرید.")
    if profile.get("is_active") is not True:
        raise AuthError("حساب کاربری شما غیرفعال است. برای پیگیری با پشتیبانی تماس بگیرید.")
    full_name = clean(profile.get("full_name"), 100)
    if not full_name:
        raise ProfileIncompleteError("برای ادامه، ابتدا نام و نام خانوادگی خود را در حساب کاربری تکمیل کنید.")
    return profile


def update_user_profile(user, data):
    full_name = clean(data.get("full_name"), 100)
    if len(full_name.split()) < 2 or len(full_name) < 5:
        raise ValueError("نام و نام خانوادگی کامل را وارد کنید.")
    existing = get_user_profile(user)
    if existing and existing.get("is_active") is not True:
        raise AuthError("حساب کاربری شما غیرفعال است. برای پیگیری با پشتیبانی تماس بگیرید.")
    # Only isolated Staging email-authenticated test accounts may provide an
    # unverified contact number. Production accounts must use Supabase's
    # verified Auth phone and cannot overwrite it through this endpoint.
    if is_staging_email_user(user):
        mobile = clean(data.get("mobile"), 30)
        if not valid_phone(mobile):
            raise ValueError("برای حساب آزمایشی، شماره تماس معتبر وارد کنید.")
    else:
        mobile = clean(user.get("phone"), 30) or None
    profile_payload = {"full_name": full_name, "mobile": mobile, "updated_at": now_iso()}
    if existing:
        rows = sb_request(
            "PATCH", "/rest/v1/profiles",
            params={"id": f"eq.{user['id']}"},
            payload=profile_payload,
            prefer="return=representation",
        ) or []
    else:
        rows = sb_request(
            "POST", "/rest/v1/profiles",
            payload={
                "id": user["id"],
                **profile_payload,
                "is_active": True,
                "created_at": now_iso(),
            },
            prefer="return=representation",
        ) or []
    if not rows:
        raise ProfileIncompleteError("ذخیره پروفایل انجام نشد. کمی بعد دوباره تلاش کنید.")
    role_rows = sb_request(
        "GET", "/rest/v1/user_roles",
        params={"user_id": f"eq.{user['id']}", "select": "role", "limit": "1"},
    ) or []
    if not role_rows:
        sb_request(
            "POST", "/rest/v1/user_roles",
            payload={"user_id": user["id"], "role": "user"},
            prefer="resolution=ignore-duplicates,return=minimal",
        )
    return rows[0]




def get_user_role(user):
    rows = sb_request(
        "GET", "/rest/v1/user_roles",
        params={"user_id": f"eq.{user['id']}", "select": "role", "limit": "1"},
    ) or []
    return rows[0].get("role", "user") if rows else "user"


def require_admin(user):
    profile = require_complete_profile(user)
    if get_user_role(user) != "admin":
        raise AuthError("این عملیات فقط برای مدیر سامانه مجاز است.")
    return profile



def sb_request_counted(path, params):
    headers = sb_headers({"Prefer": "count=exact"})
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
    profile = require_complete_profile(user)
    role = get_user_role(user)
    admin = role == "admin"
    per_page = 20
    listings_page = account_page((query.get("listings_page") or ["1"])[0])
    requests_page = account_page((query.get("requests_page") or ["1"])[0])
    listings_status = clean((query.get("listings_status") or ["all"])[0], 20)
    requests_status = clean((query.get("requests_status") or ["all"])[0], 20)
    listings_search = account_search((query.get("listings_search") or [""])[0])
    requests_search = account_search((query.get("requests_search") or [""])[0])

    listing_params = {
        "select": "id,title,product_type,weight,weight_unit,purity,province,city,description,pricing_method,pricing_value,price,image_url" + (",image_url_2" if SECOND_IMAGE_ENABLED else "") + ",status,created_at,updated_at,deleted_at,seller_name,seller_mobile,owner_user_id",
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

    listings, listings_total = sb_request_counted("/rest/v1/listings", listing_params)
    requests_rows, requests_total = sb_request_counted("/rest/v1/purchase_requests", request_params)

    # Only administrators receive the registered seller account details.
    if admin:
        owner_ids = sorted({
            str(item.get("owner_user_id"))
            for item in listings
            if item.get("owner_user_id")
        })
        seller_profiles = {}
        if owner_ids:
            profile_rows = sb_request(
                "GET", "/rest/v1/profiles",
                params={
                    "select": "id,full_name,mobile,is_active",
                    "id": "in.(" + ",".join(owner_ids) + ")",
                    "limit": str(len(owner_ids)),
                },
            ) or []
            seller_profiles = {
                str(row.get("id")): {
                    "full_name": row.get("full_name"),
                    "mobile": row.get("mobile"),
                    "is_active": row.get("is_active"),
                }
                for row in profile_rows
            }
        for listing in listings:
            owner_id = str(listing.get("owner_user_id") or "")
            listing["seller_account"] = seller_profiles.get(owner_id)

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

    return {
        "ok": True,
        "profile": profile,
        "role": role,
        "listings": listings,
        "purchase_requests": requests_rows,
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


def get_admin_members(user, query=None):
    require_admin(user)
    query = query or {}
    page = account_page((query.get("members_page") or ["1"])[0])
    search = account_search((query.get("members_search") or [""])[0])
    per_page = 20
    params = {
        "select": "id,full_name,mobile,is_active,created_at",
        "order": "created_at.desc",
        "limit": str(per_page),
        "offset": str((page - 1) * per_page),
    }
    if search:
        term = f"*{search}*"
        params["or"] = f"(full_name.ilike.{term},mobile.ilike.{term})"
    members, total = sb_request_counted("/rest/v1/profiles", params)
    member_ids = [str(item.get("id")) for item in members if item.get("id")]
    counts = {}
    for member_id in member_ids:
        counts[member_id] = {"total": 0, "active": 0, "pending": 0, "published": 0, "sold": 0, "deleted": 0}
    if member_ids:
        rows = sb_request(
            "GET", "/rest/v1/listings",
            params={
                "select": "owner_user_id,status,deleted_at",
                "owner_user_id": "in.(" + ",".join(member_ids) + ")",
                "limit": "10000",
            },
        ) or []
        for row in rows:
            owner = str(row.get("owner_user_id") or "")
            if owner not in counts:
                continue
            item = counts[owner]
            item["total"] += 1
            if row.get("deleted_at") is not None:
                item["deleted"] += 1
            else:
                item["active"] += 1
                status = row.get("status")
                if status in ("pending", "published", "sold"):
                    item[status] += 1
    roles = {}
    if member_ids:
        role_rows = sb_request(
            "GET", "/rest/v1/user_roles",
            params={
                "select": "user_id,role",
                "user_id": "in.(" + ",".join(member_ids) + ")",
                "limit": "1000",
            },
        ) or []
        roles = {str(row.get("user_id")): row.get("role") or "user" for row in role_rows}
    for member in members:
        member["listing_counts"] = counts.get(str(member.get("id")), {})
        member["role"] = roles.get(str(member.get("id")), "user")
    return {
        "ok": True,
        "members": members,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "search": search,
        },
    }


def get_admin_member_listings(user, query=None):
    require_admin(user)
    query = query or {}
    member_id = clean((query.get("member_id") or [""])[0], 80)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", member_id):
        raise ValueError("شناسه عضو معتبر نیست.")
    page = account_page((query.get("member_listings_page") or ["1"])[0])
    per_page = 10
    status = clean((query.get("member_listings_status") or ["all"])[0], 20)
    params = {
        "select": "id,title,product_type,weight,weight_unit,purity,province,city,description,pricing_method,pricing_value,price,image_url" + (",image_url_2" if SECOND_IMAGE_ENABLED else "") + ",status,created_at,updated_at,deleted_at,invoice_path",
        "owner_user_id": f"eq.{member_id}",
        "order": "created_at.desc",
        "limit": str(per_page),
        "offset": str((page - 1) * per_page),
    }
    if status == "deleted":
        params["deleted_at"] = "not.is.null"
    else:
        params["deleted_at"] = "is.null"
        if status in ("pending", "published", "sold"):
            params["status"] = f"eq.{status}"
        elif status != "all":
            status = "all"
    rows, total = sb_request_counted("/rest/v1/listings", params)
    listings = []
    for row in rows:
        item = public_listing(row)
        item["updated_at"] = row.get("updated_at")
        item["deleted_at"] = row.get("deleted_at")
        item["invoice_available"] = bool(row.get("invoice_path"))
        listings.append(item)
    return {
        "ok": True,
        "member_id": member_id,
        "listings": listings,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max(1, (total + per_page - 1) // per_page),
            "status": status,
        },
    }


def admin_member_update(user, data):
    require_admin(user)
    member_id = clean(data.get("member_id"), 80)
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", member_id):
        raise ValueError("شناسه عضو معتبر نیست.")
    if member_id == user["id"] and data.get("is_active") is False:
        raise ValueError("برای جلوگیری از قفل‌شدن پنل، نمی‌توانید حساب خودتان را غیرفعال کنید.")
    full_name = clean(data.get("full_name"), 100)
    if len(full_name.split()) < 2 or len(full_name) < 5:
        raise ValueError("نام و نام خانوادگی معتبر وارد کنید.")
    active_value = data.get("is_active")
    if not isinstance(active_value, bool):
        raise ValueError("وضعیت فعال‌بودن حساب معتبر نیست.")
    rows = sb_request(
        "PATCH", "/rest/v1/profiles",
        params={"id": f"eq.{member_id}"},
        payload={"full_name": full_name, "is_active": active_value, "updated_at": now_iso()},
        prefer="return=representation",
    ) or []
    if not rows:
        raise ValueError("عضو پیدا نشد یا اطلاعات او به‌روزرسانی نشد.")
    return {"ok": True, "member": rows[0], "message": "اطلاعات عضو به‌روزرسانی شد."}


def get_admin_storage_usage(user):
    require_admin(user)
    buckets = ("listing-images", "listing-invoices")
    result = []
    for bucket in buckets:
        total_bytes = 0
        file_count = 0
        unknown_size_files = 0
        truncated = False
        diagnostics = []
        pending_prefixes = [""]
        visited_prefixes = set()
        processed_entries = 0
        page_size = 1000

        while pending_prefixes:
            prefix = pending_prefixes.pop(0)
            if prefix in visited_prefixes:
                continue
            visited_prefixes.add(prefix)
            offset = 0

            while True:
                objects = sb_request(
                    "POST", f"/storage/v1/object/list/{bucket}",
                    payload={"prefix": prefix, "limit": page_size, "offset": offset},
                ) or []
                if not isinstance(objects, list):
                    raise RuntimeError("پاسخ فهرست فایل‌های Storage معتبر نیست.")

                for item in objects:
                    processed_entries += 1
                    if processed_entries > 10000:
                        truncated = True
                        break

                    name = clean(item.get("name"), 1000)
                    # Supabase returns folders with a null id and files with an id.
                    if item.get("id") is None:
                        if name and not name.startswith("/") and all(
                            part not in ("", ".", "..") for part in name.split("/")
                        ):
                            child_prefix = prefix + name.strip("/") + "/"
                            if child_prefix not in visited_prefixes:
                                pending_prefixes.append(child_prefix)
                        continue

                    file_count += 1
                    metadata = item.get("metadata") or {}
                    size = None
                    if isinstance(metadata, dict):
                        for key in ("size", "contentLength", "content_length"):
                            if metadata.get(key) is not None:
                                size = metadata.get(key)
                                break
                    if size is None:
                        size = item.get("size")
                    try:
                        size = int(size) if size is not None else None
                    except (TypeError, ValueError):
                        size = None

                    if size is None or size < 0:
                        unknown_size_files += 1
                        if len(diagnostics) < 5:
                            diagnostics.append({
                                "stage": "file_size_missing",
                                "item_keys": sorted(str(k) for k in item.keys())[:30],
                                "metadata_keys": sorted(str(k) for k in metadata.keys())[:30] if isinstance(metadata, dict) else [],
                                "has_file_id": True,
                            })
                    else:
                        total_bytes += size

                if truncated or len(objects) < page_size:
                    break
                offset += page_size
                if offset >= 10000:
                    truncated = True
                    break

            if truncated:
                break

        result.append({
            "bucket": bucket,
            "bytes": total_bytes,
            "files": file_count,
            "unknown_size_files": unknown_size_files,
            "quota_bytes": STORAGE_QUOTA_BYTES,
            "used_percent": round((total_bytes / STORAGE_QUOTA_BYTES) * 100, 2),
            "truncated": truncated,
            "diagnostics": diagnostics,
        })
    return {"ok": True, "storage": result, "quota_bytes": STORAGE_QUOTA_BYTES}

def create_admin_invoice_link(user, listing_id):
    require_admin(user)
    try:
        listing_id = int(listing_id)
    except (TypeError, ValueError):
        raise ValueError("شناسه آگهی معتبر نیست.")
    listing = get_listing(listing_id)
    if not listing:
        raise ValueError("آگهی پیدا نشد.")
    path = clean(listing.get("invoice_path"), 500)
    parts = path.split("/")
    if (
        len(parts) != 2
        or parts[0] != "website"
        or not parts[1]
        or parts[1] in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}", parts[1])
        or not re.search(r"\.(?:jpg|jpeg|png|pdf)$", parts[1], re.IGNORECASE)
    ):
        raise ValueError("فاکتور خصوصی برای این آگهی ثبت نشده است.")
    encoded_path = "/".join(quote(part, safe="") for part in parts)
    result = sb_request(
        "POST", f"/storage/v1/object/sign/listing-invoices/{encoded_path}",
        payload={"expiresIn": 90},
    ) or {}
    signed = result.get("signedURL") or result.get("signedUrl")
    if not signed:
        raise RuntimeError("ساخت لینک موقت فاکتور ناموفق بود.")
    if signed.startswith("http://") or signed.startswith("https://"):
        pass
    elif signed.startswith("/storage/v1/"):
        signed = SUPABASE_URL + signed
    elif signed.startswith("/"):
        # Supabase Storage returns paths relative to the Storage API base.
        signed = SUPABASE_URL + "/storage/v1" + signed
    else:
        signed = SUPABASE_URL + "/storage/v1/" + signed.lstrip("/")
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


def admin_listing_action(user, data, dry_run=False):
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
    if dry_run:
        return {"ok": True, "preview": True, "message": "اعتبارسنجی عملیات مدیریتی آگهی انجام شد؛ تغییری ذخیره نشد."}
    row = update_listing_status(listing_id, patch, filters)
    return {"ok": True, "listing": row, "message": "وضعیت آگهی به‌روزرسانی شد."}


def owner_listing_action(user, data, dry_run=False):
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
    if dry_run:
        return {"ok": True, "preview": True, "message": "اعتبارسنجی مالکیت و عملیات آگهی انجام شد؛ تغییری ذخیره نشد."}
    row = update_listing_status(listing_id, patch, filters)
    return {"ok": True, "listing": row, "message": "آگهی شما به‌روزرسانی شد."}


def admin_purchase_action(user, data, dry_run=False):
    require_admin(user)
    try:
        request_id = int(data.get("request_id", 0))
    except (TypeError, ValueError):
        raise ValueError("شناسه درخواست خرید معتبر نیست.")
    action = clean(data.get("request_action"), 30)
    if action != "forward":
        raise ValueError("عملیات درخواست خرید معتبر نیست.")
    if dry_run:
        return {"ok": True, "preview": True, "message": "اعتبارسنجی عملیات مدیریتی درخواست خرید انجام شد؛ تغییری ذخیره نشد."}
    rows = sb_request(
        "PATCH", "/rest/v1/purchase_requests",
        params={"id": f"eq.{request_id}", "status": "eq.pending"},
        payload={"status": "forwarded", "updated_at": now_iso()},
        prefer="return=representation",
    ) or []
    if not rows:
        raise ValueError("درخواست خرید پیدا نشد یا قبلاً بررسی شده است.")
    return {"ok": True, "request": rows[0], "message": "درخواست به وضعیت بررسی‌شده تغییر کرد."}

def upload_storage(bucket, path, content, mime):
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    sb_request(
        "POST", f"/storage/v1/object/{bucket}/{encoded_path}",
        raw=content, content_type=mime,
        prefer="resolution=ignore-duplicates",
    )
    if bucket == "listing-images":
        return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{encoded_path}"
    return path


def delete_storage(bucket, path):
    if not path:
        return
    try:
        encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
        sb_request("DELETE", f"/storage/v1/object/{bucket}/{encoded_path}")
    except Exception as exc:
        print("STORAGE CLEANUP FAILED:", str(exc)[:200])


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
        limit_mb = "۵۰۰ کیلوبایت" if max_size == MAX_PHOTO_SIZE else "۱٫۵ مگابایت"
        raise ValueError(f"حجم {label} نباید بیشتر از {limit_mb} باشد.")
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


def create_listing(fields, uploads, user):
    profile = require_complete_profile(user)
    photo = check_file(uploads.get("sellerPhoto") or uploads.get("photo"), {"image/jpeg", "image/png", "image/webp"}, "عکس محصول", max_size=MAX_PHOTO_SIZE)
    second_photo = uploads.get("sellerPhoto2")
    if second_photo:
        second_photo = check_file(second_photo, {"image/jpeg", "image/png", "image/webp"}, "عکس دوم محصول", max_size=MAX_PHOTO_SIZE)
        if not sniff_photo(second_photo["data"]):
            raise ValueError("محتوای عکس دوم محصول معتبر نیست.")
        if not SECOND_IMAGE_ENABLED:
            raise ValueError("ثبت عکس دوم هنوز فعال نشده است؛ پس از تکمیل به‌روزرسانی پایگاه داده فعال می‌شود.")
    invoice = check_file(uploads.get("sellerInvoice") or uploads.get("invoice"), {"image/jpeg", "image/png", "image/webp", "application/pdf"}, "فاکتور فروش")
    if not sniff_photo(photo["data"]):
        raise ValueError("محتوای عکس محصول معتبر نیست.")
    if not sniff_invoice(invoice["data"]):
        raise ValueError("محتوای فاکتور معتبر نیست.")

    seller_name = clean(profile.get("full_name"), 100)
    # Staging email-authenticated test accounts use the profile contact number;
    # real accounts always use the phone verified by Supabase Auth.
    seller_mobile = clean(profile.get("mobile"), 30) if is_staging_email_user(user) else clean(user.get("phone"), 30)
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
    title = clean(fields.get("title"), 150) or product_type
    token = uuid.uuid4().hex
    photo_ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[photo["content_type"]]
    invoice_ext = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[invoice["content_type"]]
    photo_path = f"website/{token}.{photo_ext}"
    second_photo_ext = None
    second_photo_path = None
    if second_photo:
        second_photo_ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[second_photo["content_type"]]
        second_photo_path = f"website/{token}-2.{second_photo_ext}"
    invoice_path = f"website/{token}.{invoice_ext}"
    photo_uploaded = second_photo_uploaded = invoice_uploaded = False
    try:
        image_url = upload_storage("listing-images", photo_path, photo["data"], photo["content_type"])
        photo_uploaded = True
        image_url_2 = None
        if second_photo:
            image_url_2 = upload_storage("listing-images", second_photo_path, second_photo["data"], second_photo["content_type"])
            second_photo_uploaded = True
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
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "seller_name": seller_name,
            "seller_mobile": seller_mobile,
            "weight_unit": weight_unit,
            "province": province,
            "city": city,
            "invoice_path": saved_invoice_path,
        }
        if SECOND_IMAGE_ENABLED:
            row["image_url_2"] = image_url_2
        inserted = sb_request("POST", "/rest/v1/listings", payload=row, prefer="return=representation")
        if not inserted:
            raise RuntimeError("شناسه آگهی از Supabase دریافت نشد.")
        record = inserted[0]
        listing_id = int(record["id"])
        listing = {
            "id": listing_id, "seller_name": seller_name, "seller_mobile": seller_mobile,
            "product_type": product_type, "weight": weight, "weight_unit": weight_unit,
            "purity": purity, "province": province, "city": city,
            "pricing_method": pricing_method, "description": description,
            "_weight_label": clean(fields.get("parsianWeight") if weight_unit == "soot" else fields.get("sellerWeight")) or "ثبت نشده",
            "_weight_unit_label": "سوت" if weight_unit == "soot" else "گرم",
            "_price_label": price_label,
            "image_url": image_url,
            "image_url_2": image_url_2,
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
        if invoice_uploaded:
            delete_storage("listing-invoices", invoice_path)
        if second_photo_uploaded:
            delete_storage("listing-images", second_photo_path)
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
        "photo_url_2": item.get("image_url_2"), "image_url_2": item.get("image_url_2"),
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
            if action == "profile":
                user = authenticate_user(self)
                profile = get_user_profile(user)
                role = get_user_role(user) if profile else "user"
                respond(self, 200, {"ok": True, "profile": profile, "role": role})
                return
            if action == "account":
                user = authenticate_user(self)
                respond(self, 200, get_account_data(user, query))
                return
            if action == "admin_members":
                user = authenticate_user(self)
                respond(self, 200, get_admin_members(user, query))
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
                result = create_admin_invoice_link(user, (query.get("listing_id") or [""])[0])
                respond(self, 200, result)
                return
            # Read directly from listings so deleted_at filtering does not depend on an old RPC.
            rows = sb_request(
                "GET",
                "/rest/v1/listings",
                params={
                    "select": "id,title,product_type,weight,weight_unit,purity,province,city,description,pricing_method,pricing_value,price,image_url" + (",image_url_2" if SECOND_IMAGE_ENABLED else "") + ",created_at,status,deleted_at",
                    "status": "in.(published,sold)",
                    "deleted_at": "is.null",
                    "order": "created_at.desc",
                },
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
            user = authenticate_user(self)
            fields, uploads = parse_body(self)
            if fields.get("action") == "complete_profile":
                # Allow only the minimal account-profile write for email-authenticated
                # test users in the isolated Staging project. All other preview writes
                # remain protected by PREVIEW_DRY_RUN.
                if PREVIEW_DRY_RUN and not is_staging_email_user(user):
                    respond(self, 200, {
                        "ok": True, "preview": True,
                        "message": "اعتبارسنجی فرم تکمیل پروفایل در حالت آزمایشی انجام شد؛ اطلاعات ذخیره نشد.",
                    })
                    return
                profile = update_user_profile(user, fields)
                respond(self, 200, {"ok": True, "profile": profile, "message": "پروفایل شما با موفقیت تکمیل شد."})
                return
            if fields.get("action") == "admin_member_update":
                require_admin(user)
                if PREVIEW_DRY_RUN:
                    respond(self, 200, {"ok": True, "preview": True, "message": "حالت آزمایشی: تغییر حساب ذخیره نشد."})
                    return
                result = admin_member_update(user, fields)
                respond(self, 200, result)
                return
            if fields.get("action") in ("admin_listing_action", "owner_listing_action", "admin_purchase_action"):
                if fields.get("action") == "admin_listing_action":
                    result = admin_listing_action(user, fields, dry_run=PREVIEW_DRY_RUN)
                elif fields.get("action") == "owner_listing_action":
                    result = owner_listing_action(user, fields, dry_run=PREVIEW_DRY_RUN)
                else:
                    result = admin_purchase_action(user, fields, dry_run=PREVIEW_DRY_RUN)
                respond(self, 200, result)
                return
            if PREVIEW_DRY_RUN:
                if uploads:
                    photo = check_file(uploads.get("sellerPhoto"), {"image/jpeg", "image/png", "image/webp"}, "عکس اول محصول", max_size=MAX_PHOTO_SIZE)
                    if not sniff_photo(photo["data"]):
                        raise ValueError("محتوای عکس اول محصول معتبر نیست.")
                    second_photo = uploads.get("sellerPhoto2")
                    if second_photo:
                        second_photo = check_file(second_photo, {"image/jpeg", "image/png", "image/webp"}, "عکس دوم محصول", max_size=MAX_PHOTO_SIZE)
                        if not sniff_photo(second_photo["data"]):
                            raise ValueError("محتوای عکس دوم محصول معتبر نیست.")
                    invoice = check_file(uploads.get("sellerInvoice"), {"image/jpeg", "image/png", "image/webp", "application/pdf"}, "فاکتور فروش")
                    if not sniff_invoice(invoice["data"]):
                        raise ValueError("محتوای فاکتور فروش معتبر نیست.")
                respond(self, 200, {
                    "ok": True, "preview": True,
                    "message": "اعتبارسنجی عکس اول، عکس دوم (در صورت انتخاب) و فاکتور انجام شد؛ هیچ آگهی یا فایلی ذخیره نشد.",
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
