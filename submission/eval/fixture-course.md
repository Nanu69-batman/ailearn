# Eval Fixture Course — "Bioenergetics 101" (synthetic)

Self-contained fixture for the offline eval harness (`apps/api/app/eval/`).
All content is invented for testing; any resemblance to a real course is
coincidental. Programmatic source of truth: `app/eval/fixture.py`
(chunk ids are `sha1(resource_id | segment_key | text_hash)`, one chunk per
segment / page / slide, course `course_fixture`).

## Video lecture — `res_video` (60 min, 12 x 5-min segments, `vid_bioenergetics`)

| Segment | Range (ms) | Topic | Distinctive keywords |
|---|---|---|---|
| V0 | 0 – 300,000 | Course intro w/ Dr. Maya Rao; bioenergetics defined; sequoia teaser; roadmap incl. Elodea lab | Maya Rao, bioenergetics, sequoia, roadmap |
| V1 | 300,000 – 600,000 | Chloroplast structure: grana/thylakoids, stroma, chlorophyll a (reaction center) + b | grana, chlorophyll b, double-membrane |
| V2 | 600,000 – 900,000 | Light-dependent reactions: photosystem II, P680, photolysis of water, O2 released, protons to thylakoid lumen, ATP + NADPH | P680, photolysis, thylakoid lumen, NADPH |
| V3 | 900,000 – 1,200,000 | Calvin cycle in stroma: RuBisCO + CO2 onto RuBP → 3-PGA → G3P → RuBP regeneration; 6 turns = 1 glucose | RuBisCO, RuBP, 3-PGA, G3P |
| V4 | 1,200,000 – 1,500,000 | C3 vs C4 vs CAM: photorespiration, PEP carboxylase, bundle-sheath cells, pineapple night stomata | PEP carboxylase, bundle-sheath, CAM, pineapple |
| V5 | 1,500,000 – 1,800,000 | Glycolysis in cytoplasm: glucose → 2 pyruvate, net 2 ATP + 2 NADH, hexokinase step one | cytoplasm, pyruvate, hexokinase |
| V6 | 1,800,000 – 2,100,000 | Pyruvate oxidation + Krebs in matrix: acetyl-CoA, citrate synthase → citrate, 3 NADH + FADH2 + ATP per turn | acetyl-CoA, citrate synthase, matrix, FADH2 |
| V7 | 2,100,000 – 2,400,000 | ETC + oxidative phosphorylation on cristae: NADH dehydrogenase, chemiosmosis, ~34 ATP, O2 final acceptor → water | cristae, chemiosmosis, final electron acceptor |
| V8 | 2,400,000 – 2,700,000 | Fermentation: lactic acid (muscles, Lactobacillus/yogurt) vs alcoholic (yeast, ethanol, bread/beer) | Lactobacillus, yogurt, yeast, ethanol |
| V9 | 2,700,000 – 3,000,000 | Photo vs respiration mirror; endosymbiosis theory, Lynn Margulis, double membranes + private DNA | endosymbiosis, Lynn Margulis, mirror |
| V10 | 3,000,000 – 3,300,000 | Elodea pondweed lab: sodium bicarbonate, lamp at 10/20/40 cm, count O2 bubbles/min, saturation plateau | Elodea, sodium bicarbonate, plateau |
| V11 | 3,300,000 – 3,600,000 | Wrap-up + NEXT-module genetics teaser: Mendel peas, 3:1 ratio, alleles (not bioenergetics content) | Mendel, pea, three to one, alleles |

## PDF handout — `res_pdf` ("Bioenergetics Handout", 6 pages)

| Page | Contents (only source for …) |
|---|---|
| 1 | Glossary: thylakoid, stroma, RuBisCO, ATP synthase, chemiosmosis |
| 2 | Balanced equation 6CO2 + 6H2O → C6H12O6 + 6O2; **18 oxygen atoms on each side** |
| 3 | Absorption spectrum: **430 nm blue / 662 nm red peaks**, green reflected |
| 4 | Safety: goggles+gloves, **DPIP stains skin/clothing blue**, no eating at bench |
| 5 | Hill reaction: **DPIP deep blue → colorless** under light; dark tube = control |
| 6 | References + office hours (Tue 3pm, room 214) |

## PPT deck — `res_ppt` (8 slides)

| Slide | Contents (only source for …) |
|---|---|
| 1 | Title: Bioenergetics 101, Dr. Maya Rao |
| 2 | Chloroplast diagram: 5 labels incl. starch grain |
| 3 | Light-reactions flowchart |
| 4 | Calvin diagram + callout: **RuBisCO is the most abundant enzyme on Earth** |
| 5 | ATP table: **glycolysis 2 / Krebs 2 / ETC 34 = total 38** ("two two thirty-four") |
| 6 | Fermentation table: lactate vs ethanol + CO2, both regenerate NAD+ |
| 7 | Elodea procedure, **exactly 5 ordered steps** |
| 8 | Summary + quiz key A, C, B |

## Golden-set design notes (`submission/eval/golden.jsonl`, 25 items)

- 6 `video_before`, 4 `video_later`, 4 `pdf`, 3 `ppt`, 3 `unanswerable`,
  3 `followup` (one chain: `eval-f1 → eval-f2 → eval-f3`), 2 `adversarial`.
- Edge pauses: `T=0` (`eval-pdf1`: only docs may answer), segment boundary
  `T=300,000` (`eval-vb6`), `T=end=3,600,000` (`eval-vb4`, `eval-u2`).
- `video_later` / `adversarial` gold sources point at the *withheld* future
  chunk, so any retrieval of them is machine-checkable leakage.
- Every pause falls exactly on a chunk boundary, so the eval-strict rule
  (`end_ms <= pause_at_ms`) and the production gate (`end_ms <= T`) agree.
- Refuse-case questions use vocabulary that appears only in withheld or
  nonexistent content, so gated retrieval cannot support an answer.
- The harness enforces eval-strict visibility (`end_ms <= T` for video) via
  `apply_strict_cutoff` *before* ranking, then re-asserts with
  `detect_leakage`. The production store gate is now the same strict rule
  (`end_ms IS NOT NULL AND end_ms <= :current_time_ms`, see
  `retrieval/store.py`), so the harness and production agree by construction.
  `production_gap_rate` is measured on raw production retrieval over the full
  pool and must be 0; any nonzero value is a parity regression, not a score.
