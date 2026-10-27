-- Remove rows the API test suite wrote into a live project.
--
-- Before tests/conftest.py disabled Supabase for tests, every run with
-- Supabase keys in apps/api/.env mirrored its fixtures (subjects such as
-- "Physics 101", "Mine", "Theirs", resources such as lecture.mp4, sessions for
-- course_1 / c1 / c) into the real database. This keeps only the demo subject
-- and real user data. Not a migration: run it by hand in the SQL Editor.
--
-- Afterwards, delete the leftover test files in Storage -> media -> public:
-- the folders named res_* (the demo resources are not stored there).
-- Leave public/chat alone. Then restart the API so it reloads subjects.

begin;

select 'subjects' as t, count(*) from public.subjects where id <> 'sub_06dea46b30bf4ee7'
union all select 'resources', count(*) from public.resources
  where course_id in ('course_1', 'course_real', 'course_elsewhere', 'c1', 'c')
union all select 'chat_sessions', count(*) from public.chat_sessions
  where course_id in ('course_1', 'course_real', 'course_elsewhere', 'c1', 'c');

delete from public.subjects where id <> 'sub_06dea46b30bf4ee7';
delete from public.resources
  where course_id in ('course_1', 'course_real', 'course_elsewhere', 'c1', 'c');
-- Threads, messages and attachments go with their session (on delete cascade).
delete from public.chat_sessions
  where course_id in ('course_1', 'course_real', 'course_elsewhere', 'c1', 'c');

commit;
