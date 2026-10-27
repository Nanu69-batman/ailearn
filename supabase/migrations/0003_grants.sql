-- Table privileges. Without these every PostgREST call fails with
-- "42501 permission denied for table ..." even with the secret key: this
-- project does not grant new public tables to the API roles automatically,
-- so 0001/0002 created tables nobody could read or write. RLS (on every
-- table, see 0001) still decides which rows each signed-in user sees; the
-- service role bypasses RLS and is used only by the API server.

grant usage on schema public to anon, authenticated, service_role;

grant all on all tables in schema public to service_role;
grant all on all sequences in schema public to service_role;
grant execute on all functions in schema public to service_role;

-- The browser reads and writes its own rows (study streak, lesson progress,
-- quiz attempts) and reads public subjects; RLS limits it to those rows.
grant select, insert, update, delete on all tables in schema public to authenticated;
grant usage, select on all sequences in schema public to authenticated;
grant execute on all functions in schema public to authenticated;
grant select on public.subjects, public.resources to anon;

-- Tables added later get the same privileges.
alter default privileges in schema public grant all on tables to service_role;
alter default privileges in schema public grant all on sequences to service_role;
alter default privileges in schema public grant execute on functions to service_role;
alter default privileges in schema public
  grant select, insert, update, delete on tables to authenticated;

-- Chat images are kept in Storage (bucket "media", under {owner}/chat/...).
alter table public.chat_attachments add column if not exists storage_path text;
