"""Chainlit UI. Chat + file upload (ingest on the fly). History via Redis (thread = session id)."""
import os
import sys
from pathlib import Path

# ponytail: src/ + prompts/ live under app/; put app/ on path so `src.*` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import chainlit as cl

from src.agent import build_agent, run
from src import ingest


@cl.on_chat_start
async def start():
    # Rebuild per chat so freshly-saved rules load into the system prompt.
    cl.user_session.set("agent", build_agent())
    await cl.Message(
        content="Trợ lý knowledge nội bộ sẵn sàng. Hỏi câu hỏi, hoặc đính kèm file để nạp vào kho."
    ).send()


async def _ingest_files(files) -> None:
    for f in files:
        n = await cl.make_async(ingest.ingest)(f.path)
        await cl.Message(content=f"Đã nạp `{os.path.basename(f.path)}` — {n} đoạn.").send()


@cl.on_message
async def on_message(msg: cl.Message):
    if msg.elements:
        files = [e for e in msg.elements if getattr(e, "path", None)]
        if files:
            await _ingest_files(files)
            if not msg.content.strip():
                return

    agent = cl.user_session.get("agent")
    cb = cl.LangchainCallbackHandler()
    answer = await cl.make_async(run)(
        agent, cl.context.session.id, msg.content, {"callbacks": [cb]}
    )
    await cl.Message(content=answer).send()
