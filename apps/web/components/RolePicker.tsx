"use client";

import { ROLES, type Role } from "@/lib/role";

/** Three radio cards: Individual / Teacher / Student. */
export function RolePicker({
  value,
  onChange,
}: {
  value: Role;
  onChange: (role: Role) => void;
}) {
  return (
    <div role="radiogroup" className="grid gap-2">
      {ROLES.map((r) => {
        const active = r.id === value;
        return (
          <button
            key={r.id}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(r.id)}
            className={`flex items-start gap-3 rounded-xl border px-3.5 py-2.5 text-left transition focus:outline-none focus-visible:ring-2 focus-visible:ring-violet-300 ${
              active
                ? "border-violet-400 bg-violet-50"
                : "border-gray-200 bg-white hover:border-violet-200"
            }`}
          >
            <span
              className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                active ? "border-violet-500" : "border-gray-300"
              }`}
            >
              {active && <span className="h-2 w-2 rounded-full bg-violet-500" />}
            </span>
            <span>
              <span className="block text-sm font-semibold text-gray-900">{r.label}</span>
              <span className="block text-xs font-normal text-gray-500">{r.blurb}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
