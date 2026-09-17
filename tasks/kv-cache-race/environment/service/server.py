import asyncio
import os

from aiohttp import web

from . import model
from .kv_pool import BlockPool

NUM_BLOCKS = int(os.environ.get("NUM_BLOCKS", "16"))
COMPUTE_SEC_PER_TOKEN = float(os.environ.get("COMPUTE_SEC_PER_TOKEN", "0.05"))
GENERATION_FILE = "/tmp/server_generation"

pool = BlockPool(NUM_BLOCKS)
active: dict[str, "Request"] = {}


class Request:
    __slots__ = ("id", "prompt_tokens", "max_new_tokens", "tokens", "block_id", "cancelled")

    def __init__(self, request_id: str, prompt_tokens: list[int], max_new_tokens: int):
        self.id = request_id
        self.prompt_tokens = prompt_tokens
        self.max_new_tokens = max_new_tokens
        self.tokens: list[int] = []
        self.block_id: int | None = None
        self.cancelled = False


async def run_request(req: Request) -> None:
    state = model.initial_state(req.prompt_tokens)
    req.block_id = await pool.allocate(req.id, state)
    if req.cancelled:
        # Cancelled while still queued for a block (block_id was still None,
        # so handle_cancel had nothing to free): we're the only one who
        # knows about this block, so we free it ourselves. No other
        # coroutine can observe req.block_id between the assignment above
        # and this check (no await in between), so this and handle_cancel's
        # own free are mutually exclusive -- never both, never neither.
        await pool.free(req.block_id)
        return
    try:
        for i in range(req.max_new_tokens):
            if req.cancelled:
                return
            block = pool.blocks[req.block_id]
            async with block.lock:
                cur_state = block.state
                await asyncio.sleep(COMPUTE_SEC_PER_TOKEN)
                token = model.next_token(cur_state, len(req.prompt_tokens) + i)
                block.state = model.advance_state(cur_state, token)
            req.tokens.append(token)
    finally:
        if not req.cancelled:
            await pool.free(req.block_id)
            active.pop(req.id, None)


async def handle_generate(request: web.Request) -> web.Response:
    body = await request.json()
    req = Request(body["request_id"], body["prompt_tokens"], body["max_new_tokens"])
    active[req.id] = req
    await run_request(req)
    return web.json_response({"request_id": req.id, "tokens": req.tokens, "cancelled": req.cancelled})


async def handle_cancel(request: web.Request) -> web.Response:
    request_id = request.match_info["request_id"]
    req = active.pop(request_id, None)
    if req is None:
        return web.json_response({"ok": False, "reason": "not_found"}, status=404)
    req.cancelled = True
    if req.block_id is not None:
        await pool.free(req.block_id)
    return web.json_response({"ok": True})


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_version(request: web.Request) -> web.Response:
    try:
        with open(GENERATION_FILE) as f:
            generation = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        generation = 0
    return web.json_response({"generation": generation})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/generate", handle_generate)
    app.router.add_post("/cancel/{request_id}", handle_cancel)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/version", handle_version)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host="0.0.0.0", port=8000, print=None)
