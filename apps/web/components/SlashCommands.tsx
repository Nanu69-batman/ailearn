"use client";

import type { ChatMode, QuizQuestionType } from "../lib/api";

/**
 * Slash commands for the lesson chat. They replace the old answer-style
 * chips: `/analogy …` asks in that style, `/quiz …` opens a quiz. Typing `/`
 * lists the commands; after one is picked, its options are listed. Picking
 * fills the input; Enter sends.
 */

export interface SlashOption {
  text: string;
  /** /quiz only: how many questions, and of which types. */
  count?: number;
  types?: QuizQuestionType[];
}

export interface SlashCommand {
  name: string;
  hint: string;
  /** The answer style sent with the question. Absent for /quiz. */
  mode?: ChatMode;
  /** Opens the Quiz tab instead of asking. */
  quiz?: boolean;
  /** Starts the question: "/analogy from sports" asks "Give me an analogy from sports". */
  lead: string;
  /** The question when nothing follows the command. */
  fallback: string;
  options: SlashOption[];
}

const ALL_TYPES: QuizQuestionType[] = ["mcq", "numerical", "subjective"];

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "explain",
    lead: "Explain",
    hint: "Explain it simply",
    mode: "explain_simple",
    fallback: "Explain what I've watched so far simply",
    options: [
      { text: "the last idea simply" },
      { text: "this step by step" },
      { text: "the formula on screen" },
    ],
  },
  {
    name: "analogy",
    lead: "Give me an analogy",
    hint: "Explain with an analogy",
    mode: "analogy",
    fallback: "Give me an analogy for the last concept",
    options: [
      { text: "for the last concept" },
      { text: "from everyday life" },
      { text: "from sports or games" },
    ],
  },
  {
    name: "summarize",
    lead: "Summarize",
    hint: "Recap what you've learned so far",
    mode: "summarize",
    fallback: "Summarize what I've watched so far",
    options: [
      { text: "what I've watched so far" },
      { text: "the last few minutes" },
      { text: "just the key formulas" },
    ],
  },
  {
    name: "compare",
    lead: "Compare",
    hint: "Two ideas side by side",
    mode: "compare",
    fallback: "Compare the last two ideas",
    options: [
      { text: "the last two ideas" },
      { text: "this method with the previous one" },
    ],
  },
  {
    name: "quiz",
    lead: "",
    hint: "Practice questions on this lesson",
    quiz: true,
    fallback: "",
    options: [
      { text: "5 mixed questions", count: 5, types: ALL_TYPES },
      { text: "3 multiple-choice questions", count: 3, types: ["mcq"] },
      { text: "5 numerical problems", count: 5, types: ["numerical"] },
      { text: "10 mixed questions", count: 10, types: ALL_TYPES },
    ],
  },
];

/** The question a command asks, as sent and as shown in the chat. */
export function commandQuestion(command: SlashCommand, rest: string): string {
  return rest ? `${command.lead} ${rest}` : command.fallback;
}

/** `/name rest` -> the command and what follows it; plain text -> null. */
export function parseCommand(text: string): { command: SlashCommand; rest: string } | null {
  const m = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!m) return null;
  const command = SLASH_COMMANDS.find((c) => c.name === m[1].toLowerCase());
  return command ? { command, rest: (m[2] ?? "").trim() } : null;
}

export type SlashMenu =
  | { kind: "commands"; items: SlashCommand[] }
  | { kind: "options"; command: SlashCommand; items: SlashOption[] };

/** What the menu shows for the current input, or null for no menu. */
export function slashMenu(input: string): SlashMenu | null {
  if (!input.startsWith("/")) return null;
  const typing = /^\/(\S*)$/.exec(input);
  if (typing) {
    const items = SLASH_COMMANDS.filter((c) => c.name.startsWith(typing[1].toLowerCase()));
    return items.length ? { kind: "commands", items } : null;
  }
  const parsed = /^\/(\S+)\s+([\s\S]*)$/.exec(input);
  const command = parsed && SLASH_COMMANDS.find((c) => c.name === parsed[1].toLowerCase());
  if (!parsed || !command) return null;
  const rest = parsed[2].trim().toLowerCase();
  // Once an option is written out in full, the menu steps aside.
  if (command.options.some((o) => o.text.toLowerCase() === rest)) return null;
  const items = command.options.filter((o) => o.text.toLowerCase().includes(rest));
  return items.length ? { kind: "options", command, items } : null;
}

/** The text an item writes into the input. */
export function slashFill(menu: SlashMenu, index: number): string {
  if (menu.kind === "commands") return `/${menu.items[index].name} `;
  return `/${menu.command.name} ${menu.items[index].text}`;
}

/** The menu above the input bar. Mouse-down keeps focus in the textarea. */
export function SlashMenuList({
  menu,
  active,
  onPick,
  onHover,
}: {
  menu: SlashMenu;
  active: number;
  onPick: (index: number) => void;
  onHover: (index: number) => void;
}) {
  return (
    <div
      id="slash-menu"
      role="listbox"
      aria-label={menu.kind === "commands" ? "Commands" : `/${menu.command.name} options`}
      className="absolute bottom-full left-0 right-0 z-20 mb-2 overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-xl shadow-gray-900/10"
    >
      <p className="border-b border-gray-100 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-gray-400">
        {menu.kind === "commands" ? "Commands" : `/${menu.command.name} · ${menu.command.hint}`}
      </p>
      <ul className="max-h-60 overflow-y-auto p-1.5">
        {menu.items.map((item, i) => (
          <li key={"name" in item ? item.name : item.text}>
            <button
              type="button"
              role="option"
              id={`slash-opt-${i}`}
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => onHover(i)}
              onClick={() => onPick(i)}
              className={`flex w-full items-baseline gap-3 rounded-xl px-3 py-2 text-left text-sm transition ${
                i === active ? "bg-violet-50 text-violet-900" : "text-gray-700"
              }`}
            >
              {"name" in item ? (
                <>
                  <span className="w-24 shrink-0 font-semibold text-violet-700">/{item.name}</span>
                  <span className="min-w-0 flex-1 truncate text-gray-500">{item.hint}</span>
                </>
              ) : (
                <span className="min-w-0 flex-1 truncate">{item.text}</span>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
