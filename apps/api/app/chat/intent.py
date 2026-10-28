"""Conversational intent: what KIND of message the learner sent, before retrieval.

Pure phrase matching -- no LLM call, no network, no state -- in the same style
as :mod:`app.retrieval.recency`. Three intents are recognised; everything else
is ``None`` and takes the ordinary grounded path unchanged.

* ``smalltalk`` -- the WHOLE message is a greeting, a self-introduction,
  thanks / acknowledgement, or a question about the assistant itself ("who are
  you"). Such a message has no course answer, so it must never be sent through
  retrieval (that produced a cited "answer" to "hi") or through the
  general-knowledge fallback (that produced an invented Chain Rule lesson for
  "who are you"). Patterns are anchored on the full message, so "hi, what is a
  partial derivative?" is NOT smalltalk.
* ``study_questions`` -- the learner wants exam-style questions about what they
  have covered ("what questions can the prof ask me"). Routed to ``quiz``.
* ``overview`` -- the learner wants a recap of what they have covered ("what is
  this lesson about", "what have we covered"). Routed to ``summarize``.

Both routed modes are in ``SUPPLEMENTAL_DISABLED_MODES``, so a study or
overview request can never turn into an uncited general-knowledge answer, and
both still go through the strict-T gate and citation validation.
"""

from __future__ import annotations

import re

SMALLTALK = "smalltalk"
STUDY_QUESTIONS = "study_questions"
OVERVIEW = "overview"

_GREETING = (
    r"(?:hi+|hey+|hello+|hii+|heya|yo|hola|namaste|good (?:morning|afternoon|evening|night)"
    r"|greetings|sup|what'?s up)"
)
_ADDRESS = r"(?:\s+(?:there|malai|sir|ma'?am|bhai|bro|buddy|tutor|everyone|all))?"
_SELF_INTRO = (
    r"(?:i am|i'?m|im|this is|my name is|myself)\s+[^?]{1,80}"
)
_PUNCT = r"[\s,.!:;~)(\-]*"
#: Interjections that may open a question about the assistant.
_LEADIN = (
    r"(?:(?:really|wow|oh|ok(?:ay)?|so|and|but|cool|nice|hmm+|great|then|well|"
    r"interesting)[\s,.!?:;~\-]*)*"
)

#: Greetings and self-introductions. A self-introduction is open-ended text
#: ("i am an engineering student"), so these only count as smalltalk when the
#: message carries no learning signal -- see :data:`_LEARNING_SIGNAL_RE`.
INTRO_PATTERNS: tuple[str, ...] = (
    # "hi", "hello there!", "hi, i am an engineering student", "hey, i'm Riya"
    rf"^{_GREETING}{_ADDRESS}{_PUNCT}(?:{_SELF_INTRO}{_PUNCT})?$",
    # A bare self-introduction with no question in it.
    rf"^{_SELF_INTRO}{_PUNCT}$",
)

#: "i'm confused about partial derivatives" is a self-introduction in form and
#: a subject question in substance; it must take the grounded path.
_LEARNING_SIGNAL_RE = re.compile(
    r"\b(?:confus\w*|understand\w*|explain\w*|doubt\w*|stuck|struggl\w*|lost|"
    r"don'?t get|not getting|what|how|why|when|where|which|mean\w*|"
    r"derivative\w*|integral\w*|equation\w*|formula\w*|function\w*|"
    r"learn\w*|revis\w*|prepar\w*|exam\w*)\b",
    re.IGNORECASE,
)

SMALLTALK_PATTERNS: tuple[str, ...] = (
    # Thanks and acknowledgements.
    (
        r"^(?:ok(?:ay)?|k|cool|nice|great|awesome|got it|understood|makes sense|"
        r"perfect|alright|sure|thanks?|thank you|thank u|thx|ty|tysm|"
        r"thanks a lot|thank you so much)(?:\s+(?:so much|a lot|malai|sir|bro|"
        rf"thanks?|thank you))*{_PUNCT}$"
    ),
    r"^(?:bye|goodbye|see you|see ya|good night|gn)" + _ADDRESS + _PUNCT + r"$",
    # Questions about the assistant itself, optionally after an interjection
    # ("really?? what are your capabilities", "ok so what can you do").
    rf"^{_LEADIN}(?:who|what) (?:are|r) (?:you|u)(?: exactly| really)?{_PUNCT}\??$",
    rf"^{_LEADIN}(?:who|what) is (?:this|malai){_PUNCT}\??$",
    (
        rf"^{_LEADIN}(?:what|how) (?:else )?(?:can|do|could) (?:you|u) "
        rf"(?:do|help(?: me)?(?: with)?)(?: for me)?{_PUNCT}\??$"
    ),
    (
        rf"^{_LEADIN}(?:what (?:are|r) (?:your|ur) (?:capabilities|abilities|features|"
        r"skills|powers|limitations|limits)|what (?:are )?(?:you|u) (?:capable of|able to do|"
        r"good at)|tell me (?:about|more about) (?:yourself|you)|introduce yourself|"
        r"who (?:made|built|created|trained) (?:you|u)|what model are (?:you|u)(?: using)?|"
        rf"which model are (?:you|u)(?: using)?){_PUNCT}\??$"
    ),
    (
        rf"^{_LEADIN}(?:how can you help(?: me)?|help|help me|what should i ask(?: you)?|"
        rf"how does this work|how do i use (?:this|you)){_PUNCT}\??$"
    ),
    rf"^{_LEADIN}(?:are you (?:an? )?(?:ai|bot|robot|human|real|chatgpt|gemini)){_PUNCT}\??$",
    r"^(?:how are you|how r u|how's it going|how is it going)" + _PUNCT + r"\??$",
)

