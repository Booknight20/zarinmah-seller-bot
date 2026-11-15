-- Admin-only audit trail for member administration.
-- Apply only to the isolated Supabase Staging project.
create table if not exists public.admin_audit_logs (
  id bigint generated always as identity primary key,
  actor_user_id uuid not null,
  target_user_id uuid not null,
  action text not null check (char_length(action) <= 60),
  old_values jsonb not null default '{}'::jsonb,
  new_values jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists admin_audit_logs_target_created_idx
  on public.admin_audit_logs (target_user_id, created_at desc);
create index if not exists admin_audit_logs_actor_created_idx
  on public.admin_audit_logs (actor_user_id, created_at desc);

alter table public.admin_audit_logs enable row level security;
revoke all on table public.admin_audit_logs from anon, authenticated;
grant all on table public.admin_audit_logs to service_role;
