"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import YouTubeIngestForm, {
  type IngestedVideo,
} from "@/components/YouTubeIngestForm";
import FileIngestForm, {
  type IngestedFile,
} from "@/components/FileIngestForm";
import IngestDialog from "@/components/IngestDialog";
import ContentCard, { ContentPendingCard } from "@/components/ContentCard";
import ContentConfirmDialog from "@/components/ContentConfirmDialog";
import type { IngestProgress, IngestSource } from "@/components/IngestProgress";
import { useLearnSession } from "@/components/LearnSessionProvider";
import { useCourseResources } from "@/components/useCourseResources";
import {
  isApiConfigured,
  getSubjectResources,
  deleteResource,
  formatApiError,
  type CourseResource,
} from "@/lib/api";
import {
  DASHBOARD_PATH,
  SUBJECTS_PATH,
  contentPath,
  firstParam,
  sampleClipSelected,
  toSelected,
} from "@/lib/learn";

type Jobs = Record<IngestSource, IngestProgress | null>;

/**
 * /ai-tutor/subjects/[subjectId] — the subject's CONTENT, as a card grid.
 *
 * Adding content lives in a compact "Add content" dialog (YouTube link / file
 * upload). Both ingest forms stay mounted for the life of the page, so a job
 * keeps polling after the dialog closes; its progress is mirrored here as a
 * pending card that is replaced by the real resource when the list reloads.
 *
 * Opening a resource is a real navigation (a `Link` to the content route),
 * never a state flip, so the browser back button returns here.
 *
 * `subjectId` is whatever the backend put in the URL — an opaque id, never a
 * literal baked into this file.
 */
