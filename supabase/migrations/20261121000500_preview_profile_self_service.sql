-- Staging self-service profile completion using the authenticated user's JWT.
-- No service-role credential is required for a user to read or edit their own profile.
grant usage on schema public to authenticated;
grant select on table public.profiles, public.user_roles to authenticated;

grant insert (id, full_name, mobile, email, username, province, city, address, national_id)
  on table public.profiles to authenticated;
grant update (full_name, mobile, email, username, province, city, address, national_id, updated_at)
  on table public.profiles to authenticated;

create unique index if not exists profiles_username_lower_uidx
  on public.profiles (lower(username))
  where username is not null and btrim(username) <> '';

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname='public' and tablename='profiles'
      and policyname='Users can create own profile'
  ) then
    create policy "Users can create own profile"
      on public.profiles for insert to authenticated
      with check (auth.uid() = id);
  end if;

  if not exists (
    select 1 from pg_policies
    where schemaname='public' and tablename='profiles'
      and policyname='Users can update own profile'
  ) then
    create policy "Users can update own profile"
      on public.profiles for update to authenticated
      using (auth.uid() = id)
      with check (auth.uid() = id);
  end if;
end
$$;
