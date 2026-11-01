# Changelog (since 5769644)

36 commits, `5769644..21d8cc4`. The rest of `documentation/` describes the state at `5769644`; this file lists what has changed since then.

## Accounts and auth
- **Session token removed entirely.** Google or email sign-in is the only way in. Every session route checks that the signed-in user owns the session (`6e3cf40`, `e70cf5d`).
- **Settings saved to the account.** BYOK keys and model settings live in the `user_settings` table. New Settings and Profile pages, and the sidebar account chip opens the profile (`86b2b31`).
- **Settings split into tabs:** Models, API keys, Personalization, Account (`fcf143e`).
- **Shared-browser fix:** chats and the user's name no longer carry over between accounts (`ce9118c`).

## Models and BYOK
- **Model modes:** Auto, Fast, Balanced and Powerful run over OpenRouter, falling back across models. Bring-your-own key is supported (`4d86d57`).
- **Own key for any provider** under Advanced, with that key's models to pick from. The four modes stay on free models (`acb4d1d`).
- **Multiple BYOK keys.** Models can be switched on or off in Settings, and there is a free-only filter (`023a627`).
- **Model chooser** groups models by key, and the Models tab lists each key's models (`6196796`). Dropdowns open upward or flip to fit the window instead of stretching the page (`32bd463`, `21d8cc4`).
- **Intent labelling:** a small Gemini model labels messages the intent regex can't place. It is used for chat only at high confidence; on timeout or error it falls back to the grounded path (`b77a717`).

## Agentic chat (subject chats)
- **The agent decides when to search.** It fixes typos before searching, searches materials it can't read directly, and asks the learner for access or permission to use general knowledge (`c94ba7e`).
- **Searches every material in one call.** The agent also has room to ask questions and runs tool calls written as text. General chats can search too (`af3c6da`).
- **Asks before answering outside its materials** (`adf7bd6`). A subject chat with no allowed materials still answers (`d500bc5`).
- **Named materials drive access and sources.** Tool calls are never sent as replies, and the chat answers in the learner's language without a hardcoded Hindi rule (`815c8b2`, `f4f9492`).
- **Citation handling:** chunk IDs cited in the model's own style are accepted. They are still validated against the retrieved set (`6f13155`).
- **Maths:** plain-text and Unicode maths from PDFs is rewritten as LaTeX (`34ba456`).
- **Assistant questions** ("what are your capabilities") are treated as smalltalk: no lesson search and no general-knowledge tag (`68c2b65`).
- **Thinking lines:** witty thinking-status lines while the model works (`023a627`).

## Malai workspace
- **Per-chat resource allowance** and a subject completion checklist (`eea0490`).
- **Access requests:** the chat asks for access when an answer needs a material it can't read (`cc941c8`, `fe2506d`). For videos it asks "till you've watched" or "whole video" (`0189cdf`).
- **Video:** watch progress and subject analytics, with chapter segments on the seek bar (`eea0490`). Watch progress and chat allowances sync to the account (`cc941c8`). Videos marked done are readable, and chats with no player read granted videos (`555afe3`, `09cfc31`).
- **Chat and split view are one UI.** The chooser has play and close, citations open the split view, and a floating quiz button was added (`f4f9492`). The split view has no chat picker of its own; the sidebar manages chats (`a02c7b2`).
- **Subjects:** a New subject button in the subjects list and in "Move to subject" (`7ea8751`). Subject dialogs are portalled above the page (`05b5ba9`), and the Personalization modal is shorter and scrolls inside itself (`b8fc87d`).
- **Voice button** takes Send's place while the input is empty (`4d86d57`).

## Quizzes
- **Model choice:** the learner picks the model that writes a quiz (default fastest, free mode, or own key). A quiz requested in chat uses the chat's model (`e9ff239`). The quiz picker lists all enabled models (`6196796`).
- **Saved quizzes** appear under Resources > Quizzes. Each has its own page with analytics, re-attempt and past attempts, which can be viewed or deleted (`fd511bf`).
- **Account sync:** quizzes sync to Supabase and are graded as the signed-in user. Chats with no subject list their quizzes in the chat menu (`173f97b`).

## Developer tooling
- **Developer view:** run logs can be exported, and the button floats (`5ec41ea`). Dev logs show full prompts, replies and requests for each call (`09cfc31`).
- **Tests** never write to the live Supabase project. A script removes leaked test rows (`95092fd`).

## Unchanged invariants
- Strict timestamp gate (`end_ms <= T`).
- Citations validated against the retrieved set.
- Secrets only in `apps/api/.env`.