export default function SubjectPage() {
  const params = useParams<{ subjectId?: string | string[] }>();
  const router = useRouter();
  const subjectId = firstParam(params.subjectId);

  const { session, offline, subject, refreshResources } = useLearnSession();
  const apiLive = isApiConfigured() && !offline;
  const enabled = Boolean(session) && apiLive && subjectId !== null;
  // Subject-scoped, not course-scoped: `subjectId` is not a course id, so
  // `/courses/{subjectId}/resources` matched nothing and every attached
  // material rendered as "no videos". Falling back to `subject.course_id`
  // would be worse — two subjects on one course would show identical lists.
  const {
    resources,
    state: resourcesState,
    error: resourcesError,
    reload,
  } = useCourseResources({
      courseId: subjectId ?? "",
      enabled,
      load: getSubjectResources,
    });

  // ---- Add-content dialog + in-flight jobs -------------------------------
  const [dialogOpen, setDialogOpen] = useState(false);
  const [tab, setTab] = useState<IngestSource>("youtube");
  const [jobs, setJobs] = useState<Jobs>({ youtube: null, file: null });
  const [justAdded, setJustAdded] = useState<string | null>(null);

  const onYouTubeProgress = useCallback(
    (p: IngestProgress | null) => setJobs((j) => ({ ...j, youtube: p })),
    [],
  );
  const onFileProgress = useCallback(
    (p: IngestProgress | null) => setJobs((j) => ({ ...j, file: p })),
    [],
  );

  // When a job leaves the in-flight state (done, cancelled, dismissed),
  // re-read the list from the server so the pending card is replaced by the
  // real resource — we never claim it appeared before the server says so.
  const prevJobs = useRef<Jobs>(jobs);
  useEffect(() => {
    const prev = prevJobs.current;
    prevJobs.current = jobs;
    if ((prev.youtube && !jobs.youtube) || (prev.file && !jobs.file)) {
      reload();
    }
  }, [jobs, reload]);

  const openDialog = useCallback((which?: IngestSource) => {
    if (which) setTab(which);
    setDialogOpen(true);
  }, []);
  const closeDialog = useCallback(() => setDialogOpen(false), []);

  const markAdded = useCallback(
    (resourceId: string): void => {
      setJustAdded(resourceId);
      setDialogOpen(false);
      reload();
      // The chat grant is minted per resource set — let it re-mint.
      refreshResources();
    },
    [reload, refreshResources],
  );

  useEffect(() => {
    if (!justAdded) return;
    const id = window.setTimeout(() => setJustAdded(null), 6000);
    return () => window.clearTimeout(id);
  }, [justAdded]);

  const handleIngested = useCallback(
    (video: IngestedVideo): void => markAdded(video.resourceId),
    [markAdded],
  );
  /**
   * A completed file ingest is shown in the list, not navigated to: the
   * learner picks it from the list when they are ready to open it.
   */
  const handleFileIngested = useCallback(
    (file: IngestedFile): void => markAdded(file.resourceId),
    [markAdded],
  );

  // ---- Delete -------------------------------------------------------------
  // The delete route is course-scoped; the subject id is not a course id. The
  // real course id comes from the subject record the provider already loaded.
  const courseId = subject?.course_id ?? null;
  const [confirming, setConfirming] = useState<CourseResource | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const handleDelete = useCallback(
    async (resourceId: string): Promise<void> => {
      if (!courseId) return;
      setDeletingId(resourceId);
      setDeleteError(null);
      try {
        await deleteResource(courseId, resourceId);
        setConfirming(null);
        reload();
        // Drop the stored grant so the next chat re-mints without this id.
        refreshResources();
      } catch (err) {
        setDeleteError(formatApiError(err));
      } finally {
        setDeletingId(null);
      }
    },
    [courseId, reload, refreshResources],
  );
  const cancelDelete = useCallback(() => {
    setConfirming(null);
    setDeleteError(null);
  }, []);

  const openSampleClip = useCallback((): void => {
    if (!subjectId) return;
    router.push(contentPath(subjectId, sampleClipSelected().resourceId));
  }, [router, subjectId]);

  if (subjectId === null) {
    return (
      <div
        className="rounded-2xl border border-amber-200 bg-amber-50 p-6 shadow-sm"
        role="alert"
      >
        <p className="font-semibold text-amber-800">
          No subject was specified.
        </p>
        <p className="mt-1 text-sm text-gray-700">
          Open a subject from the{" "}
          <Link className="font-medium underline" href={SUBJECTS_PATH}>
            subject list
          </Link>
          .
        </p>
      </div>
    );
  }

  const pending = (["youtube", "file"] as const)
    .map((s) => jobs[s])
    .filter((p): p is IngestProgress => p !== null);
  const canDelete = apiLive && courseId !== null;
  const ready = resourcesState === "ready";
  const isEmpty = ready && resources.length === 0 && pending.length === 0;
  const countLabel = ready
    ? `${resources.length} ${resources.length === 1 ? "item" : "items"}`
    : null;

  return (
    <div className="flex flex-col gap-8">
      {/* ---- Header ---------------------------------------------------- */}
      <header className="flex flex-col gap-4">
        <Link
          href={DASHBOARD_PATH}
          className="inline-flex w-fit items-center gap-1.5 rounded-lg text-sm font-medium text-gray-500 transition hover:text-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300"
        >
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 18l-6-6 6-6" />
          </svg>
          Dashboard
        </Link>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold tracking-tight text-gray-900 sm:text-3xl">
              {subject?.name ?? "Subject"}
            </h1>
            {subject?.description ? (
              <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-gray-500">
                {subject.description}
              </p>
            ) : null}
            {countLabel && (
              <p className="mt-2 text-xs font-medium uppercase tracking-wide text-gray-400">
                {countLabel}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => openDialog()}
            disabled={!apiLive}
            title={apiLive ? undefined : "Adding content needs the live API"}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
            Add content
            {pending.some((p) => p.phase !== "error") && (
              <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent" aria-label="Processing" />
            )}
          </button>
        </div>
        {!apiLive && (
          <p className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-700" role="note">
            Adding content needs the live API — offline mode can still open the
            sample clip below.
          </p>
        )}
      </header>

      {/* ---- Content ---------------------------------------------------- */}
      <section aria-label="Subject content">
        {resourcesState === "loading" && resources.length === 0 && (
          <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="Loading subject content">
            {[0, 1, 2].map((i) => (
              <li key={i} className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
                <div className="aspect-video animate-pulse bg-gray-100" />
                <div className="space-y-2 p-4">
                  <div className="h-3.5 w-3/4 animate-pulse rounded bg-gray-100" />
                  <div className="h-3 w-1/3 animate-pulse rounded bg-gray-100" />
                </div>
              </li>
            ))}
          </ul>
        )}

        {resourcesState === "error" && (
          <div
            className="flex flex-wrap items-center gap-3 rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 shadow-sm"
            role="alert"
          >
            <span className="flex-1">Could not load subject content: {resourcesError}</span>
            <button
              type="button"
              onClick={reload}
              className="rounded-lg border border-red-200 bg-white px-3 py-1 text-xs font-medium text-red-600 hover:bg-red-50"
            >
              Retry
            </button>
          </div>
        )}

        {isEmpty && (
          <div className="flex flex-col items-center rounded-2xl border-2 border-dashed border-gray-200 bg-white px-6 py-14 text-center" role="status">
            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-violet-50 text-violet-600" aria-hidden="true">
              <svg viewBox="0 0 24 24" className="h-7 w-7" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                <rect x="2.5" y="5" width="19" height="14" rx="3" />
                <path d="m10 9.5 5 2.5-5 2.5z" fill="currentColor" />
              </svg>
            </span>
            <h2 className="mt-4 text-base font-semibold text-gray-900">
              No content yet
            </h2>
            <p className="mt-1 max-w-sm text-sm text-gray-500">
              No videos in this subject yet — add a YouTube link or upload a
              file, and Malai will make it askable.
            </p>
            <div className="mt-5 flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={() => openDialog("youtube")}
                disabled={!apiLive}
                className="rounded-xl bg-violet-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700 disabled:opacity-50"
              >
                Add a YouTube link
              </button>
              <button
                type="button"
                onClick={() => openDialog("file")}
                disabled={!apiLive}
                className="rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 disabled:opacity-50"
              >
                Upload a file
              </button>
            </div>
          </div>
        )}

        {(resources.length > 0 || pending.length > 0) && (
          <ul className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {pending.map((p) => (
              <ContentPendingCard
                key={`pending-${p.source}`}
                progress={p}
                onOpen={() => openDialog(p.source)}
              />
            ))}
            {resources.map((r) => {
              const s = toSelected(r);
              return (
                <ContentCard
                  key={r.id}
                  resource={r}
                  title={s?.title ?? r.title ?? r.id}
                  href={s ? contentPath(subjectId, r.id) : null}
                  highlight={justAdded === r.id}
                  deleting={deletingId === r.id}
                  onDelete={
                    canDelete
                      ? () => {
                          setDeleteError(null);
                          setConfirming(r);
                        }
                      : undefined
                  }
                />
              );
            })}
          </ul>
        )}

        {resources.some((r) => !toSelected(r)) && (
          <p className="mt-4 text-sm text-gray-400" role="note">
            Some ingested files can no longer be played because their original
            file is missing — re-ingest them to restore playback.
          </p>
        )}

        <div className="mt-8 flex flex-wrap items-center gap-3 border-t border-gray-100 pt-5">
          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-500 shadow-sm transition hover:bg-gray-50 hover:text-gray-700"
            onClick={openSampleClip}
          >
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5" fill="currentColor" aria-hidden="true">
              <path d="M8 5v14l11-7z" />
            </svg>
            Play sample clip
          </button>
          <span className="text-xs text-gray-400">
            Not course content — lets the player render before anything is
            ingested.
          </span>
        </div>
      </section>

      {/* ---- Dialogs ---------------------------------------------------- */}
      <IngestDialog
        open={dialogOpen}
        onClose={closeDialog}
        tab={tab}
        onTabChange={setTab}
        busy={{
          youtube: jobs.youtube !== null && jobs.youtube.phase !== "error",
          file: jobs.file !== null && jobs.file.phase !== "error",
        }}
        youtube={
          <YouTubeIngestForm
            courseId={subjectId}
            subjectId={subjectId}
            onReady={handleIngested}
            onProgress={onYouTubeProgress}
            disabled={!apiLive}
            embedded
          />
        }
        file={
          <FileIngestForm
            courseId={subjectId}
            subjectId={subjectId}
            onReady={handleFileIngested}
            onProgress={onFileProgress}
            disabled={!apiLive}
            embedded
          />
        }
      />

      <ContentConfirmDialog
        open={confirming !== null}
        title="Delete this content?"
        confirmLabel="Delete"
        busyLabel="Deleting…"
        busy={deletingId !== null}
        error={deleteError ? `Could not delete: ${deleteError}` : null}
        onCancel={cancelDelete}
        onConfirm={() => {
          if (confirming) void handleDelete(confirming.id);
        }}
      >
        <span className="font-medium text-gray-700">
          “{confirming ? (confirming.title ?? confirming.id) : ""}”
        </span>{" "}
        will be removed from this subject and its index. This can’t be undone.
      </ContentConfirmDialog>
    </div>
  );
}
