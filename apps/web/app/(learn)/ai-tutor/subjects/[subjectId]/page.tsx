"use client";

import { useCallback } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import YouTubeIngestForm, {
  type IngestedVideo,
} from "@/components/YouTubeIngestForm";
import { useLearnSession } from "@/components/LearnSessionProvider";
import { useCourseResources } from "@/components/useCourseResources";
import { isApiConfigured, getSubjectResources } from "@/lib/api";
import {
  SUBJECTS_PATH,
  contentPath,
  firstParam,
  resourceIcon,
  sampleClipSelected,
  toSelected,
} from "@/lib/learn";

/**
 * /ai-tutor/subjects/[subjectId] — everything in one subject, as a list.
 *
 * Selecting a resource is a `router.push` to the content route, never a state
 * flip. That is deliberate: a real navigation leaves a history entry, so the
 * browser back button returns here, and the content URL is shareable.
 *
 * `subjectId` is whatever the backend put in the URL — an opaque id, never a
 * literal baked into this file.
 */
export default function SubjectPage() {
  const params = useParams<{ subjectId?: string | string[] }>();
  const router = useRouter();
  const subjectId = firstParam(params.subjectId);

  const { session, offline } = useLearnSession();
  const apiLive = isApiConfigured() && !offline;
  const enabled = Boolean(session) && apiLive && subjectId !== null;
  // Subject-scoped, not course-scoped: `subjectId` is not a course id, so
  // `/courses/{subjectId}/resources` matched nothing and every attached
  // material rendered as "no videos". Falling back to `subject.course_id`
  // would be worse — two subjects on one course would show identical lists.
  const { resources, state: resourcesState, error: resourcesError } =
    useCourseResources({
      courseId: subjectId ?? "",
      enabled,
      load: getSubjectResources,
    });

  const open = useCallback(
    (resourceId: string): void => {
      if (!subjectId) return;
      router.push(contentPath(subjectId, resourceId));
    },
    [router, subjectId],
  );

  /** A freshly ingested video is already a resource — open its real page. */
  const handleIngested = useCallback(
    (video: IngestedVideo): void => {
      open(video.resourceId);
    },
    [open],
  );

  const openSampleClip = useCallback((): void => {
    // Sanity: the synthetic selection must be reachable at its own URL, so the
    // sample clip navigates exactly like any other resource.
    open(sampleClipSelected().resourceId);
  }, [open]);

  if (subjectId === null) {
    return (
      <div
        className="rounded-xl border border-amber-200 bg-amber-50 p-6 shadow-sm"
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

  return (
    <div className="flex flex-col gap-6">
      <YouTubeIngestForm
        courseId={subjectId}
        subjectId={subjectId}
        onReady={handleIngested}
        disabled={!apiLive}
      />
      {!apiLive && (
        <p className="text-sm text-amber-600" role="note">
          YouTube ingest needs the live API — offline mode can still open the
          sample clip below.
        </p>
      )}

      <section aria-label="Subject videos">
        <h2 className="mb-4 text-lg font-semibold text-gray-900">
          Subject videos
        </h2>
        {resourcesState === "loading" && (
          <div
            className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm"
            role="status"
          >
            Loading subject videos…
          </div>
        )}
        {resourcesState === "error" && (
          <div
            className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600 shadow-sm"
            role="alert"
          >
            Could not load subject videos: {resourcesError}
          </div>
        )}
        {resourcesState === "ready" && resources.length === 0 && (
          <div
            className="rounded-xl border border-gray-200 bg-white p-4 text-sm text-gray-500 shadow-sm"
            role="status"
          >
            No videos in this subject yet — paste a YouTube link above to add
            the first one.
          </div>
        )}
        {resources.length > 0 && (
          <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {resources.map((r) => {
              const s = toSelected(r);
              if (!s) return null;
              return (
                <li
                  key={r.id}
                  className="group flex flex-col gap-3 rounded-xl border border-gray-200 bg-white p-4 shadow-sm transition hover:border-violet-300 hover:shadow"
                >
                  <div className="flex items-center gap-3">
                    <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-violet-50 text-xl">
                      {resourceIcon(r)}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-semibold text-gray-900">
                        {s.title}
                      </div>
                      <div className="text-xs text-gray-400">
                        {r.provider === "youtube" ? "YouTube" : "File"}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center justify-between">
                    {r.status ? (
                      <span className={statusStyle(r.status)}>{r.status}</span>
                    ) : (
                      <span />
                    )}
                    <button
                      type="button"
                      className="rounded-lg bg-violet-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-violet-700"
                      onClick={() => open(r.id)}
                    >
                      {r.type === "pdf" ? "Study" : "Watch"}
                    </button>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        <div className="mt-6 flex flex-col gap-3">
          <button
            type="button"
            className="self-start rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm text-gray-600 shadow-sm transition hover:bg-gray-50"
            onClick={openSampleClip}
          >
            ▶ Play sample clip (not course content)
          </button>
          <p className="text-xs text-gray-400">
            The sample clip is not part of any subject. It exists so the player
            has something real to render before a video has been ingested.
          </p>
          {resources.some((r) => !toSelected(r)) && (
            <p className="text-sm text-gray-400" role="note">
              Some ingested files can no longer be played because their original
              file is missing — re-ingest them from the course folder to restore
              playback.
            </p>
          )}
        </div>
      </section>
    </div>
  );
}

/** Row badge, matching the sources panel palette. */
function statusStyle(status: string): string {
  if (status === "done")
    return "rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-600";
  if (status === "failed")
    return "rounded-full border border-red-200 bg-red-50 px-2.5 py-0.5 text-xs font-medium text-red-600";
  return "rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-600";
}