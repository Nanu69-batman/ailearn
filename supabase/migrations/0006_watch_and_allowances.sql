-- Learning progress that follows the account:
--
-- 1. How far each video has been watched (lesson_progress.watched_ms): the
--    furthest point reached by playing on from the start, plus the video's
--    length so progress can be shown as a share.
-- 2. Each Malai chat's resource allowance (chat_allowances): what that chat
--    may read, stored as its changes on top of the subject's completed
--    resources (allow_add / allow_remove), so completing a resource for the
--    subject still reaches every chat.
--
-- Safe to re-run. Run after 0001-0005 in the Supabase SQL Editor.

alter table public.lesson_progress add column if not exists watched_ms  bigint not null default 0;
alter table public.lesson_progress add column if not exists duration_ms bigint not null default 0;

create table if not exists public.chat_allowances (
  user_id       uuid not null references auth.users(id) on delete cascade,
  chat_id       text not null,
  subject_id    text not null,
  allow_add     text[] not null default '{}',
  allow_remove  text[] not null default '{}',
  updated_at    timestamptz not null default now(),
  primary key (user_id, chat_id)
);

alter table public.chat_allowances enable row level security;

drop policy if exists chat_allowances_owner_all on public.chat_allowances;
create policy chat_allowances_owner_all on public.chat_allowances
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

grant select, insert, update, delete on public.chat_allowances to authenticated;
grant all on public.chat_allowances to service_role;
