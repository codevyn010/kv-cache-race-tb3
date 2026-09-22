"""Load generator + scorer sidecar.

Runs alongside the agent's `main` service for the whole session, firing
concurrent-load measurement passes back-to-back (NUM_REQUESTS requests
with varying prompt/length, a fraction cancelled mid-flight to trigger the
block-reuse race, checked against the pure reference recurrence in
model.py) until the environment is torn down or MAX_WAIT_SEC elapses.
Every pass overwrites /results/results.json (scored) and
/results/measure.log (human-readable), which the separate verifier reads
as collected sidecar artifacts -- so whatever the *last completed* pass
shows is what gets graded.

Earlier versions tried to detect "the agent just restarted the server and
then went quiet" and fire a single decisive pass at that point. That's
wrong: a real agent restarts the server many times while iterating, and a
one-shot measurement after the first restart only ever grades that first,
often-incomplete attempt -- every later, more correct restart goes
unmeasured. Repeating passes continuously removes the need to guess when
the agent is "done" at all; the agent's own instruction to keep the
session alive briefly after its last restart is what guarantees one more
full pass lands against the final code before teardown.
"""

import asyncio
import json
import os
import random
import statistics
import time

import aiohttp

import model

MAIN_HOST = os.environ.get("MAIN_HOST", "main")
MAIN_PORT = os.environ.get("MAIN_PORT", "8000")
BASE_URL = f"http://{MAIN_HOST}:{MAIN_PORT}"

# Overall safety cap on total run time, not a "wait for quiescence" budget
# -- passes just keep repeating until this elapses or the container is
# torn down, whichever happens first.
MAX_WAIT_SEC = float(os.environ.get("MAX_WAIT_SEC", "14000"))

NUM_REQUESTS = int(os.environ.get("NUM_REQUESTS", "60"))
CANCEL_FRACTION = float(os.environ.get("CANCEL_FRACTION", "0.35"))
LOAD_SEED = int(os.environ.get("LOAD_SEED", "42"))
PROMPT_LEN_MIN = int(os.environ.get("PROMPT_LEN_MIN", "1"))
PROMPT_LEN_MAX = int(os.environ.get("PROMPT_LEN_MAX", "15"))
MAX_NEW_TOKENS_MIN = int(os.environ.get("MAX_NEW_TOKENS_MIN", "20"))
MAX_NEW_TOKENS_MAX = int(os.environ.get("MAX_NEW_TOKENS_MAX", "50"))
CANCEL_DELAY_MIN_SEC = float(os.environ.get("CANCEL_DELAY_MIN_SEC", "0.02"))
CANCEL_DELAY_MAX_SEC = float(os.environ.get("CANCEL_DELAY_MAX_SEC", "0.15"))
LATENCY_SLA_SEC = float(os.environ.get("LATENCY_SLA_SEC", "8.0"))

# A pass interrupted mid-flight (server restart, or teardown killing
# connections while a pass is in progress) still has completed_count > 0 but
# is a degenerate, mostly-errored snapshot -- persisting it would clobber the
# last real full pass. Require most of the expected non-cancelled requests to
# have actually completed before this pass is allowed to overwrite results.
MIN_COMPLETED_TO_PERSIST = int(os.environ.get(
    "MIN_COMPLETED_TO_PERSIST",
    str(int(NUM_REQUESTS * (1 - CANCEL_FRACTION) * 0.75)),
))

RESULTS_DIR = "/results"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(f"{RESULTS_DIR}/loadgen.log", "a") as f:
        f.write(line + "\n")