STUDY_QUESTION_PATTERNS: tuple[str, ...] = (
    r"\bwhat (?:kind of |type of |sort of )?questions? (?:can|could|would|will|might|may)\b",
    (
        r"\b(?:exam|test|viva|interview|practice|important|likely|possible|sample|"
        r"mcq|review|revision)[- ]?(?:style )?questions?\b"
    ),
    r"\bquiz me\b",
    r"\btest me\b",
    r"\b(?:ask|give) me (?:some |a few |\d+ )?questions?\b",
    r"\b(?:prof|professor|teacher|examiner|faculty|lecturer)\b.*\bask\b",
)

OVERVIEW_PATTERNS: tuple[str, ...] = (
    r"\bwhat (?:have|did) (?:we|i) (?:covered?|learn(?:ed|t)?|studied|done|seen)\b",
    r"\bwhat (?:is|'s) (?:this|the) (?:video|lesson|lecture|class|chapter|topic) about\b",
    r"\bwhat (?:is|are) (?:we|you) (?:covering|studying|learning|talking about)\b",
    (
        r"\b(?:recap|summari[sz]e|summary|overview)\b.*\b(?:so far|till now|until now|"
        r"up to now|this (?:video|lesson|lecture)|what i(?:'ve| have) watched)\b"
    ),
    r"^(?:recap|summary|summari[sz]e|give me (?:a )?(?:recap|summary|overview))"
    + _PUNCT
    + r"\??$",
    r"\bkey (?:points|takeaways|ideas|concepts) (?:so far|of this|from this)\b",
)

_INTRO_RE = re.compile("|".join(INTRO_PATTERNS), re.IGNORECASE)
_SMALLTALK_RE = re.compile("|".join(SMALLTALK_PATTERNS), re.IGNORECASE)
_STUDY_RE = re.compile("|".join(STUDY_QUESTION_PATTERNS), re.IGNORECASE)
_OVERVIEW_RE = re.compile("|".join(OVERVIEW_PATTERNS), re.IGNORECASE)

#: Intent -> chat mode it is routed to. Smalltalk has no mode: it never
#: reaches retrieval.
INTENT_MODES: dict[str, str] = {STUDY_QUESTIONS: "quiz", OVERVIEW: "summarize"}

#: Retrieval query used for study/overview intents in place of the literal
#: meta-question, which has no semantic overlap with the lesson itself.
COVERAGE_QUERY = "key concepts, definitions, formulas, methods and worked examples"


def classify_intent(text: str) -> str | None:
    """Return ``smalltalk``, ``study_questions``, ``overview`` or ``None``."""
    msg = (text or "").strip()
    if not msg:
        return None
    if _SMALLTALK_RE.match(msg):
        return SMALLTALK
    if _INTRO_RE.match(msg) and not _LEARNING_SIGNAL_RE.search(msg):
        return SMALLTALK
    if _STUDY_RE.search(msg):
        return STUDY_QUESTIONS
    if _OVERVIEW_RE.search(msg):
        return OVERVIEW
    return None


#: Words that carry no subject matter. A message made only of these (plus
#: words of three letters or fewer) is not a subject question, so it must not
#: trigger the general-knowledge fallback: there is no concept in it to teach,
#: and the model would invent one.
_NON_CONTENT_WORDS = frozenset(
    re.findall(
        r"[a-z]+",
        """
    about above after again also always another anything around because been
    before being below between both cannot could does doing done during each
    either else even ever every from give have having here hers herself himself
    into itself just know like make many maybe might mine more most much must
    myself never next once only other ours over please really same should since
    some something still such sure tell than that their theirs them then there
    these they thing things this those though through very want were what
    whatever when where which while whom whose will with within without would
    your yours yourself
    """,
    )
)


def has_subject_content(text: str) -> bool:
    """True when the message names at least one possible subject concept.

    Used only to GATE the general-knowledge fallback; it never affects grounded
    answers. A false positive keeps today's behaviour; a false negative turns a
    would-be supplemental answer into the ordinary refusal.
    """
    words = re.findall(r"[A-Za-z]+", (text or "").lower())
    return any(len(w) > 3 and w not in _NON_CONTENT_WORDS for w in words)


#: Nouns that name the learner's own course material.
_MATERIAL_NOUN = (
    r"(?:course\s+)?(?:materials?|notes|lectures?|videos?|pdfs?|slides?|"
    r"slide\s*deck|documents?|docs|resources?|uploads?|files?|lessons?|course(?!\s+of\b)|"
    r"syllabus|textbook|readings?|subjects?|transcripts?)"
)

#: An explicit pointer at that material: "my notes", "the uploaded pdf",
#: "check the lecture", "according to the slides", "in the video".
_MATERIAL_RE = re.compile(
    r"\b(?:my|our|the|this|these|those|uploaded|attached)\s+(?:\w+\s+)?"
    + _MATERIAL_NOUN
    + r"\b",
    re.IGNORECASE,
)


def refers_to_materials(text: str) -> bool:
    """True when the learner explicitly asks about their own materials.

    Used by a chat that belongs to no subject: such a chat answers from general
    knowledge and only searches the learner's materials when asked to ("what
    do my notes say about X", "explain the second lecture"). A miss costs only
    a general answer; a hit runs the ordinary grounded pipeline.
    """
    return bool(_MATERIAL_RE.search(text or ""))
