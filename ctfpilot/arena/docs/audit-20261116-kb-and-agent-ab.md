# CTFPilot evolution audit: KB cleanup + recall-effectiveness accounting + agent-layer A/B experiments

## Scope (2026-11-16)

Nine-section console audit of the production knowledge base, followed by controlled
experiments on whether skills/memory actually help a solver agent. Production data
was cleaned in place; every destructive step has a timestamped backup.

## 1. Knowledge-base cleanup (cases 121 -> 119, lessons 141 -> 137)

- Dropped 2 ops-noise cards (`SKIP-github-api-rate-limit`, `本轮跳过-API限流`) and
  their lessons: operational records, not solving experience, and the latter was the
  #2 most-recalled lesson (74 hits) — pure poisoning of retrieval.
- Retracted fake flag `flag{"+rr.hex()+"}` (challenge 108): a Python template string
  inside a zip mis-credited as solved by the `unpack archives` detector. Attempt
  re-classed `semantic:template-flag`. raw-echo gate catches verbatim echoes, not
  source-code placeholders — new error class added.
- Tagged 13 self-test rows `mode='selftest'` (kept, but out of production metrics):
  the *real* battle record after isolation is 0/104. The previous 12% headline was
  self-test replays + 1 fake flag.
- Rebuilt lessons (137 rows, 131 recallable). Retrieval regression vs baseline:
  top1 +0.0pp / top3 +0.0pp / MRR -0.002 → PASS (no degradation from cleanup).

## 2. Recall-effectiveness accounting fix (trajstore.py + deep/loop.py)

`bump_success(category, technique)` could never match case cards — their technique
is `case:<name>`, never a detector name — so `success_count` was structurally 0 and
the fixation trigger only ever measured *re-use*, not verified value.

- `recall_lessons()` now returns lesson ids; `bump_success_by_ids()` credits only
  `case:` cards (detector-family counting stays with the old call).
- `deep/loop.py` tracks every lesson that entered the context (including JIT
  re-recalls) and credits them on solve.
- Verified end-to-end on a DB copy: recall 3 misc cards -> solve -> +1 on each.
- Selftest 6/6 after the change; eval-retrieval PASS (top1 62.5% unchanged).

## 3. Web/pwn seed cards (119 -> 128 cases)

9 static-read-only cards distilled from local pull snapshots (5 web, 4 pwn):
prototype-pollution + ejs.render SSTI chain, cookie-type-confusion logic bypass,
`<%-` attribute escape, char-whitelist `shell_exec`, tcache-dup vs fastbin-dup on
the same note codebase (libc 2.31 vs 2.23), constraint-gate pwn (not overflow),
xinetd+chroot deployment semantics. All unverified chains marked as such.
Recall spot-check: new cards rank top-4 for web queries, top-2 for pwn queries.

## 4. Arena ablation (3 variants x 6 challenges, engine layer)

`baseline-fast / baseline-auto / no-memory` on a 6-challenge local pool, all
solved=False, adversarial clock 120.00s each (full penalty). Interface clean
(`variant_unsupported=0`, `degraded=0`), but the run is *informative-null*, not a
result: the deep channel runs `brain=mock (backend=none)`, and `_mock_strategy`
takes no lessons/context argument — recalled cards are architecturally unable to
reach the mock planner, so memory cannot show value in this harness. Two engine
findings: fast path has no large-file gate (2.9MB JSON hit the full 120s timeout);
local dry-pull sources are mostly remote-interactive or dummy rounds. Verdict
`no-winner`, incumbent kept. Findings + quarantine ledger committed under
`arena/state` are intentionally gitignored for state, so the verdicts are mirrored
in this audit note.

## 5. Agent-layer experiments (v1 skill vs no skill vs v2 skill, 2 output modes)

Motivation: if the memory is to be consumed by a model, the model must be the
solver. Built `arena/kb_recall.py` (read-only stdio recall, no hit_count writes)
and 3 synthetic transfer tasks (zip prefix+fake-encryption / periodic-5 XOR with
MZ crib / reversed multi-layer encoding chain), each self-verified before use.

Round 1 (hand-written prompts — later rejected as a confound): memory group 4.5x
slower, no solve-rate difference.

Round 2 (corrected): subagents load skills themselves via the skill tool.
- A (ctf-solve v1): 105/265/141s, 3/3
- B (no skill, full report): 60/420/300s, 3/3
- B' (no skill, flag-only output): 78.5/199/56s, 3/3
- C1 (ctf-solve-v2, full report): 445/860/175s, 3/3
- C2 (ctf-solve-v2, flag-only): 145/2127/54.5s, 3/3

Findings:
- Output format dominates skill presence: report vs flag-only moved the chain task
  300s -> 56s (5.4x). Requiring per-step narration changes the solving posture
  (more intermediate narration + tool round-trips), it is not just the final report.
- Variance swamps agent-layer ablation at n=1: same "minimal output" condition on
  xor spans 199s -> 2127s (10.7x). Only the deterministic chain task is stable,
  and there C2 (54.5s) ~= B' (56s): for tasks the model solves fast anyway, v2 has
  no net gain; its home turf is "model stalls 5-30min, fast path solves" — which
  the current pool does not contain. The 2x2 attribution is therefore *invalid*
  as a quantitative claim; verdict recorded as variance-dominates.
- First direct measurement of the timing-boundary clause: C1 zip reports flag at
  185s but 445s including writeup+learn — 58% of wall clock is post-solve bookkeeping.
- Process narration is not trustworthy evidence: C1 xor claimed it "reconciled the
  key from the knowledge base"; full audit of lessons/learned/writeups found 0
  hits (the card had been cleaned). It actually re-derived the key via crib. Flag
  was true; the stated method was a narrative hallucination.
- Parallel same-task groups risk cross-contamination through learn-writeup; future
  runs must be serial or use isolated DB copies.

## 6. ctf-solve-v2 skill (pending full eval)

`skills/ctf-solve-v2/SKILL.md` (TeleAgent user-config dir, outside this repo):
fast-first, delayed recall, >2MB sampling, JSON result parsing, no-writeup-on-
unsolved, auto raw-echo verdict, and the new timing-boundary/minimal-output
section (clock stops at verified flag; writeup/learn are out-of-clock and skipped
by default). Every change cites an arena or agent experiment above. Final
promotion gated on the v2-mainline pool experiment.

## 7. Experiment hygiene ledger

- All experiment pollution removed (challenges 120-135, 272+49 attempts, lessons,
  learned cards to recycle bin), pre/post states verified: max id 119, lessons 146
  (recallable 140), cases 128.
- Incident: one cleanup pass used `id>146` as the deletion boundary and dropped 40
  legitimate lessons (new ids had crossed 147). Recovered from backup
  `bak-20261116-122817`, re-deleted only the known Round-A rows. Lesson recorded:
  delete by timestamp/name match, never by id threshold.
- db backup chain: bak-20261116-102014 / 122817 (clean baseline) / 133650 /
  broken-134100 (incident scene, kept for forensics).

## 8. Next

1. Restart to load the 4183-byte v2 into the skill cache (this round tested the
   3347-byte cached version — timing-boundary clause was absent from it).
2. Build the "model stalls, fast solves" pool (deep encoding chains, nested zip +
   transforms, dummy rounds, >2MB files) — the only regime where v2 can show
   net gain.
3. Discipline: n>=3 per task, serial same-task groups, fixed parallelism, steps
   spot-audits; quantitative claims go to the engine-layer arena only.
