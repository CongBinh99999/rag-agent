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


from langchain_core.messages import SystemMessage
from langchain_community.chat_message_histories import RedisChatMessageHistory
from src import config, graph


async def _ingest_files(files) -> None:
    for f in files:
        n = await cl.make_async(ingest.ingest)(f.path)
        await cl.Message(content=f"Đã nạp `{os.path.basename(f.path)}` — {n} đoạn.").send()

        # Extract graph triples and write to Neo4j
        try:
            triples = await cl.make_async(graph.ingest_doc_graph)(f.path)
            if triples > 0:
                await cl.Message(content=f"Đã trích xuất và nạp {triples} quan hệ vào GraphDB.").send()
        except Exception as e:
            print(f"Error extracting graph from uploaded file: {e}")
        
        # Inject system message to notify LLM that database state has changed
        hist = RedisChatMessageHistory(cl.context.session.id, url=config.REDIS_URL)
        hist.add_message(
            SystemMessage(
                content=f"Hệ thống: Tài liệu `{os.path.basename(f.path)}` đã được tải lên và lập chỉ mục thành công vào cơ sở dữ liệu. Nếu người dùng hỏi về tài liệu này hoặc hỏi lại câu hỏi trước đó, bạn cần sử dụng lại công cụ tìm kiếm (`knowledge_agent`) để tra cứu thông tin mới."
            )
        )


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