async def wait_for_healthy(session: aiohttp.ClientSession) -> None:
    while True:
        try:
            async with session.get(f"{BASE_URL}/health", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        await asyncio.sleep(1)


async def run_one(session: aiohttp.ClientSession, rng: random.Random, request_id: str) -> dict:
    prompt_len = rng.randint(PROMPT_LEN_MIN, PROMPT_LEN_MAX)
    prompt_tokens = [rng.randint(0, model.VOCAB_SIZE - 1) for _ in range(prompt_len)]
    max_new_tokens = rng.randint(MAX_NEW_TOKENS_MIN, MAX_NEW_TOKENS_MAX)
    will_cancel = rng.random() < CANCEL_FRACTION
    cancel_delay = rng.uniform(CANCEL_DELAY_MIN_SEC, CANCEL_DELAY_MAX_SEC) if will_cancel else None

    async def do_generate() -> dict:
        payload = {"request_id": request_id, "prompt_tokens": prompt_tokens, "max_new_tokens": max_new_tokens}
        async with session.post(f"{BASE_URL}/generate", json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
            return await resp.json()

    async def do_cancel() -> None:
        await asyncio.sleep(cancel_delay)
        try:
            async with session.post(f"{BASE_URL}/cancel/{request_id}", timeout=aiohttp.ClientTimeout(total=5)):
                pass
        except Exception:
            pass

    start = time.monotonic()
    gen_task = asyncio.ensure_future(do_generate())
    if will_cancel:
        asyncio.ensure_future(do_cancel())
    data = await gen_task
    elapsed = time.monotonic() - start

    return {
        "request_id": request_id,
        "prompt_tokens": prompt_tokens,
        "max_new_tokens": max_new_tokens,
        "tokens": data["tokens"],
        "cancelled": data["cancelled"],
        "cancel_scheduled": will_cancel,
        "elapsed_sec": elapsed,
    }


async def run_pass(session: aiohttp.ClientSession) -> dict:
    rng = random.Random(LOAD_SEED)
    tasks = [asyncio.ensure_future(run_one(session, rng, f"r{i}")) for i in range(NUM_REQUESTS)]
    # The agent is expected to restart the server mid-session to pick up a
    # fix, which can abort whatever requests are in flight at that instant.
    # That's a normal, encouraged part of solving this task -- it must not
    # take down the measurement loop, so errored requests are dropped from
    # this pass (like a cancellation) rather than raised.
    raw_outcomes = await asyncio.gather(*tasks, return_exceptions=True)
    errored = [o for o in raw_outcomes if isinstance(o, Exception)]
    outcomes = [o for o in raw_outcomes if not isinstance(o, Exception)]

    completed = [o for o in outcomes if not o["cancelled"]]
    cancelled = [o for o in outcomes if o["cancelled"]]

    mismatches = []
    for o in completed:
        expected = model.expected_tokens(o["prompt_tokens"], o["max_new_tokens"])
        if o["tokens"] != expected:
            mismatches.append({"request_id": o["request_id"], "expected": expected, "actual": o["tokens"]})

    latencies = sorted(o["elapsed_sec"] for o in completed)
    p50 = statistics.median(latencies) if latencies else None
    p99 = latencies[max(0, int(len(latencies) * 0.99) - 1)] if latencies else None

    latency_ok = p99 is not None and p99 <= LATENCY_SLA_SEC
    correctness_ok = len(mismatches) == 0
    ok = latency_ok and correctness_ok and len(completed) > 0

    return {
        "ok": ok,
        "correctness_ok": correctness_ok,
        "latency_ok": latency_ok,
        "total_requests": NUM_REQUESTS,
        "completed_count": len(completed),
        "cancelled_count": len(cancelled),
        "errored_count": len(errored),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:20],
        "p50_latency_sec": p50,
        "p99_latency_sec": p99,
        "latency_sla_sec": LATENCY_SLA_SEC,
    }


async def main() -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    open(f"{RESULTS_DIR}/loadgen.log", "a").close()

    async with aiohttp.ClientSession() as session:
        log("waiting for server health")
        await wait_for_healthy(session)
        log("server healthy; starting continuous measurement passes")

        start = time.monotonic()
        pass_num = 0
        while time.monotonic() - start < MAX_WAIT_SEC:
            pass_num += 1
            try:
                result = await run_pass(session)
            except Exception as exc:
                # A pass can hit more than just per-request errors (e.g. the
                # whole connection pool drops during a restart); never let a
                # single bad pass end continuous measurement.
                log(f"pass {pass_num}: pass-level error ({exc!r}), retrying")
                continue

            if result["completed_count"] < MIN_COMPLETED_TO_PERSIST:
                # Too few completions to be a real full pass (server down
                # for a restart, or teardown killed connections mid-pass) --
                # keep whatever the last real measurement showed rather than
                # clobbering it with a truncated, mostly-errored snapshot
                # that happens to land last.
                log(f"pass {pass_num}: too few completions "
                    f"({result['completed_count']} < {MIN_COMPLETED_TO_PERSIST}, "
                    f"errored={result['errored_count']}), not persisted")
                await asyncio.sleep(0.5)
                continue

            with open(f"{RESULTS_DIR}/results.json", "w") as f:
                json.dump(result, f, indent=2)
            with open(f"{RESULTS_DIR}/measure.log", "w") as f:
                f.write(json.dumps(result, indent=2) + "\n")

            log(f"pass {pass_num}: ok={result['ok']} mismatches={result['mismatch_count']} "
                f"errored={result['errored_count']} p99={result['p99_latency_sec']} sla={LATENCY_SLA_SEC}")

        log("MAX_WAIT_SEC elapsed; stopping")


if __name__ == "__main__":
    asyncio.run(main())
