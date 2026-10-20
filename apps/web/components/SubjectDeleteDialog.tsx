"use client";

import { useEffect, useState } from "react";
import { deleteSubject, formatApiError, type Subject } from "@/lib/api";
import SubjectModal from "@/components/SubjectModal";

export interface SubjectDeleteDialogProps {
  /** The subject to delete; null keeps the dialog closed. */
  subject: Subject | null;
  onClose: () => void;
  onDeleted: (subjectId: string) => void;
}

export default function SubjectDeleteDialog({
  subject,
  onClose,
  onDeleted,
}: SubjectDeleteDialogProps) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = subject !== null;

  useEffect(() => {
    if (open) {
      setDeleting(false);
      setError(null);
    }
  }, [open]);

  async function onConfirm(): Promise<void> {
    if (!subject || deleting) return;
    setDeleting(true);
    setError(null);
    try {
      await deleteSubject(subject.id);
      onDeleted(subject.id);
    } catch (err) {
      setError(formatApiError(err));
      setDeleting(false);
    }
  }

  const count = subject?.resource_count ?? 0;

  return (
    <SubjectModal
      open={open}
      onClose={onClose}
      busy={deleting}
      tone="red"
      title="Delete this subject?"
      description={
        <>
          <span className="font-medium text-gray-800">{subject?.name}</span>{" "}
          will be removed from your dashboard.
        </>
      }
      icon={
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-5 w-5"
        >
          <path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6M10 11v6M14 11v6" />
        </svg>
      }
    >
      <div className="flex items-start gap-2.5 rounded-xl border border-gray-200 bg-gray-50 px-3.5 py-3 text-sm text-gray-600">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="mt-0.5 h-4 w-4 shrink-0 text-violet-600"
          aria-hidden="true"
        >
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
        <p>
          Only the subject grouping is removed. Your videos and files
          {count > 0 ? ` (${count === 1 ? "1 resource" : `${count} resources`})` : ""}{" "}
          stay indexed and are not deleted.
        </p>
      </div>

      {error && (
        <p
          role="alert"
          className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700"
        >
          {error}
        </p>
      )}

      <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button
          type="button"
          data-autofocus
          onClick={onClose}
          disabled={deleting}
          className="rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 disabled:opacity-50"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void onConfirm()}
          disabled={deleting}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {deleting && (
            <span
              className="h-4 w-4 animate-spin rounded-full border-2 border-white/60 border-t-transparent"
              aria-hidden="true"
            />
          )}
          {deleting ? "Deleting…" : "Delete subject"}
        </button>
      </div>
    </SubjectModal>
  );
}
