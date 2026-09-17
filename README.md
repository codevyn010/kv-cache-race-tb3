# kv-cache-race — Terminal-Bench 3 task submission

Original Terminal-Bench 3 task built for the Klavis AI Founding Engineer coding assignment.

The task: `terminal-bench/kv-cache-race` — a continuous-batching LLM inference server has a
use-after-free race in its KV-cache block pool. Cancelling a request frees its block without
waiting for an in-flight generation step still holding a reference to it, so a newly admitted
request can start writing into a block a stale step is still reading/writing. It never reproduces
under single-request testing, only under concurrent load with cancellations in flight. Full
details in [`tasks/kv-cache-race/README.md`](tasks/kv-cache-race/README.md) and
[`tasks/kv-cache-race/instruction.md`](tasks/kv-cache-race/instruction.md).

## Reproducing

```bash
uv tool install harbor
docker ps   # Docker Desktop must be running

# Static checks, oracle, nop — see docs/results/ for actual output
harbor run -p tasks/kv-cache-race --agent oracle --env docker
harbor run -p tasks/kv-cache-race --agent nop --env docker

# Standard trials (current TB3 CI default config)
harbor run -p tasks/kv-cache-race \
  --agent claude-code --model anthropic/claude-opus-5 \
  --env docker --yes --ae CLAUDE_FORCE_OAUTH=1 --ae CLAUDE_CODE_OAUTH_TOKEN=<your_token> \
  --ak reasoning_effort=max

harbor run -p tasks/kv-cache-race \
  --agent codex --model openai/gpt-5.6-sol \
  --env docker --yes --ae CODEX_FORCE_AUTH_JSON=1 --ak reasoning_effort=xhigh
```

## Results

Full commands, configs, and outputs for every required check and trial are documented in
[`docs/results/`](docs/results/):

| Check | Status |
|---|---|
| Static checks (22/22) | ✅ Pass |
| `harbor check` implementation rubric | ⏳ Pending re-run (env files changed since last pass) |
| Docker build | ✅ Pass |
| Oracle reward = 1.0 | ✅ Pass |
| Nop reward < 1.0 | ✅ Pass (0.0) |
| Standard trials — 3× claude-code (opus-5, max) | ⏳ 0/3 valid (1 stale run predates current environment) |
| Standard trials — 3× codex (gpt-5.6-sol, xhigh) | ⏳ 0/3 — blocked, see below |
| Adversarial trials — 1× claude-code, 1× codex | ⏳ Not yet run |

**Verifier bug found and fixed during oracle validation:** the loadgen sidecar's measurement
loop had two robustness bugs that produced false-negative oracle scores unrelated to the actual
fix's correctness — (1) `asyncio.gather()` had no exception handling, so a request erroring out
when the agent restarts the server (an expected, encouraged part of solving this task) crashed
the entire sidecar and permanently froze `results.json` on a pre-fix measurement; (2) even after
fixing that, a zero-completion "pass" produced during harbor's teardown (after it stops the
`main` service but before it collects the sidecar artifact) could still overwrite a real, correct
measurement with a degenerate one. Both are now fixed in `loadgen.py`: per-request errors are
excluded from a pass instead of raised, and only passes with at least one real completion are
persisted. Verified oracle went 0.0 → 1.0 after each fix in turn, confirming the actual KV-cache
fix was correct all along and the failures were purely verifier artifacts.

**Open blocker:** the current TB3 CI codex default is `gpt-5.6-sol`. That model is rejected
outright by ChatGPT-account Codex auth (`400: model not supported when using Codex with a
ChatGPT account`); an OpenAI API key with access to `gpt-5.6-sol` is required to complete the
codex standard and adversarial trials.

This README and `docs/results/` will be updated as each remaining check/trial completes.
