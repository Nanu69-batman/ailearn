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

-- Images attached to one chat: only the vision transcript is kept (never the
-- image, never a resource). Read by the API to give later answers the context.
create table if not exists public.chat_attachments (
  id           text primary key,
  owner_id     uuid references auth.users(id) on delete cascade,
  session_id   text not null references public.chat_sessions(id) on delete cascade,
  thread_id    text not null,
  name         text not null default 'image',
  mime         text,
  description  text not null,
  created_at   timestamptz not null default now()
);
create index if not exists chat_attachments_chat_idx
  on public.chat_attachments (session_id, thread_id, created_at);

alter table public.chat_attachments enable row level security;

drop policy if exists chat_attachments_owner_all on public.chat_attachments;
create policy chat_attachments_owner_all on public.chat_attachments
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);
