-- ZarinMah: add optional second listing image URL.
-- Prepared for review only. DO NOT RUN until the live Supabase view definition
-- and grants/options have been inspected and this migration is explicitly approved.
--
-- Compatibility:
--   * Existing image_url remains unchanged.
--   * Existing rows receive NULL for image_url_2.
--   * No listing rows or Storage objects are modified or deleted.
--
-- This first-stage migration adds only the nullable column.
-- The public_listings view update is intentionally NOT included here:
-- CREATE OR REPLACE VIEW must preserve the exact current SELECT list, column
-- order, predicates, security_invoker/security_barrier options and grants.
-- Retrieve the live definition first, then append listings.image_url_2 as the
-- final output column in a separately reviewed migration.

begin;

alter table public.listings
  add column if not exists image_url_2 text;

commit;
