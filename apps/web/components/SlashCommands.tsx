"use client";

import { useEffect, useState, type KeyboardEvent } from "react";
import type { ChatMode, QuizQuestionType } from "../lib/api";

/**
 * Slash commands, in every chat. `/analogy …` asks in that style, `/quiz …`
 * opens a quiz. Typing `/` lists the commands; after one is picked, its
 * options are listed. Picking fills the input; Enter sends.
 *
 * What a command can reach is the chat's, never more: the wording of its
 * options follows the chat's context (a video, a document, a subject, or no
 * subject at all), and the request goes out with that chat's own scope.
 */

/** What the chat can read: the open video or document, a subject, or nothing. */
export type SlashContext = "video" | "document" | "subject" | "general";

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
  /** Opens a quiz instead of asking. */
  quiz?: boolean;
  /** Starts the question: "/analogy from sports" asks "Give me an analogy from sports". */
  lead: string;
  /** The question when nothing follows the command. */
  fallback: string;
  options: SlashOption[];
}

const ALL_TYPES: QuizQuestionType[] = ["mcq", "numerical", "subjective"];

const QUIZ_OPTIONS: SlashOption[] = [
  { text: "5 mixed questions", count: 5, types: ALL_TYPES },
  { text: "3 multiple-choice questions", count: 3, types: ["mcq"] },
  { text: "5 numerical problems", count: 5, types: ["numerical"] },
  { text: "10 mixed questions", count: 10, types: ALL_TYPES },
];

/** Per context: [fallback, options] for each command that asks something. */
const WORDING: Record<SlashContext, Record<"explain" | "analogy" | "summarize" | "compare", [string, string[]]>> = {
  video: {
    explain: ["Explain what I've watched so far simply", ["the last idea simply", "this step by step", "the formula on screen"]],
    analogy: ["Give me an analogy for the last concept", ["for the last concept", "from everyday life", "from sports or games"]],
    summarize: ["Summarize what I've watched so far", ["what I've watched so far", "the last few minutes", "just the key formulas"]],
    compare: ["Compare the last two ideas", ["the last two ideas", "this method with the previous one"]],
  },
  document: {
    explain: ["Explain the key idea of this material simply", ["the key idea simply", "this step by step", "the main formula"]],
    analogy: ["Give me an analogy for the key concept here", ["for the key concept here", "from everyday life", "from sports or games"]],
    summarize: ["Summarize this material", ["this material", "just the key formulas", "the main definitions"]],
    compare: ["Compare the two main ideas in this material", ["the two main ideas in this material", "the methods it describes"]],
  },
  subject: {
    explain: ["Explain the key ideas of this subject simply", ["the key ideas of this subject simply", "a topic from my materials step by step"]],
    analogy: ["Give me an analogy for a key concept in this subject", ["for a key concept in this subject", "from everyday life", "from sports or games"]],
    summarize: ["Summarize this subject's materials", ["this subject's materials", "just the key formulas", "the main definitions"]],
    compare: ["Compare the main methods in this subject", ["the main methods in this subject", "the last two ideas"]],
  },
  general: {
    explain: ["Explain the last idea simply", ["the last idea simply", "this step by step"]],
    analogy: ["Give me an analogy for the last concept", ["for the last concept", "from everyday life", "from sports or games"]],
    summarize: ["Summarize this conversation", ["this conversation", "the key points so far"]],
    compare: ["Compare the last two ideas", ["the last two ideas"]],
  },
};

const QUIZ_HINT: Record<SlashContext, string> = {
  video: "Practice questions on this lesson",
  document: "Practice questions on this material",
  subject: "Practice questions on this subject",
  general: "Practice questions on a topic",
};

/** The commands, worded for what this chat can read. */
export function slashCommands(context: SlashContext): SlashCommand[] {
  const w = WORDING[context];
  const ask = (name: keyof typeof w, lead: string, hint: string, mode: ChatMode): SlashCommand => ({
    name,
    lead,
    hint,
    mode,
    fallback: w[name][0],
    options: w[name][1].map((text) => ({ text })),
  });
  return [
    ask("explain", "Explain", "Explain it simply", "explain_simple"),
    ask("analogy", "Give me an analogy", "Explain with an analogy", "analogy"),
    ask("summarize", "Summarize", context === "general" ? "Recap the conversation" : "Recap what you've covered", "summarize"),
    ask("compare", "Compare", "Two ideas side by side", "compare"),
    { name: "quiz", lead: "", hint: QUIZ_HINT[context], quiz: true, fallback: "", options: QUIZ_OPTIONS },
  ];
}

/** The question a command asks, as sent and as shown in the chat. */
export function commandQuestion(command: SlashCommand, rest: string): string {
  return rest ? `${command.lead} ${rest}` : command.fallback;
}

/** `/name rest` -> the command and what follows it; plain text -> null. */
export function parseCommand(
  text: string,
  context: SlashContext = "video",
): { command: SlashCommand; rest: string } | null {
  const m = /^\/(\S+)(?:\s+([\s\S]*))?$/.exec(text.trim());
  if (!m) return null;
  const command = slashCommands(context).find((c) => c.name === m[1].toLowerCase());
  return command ? { command, rest: (m[2] ?? "").trim() } : null;
}

/**
 * A `/quiz …` command's settings: a listed option sets the size and types;
 * any other text is the quiz's instructions ("/quiz on entropy, harder").
 */
export function quizFromCommand(command: SlashCommand, rest: string): {
  count: number;
  types: QuizQuestionType[];
  prompt: string;
} {
  const option = command.options.find((o) => o.text.toLowerCase() === rest.toLowerCase());
  return {
    count: option?.count ?? 5,
    types: option?.types ?? ALL_TYPES,
    prompt: option ? "" : rest,
  };
}

export type SlashMenu =
  | { kind: "commands"; items: SlashCommand[] }
  | { kind: "options"; command: SlashCommand; items: SlashOption[] };

/** What the menu shows for the current input, or null for no menu. */
export function slashMenu(input: string, context: SlashContext = "video"): SlashMenu | null {
  if (!input.startsWith("/")) return null;
  const commands = slashCommands(context);
  const typing = /^\/(\S*)$/.exec(input);
  if (typing) {
    const items = commands.filter((c) => c.name.startsWith(typing[1].toLowerCase()));
    return items.length ? { kind: "commands", items } : null;
  }
  const parsed = /^\/(\S+)\s+([\s\S]*)$/.exec(input);
  const command = parsed && commands.find((c) => c.name === parsed[1].toLowerCase());
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

/**
 * The menu's state for one input box: which row is lit, Esc to hide it, and
 * the keys that move through it. `onKeyDown` returns true when it used the key.
 */
export function useSlashMenu(input: string, setInput: (v: string) => void, context: SlashContext) {
  const [index, setIndex] = useState(0);
  const [dismissed, setDismissed] = useState(false);
  useEffect(() => {
    setIndex(0);
    setDismissed(false);
  }, [input]);
  const menu = dismissed ? null : slashMenu(input, context);
  const active = menu ? Math.min(index, menu.items.length - 1) : 0;
  const pick = (i: number): void => {
    if (menu) setInput(slashFill(menu, i));
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): boolean => {
    if (!menu) return false;
    const n = menu.items.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => (i + (e.key === "ArrowDown" ? 1 : n - 1)) % n);
      return true;
    }
    if ((e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) || e.key === "Tab") {
      e.preventDefault();
      pick(active);
      return true;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setDismissed(true);
      return true;
    }
    return false;
  };
  return { menu, active, pick, setActive: setIndex, onKeyDown };
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
