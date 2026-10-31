-- Per-user app settings: the chat model mode, the learner's own provider key
-- and model (BYOK), and the quiz model. One row per user; only its owner can
-- read or write it (RLS). Kept out of user_metadata on purpose: that is copied
-- into every access token, and the key must not ride along on every request.
create table if not exists public.user_settings (
  owner_id     uuid primary key references auth.users(id) on delete cascade,
  model        jsonb not null default '{}'::jsonb,
  quiz_model   text not null default 'default',
  updated_at   timestamptz not null default now()
);

alter table public.user_settings enable row level security;

drop policy if exists user_settings_owner_all on public.user_settings;
create policy user_settings_owner_all on public.user_settings
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);

grant select, insert, update, delete on public.user_settings to authenticated;
grant all on public.user_settings to service_role;

-- Profiles are edited from the profile page: make sure the owner can upsert.
grant select, insert, update on public.profiles to authenticated;
