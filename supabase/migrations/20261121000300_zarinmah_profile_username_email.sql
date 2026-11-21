-- ZarinMah account profile extensions.
-- Additive migration: preserves existing members and makes usernames unique case-insensitively.
alter table public.profiles
  add column if not exists username text,
  add column if not exists email text,
  add column if not exists address text,
  add column if not exists national_id text,
  add column if not exists province text,
  add column if not exists city text;

create unique index if not exists profiles_username_lower_uidx
  on public.profiles (lower(username))
  where username is not null and btrim(username) <> '';

comment on column public.profiles.username is 'Unique public account username chosen by the member.';
comment on column public.profiles.email is 'Optional contact email stored in the member profile.';
