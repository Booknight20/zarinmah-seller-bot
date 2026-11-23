# ZarinMah Backend Cloudflare Migration Notes

Status: isolated preparation branch only; the current Vercel backend remains untouched.

## Source baseline
- Repository: `Booknight20/zarinmah-seller-bot`
- Baseline branch: `fix/phone-password-username-profile-2026-11-21`
- Baseline commit: `191ef7719419f8e3e8d70d3c32c92917dff47cb4`
- Existing runtime: Vercel Python functions; `requirements.txt` currently pins `requests==2.32.5`.

## API responsibilities to preserve
- `api/listings.py`: primary seller/listings API, Supabase access, auth validation, admin-only actions, payment receipts and subscription package workflows.
- `api/webhook.py`: Telegram webhook and moderation/admin actions.
- `api/market_history.py`: market-history persistence API.
- `api/photo.py`: image/photo handling.
- GitHub Actions workflows: seller bot and Telegram webhook registration/scheduling.

## Cloudflare target design
- Convert the HTTP listings API to a TypeScript Worker/Pages Function with strict request-size limits, Supabase JWT verification, role checks, RLS-aware behavior, and private Storage access.
- Keep Telegram bot polling/webhook and scheduled jobs separate from the public storefront Worker unless a specific workload is deliberately migrated and tested.
- Do not expose Supabase service-role credentials, Telegram bot tokens, or SMS provider secrets to the browser.
- Keep Staging-only bindings in the preview environment; Production secrets must not be copied here.

## Parity gate
Before Cloudflare Preview can be considered functional, test:
- anonymous published-listing reads;
- authenticated profile and listing actions;
- admin-only actions and audit logging;
- seller payment receipt submission and admin review;
- subscription package purchase, activation, limits, and cancellation;
- banner and site-slogan management;
- Telegram webhook and seller-bot operations remain unaffected;
- market-history read/write and stale-price handling.

No Production deployment, DNS changes, data migration, or destructive operation is authorized by this preparation branch.
