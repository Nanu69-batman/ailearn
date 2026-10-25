-- =============================================================================
-- 0001_init.sql  --  Malai Pioneer / AI Learning Assistant: initial Supabase schema
--
-- HOW TO APPLY
--   1. Open your Supabase project -> SQL Editor -> New query.
--   2. Paste this whole file and press Run.
--   3. Run it once. It is written to be safe to re-run: tables use IF NOT EXISTS,
--      functions use CREATE OR REPLACE, policies and triggers are dropped before
--      being recreated, and buckets are inserted with ON CONFLICT DO NOTHING.
--
-- Contract: docs/SUPABASE_PLAN.md. Vectors are NOT stored here (Chroma/Pinecone).
-- Every public table has RLS enabled. The API uses the service-role key, which
-- bypasses RLS; the policies below govern browser (anon / authenticated) access.
-- =============================================================================

create extension if not exists pgcrypto;  -- gen_random_uuid()

-- -----------------------------------------------------------------------------
-- Shared helper: keep updated_at current
-- -----------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- -----------------------------------------------------------------------------
-- Tables
-- -----------------------------------------------------------------------------

-- profiles: one row per auth user, created by the handle_new_user trigger
create table if not exists public.profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  display_name  text,
  avatar_url    text,
  created_at    timestamptz not null default now()
);

-- subjects: owner_id null = public demo subject
create table if not exists public.subjects (
  id                text primary key,
  owner_id          uuid references auth.users(id) on delete cascade,
  name              text not null,
  description       text not null default '',
  course_id         text,
  resource_ids      text[] not null default '{}',
  archived          boolean not null default false,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  last_accessed_at  timestamptz
);
-- Re-runs on a database created before the column existed add it in place.
alter table public.subjects add column if not exists description text not null default '';
create index if not exists subjects_owner_id_idx on public.subjects (owner_id);

-- resources: owner_id null = public demo resource
create table if not exists public.resources (
  id            text primary key,
  owner_id      uuid references auth.users(id) on delete cascade,
  course_id     text,
  subject_id    text,
  title         text,
  source_type   text check (source_type in ('video', 'pdf', 'pptx', 'image', 'youtube')),
  storage_path  text,
  mime          text,
  size_bytes    bigint,
  duration_ms   bigint,
  status        text,
  created_at    timestamptz not null default now()
);
create index if not exists resources_owner_id_idx   on public.resources (owner_id);
create index if not exists resources_subject_id_idx on public.resources (subject_id);
create index if not exists resources_course_id_idx  on public.resources (course_id);

