-- ONE-OFF CLEANUP: deletes EVERY chat for EVERY user. Not a migration; run it
-- by hand in the SQL Editor only when you mean to wipe chat history.
-- Keeps users, subjects, resources, files, progress and quiz attempts.
-- Run after 0002 + 0003. Chat image files in Storage (media/*/chat/...) are
-- removed separately: Storage > media > each user folder > chat.

begin;

-- Counts first, so you can see what is about to go.
select 'chat_messages' as table_name, count(*) from public.chat_messages
union all select 'chat_threads', count(*) from public.chat_threads
union all select 'chat_sessions', count(*) from public.chat_sessions
union all select 'malai_chats', count(*) from public.malai_chats
union all select 'chat_attachments', count(*) from public.chat_attachments;

delete from public.chat_attachments;
delete from public.malai_chats;
delete from public.chat_messages;
delete from public.chat_threads;
delete from public.chat_sessions;

commit;
