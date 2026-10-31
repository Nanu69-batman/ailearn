-- Saved quizzes (Malai: Resources > Quizzes, or a chat's menu when it has no
-- subject). Questions and attempts are kept as JSON, as the web client holds
-- them. Times are epoch milliseconds.
create table if not exists public.saved_quizzes (
  id          text not null,
  owner_id    uuid not null references auth.users(id) on delete cascade,
  subject_id  text,
  chat_id     text,
  title       text not null default '',
  questions   jsonb not null default '[]'::jsonb,
  attempts    jsonb not null default '[]'::jsonb,
  created_ms  bigint not null,
  primary key (owner_id, id)
);
create index if not exists saved_quizzes_owner_created_idx
  on public.saved_quizzes (owner_id, created_ms desc);

alter table public.saved_quizzes enable row level security;

drop policy if exists saved_quizzes_owner_all on public.saved_quizzes;
create policy saved_quizzes_owner_all on public.saved_quizzes
  for all to authenticated
  using (auth.uid() = owner_id)
  with check (auth.uid() = owner_id);

grant select, insert, update, delete on public.saved_quizzes to authenticated;
grant all on public.saved_quizzes to service_role;
