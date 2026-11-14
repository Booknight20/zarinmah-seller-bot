-- Privacy-conscious, anonymous visit/session analytics for ZarinMah.
-- Apply only to the isolated Supabase Staging project for Preview validation.
create table if not exists public.site_visit_events (
  id bigint generated always as identity primary key,
  visitor_id uuid not null,
  session_id uuid not null unique,
  path text not null default '/',
  created_at timestamptz not null default now(),
  constraint site_visit_events_path_length check (char_length(path) <= 200)
);

create index if not exists site_visit_events_created_at_idx
  on public.site_visit_events (created_at desc);
create index if not exists site_visit_events_visitor_created_at_idx
  on public.site_visit_events (visitor_id, created_at desc);

alter table public.site_visit_events enable row level security;
revoke all on table public.site_visit_events from anon, authenticated;
grant all on table public.site_visit_events to service_role;

create or replace function public.zarinmah_admin_dashboard_stats()
returns jsonb
language sql
security definer
set search_path = public
as $$
  with daily as (
    select
      (created_at at time zone 'Asia/Tehran')::date as day,
      count(*)::bigint as visits,
      count(distinct visitor_id)::bigint as unique_visitors
    from public.site_visit_events
    where created_at >= (((now() at time zone 'Asia/Tehran')::date - 29)::timestamp at time zone 'Asia/Tehran')
    group by 1
  ),
  days as (
    select generate_series(
      (now() at time zone 'Asia/Tehran')::date - 29,
      (now() at time zone 'Asia/Tehran')::date,
      interval '1 day'
    )::date as day
  )
  select jsonb_build_object(
    'members_total', (select count(*) from public.profiles),
    'members_active', (select count(*) from public.profiles where is_active is true),
    'members_inactive', (select count(*) from public.profiles where is_active is not true),
    'members_with_listings', (
      select count(distinct owner_user_id)
      from public.listings
      where owner_user_id is not null and deleted_at is null
    ),
    'listings_total', (select count(*) from public.listings where deleted_at is null),
    'listings_pending', (select count(*) from public.listings where deleted_at is null and status = 'pending'),
    'listings_published', (select count(*) from public.listings where deleted_at is null and status = 'published'),
    'listings_sold', (select count(*) from public.listings where deleted_at is null and status = 'sold'),
    'visits_today', (
      select count(*) from public.site_visit_events
      where created_at >= ((now() at time zone 'Asia/Tehran')::date::timestamp at time zone 'Asia/Tehran')
    ),
    'visits_7d', (
      select count(*) from public.site_visit_events where created_at >= now() - interval '7 days'
    ),
    'visits_30d', (
      select count(*) from public.site_visit_events where created_at >= now() - interval '30 days'
    ),
    'visits_total', (select count(*) from public.site_visit_events),
    'visitors_30d', (
      select count(distinct visitor_id) from public.site_visit_events where created_at >= now() - interval '30 days'
    ),
    'visitors_total', (select count(distinct visitor_id) from public.site_visit_events),
    'visits_daily', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'day', to_char(days.day, 'YYYY-MM-DD'),
          'visits', coalesce(daily.visits, 0),
          'unique_visitors', coalesce(daily.unique_visitors, 0)
        ) order by days.day
      )
      from days left join daily using (day)
    ), '[]'::jsonb)
  );
$$;

revoke all on function public.zarinmah_admin_dashboard_stats() from public, anon, authenticated;
grant execute on function public.zarinmah_admin_dashboard_stats() to service_role;
