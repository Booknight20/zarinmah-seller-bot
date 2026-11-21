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
