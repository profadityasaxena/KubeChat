from __future__ import annotations

import os, uuid, pathlib, json, unicodedata
from typing import List, Dict, Any, Optional

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, PointStruct,
    Filter, FieldCondition, MatchValue,
)

# ---------- UTF-8 SANITIZE ----------
def sanitize(s: str) -> str:
    s = unicodedata.normalize("NFC", s or "")
    # drop any invalid UTF-8 / surrogate code points
    return s.encode("utf-8", "ignore").decode("utf-8", "ignore")

# ---------- Config ----------
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
COLLECTION = os.environ.get("COLLECTION_NAME", "docs")
EMBED_MODEL = os.environ.get("EMBEDDING_MODEL", "nomic-embed-text")
GEN_MODEL = os.environ.get("GENERATION_MODEL", "llama3:8b-instruct-q4_K_M")
DOCS_DIR = os.environ.get("DOCS_DIR", "/app/docs")
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "800"))
CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", "200"))

app = FastAPI(title="RAG API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ---------- Utilities ----------
async def ollama_embed(client: httpx.AsyncClient, text: str) -> List[float]:
    text = sanitize(text)
    r = await client.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
    )
    r.raise_for_status()
    return r.json()["embedding"]

async def ensure_collection(qc: QdrantClient, client: httpx.AsyncClient) -> int:
    dim = len(await ollama_embed(client, "dimension probe"))
    names = [c.name for c in qc.get_collections().collections]
    if COLLECTION not in names:
        qc.recreate_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
        )
    return dim

def read_text(path: pathlib.Path) -> str:
    text = ""
    if path.suffix.lower() in [".txt", ".md"]:
        text = path.read_text(encoding="utf-8", errors="ignore")
    elif path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            r = PdfReader(str(path))
            text = "\n".join(p.extract_text() or "" for p in r.pages)
        except Exception as e:
            text = f"[PDF parse error: {e}]"
    return sanitize(text)

def chunk_text(s: str, size: int, overlap: int) -> List[str]:
    s = sanitize(" ".join(s.split()))
    if not s:
        return []
    chunks, start = [], 0
    while start < len(s):
        end = min(start + size, len(s))
        chunks.append(s[start:end])
        if end == len(s):
            break
        start = max(0, end - overlap)
    return chunks

# ---------- Models ----------
class IngestResponse(BaseModel):
    files_indexed: int
    chunks_indexed: int

class ChatRequest(BaseModel):
    question: str
    top_k: int = 5
    num_predict: int = 256
    num_gpu: int = 32
    # optional filters by file path
    path_exact: Optional[str] = None
    path_contains: Optional[str] = None

class ChatResponse(BaseModel):
    answer: str
    sources: List[Dict[str, Any]]

# ---------- Routes ----------
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/ingest", response_model=IngestResponse)
async def ingest():
    docs_path = pathlib.Path(DOCS_DIR)
    files = [
        p for p in docs_path.rglob("*")
        if p.is_file() and p.suffix.lower() in [".txt", ".md", ".pdf"]
    ]
    if not files:
        return IngestResponse(files_indexed=0, chunks_indexed=0)

    async with httpx.AsyncClient(timeout=120) as client:
        qc = QdrantClient(url=QDRANT_URL)
        await ensure_collection(qc, client)

        total_chunks = 0
        for f in files:
            raw = read_text(f)
            if not raw:
                continue
            chunks = chunk_text(raw, CHUNK_SIZE, CHUNK_OVERLAP)
            points = []
            for i, ch in enumerate(chunks):
                emb = await ollama_embed(client, ch)
                points.append(PointStruct(
                    id=uuid.uuid4().hex,
                    vector=emb,
                    payload={
                        "path": sanitize(str(f.relative_to(docs_path))),
                        "chunk_id": i,
                        "text": ch,  # already sanitized
                    },
                ))
            if points:
                qc.upsert(collection_name=COLLECTION, points=points)
                total_chunks += len(points)

    return IngestResponse(files_indexed=len(files), chunks_indexed=total_chunks)

# ... keep the code above unchanged ...

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    user_q = sanitize(req.question)

    async with httpx.AsyncClient(timeout=None) as client:
        qc = QdrantClient(url=QDRANT_URL)

        # 1) embed the question
        q_emb = await ollama_embed(client, user_q)

        # 2) optional server-side filter (EXACT match)
        q_filter = None
        if req.path_exact:
            q_filter = Filter(must=[
                FieldCondition(key="path", match=MatchValue(value=sanitize(req.path_exact)))
            ])

        # 3) search WITH server-side filter when present
        hits = qc.search(
            collection_name=COLLECTION,
            query_vector=q_emb,
            limit=req.top_k,
            with_payload=True,
            query_filter=q_filter,   # <— this is the key line
        )

        # 4) keep only non-empty texts (no more local path filtering needed for exact)
        contexts, sources = [], []
        for h in hits:
            payload = h.payload or {}
            txt = sanitize(payload.get("text", "") or "")
            if not txt:
                continue
            contexts.append(txt)
            sources.append({
                "path": sanitize(str(payload.get("path") or "")),
                "chunk_id": payload.get("chunk_id"),
                "score": h.score,
            })

        if not contexts:
            return ChatResponse(answer="I don't know based on the indexed documents.", sources=[])

        system = (
            "You are a helpful assistant. Use the provided CONTEXT strictly. "
            "If the answer isn't in the context, say you don't know."
        )
        context_block = "\n\n".join(f"Snippet {i+1}:\n{c}" for i, c in enumerate(contexts))
        prompt = (
            f"SYSTEM:\n{system}\n\nCONTEXT:\n{context_block}\n\n"
            f"USER QUESTION:\n{user_q}\n\nASSISTANT:"
        )

        resp = await client.post(
            f"{OLLAMA_BASE}/api/generate",
            json={"model": GEN_MODEL, "prompt": prompt,
                  "options": {"num_predict": req.num_predict, "num_gpu": req.num_gpu},
                  "stream": False},
        )
        resp.raise_for_status()
        answer = sanitize(resp.json().get("response", "")).strip()
        return ChatResponse(answer=answer, sources=sources)
