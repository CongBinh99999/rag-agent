"""Central config: env + clients + prompt loader. One place only."""
import os
from pathlib import Path
from functools import lru_cache

from dotenv import load_dotenv
from pymongo import MongoClient
from qdrant_client import QdrantClient
import redis
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"

GOOGLE_API_KEY = os.environ["GOOGLE_API_KEY"]
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.1-flash-lite")
EMBED_MODEL = os.getenv("EMBED_MODEL", "gemini-embedding-001")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27018")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
NEO4J_URL = os.getenv("NEO4J_URL", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password123")
ORG_ID = os.getenv("ORG_ID", "default")
COLLECTION = os.getenv("QDRANT_COLLECTION", "knowledge")
# Retrieval knobs (one place to tune for eval Plan A). int() so .env overrides work.
TOP_K = int(os.getenv("TOP_K", "4"))
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "400"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "50"))
# B2: LLM filter sub-agent after retrieval (drops chunks irrelevant to the query).
FILTER = os.getenv("FILTER", "1") == "1"
# Rerank: fetch RERANK_POOL hits by cosine, cross-encoder re-ranks, cut to TOP_K.
# Fixes the "answer chunk buried at rank 3" case bi-encoder cosine can't see.
RERANK = os.getenv("RERANK", "1") == "1"
RERANK_POOL = int(os.getenv("RERANK_POOL", "12"))


@lru_cache
def mongo() -> MongoClient:
    return MongoClient(MONGO_URL)


@lru_cache
def qdrant() -> QdrantClient:
    return QdrantClient(url=QDRANT_URL)


@lru_cache
def rds() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


@lru_cache
def neo4j():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(NEO4J_URL, auth=(NEO4J_USER, NEO4J_PASSWORD))


@lru_cache
def llm() -> ChatGoogleGenerativeAI:
    return ChatGoogleGenerativeAI(model=LLM_MODEL, google_api_key=GOOGLE_API_KEY)


@lru_cache
def embeddings() -> GoogleGenerativeAIEmbeddings:
    return GoogleGenerativeAIEmbeddings(model=EMBED_MODEL, google_api_key=GOOGLE_API_KEY)


@lru_cache
def ranker():
    # flashrank: ONNX cross-encoder, no torch. Default model ~4MB, downloaded once.
    from flashrank import Ranker
    return Ranker()


def db():
    return mongo()["ragagent"]


def load_prompt(name: str) -> str:
    return (PROMPTS / name).read_text(encoding="utf-8")


def ping() -> dict:
    """Check all 4 stores reachable. Run: python -m src.config"""
    status = {}
    try:
        mongo().admin.command("ping"); status["mongo"] = "ok"
    except Exception as e:
        status["mongo"] = f"FAIL {e}"
    try:
        qdrant().get_collections(); status["qdrant"] = "ok"
    except Exception as e:
        status["qdrant"] = f"FAIL {e}"
    try:
        rds().ping(); status["redis"] = "ok"
    except Exception as e:
        status["redis"] = f"FAIL {e}"
    try:
        neo4j().verify_connectivity(); status["neo4j"] = "ok"
    except Exception as e:
        status["neo4j"] = f"FAIL {e}"
    return status


if __name__ == "__main__":
    for k, v in ping().items():
        print(f"{k:8} {v}")
