-- ZarinMah: add optional second listing image URL and expose it publicly.
-- Prepared for review only. DO NOT RUN until separately approved.
--
-- Verified against the live database:
--   * public_listings selects the columns listed below from public.listings.
--   * Its filter is status IN ('published', 'sold') AND deleted_at IS NULL.
--   * Its reloption is security_invoker=true.
--   * Existing grants were inspected; this migration does not alter grants.
--
-- Compatibility:
--   * Existing image_url remains unchanged.
--   * Existing rows receive NULL for image_url_2.
--   * The new view column is appended, preserving all existing column positions.
--   * CREATE OR REPLACE VIEW preserves the existing view ownership and ACLs.
--   * No listing rows or Storage objects are modified or deleted.

begin;

alter table public.listings
  add column if not exists image_url_2 text;

create or replace view public.public_listings
with (security_invoker = true)
as
select
  id,
  title,
  product_type,
  weight,
  purity,
  pricing_method,
  price,
  description,
  image_url,
  status,
  created_at,
  updated_at,
  image_url_2
from public.listings
where status = any (array['published'::text, 'sold'::text])
  and deleted_at is null;

commit;