-- chat_sessions: data holds the full serialized session row
create table if not exists public.chat_sessions (
  id              text primary key,
  owner_id        uuid references auth.users(id) on delete cascade,
  course_id       text,
  resource_id     text,
  subject_id      text,
  resource_scope  jsonb,
  data            jsonb not null default '{}'::jsonb,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists chat_sessions_owner_id_idx   on public.chat_sessions (owner_id);
create index if not exists chat_sessions_subject_id_idx on public.chat_sessions (subject_id);

-- chat_threads: pk (session_id, id), cascades with its session
create table if not exists public.chat_threads (
  id          text not null,
  session_id  text not null references public.chat_sessions(id) on delete cascade,
  owner_id    uuid references auth.users(id) on delete cascade,
  title       text,
  created_at  timestamptz not null default now(),
  primary key (session_id, id)
);
create index if not exists chat_threads_owner_id_idx on public.chat_threads (owner_id);

-- chat_messages
create table if not exists public.chat_messages (
  id          text primary key,
  session_id  text not null,
  thread_id   text,
  owner_id    uuid references auth.users(id) on delete cascade,
  role        text not null,
  content     text,
  citations   jsonb,
  data        jsonb,
  created_at  timestamptz not null default now()
);
create index if not exists chat_messages_session_id_idx on public.chat_messages (session_id, created_at);
create index if not exists chat_messages_owner_id_idx   on public.chat_messages (owner_id);

-- study_activity: seconds studied per user per day
create table if not exists public.study_activity (
  user_id  uuid not null references auth.users(id) on delete cascade,
  day      date not null,
  seconds  integer not null default 0 check (seconds between 0 and 86400),
  primary key (user_id, day)
);
-- the primary key (user_id, day) already serves user_id + day lookups

-- lesson_progress
create table if not exists public.lesson_progress (
  user_id           uuid not null references auth.users(id) on delete cascade,
  resource_id       text not null,
  subject_id        text,
  completed         boolean not null default false,
  last_position_ms  bigint not null default 0,
  title             text,
  last_opened_at    timestamptz not null default now(),
  primary key (user_id, resource_id)
);
create index if not exists lesson_progress_user_opened_idx on public.lesson_progress (user_id, last_opened_at desc);

-- quiz_attempts
create table if not exists public.quiz_attempts (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references auth.users(id) on delete cascade,
  resource_id  text,
  subject_id   text,
  score        numeric(6,1) not null,
  total        integer not null,
  created_at   timestamptz not null default now()
);
-- Written answers earn partial credit (e.g. 2.5 / 4). Re-runs on a database
-- created with the earlier integer column widen it in place.
alter table public.quiz_attempts alter column score type numeric(6,1);
create index if not exists quiz_attempts_user_created_idx on public.quiz_attempts (user_id, created_at desc);

-- -----------------------------------------------------------------------------
-- updated_at triggers
-- -----------------------------------------------------------------------------
drop trigger if exists subjects_set_updated_at on public.subjects;
create trigger subjects_set_updated_at
  before update on public.subjects
  for each row execute function public.set_updated_at();

drop trigger if exists chat_sessions_set_updated_at on public.chat_sessions;
create trigger chat_sessions_set_updated_at
  before update on public.chat_sessions
  for each row execute function public.set_updated_at();

-- -----------------------------------------------------------------------------
-- Row Level Security
-- -----------------------------------------------------------------------------
alter table public.profiles        enable row level security;
alter table public.subjects        enable row level security;
alter table public.resources       enable row level security;
alter table public.chat_sessions   enable row level security;
alter table public.chat_threads    enable row level security;
alter table public.chat_messages   enable row level security;
alter table public.study_activity  enable row level security;
alter table public.lesson_progress enable row level security;
alter table public.quiz_attempts   enable row level security;

-- profiles: owner only (id = auth.uid())
drop policy if exists profiles_owner_all on public.profiles;
create policy profiles_owner_all on public.profiles
  for all to authenticated
  using (auth.uid() = id)
  with check (auth.uid() = id);

-- subjects: owner read/write; public demo rows (owner_id null) readable by everyone
drop policy if exists subjects_owner_all on public.subjects;
create policy subjects_owner_all on public.subjects
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);

drop policy if exists subjects_public_read on public.subjects;
create policy subjects_public_read on public.subjects
  for select to anon, authenticated
  using (owner_id is null);

-- resources: same rule as subjects
drop policy if exists resources_owner_all on public.resources;
create policy resources_owner_all on public.resources
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);

drop policy if exists resources_public_read on public.resources;
create policy resources_public_read on public.resources
  for select to anon, authenticated
  using (owner_id is null);

-- chat tables: owner only
drop policy if exists chat_sessions_owner_all on public.chat_sessions;
create policy chat_sessions_owner_all on public.chat_sessions
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);

drop policy if exists chat_threads_owner_all on public.chat_threads;
create policy chat_threads_owner_all on public.chat_threads
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);

drop policy if exists chat_messages_owner_all on public.chat_messages;
create policy chat_messages_owner_all on public.chat_messages
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);

-- analytics tables: user only
drop policy if exists study_activity_owner_all on public.study_activity;
create policy study_activity_owner_all on public.study_activity
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists lesson_progress_owner_all on public.lesson_progress;
create policy lesson_progress_owner_all on public.lesson_progress
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists quiz_attempts_owner_all on public.quiz_attempts;
create policy quiz_attempts_owner_all on public.quiz_attempts
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- -----------------------------------------------------------------------------
-- Auto-create a profile on sign-up (email + password and Google OAuth)
--   Google puts full_name/name and avatar_url/picture in raw_user_meta_data;
--   email sign-ups may pass full_name via options.data, else fall back to the email prefix.
-- -----------------------------------------------------------------------------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
  meta jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
