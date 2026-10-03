"""Synthetic fixture course for offline evaluation (Phase 6 eval assets)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.ingestion.models import Chunk, text_hash

FIXTURE_COURSE_ID = "course_fixture"
RES_VIDEO = "res_video"
RES_PDF = "res_pdf"
RES_PPT = "res_ppt"
VIDEO_ID = "vid_bioenergetics"
ALLOWED_RESOURCES = [RES_VIDEO, RES_PDF, RES_PPT]

LECTURE_MS = 3_600_000  # synthetic 60-minute lecture

# (start_ms, end_ms, transcript text) — 12 x 5-minute segments.
VIDEO_SEGMENTS: list[tuple[int, int, str]] = [
    (
        0,
        300_000,
        ("Welcome to Bioenergetics 101 with Doctor Maya Rao. Bioenergetics is the "
        "study of energy flow in living cells, from sunlight to ATP. In this course "
        "we trace how a giant sequoia tree builds tons of sugar from thin air, and "
        "how your muscles cash out that sugar during a sprint. Our roadmap covers "
        "photosynthesis, cellular respiration, and a hands-on Elodea pondweed lab. "
        "By the end you will balance the photosynthesis equation and explain "
        "chemiosmosis in plain words."),
    ),
    (
        300_000,
        600_000,
        ("Photosynthesis happens inside the chloroplast, a double-membrane organelle. "
        "Stacks of thylakoid membranes called grana trap light, while the surrounding "
        "fluid stroma hosts the carbon-fixing enzymes. Two pigments do the harvesting: "
        "chlorophyll a at the reaction center and accessory chlorophyll b that widens "
        "the net. Remember the layout with the mnemonic great green grana stacks hold "
        "chlorophyll."),
    ),
    (
        600_000,
        900_000,
        ("The light-dependent reactions run on the thylakoid membrane. At photosystem "
        "two, the P680 pigment pair absorbs red light and splits water by photolysis, "
        "releasing oxygen gas as a byproduct. Freed electrons travel down an electron "
        "transport chain while protons pile up inside the thylakoid lumen. The gradient "
        "spins ATP synthase to mint ATP, and NADP plus grabs electrons to form NADPH."),
    ),
    (
        900_000,
        1_200_000,
        ("With sunlight stored as ATP and NADPH, the Calvin cycle fixes carbon in the "
        "stroma. The enzyme RuBisCO grabs carbon dioxide onto RuBP, a five-carbon "
        "sugar, splitting it into two molecules of 3-PGA. Energy from ATP and NADPH "
        "reshapes them into G3P, and most G3P is recycled to regenerate RuBP. Six turns "
        "of the cycle export one glucose."),
    ),
    (
        1_200_000,
        1_500_000,
        ("Hot climates punish ordinary C3 plants with photorespiration, so variants "
        "evolved. C4 plants like maize pre-fix carbon with PEP carboxylase in mesophyll "
        "cells, then ship it to deep bundle-sheath cells where RuBisCO works in a "
        "carbon-rich vault. CAM plants such as pineapple open their stomata only at "
        "night and stockpile acid until dawn. Each strategy trades water for carbon."),
    ),
    (
        1_500_000,
        1_800_000,
        ("Cellular respiration spends the sugar in the cytoplasm and mitochondria. "
        "Glycolysis splits one glucose into two pyruvate molecules in the cytoplasm, "
        "netting two ATP and two NADH, with the enzyme hexokinase trapping glucose at "
        "step one. Without oxygen, pyruvate detours to fermentation, which we cover "
        "later. With oxygen, pyruvate enters the mitochondria for the Krebs cycle."),
    ),
    (
        1_800_000,
        2_100_000,
        ("Inside the mitochondrial matrix, pyruvate is oxidized to acetyl-CoA, which "
        "feeds the Krebs cycle. The entry enzyme citrate synthase fuses acetyl-CoA with "
        "oxaloacetate to make citrate. Each turn releases two carbon dioxide molecules "
        "and banks three NADH, one FADH2, and one ATP. Since one glucose yields two "
        "pyruvates, the cycle turns twice per glucose."),
    ),
    (
        2_100_000,
        2_400_000,
        ("The electron transport chain on the cristae folds harvests those carriers. "
        "NADH dehydrogenase and sibling complexes pump protons into the intermembrane "
        "space, building the gradient behind chemiosmosis. Protons flood back through "
        "ATP synthase, a rotary motor minting roughly thirty-four ATP. Oxygen waits at "
        "the end as the final electron acceptor, joining electrons and protons to form "
        "water."),
    ),
    (
        2_400_000,
        2_700_000,
        ("Without oxygen, cells regenerate NAD plus through fermentation. Your muscles "
        "run lactic acid fermentation during a sprint, and Lactobacillus bacteria use "
        "the same pathway to turn milk into yogurt. Yeast instead performs alcoholic "
        "fermentation, releasing ethanol and carbon dioxide that raise bread and brew "
        "beer. Both routes recycle NAD plus but forfeit most of the glucose energy."),
    ),
    (
        2_700_000,
        3_000_000,
        ("Photosynthesis and respiration mirror each other: chloroplasts build sugar "
        "while mitochondria burn it, and their equations run in opposite directions. "
        "Both organelles descend from engulfed bacteria through endosymbiosis, a theory "
        "championed by Lynn Margulis. Their double membranes and private DNA are the "
        "fingerprints of that ancient merger. Keep this mirror picture for the final "
        "quiz."),
    ),
    (
        3_000_000,
        3_300_000,
        ("Time for the Elodea pondweed lab. Place one Elodea sprig in sodium bicarbonate "
        "solution under a lamp at ten centimeters, then repeat at twenty and forty "
        "centimeters. Count oxygen bubbles per minute for three trials each. Closer "
        "light means more bubbles, up to a saturation plateau. Plot bubble rate against "
        "distance and report your plateau point in the worksheet."),
    ),
    (
        3_300_000,
        3_600_000,
        ("That wraps our energy story. Next module we switch from energy to inheritance "
        "with Gregor Mendel and his pea plants, where purple and white flowers split in "
        "a three to one ratio. We will cross peas, count traits, and meet dominant and "
        "recessive alleles. Preview the genetics glossary before Thursday, and bring "
        "your lab notebooks for grading."),
    ),
]

# (page_no, page text) for the permitted PDF handout.
PDF_PAGES: list[tuple[int, str]] = [
    (
        1,
        ("Bioenergetics handout glossary. Thylakoid: a membrane sac where the light "
        "reactions run. Stroma: the chloroplast fluid hosting the Calvin cycle. "
        "RuBisCO: the carbon-fixing enzyme. ATP synthase: the rotary motor making ATP. "
        "Chemiosmosis: ATP production driven by a proton gradient."),
    ),
    (
        2,
        ("Balanced photosynthesis equation: six CO2 plus six H2O yields C6H12O6 plus six "
        "O2. Count the oxygens: six times two plus six times one equals eighteen oxygen "
        "atoms on each side. Six turns of the Calvin cycle supply the six fixed carbons."),
    ),
    (
        3,
        ("Chlorophyll absorption spectrum: a tall blue peak near four hundred thirty "
        "nanometers and a red peak near six hundred sixty-two nanometers, with green "
        "light mostly reflected. Exam tip: quote 430 and 662 for full credit."),
    ),
    (
        4,
        ("Lab safety rules: wear goggles and gloves at all times. The DPIP indicator "
        "stains skin and clothing blue, so wipe spills immediately. Never pipette by "
        "mouth and never eat or drink near the bench."),
    ),
    (
        5,
        ("Hill reaction with DPIP: isolated chloroplasts reduce the dye DPIP from deep "
        "blue to colorless as it accepts electrons under bright light. The tube kept in "
        "darkness stays blue and serves as the control."),
    ),
    (
        6,
        ("References and office hours: Campbell Biology chapter eight, the openstax "
        "photosynthesis module, and the Elodea worksheet. Doctor Rao holds office hours "
        "Tuesdays at three in room two fourteen."),
    ),
]

# (slide_no, slide text) for the permitted PPT deck.
PPT_SLIDES: list[tuple[int, str]] = [
    (1, "Title slide: Bioenergetics 101, Doctor Maya Rao, photosynthesis to respiration in sixty minutes."),
    (
        2,
        ("Chloroplast diagram labels: outer membrane, inner membrane, grana stack of "
        "thylakoids, stroma fluid, starch grain. Label all five parts on the worksheet "
        "diagram."),
    ),
    (
        3,
        ("Light reactions flowchart: water to photosystem two to electron transport to "
        "photosystem one to NADPH, with protons driving ATP synthase on the side branch."),
    ),
    (
        4,
        ("Calvin cycle diagram: CO2 enters at RuBisCO, flows to 3-PGA, then G3P, then "
        "RuBP regeneration. Callout box: RuBisCO is the most abundant enzyme on Earth."),
    ),
    (
        5,
        ("Respiration ATP table per glucose: glycolysis two, Krebs cycle two, electron "
        "transport chain thirty-four, course total thirty-eight ATP. Memorize the two "
        "two thirty-four split."),
    ),
    (
        6,
        ("Fermentation comparison table: the lactic acid route makes lactate for muscles "
        "and yogurt; the alcoholic route makes ethanol plus CO2 for bread and beer. "
        "Both regenerate NAD plus."),
    ),
    (
        7,
        ("Elodea lab procedure in five steps: one, dissolve sodium bicarbonate; two, add "
        "the Elodea sprig; three, set the lamp at ten centimeters; four, count bubbles "
        "for one minute; five, repeat at twenty and forty centimeters."),
    ),
    (
        8,
        ("Summary and quiz answers: light reactions need water and light, the Calvin "
        "cycle needs ATP and NADPH, respiration mirrors photosynthesis. Quiz key: A, C, B."),
    ),
]


def _chunk_id(resource_id: str, segment_key: str, text: str) -> str:
    """Deterministic chunk id, mirroring the ingestion jobs scheme."""
    return hashlib.sha1(
        (resource_id + "|" + segment_key + "|" + text_hash(text)).encode("utf-8")
    ).hexdigest()


def build_fixture_chunks(embedder=None) -> list[Chunk]:
    """Build fixture chunks directly (no file parsing, no DB).

    One chunk per video segment / PDF page / PPT slide. When an embedder is
    supplied, embeddings are attached with its model name.
    """
    embed_model = embedder.name if embedder is not None else "hashing-v1"
    chunks: list[Chunk] = []
    for start_ms, end_ms, text in VIDEO_SEGMENTS:
        cleaned = text.strip()
        chunks.append(
            Chunk(
                id=_chunk_id(RES_VIDEO, f"t{start_ms}-{end_ms}", cleaned),
                resource_id=RES_VIDEO,
                course_id=FIXTURE_COURSE_ID,
                source_type="video",
                chunk_index=len(chunks),
                text=cleaned,
                text_hash=text_hash(cleaned),
                embed_model=embed_model,
                video_id=VIDEO_ID,
                start_ms=start_ms,
                end_ms=end_ms,
            )
        )
    for page_no, text in PDF_PAGES:
        cleaned = text.strip()
        chunks.append(
            Chunk(
                id=_chunk_id(RES_PDF, f"p{page_no}", cleaned),
                resource_id=RES_PDF,
                course_id=FIXTURE_COURSE_ID,
                source_type="pdf",
                chunk_index=len(chunks),
                text=cleaned,
                text_hash=text_hash(cleaned),
                embed_model=embed_model,
                page_no=page_no,
            )
        )
    for slide_no, text in PPT_SLIDES:
        cleaned = text.strip()
        chunks.append(
            Chunk(
                id=_chunk_id(RES_PPT, f"s{slide_no}", cleaned),
                resource_id=RES_PPT,
                course_id=FIXTURE_COURSE_ID,
                source_type="pptx",
                chunk_index=len(chunks),
                text=cleaned,
                text_hash=text_hash(cleaned),
                embed_model=embed_model,
                slide_no=slide_no,
            )
        )
    if embedder is not None:
        for chunk in chunks:
            chunk.embedding = embedder.embed(chunk.text)
    return chunks


def _ms_to_vtt_stamp(ms: int) -> str:
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, mil = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{mil:03d}"


def to_vtt_text() -> str:
    """Render the fixture lecture as a VTT caption file.

    This seeds the ``run_ingestion`` dry-run path with the same content the
    programmatic chunks carry, so ingestion chunking/parsing can be exercised
    without any binary fixtures.
    """
    lines = ["WEBVTT", ""]
    for i, (start_ms, end_ms, text) in enumerate(VIDEO_SEGMENTS, start=1):
        lines.append(str(i))
        lines.append(f"{_ms_to_vtt_stamp(start_ms)} --> {_ms_to_vtt_stamp(end_ms)}")
        lines.append(text.strip())
        lines.append("")
    return "\n".join(lines)


def write_fixture_vtt(path: str | Path) -> Path:
    """Write the fixture VTT to ``path`` and return it."""
    target = Path(path)
    target.write_text(to_vtt_text(), encoding="utf-8")
    return target


def fixture_resource_rows() -> list[dict]:
    """Resource rows matching the fixture chunks, for ``run_ingestion``."""
    return [
        {
            "id": RES_VIDEO,
            "course_id": FIXTURE_COURSE_ID,
            "type": "video",
            "video_id": VIDEO_ID,
        },
        {"id": RES_PDF, "course_id": FIXTURE_COURSE_ID, "type": "pdf"},
        {"id": RES_PPT, "course_id": FIXTURE_COURSE_ID, "type": "pptx"},
    ]
