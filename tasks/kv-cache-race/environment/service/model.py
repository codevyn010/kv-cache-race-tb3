"""Deterministic stand-in for a real autoregressive model.

Next-token is a pure, seeded hash-chain function of the running hidden
state. This keeps ground-truth output computable in closed form (no GPU,
no floating-point nondeterminism) so the serving stack's concurrency
correctness -- not model quality -- is what's under test. This file is
intentionally duplicated (not imported across container boundaries) in
environment/loadgen/model.py so both sides compute identically without the
agent's container needing network/verifier access.
"""

import hashlib

SEED = 133742
VOCAB_SIZE = 100_000


def _h(*parts: object) -> int:
    data = "|".join(str(p) for p in (SEED, *parts)).encode()
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


def initial_state(prompt_tokens: list[int]) -> int:
    state = SEED
    for i, tok in enumerate(prompt_tokens):
        state = _h(state, tok, i)
    return state


def next_token(state: int, position: int) -> int:
    return _h(state, position, "tok") % VOCAB_SIZE


def advance_state(state: int, token: int) -> int:
    return _h(state, token, "adv")
