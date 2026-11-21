-- Public, RLS-constrained read access required by the storefront.
-- Writes to market_history_state remain restricted to service_role.
grant select on table public.listings to anon, authenticated;
grant select on table public.zarinmah_banners to anon, authenticated;
grant select on table public.market_history_state to anon, authenticated;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname='public'
      and tablename='market_history_state'
      and policyname='Public can read daily market history'
  ) then
    create policy "Public can read daily market history"
      on public.market_history_state
      for select
      to anon, authenticated
      using (true);
  end if;
end
$$;

-- Seed one verified prior trading-day snapshot so daily comparisons are not empty.
-- Source: ZarinMah gold bot daily snapshot committed on 2026-11-19.
insert into public.market_history_state (id, state, updated_at)
values (
  'daily',
  jsonb_build_object(
    'days', jsonb_build_object(
      '2026-11-19', jsonb_build_object(
        'prices', jsonb_build_object(
          'gold18', 24443600,
          'emami', 250565000,
          'half', 132260000,
          'dollar', 266970
        ),
        'updatedAt', '2026-11-19T16:30:01.680046+03:30'
      )
    ),
    'latest', null
  ),
  now()
)
on conflict (id) do update
set state = jsonb_set(
  coalesce(public.market_history_state.state, '{}'::jsonb),
  '{days,2026-11-19}',
  excluded.state->'days'->'2026-11-19',
  true
),
updated_at = now();
