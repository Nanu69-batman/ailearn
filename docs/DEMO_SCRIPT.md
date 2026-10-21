# Demo video script: Malai Pioneer

The target length is **about 3 minutes**. Each scene lists what to show on screen and what to say. Lines in *italics* are directions, not narration.

The scenes follow the BuildVerse evaluation criteria in this order: core UX, grounding, timestamp awareness, architecture, production readiness, testing.

---

## Before you record

- **Warm up Render.** Open https://malai-pioneer-api.onrender.com/health about 2 minutes before recording, because the free tier sleeps.
- **Prepare the demo subject.** The calculus lecture should already be uploaded and fully processed. Don't wait on ingestion while recording.
- **Clean up the screen.** Use a clean browser window at 1080p and zoom to 110% if the text looks small. Close any tabs that show keys or the Render dashboard.
- **Keep a few questions in a notepad** so you can paste them:
  - `hi, i am an engineering student`
  - `what is a partial derivative?`
  - `what questions can prof ask me from this content`
  - `what is on the screen right now?`
  - A question about something that comes **later** in the video, such as the chain rule.
- **Keep the answer slow-paths in reserve.** OpenRouter's free tier allows about 50 requests a day. Do one dry run, then record. If an answer is slow, cut the wait in editing.

---

## Scene 1: Hook (0:00–0:15)

*Show the dashboard with the subject cards.*

> "Lecture videos are long, and when you get stuck at minute 12 you have two options: scrub around, or ask a chatbot that has never seen your lecture.
> This is Malai Pioneer, an AI tutor that watches the lecture *with* you, and only answers from what you've actually watched."

## Scene 2: Subjects and content (0:15–0:40)

*Click **New subject**, type "Calculus II", and create it. Then open the existing demo subject. Click **Add content** to show the modal with its YouTube and File tabs, then close it.*

> "Students organise their learning into subjects. Adding content is one dialog. Paste a YouTube link or upload a video, PDF or slides.
> Behind the scenes we transcribe the audio, describe what's on screen every few seconds, chunk everything with timestamps, and index it in Pinecone."

*Optional: delete the "Calculus II" subject you just made, to show delete.*

## Scene 3: The watch page and custom player (0:40–0:55)

*Open the lecture. Play it for a few seconds. Point out the custom controls and the "Malai can see up to mm:ss" chip.*

> "Here's the watch page. It has our own player, and this chip shows exactly how much of the lecture Malai is allowed to know about. That's the playhead, and it moves as you watch."

## Scene 4: Grounded answers with citations (0:55–1:25)

*Pause at about 3–4 minutes. Ask `what is a partial derivative?`. When the answer arrives, show the **From the lesson** label and the citations. **Click a citation** so the video seeks to that moment.*

> "I'll ask a real question. The answer comes back grounded in the lecture, and every claim carries a citation.
> Clicking a citation jumps the video straight to the moment it was taught.
> Citations aren't decoration. The server checks every cited passage against what was actually retrieved, and if the model cites something it didn't see, the answer is refused rather than shown."

## Scene 5: Timestamp awareness, the key feature (1:25–1:55)

*Still paused early, ask about a topic that appears **later** in the video.*

> "Now something the lecturer covers *later*. Malai won't spoil it or make it up, because it hasn't been taught yet.
> The rule is strict: a passage only counts if it *ended* before your playhead. When we tested a looser rule that only checked when a passage started, 48% of answers leaked content from the future."

*Seek past that topic, let the chip update, and ask again.*

> "Once I've watched it, the same question gets a cited answer."

## Scene 6: It behaves like a tutor (1:55–2:20)

*Ask `hi, i am an engineering student`. You should get a friendly reply with no citations.
Then ask `what questions can prof ask me from this content`. You should get exam-style questions with answers and citations.
Then ask `what is on the screen right now?` with the frame visible.*

> "It's also a tutor, not a search box. Small talk gets a normal reply, and nothing gets invented from the lecture.
> Ask what a professor might test you on, and it writes exam questions from what you've covered so far, still cited.
> And it can look at the current frame, so you can ask about the equation on the board."

## Scene 7: Fullscreen study mode (2:20–2:35)

*Click fullscreen, then press **c** or click **Ask Malai** so the chat slides in. Ask one quick question. Hide the chat and exit.*

> "Going fullscreen doesn't mean losing your tutor. The chat slides in right next to the lecture."

## Scene 8: Under the hood (2:35–2:55)

*Show the architecture diagram from the README or `docs/ARCHITECTURE.md`. Then briefly flash the test run or the `docs/` folder.*

> "Under the hood: Next.js on Vercel, FastAPI on Render, Pinecone for vectors, and Gemini for embeddings and vision. Groq Whisper does the transcription.
> The tutor is an agent with two tools, *search lesson* and *get lesson content*. Both are time-gated, so even the agent can't look ahead. If the agent fails, it falls back to our classic retrieval pipeline.
> Nearly 480 automated tests cover the timestamp gate, citation validation and refusals. The API, the architecture and the evaluation are all documented in the repo."

## Scene 9: Close (2:55–3:05)

*Return to the watch page, or show the dashboard with the live URL in the address bar.*

> "Malai Pioneer: a tutor that watches with you, cites everything, and never skips ahead. It's live at ailearn-theta.vercel.app. Thanks for watching."

---

## Cut list if you're over time

1. Drop the optional delete in Scene 2.
2. Shorten Scene 8 to the diagram plus one sentence.
3. Merge Scene 7 into Scene 6 by asking the quiz question in fullscreen.

## Recording tips

- Record the screen and the voice separately if you can, then re-record any line you fumble.
- Speed up the waits for answers to 2–4× in editing, and add a small "sped up" label so it stays honest.
- Keep the mouse still while you talk. Move it only to click.
