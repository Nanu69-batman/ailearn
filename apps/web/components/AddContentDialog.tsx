"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import YouTubeIngestForm from "./YouTubeIngestForm";
import FileIngestForm from "./FileIngestForm";
import IngestDialog from "./IngestDialog";
import type { IngestProgress, IngestSource } from "./IngestProgress";
import { isApiConfigured } from "@/lib/api";
import { notifySubjectsChanged } from "@/lib/subjectEvents";

type Jobs = Record<IngestSource, IngestProgress | null>;

interface AddContentDialogProps {
  /** The subject the new material is filed under; null renders nothing. */
  subjectId: string | null;
  open: boolean;
  onClose: () => void;
  /** Whether a job is in flight, for a spinner on the button that opened it. */
  onBusyChange?: (busy: boolean) => void;
}

/**
 * The subject page's "Add content" dialog (YouTube link / file upload, with
 * ingestion) as one piece. Keep it mounted while the page lives: the forms
 * poll their job after the dialog closes. A finished ingest tells every
 * subject list to reload, so new chats read the new material.
 */
export default function AddContentDialog({ subjectId, open, onClose, onBusyChange }: AddContentDialogProps) {
  const apiLive = isApiConfigured();
  const [tab, setTab] = useState<IngestSource>("youtube");
  const [jobs, setJobs] = useState<Jobs>({ youtube: null, file: null });

  const onYouTubeProgress = useCallback((p: IngestProgress | null) => setJobs((j) => ({ ...j, youtube: p })), []);
  const onFileProgress = useCallback((p: IngestProgress | null) => setJobs((j) => ({ ...j, file: p })), []);

  const busy = {
    youtube: jobs.youtube !== null && jobs.youtube.phase !== "error",
    file: jobs.file !== null && jobs.file.phase !== "error",
  };
  const anyBusy = busy.youtube || busy.file;
  const busyRef = useRef(onBusyChange);
  busyRef.current = onBusyChange;
  useEffect(() => busyRef.current?.(anyBusy), [anyBusy]);

  const added = useCallback((): void => {
    onClose();
    if (subjectId) notifySubjectsChanged(subjectId);
  }, [onClose, subjectId]);

  // Portalled: the sidebar can sit in a transformed drawer, which traps `fixed`.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  if (!subjectId || !mounted) return null;
  return createPortal(
    <IngestDialog
      open={open}
      onClose={onClose}
      tab={tab}
      onTabChange={setTab}
      busy={busy}
      youtube={
        <YouTubeIngestForm
          courseId={subjectId}
          subjectId={subjectId}
          onReady={added}
          onProgress={onYouTubeProgress}
          disabled={!apiLive}
          embedded
        />
      }
      file={
        <FileIngestForm
          courseId={subjectId}
          subjectId={subjectId}
          onReady={added}
          onProgress={onFileProgress}
          disabled={!apiLive}
          embedded
        />
      }
    />,
    document.body,
  );
}
