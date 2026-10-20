"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { createSubject, formatApiError, type Subject } from "@/lib/api";
import SubjectModal from "@/components/SubjectModal";

export interface SubjectCreateDialogProps {
  open: boolean;
  onClose: () => void;
  onCreated: (subject: Subject) => void;
}

const NAME_MAX = 120;
const DESCRIPTION_MAX = 500;

export default function SubjectCreateDialog({
  open,
  onClose,
  onCreated,
}: SubjectCreateDialogProps) {
  const nameId = useId();
  const descId = useId();
  const errorId = useId();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fresh form every time the dialog opens.
  useEffect(() => {
    if (open) {
      setName("");
      setDescription("");
      setError(null);
      setSaving(false);
    }
  }, [open]);

  const trimmedName = name.trim();

  async function onSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!trimmedName || saving) return;
    setSaving(true);
    setError(null);
    try {
      const desc = description.trim();
      const created = await createSubject({
        name: trimmedName,
        ...(desc ? { description: desc } : {}),
      });
      onCreated(created);
    } catch (err) {
      setError(formatApiError(err));
      setSaving(false);
    }
  }

  return (
    <SubjectModal
      open={open}
      onClose={onClose}
      busy={saving}
      title="Create a new subject"
      description="Group related videos and files so Malai can tutor you on them together."
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
          <path d="M4 19.5A2.5 2.5 0 016.5 17H20V4H6.5A2.5 2.5 0 004 6.5v13zM12 7.5v6M9 10.5h6" />
        </svg>
      }
    >
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-4" noValidate>
        <div className="flex flex-col gap-1.5">
          <label htmlFor={nameId} className="text-sm font-medium text-gray-700">
            Name <span className="text-red-500" aria-hidden="true">*</span>
          </label>
          <input
            id={nameId}
            data-autofocus
            type="text"
            required
            aria-required="true"
            maxLength={NAME_MAX}
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Linear Algebra"
            autoComplete="off"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            className="w-full rounded-xl border border-gray-300 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder-gray-400 shadow-sm outline-none transition focus:border-violet-500 focus:ring-4 focus:ring-violet-100"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between">
            <label htmlFor={descId} className="text-sm font-medium text-gray-700">
              Description <span className="font-normal text-gray-400">(optional)</span>
            </label>
            <span className="text-xs tabular-nums text-gray-400" aria-hidden="true">
              {description.length}/{DESCRIPTION_MAX}
            </span>
          </div>
          <textarea
            id={descId}
            rows={3}
            maxLength={DESCRIPTION_MAX}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="What will you study here?"
            className="w-full resize-none rounded-xl border border-gray-300 bg-white px-3.5 py-2.5 text-sm text-gray-900 placeholder-gray-400 shadow-sm outline-none transition focus:border-violet-500 focus:ring-4 focus:ring-violet-100"
          />
        </div>

        {error && (
          <p
            id={errorId}
            role="alert"
            className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              className="mt-0.5 h-4 w-4 shrink-0"
              aria-hidden="true"
            >
              <path d="M12 8v5M12 16.5v.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z" />
            </svg>
            <span>{error}</span>
          </p>
        )}

        <div className="mt-1 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 shadow-sm transition hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!trimmedName || saving}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-violet-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-violet-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving && (
              <span
                className="h-4 w-4 animate-spin rounded-full border-2 border-white/60 border-t-transparent"
                aria-hidden="true"
              />
            )}
            {saving ? "Creating…" : "Create subject"}
          </button>
        </div>
      </form>
    </SubjectModal>
  );
}
