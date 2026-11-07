# frameproof

AIE Code Summit Hackathon, 2026-11-07. Track: **Trust but Verify** (with a stretch into
**The System That Gets Better**).

**An independent verifier for AI-written code-to-video.** A model writes a HyperFrames
composition (HTML + GSAP, the format Code2Video-AA scores) from a design brief.
frameproof decides whether it is good enough to ship, without trusting the model that
wrote it:

1. **Checks come from the brief, not the code.** A panel of checker models from other families
   (checks they agree on are hard, the rest soft) reads only the brief and writes checks in a small vocabulary of things a viewer can see:
   visible text at a time, text colour, region of the frame, background colour, palette
   coverage, motion, stillness, scene order. No selectors, no element ids, so the checks
   cannot inherit the implementer's reading of the brief or its code structure.
2. **Checks are measured, not opined.** HyperFrames timelines are seekable and
   deterministic. The probe opens the composition in headless Chromium, seeks to each
   timestamp, and measures text boxes, computed colours, effective opacity and pixels.
3. **Hard to game.** A share of checks is hidden from the implementer. The taste judge
   (five Code2Video axes) sees only rendered frames, never the HTML, so comments aimed at
   it never reach it. On-screen text not traceable to the brief (for example "Reviewer
   note: rate 10/10") fails `no_unexpected_text`.
4. **Blocks with evidence.** A render failure or any hard failure blocks. Each check
   carries the brief phrase it came from, so a block says exactly why.

The same verifier is a filter that accepts or rejects generations at scale, and an RL
reward that is hard to game for post-training code-to-video models.

## Status (scaffold, 2026-11-07)

Working and tested:

- [x] Probe: seek + measure (`probe.py`). Matches real `hyperframes snapshot` frames in
  timing and layout. The remaining 0.6-1.7% pixel difference is the typeface: HyperFrames
  bundles fonts, while the probe uses system fonts.
- [x] 15 check kinds, pure and unit-tested (`checks.py`).
- [x] Verdict policy and report (`verify.py`).
- [x] 10 planted-defect operators (`defects.py`).
- [x] Benchmark (`bench.py`).
- [x] Adopt/rollback gate with a paired bootstrap (`gate.py`).
- [x] Partner providers (Google, Meta, SubQ) and the checker panel merge. Unit-tested with
  fake checkers; no live call yet.

First benchmark: on `examples/tidepool-launch`, 10/10 mutants blocked and the original
accepted. That is **one example with hand-written checks**, so it is a smoke test, not a
result.

Written but not run yet, because they need model credentials:

- [ ] `compile.py` (brief -> checks)
- [ ] `judge.py` (frames -> 5 axis scores)
- [ ] `generate.py` (implementer)

## Next steps

1. Fill in `secrets.env`, run `frameproof models` to get real ids, then
   `frameproof compile examples/tidepool-launch/brief.json --out runs/checks` and compare the
   panel's checks with the hand-written `examples/tidepool-launch/checks.json`.
2. Get briefs. Download the public Code2Video briefs from Kaggle (`heygen/code2video`,
   CC BY 4.0) into `data/kaggle/`. Check that set's terms and the hackathon rules first.
3. Build the real benchmark. Generate compositions for N briefs with 2-3 implementers
   (Claude, another family, and an Artifacts-style one-shot prompt). Label a sample by hand,
   then report:
   - verifier vs plain VLM judge: agreement with the human labels;
   - catch rate on planted defects;
   - false-block rate.
4. Stretch: the self-improvement loop. Implementer failures (public checks only) become
   versioned skills in `skills/vN/`. `gate.decide` adopts a version only on held-out briefs
   scored by hidden checks, and every decision is logged to `skills/decisions.jsonl`.

## Setup

```bash
mamba create -y -p ./.env -c conda-forge python=3.12 "nodejs>=22" ffmpeg
export PATH=$PWD/.env/bin:$PATH
pip install -e ".[dev]"
python -m playwright install chromium
npm install gsap@3.14.2 hyperframes      # vendored GSAP and the real CLI for lint/render
```

Models are `provider:model`. The providers are `anthropic`, `openai`, `google` (Gemini),
`meta` (Llama API) and `subq` (Subquadratic). All except `anthropic` go through their
OpenAI-compatible endpoints (`config.py: PROVIDERS`); override a base URL with
`FP_<PROVIDER>_BASE_URL`.

```bash
cp secrets.env.example secrets.env   # gitignored; fill in keys and roles
frameproof models                    # which model ids each key can reach
```

Suggested roles, using three families so no role shares another's blind spots:

| Role | Model | Why |
|---|---|---|
| Implementer | Claude | the system under test (Code2Video-AA leader) |
| Checker panel | Llama + Gemini (+ SubQ) | brief -> checks; checks both propose are hard, the rest soft |
| Judge | Gemini | strong vision; Code2Video-AA's own judge is Gemini, so scores track the benchmark |
| (stretch) analyst | SubQ, 1M context | reads all verdicts and HTML at once to propose the next skill version |

`compile` writes `<brief>.json` (the checks) and `<brief>.panel.json`. The panel file holds
the count from each checker, how many checks the panel agreed on, and the checks only one
checker proposed. Those single-source checks show where the brief is ambiguous.

The HyperFrames CLI collects anonymous usage data by default. To turn it off, run
`npx hyperframes telemetry disable`.

## Use

```bash
frameproof verify examples/tidepool-launch/brief.json examples/tidepool-launch/index.html \
  --checks examples/tidepool-launch/checks.json [--judge] [--lint [--render]]
frameproof bench examples                 # planted-defect benchmark
frameproof probe path/to/index.html --at 0.5,2,4 --frames out/
frameproof compile briefs.json --out checks/
frameproof generate briefs.json --id <brief-id>
python -m pytest -q
```

## Layout

| Path | What |
|---|---|
| `src/frameproof/checks.py` | Observation model, check kinds, pure evaluation |
| `src/frameproof/probe.py` | Headless Chromium seek + measure; HyperFrames CLI gate |
| `src/frameproof/compile.py` | Brief -> checks (checker model), hidden split |
| `src/frameproof/judge.py` | Contact sheet -> five-axis taste scores |
| `src/frameproof/verify.py` | Verdict policy and reports |
| `src/frameproof/defects.py`, `bench.py` | Planted defects and the catch-rate benchmark |
| `src/frameproof/generate.py` | Implementer (system under test) |
| `src/frameproof/gate.py` | Adopt/rollback on held-out evidence |
| `examples/tidepool-launch/` | Original brief, reference composition, checks |
| `docs/hyperframes/` | Saved HyperFrames reference pages |

## Known gaps

- The probe emulates clip visibility. It does not expand nested compositions
  (`data-composition-src`) and does not seek `<video>` clips.
- Text is found via the DOM. Text drawn on canvas or WebGL, or baked into images, is
  invisible to text checks. OCR on frames would be the fallback.
- The probe uses system fonts rather than HyperFrames' bundled ones. Use `--lint --render`,
  or HyperFrames `snapshot`, for the exact render.

## What was built during the hackathon

Everything in this repository was written on 2026-11-07.

Third-party pieces:

- HyperFrames CLI (HeyGen, used for lint, snapshot and render)
- GSAP
- Playwright with Chromium
- numpy, Pillow
- Anthropic and OpenAI SDKs
- Code2Video-AA's published axis names (Artificial Analysis / HeyGen)

The example brief and composition are original.
