-- Store administrator rejection details for seller listings.
-- Apply this migration only to the isolated Supabase Staging project first.

alter table public.listings
  add column if not exists rejection_reason_code text,
  add column if not exists rejection_reason_label text,
  add column if not exists rejection_reason_text text,
  add column if not exists rejected_at timestamptz;

comment on column public.listings.rejection_reason_code is
  'Machine-readable reason selected by a listing moderator when rejecting the listing.';
comment on column public.listings.rejection_reason_label is
  'Persian label of the listing rejection reason shown to the seller.';
comment on column public.listings.rejection_reason_text is
  'Optional moderator explanation shown privately to the listing owner.';
comment on column public.listings.rejected_at is
  'Timestamp when the listing was rejected by a moderator.';
