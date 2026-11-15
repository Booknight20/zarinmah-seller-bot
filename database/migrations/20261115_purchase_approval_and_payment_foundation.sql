-- Purchase approval and access-payment foundation.
-- Apply ONLY to the isolated ZarinMah Staging Supabase project.
alter table public.purchase_requests
  add column if not exists seller_approved_at timestamptz,
  add column if not exists expires_at timestamptz,
  add column if not exists paid_at timestamptz;

create index if not exists purchase_requests_listing_status_created_idx
  on public.purchase_requests (listing_id, status, created_at desc);

create unique index if not exists purchase_requests_buyer_listing_active_uidx
  on public.purchase_requests (buyer_user_id, listing_id)
  where buyer_user_id is not null
    and status in ('pending', 'approved', 'forwarded', 'paid');

create table if not exists public.purchase_access_payments (
  id bigint generated always as identity primary key,
  request_id bigint not null unique
    references public.purchase_requests(id) on delete restrict,
  buyer_user_id uuid not null,
  provider text not null default 'zarinpal',
  amount_toman bigint not null check (amount_toman > 0),
  amount_rial bigint not null check (amount_rial > 0),
  authority text unique,
  reference_id text,
  return_url text,
  status text not null default 'created'
    check (status in ('created', 'pending', 'paid', 'failed', 'refunded')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  verified_at timestamptz
);

create index if not exists purchase_access_payments_buyer_created_idx
  on public.purchase_access_payments (buyer_user_id, created_at desc);

alter table public.purchase_access_payments enable row level security;
revoke all on table public.purchase_access_payments from anon, authenticated;
grant all on table public.purchase_access_payments to service_role;
