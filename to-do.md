# To-do

## Lesson page: chapters and transcript

The lesson page's panel (Chapters / Transcript / Resources) is built, UI only.
Chapters and Transcript show "not ready yet" notes until these land.

- [ ] **Chapter processing (API).** Split each video into chapters
      (start, end, title) after ingestion, e.g. from the transcript with Gemini.
      Store them with the resource.
- [ ] **Chapters endpoint.** Expose them, e.g. `GET /resources/{id}/chapters`
      -> `{ chapters: [{ start_ms, end_ms, title }] }`, or as a field on the
      subject's resource list.
- [ ] **Wire the web panel.** `apps/web/components/LessonPanel.tsx` already
      takes `chapters: Chapter[]` (`startMs`, `endMs`, `title`); the content
      page passes `null` today (`TODO(chapters)` in
      `apps/web/app/(learn)/ai-tutor/subjects/[subjectId]/content/[resourceId]/page.tsx`).
      Clicking a chapter seeks; the current and watched ones are highlighted.
- [ ] **Stream-mode videos.** Decide what to show for a partly processed
      video: only the chapters processed so far.
- [ ] **Transcript tab.** A readable transcript that follows the playhead,
      with click-to-seek. Needs an endpoint returning the transcript segments.
