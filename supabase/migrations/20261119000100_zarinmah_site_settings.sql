-- ZarinMah homepage slogan settings.
-- The application accesses this table only through the backend service-role key.
create table if not exists public.zarinmah_site_settings (
  setting_key text primary key,
  setting_value jsonb not null default '{}'::jsonb,
  updated_by uuid null references auth.users(id) on delete set null,
  updated_at timestamptz not null default now()
);

alter table public.zarinmah_site_settings enable row level security;
revoke all on table public.zarinmah_site_settings from anon, authenticated;
grant all on table public.zarinmah_site_settings to service_role;
