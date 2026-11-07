# frameproof studio (ADK 2 graph)

A Google ADK 2 `Workflow` that writes a HyperFrames video for a brief and ships it only
if frameproof accepts it. The verifier is the gate: it is deterministic code, not an
LLM, so the agent cannot argue its way past it.

```
intake -> (reader_1, reader_2, reader_3) -> join_readers -> panel -> proposer -> gate
                                                               ^                   |
                                                               +----- REVISE ------+   public failures only
                                                    ACCEPT -> desk (pauses) -> deliver
                                                    GIVE_UP -> give_up
```

| Node | Does | Sees |
|---|---|---|
| `intake` | reads the brief and opens `runs/studio/<run>/` | the user's message |
| `reader_1..3` | run in parallel; each checker model in `FP_CHECKER_MODELS` proposes checks | the brief only |
| `panel` | merges the readers' checks, collects SubQ Snap votes and hides a share from the proposer (details below) | the readers' checks |
| `proposer` | the implementer writes `attempt-N/index.html` | the brief, plus **public** failures from the last attempt |
| `gate` | `frameproof.verify` + probe preview + `report.html`, then routes ACCEPT / REVISE / GIVE_UP | everything, including hidden checks |
| `desk` | starts the final render and **pauses** on a `RequestInput` whose id is the job id | the accepted attempt |
| `deliver` | resumes on the answer, writes `summary.json` and the run page | the desk's answer |

The `panel` step in detail:
- Agreement: a check proposed by 2 or more readers is hard; one proposed by a single reader is soft.
- Voting: SubQ Snap (a decision model) votes yes or no on each check, and a yes counts as one more backer.
- Hiding: about 30% of the checks are hidden from the proposer.
- Fallback: with no working checker model, it uses the example's `checks.json` (fixture mode).

Independence comes from three things, and the tests check the first:
1. The proposer never receives a hidden check.
2. The gate never sees the proposer's prompt.
3. The checks come from the brief alone.

## Run it

```bash
export PATH=$PWD/.env/bin:$PATH
python scripts/preflight.py                      # every line a real probe
python -m agent.run examples/tidepool-launch     # terminal: runs, waits at the desk, answers it, delivers
adk web --port 8000 apps                         # browser: pick frameproof_studio, send the brief
```

In `adk web` the run pauses at the desk. Answer `{"approved": true}` once the render it
names is ready. `python -m agent.run` does that for you: it waits for the render job, then
answers by id. That is `deliver.py`, the delivery console.

Inputs: an example folder, `briefs.json#<id>`, a JSON brief, or the brief text.

## Modes (labelled in every report)

| Variable | Default | Effect |
|---|---|---|
| `FP_CHECKER_MODELS` | none | readers; with none working, the panel uses the example's fixture checks |
| `FP_IMPLEMENTER_MODEL` | `anthropic:claude-opus-5-5` | the proposer, when its key is set |
| `FP_PROPOSER=replay`, `FP_REPLAY` | `shift_timing,typo,original` | without an implementer key: submit planted-defect variants of the example in order, to show block -> revise -> accept |
| `FP_MAX_ATTEMPTS` | 4 | then GIVE_UP |
| `FP_REAL_RENDER` | 0 | 1 = the desk renders with HyperFrames (about 5 min per 6 s clip on a 2-CPU node). 0 = stand-in: the probe preview the gate already made |
| `FP_SKILLS` | none | folder of `*.md` skills appended to the proposer's prompt (versioned skills for the self-improvement gate) |

## Snap as reader and judge (a SubQ key only)

SubQ Snap is a decision model. It takes some text, up to 4 images and up to 128
questions, and returns a probability for each answer. It never writes text, so it
cannot write checks or code. It can still fill two of the three roles:

- **Reader.** `frameproof.candidates` parses the brief into candidate checks, with no
  model involved:
  - quoted copy becomes visible / hidden / order checks;
  - the palette's **type** colour becomes text-colour checks;
  - a colour put on the words ("in gold") also becomes a text-colour check;
  - accent colours become fill checks;
  - region words, motion verbs and holds each become a check.

  It deliberately over-generates, including plausible wrong candidates. Snap votes on
  all of them in one call. P >= 0.75 makes a hard check, 0.5–0.75 a soft one, and
  anything lower is dropped. Every vote is saved in `panel.json` under
  `snap_candidates`. With no text checker configured, a decision model in
  `FP_CHECKER_MODELS` fills a reader slot this way, and a model that read never also
  votes.
- **Judge.** `FP_JUDGE_MODEL=subq:subq-snap-1.1` gives a 5-level rating on each of
  the five axes from 4 frames. It never sees the HTML, and it writes no free text that
  on-screen "rate this 10/10" could steer. Hard failures block on their own; the judge
  only moves the score (`FP_JUDGE_WEIGHT`, default 0.3). A judge error is recorded and
  never sinks the gate.
- **Proposer: no.** Writing HTML needs a text model (Claude, Gemini or Llama).

Live run on 2026-11-07: Snap reader + Snap judge, replay proposer, 61 s, 1 reader
call + 3 judge calls.

- **Candidates:** 23. Snap rejected the title "on the right" (P = 0.00) and, too
  strictly, the white CTA text (P = 0.11).
- **Checks:** 27, 6 of them hidden.
- **Attempts:** shift_timing was blocked (judge 0.57), typo was blocked (judge 0.76),
  and the original was accepted (judge 0.81) and delivered.
- **The run before it** blocked the good original. The generator had proposed
  "coral CTA text" from "a coral button reading …" and a 3% fill check for the type
  colour, and Snap accepted both. The fix was in the generator, which now respects
  palette roles; a test covers it.

## Verified on 2026-11-07

- **Terminal run, replay mode, about 60 s:**
  1. `shift_timing` was blocked; 6 public failures went back and 2 hidden ones were withheld.
  2. `typo` was blocked; 2 public failures went back and 1 hidden one was withheld.
  3. `original` was accepted, went through the desk with the stand-in render, and was delivered.
- **`adk web` (ADK 2.14.0):** the same run paused at `desk-<run>`. Answering by id through
  `/run` with a `functionResponse` delivered it.
- `tests/test_agent.py` covers input parsing, desk answers, the replay sequence, unanswered-call
  tracking, the graph's shape, and one end-to-end replay. The end-to-end test asserts that
  every item of feedback the proposer saw came from a public check.

Not yet run with live models: readers, proposer and judge all need keys.
