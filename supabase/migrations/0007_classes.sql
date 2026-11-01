-- Teacher classes (apps/api/app/classes). The API is the only reader/writer
-- (service role); the whole class (members, subject ids) lives in `data`.
-- Account roles (individual | teacher | student) live in auth user_metadata.role.
create table if not exists public.classes (
  id          text primary key,
  teacher_id  uuid not null references auth.users(id) on delete cascade,
  join_code   text not null unique,
  data        jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
);

create index if not exists classes_teacher_idx on public.classes (teacher_id);

alter table public.classes enable row level security;
-- No policies for `authenticated`: browsers go through the API only.
grant all on public.classes to service_role;
