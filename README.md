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
| `harbor check` implementation rubric | ✅ Pass (Mean 1.000, all criteria pass or n/a) |
| Docker build | ✅ Pass |
| Oracle reward = 1.0 | ✅ Pass |
| Nop reward < 1.0 | ✅ Pass (0.0) |
| Standard trials — 3× claude-code (opus-5, max) | ✅ 3/3 - valid |
| Standard trials — 3× codex (gpt-5.6-sol, xhigh) | ✅ 3/3 — valid |
| Adversarial trials — 1× claude-code | ✅ Pass |
| Adversarial trials — 1× codex | ✅ Pass |

This README and `docs/results/` will be updated as each remaining check/trial completes.