begin
  insert into public.profiles (id, display_name, avatar_url)
  values (
    new.id,
    coalesce(
      nullif(meta ->> 'full_name', ''),
      nullif(meta ->> 'name', ''),
      nullif(split_part(coalesce(new.email, ''), '@', 1), '')
    ),
    coalesce(
      nullif(meta ->> 'avatar_url', ''),
      nullif(meta ->> 'picture', '')
    )
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Backfill profiles for users who signed up before this migration ran
insert into public.profiles (id, display_name, avatar_url)
select
  u.id,
  coalesce(
    nullif(u.raw_user_meta_data ->> 'full_name', ''),
    nullif(u.raw_user_meta_data ->> 'name', ''),
    nullif(split_part(coalesce(u.email, ''), '@', 1), '')
  ),
  coalesce(
    nullif(u.raw_user_meta_data ->> 'avatar_url', ''),
    nullif(u.raw_user_meta_data ->> 'picture', '')
  )
from auth.users u
on conflict (id) do nothing;

-- -----------------------------------------------------------------------------
-- RPC: add_study_seconds(p_day, p_seconds)
--   Atomic upsert-increment for the caller's row; the daily total is clamped to 0..86400.
--   Returns the new total for that day.
-- -----------------------------------------------------------------------------
create or replace function public.add_study_seconds(p_day date, p_seconds int)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  uid   uuid := auth.uid();
  delta integer;
  total integer;
begin
  if uid is null then
    raise exception 'add_study_seconds: not authenticated' using errcode = '28000';
  end if;
  if p_day is null then
    raise exception 'add_study_seconds: p_day is required' using errcode = '22004';
  end if;

  delta := greatest(0, least(86400, coalesce(p_seconds, 0)));

  insert into public.study_activity as sa (user_id, day, seconds)
  values (uid, p_day, delta)
  on conflict (user_id, day)
  do update set seconds = least(86400, greatest(0, sa.seconds + excluded.seconds))
  returning sa.seconds into total;

  return total;
end;
$$;

-- -----------------------------------------------------------------------------
-- RPC: get_streak()
--   current_streak: length of the run of consecutive days with seconds > 0 that ends
--                   today or yesterday (current_date); 0 otherwise.
--   longest_streak: longest such run ever.
--   total_days:     number of days with seconds > 0.
--   total_seconds:  sum of all seconds.
-- -----------------------------------------------------------------------------
create or replace function public.get_streak()
returns table (current_streak int, longest_streak int, total_days int, total_seconds bigint)
language plpgsql
stable
security definer
set search_path = public
as $$
declare
  uid uuid := auth.uid();
begin
  if uid is null then
    raise exception 'get_streak: not authenticated' using errcode = '28000';
  end if;

  return query
  with active as (
    select sa.day, sa.seconds
    from public.study_activity sa
    where sa.user_id = uid and sa.seconds > 0
  ),
  grouped as (
    -- gaps-and-islands: consecutive days share the same anchor date
    select a.day, a.day - (row_number() over (order by a.day))::int as anchor
    from active a
  ),
  runs as (
    select max(g.day) as run_end, count(*)::int as run_len
    from grouped g
    group by g.anchor
  )
  select
    coalesce((select r.run_len from runs r
              where r.run_end >= current_date - 1
              order by r.run_end desc
              limit 1), 0)::int,
    coalesce((select max(r.run_len) from runs r), 0)::int,
    (select count(*) from active)::int,
    coalesce((select sum(a.seconds) from active a), 0)::bigint;
end;
$$;

revoke all on function public.add_study_seconds(date, int) from public, anon;
revoke all on function public.get_streak() from public, anon;
grant execute on function public.add_study_seconds(date, int) to authenticated;
grant execute on function public.get_streak() to authenticated;

-- -----------------------------------------------------------------------------
-- Storage buckets
--   media   (private): {owner_id}/{resource_id}/{filename}
--   avatars (public):  {user_id}/{filename}
-- -----------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('media', 'media', false)
on conflict (id) do nothing;

insert into storage.buckets (id, name, public)
values ('avatars', 'avatars', true)
on conflict (id) do nothing;

-- media: read and write only under the caller's own {auth.uid()}/ prefix
drop policy if exists media_owner_select on storage.objects;
create policy media_owner_select on storage.objects
  for select to authenticated
  using (bucket_id = 'media' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists media_owner_insert on storage.objects;
create policy media_owner_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'media' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists media_owner_update on storage.objects;
create policy media_owner_update on storage.objects
  for update to authenticated
  using (bucket_id = 'media' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'media' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists media_owner_delete on storage.objects;
create policy media_owner_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'media' and (storage.foldername(name))[1] = auth.uid()::text);

-- avatars: anyone can read; writes only under the caller's own {auth.uid()}/ prefix
drop policy if exists avatars_public_select on storage.objects;
create policy avatars_public_select on storage.objects
  for select to anon, authenticated
  using (bucket_id = 'avatars');

drop policy if exists avatars_owner_insert on storage.objects;
create policy avatars_owner_insert on storage.objects
  for insert to authenticated
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists avatars_owner_update on storage.objects;
create policy avatars_owner_update on storage.objects
  for update to authenticated
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists avatars_owner_delete on storage.objects;
create policy avatars_owner_delete on storage.objects
  for delete to authenticated
  using (bucket_id = 'avatars' and (storage.foldername(name))[1] = auth.uid()::text);

-- End of 0001_init.sql
