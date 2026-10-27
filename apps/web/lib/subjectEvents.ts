/**
 * "A subject's materials changed" (an ingest finished), so every subject list
 * and resource list on the page reloads instead of showing a stale set.
 */
const EVENT = "ailearn:subjects-changed";

export function notifySubjectsChanged(subjectId: string): void {
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: subjectId }));
}

export function onSubjectsChanged(listener: (subjectId: string) => void): () => void {
  const handler = (e: Event) => listener((e as CustomEvent<string>).detail);
  window.addEventListener(EVENT, handler);
  return () => window.removeEventListener(EVENT, handler);
}
