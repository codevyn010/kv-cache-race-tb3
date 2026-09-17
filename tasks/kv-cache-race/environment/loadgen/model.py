"""Ground-truth reference model -- must stay byte-for-byte identical to
service/model.py. Deliberately duplicated rather than imported: the loadgen
image is a separate build context from the agent-visible `service/` image,
so ground truth never ships inside the agent's own container.
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


def expected_tokens(prompt_tokens: list[int], max_new_tokens: int) -> list[int]:
    """Reference sequential generation -- no blocks, no concurrency, just
    the pure recurrence a correct server must reproduce under any amount
    of concurrent load."""
    state = initial_state(prompt_tokens)
    out = []
    for i in range(max_new_tokens):
        tok = next_token(state, len(prompt_tokens) + i)
        out.append(tok)
        state = advance_state(state, tok)
    return out
