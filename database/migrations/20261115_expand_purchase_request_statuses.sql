-- Expand purchase request lifecycle states for the seller approval and buyer payment flow.
-- Apply ONLY to the isolated ZarinMah Staging Supabase project.
alter table public.purchase_requests
  drop constraint if exists purchase_requests_status_check;

alter table public.purchase_requests
  add constraint purchase_requests_status_check
  check (status in ('pending', 'approved', 'forwarded', 'rejected', 'expired', 'paid'));
