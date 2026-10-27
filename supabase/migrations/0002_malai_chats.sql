-- The Malai chat list (sidebar index): which chats a user has, their titles,
-- and the session + thread each one lives in. Messages stay in chat_messages.
-- Times are epoch milliseconds, as the web client keeps them.
create table if not exists public.malai_chats (
  id           text not null,
  owner_id     uuid not null references auth.users(id) on delete cascade,
  session_id   text not null,
  thread_id    text not null,
  title        text not null default '',
  subject_id   text,
  resource_id  text,
  preview      text not null default '',
  created_ms   bigint not null,
  updated_ms   bigint not null,
  primary key (owner_id, id)
);
create index if not exists malai_chats_owner_updated_idx
  on public.malai_chats (owner_id, updated_ms desc);

alter table public.malai_chats enable row level security;

drop policy if exists malai_chats_owner_all on public.malai_chats;
create policy malai_chats_owner_all on public.malai_chats
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);
