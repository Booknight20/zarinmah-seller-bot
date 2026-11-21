-- ZarinMah market history state (Staging only)
-- Run this in the SQL Editor of Supabase project qijahxokldkaqmjinfvy.
create table if not exists public.market_history_state (
  id text primary key check (id = 'daily'),
  state jsonb not null,
  updated_at timestamptz not null default now()
);

alter table public.market_history_state enable row level security;

revoke all on table public.market_history_state from anon, authenticated;
grant all on table public.market_history_state to service_role;

comment on table public.market_history_state is
  'ZarinMah daily market price history state; service-role access only.';
