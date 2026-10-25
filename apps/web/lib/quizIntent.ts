import { MAX_QUIZ_QUESTIONS, type QuizQuestionType } from "./api";

/**
 * Is this chat message asking for a quiz? "quiz me on entropy", "give me 5
 * MCQs on the chain rule", "can you make a practice test?" open the quiz
 * panel with those settings instead of being answered in the chat. Pattern
 * matching only, no LLM call: a miss is just an ordinary answer, and the
 * patterns need a request verb so "I have 2 questions about this" is not one.
 */

export interface QuizIntent {
  count: number;
  types: QuizQuestionType[];
  /** The message itself: its topic and wording steer the quiz. */
  prompt: string;
}

const ALL_TYPES: QuizQuestionType[] = ["mcq", "numerical", "subjective"];

const WORD_NUMBERS: Record<string, number> = {
  a: 1, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  nine: 9, ten: 10, eleven: 11, twelve: 12, fifteen: 15, twenty: 20,
  couple: 2, few: 3,
};

const COUNT = String.raw`(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)`;
const NOUN = String.raw`(?:quiz(?:zes)?|mcqs?|multiple[- ]choice(?: questions?)?|mock tests?|practice (?:questions?|problems?|tests?|quiz)|tests?|numericals?|(?:${COUNT}|some|a few|a couple of|more|few)\s+(?:more\s+)?(?:[\w-]+\s+)?(?:questions?|problems?|exercises?))`;
const VERB = String.raw`(?:give|make|create|generate|write|set|prepare|build|want|send|take|start|need|try|throw|ask)`;

const PATTERNS: RegExp[] = [
  // "quiz me", "test me", "question me"
  /\b(?:quiz|test|question|examine)\s+me\b/i,
  // "give me 5 questions on…", "can you make a quiz", "I want to take a test"
  new RegExp(String.raw`\b${VERB}\b[^.?!\n]{0,30}?\b${NOUN}\b`, "i"),
  // a message that starts with the noun: "quiz on thermodynamics", "5 MCQs on…"
  new RegExp(String.raw`^\s*(?:a\s+)?${NOUN}\s+(?:on|about|from|for|in)\b`, "i"),
  // just "quiz" / "quiz please"
  /^\s*quiz(?:\s+(?:please|pls|time))?\s*[.!?]*\s*$/i,
];

export function detectQuizIntent(text: string): QuizIntent | null {
  const t = text.trim();
  if (!t || t.startsWith("/") || t.length > 400) return null;
  if (!PATTERNS.some((re) => re.test(t))) return null;
  return { count: quizCount(t), types: quizTypes(t), prompt: t };
}

function quizCount(t: string): number {
  const m = new RegExp(String.raw`\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|few|couple)\s+(?:of\s+)?(?:more\s+)?(?:[\w-]+\s+){0,2}?(?:questions?|problems?|mcqs?|numericals?|exercises?)\b`, "i").exec(t);
  const raw = m?.[1]?.toLowerCase();
  const n = raw ? (Number.isNaN(Number(raw)) ? WORD_NUMBERS[raw] : Number(raw)) : undefined;
  return Math.min(MAX_QUIZ_QUESTIONS, Math.max(1, n ?? 5));
}

function quizTypes(t: string): QuizQuestionType[] {
  const types: QuizQuestionType[] = [];
  if (/\b(?:mcqs?|multiple[- ]choice|objective)\b/i.test(t)) types.push("mcq");
  if (/\b(?:numericals?|calculations?|problems?|numeric)\b/i.test(t)) types.push("numerical");
  if (/\b(?:written|subjective|short[- ]answer|long[- ]answer|theory|theoretical|conceptual|descriptive)\b/i.test(t)) {
    types.push("subjective");
  }
  return types.length ? types : ALL_TYPES;
}
