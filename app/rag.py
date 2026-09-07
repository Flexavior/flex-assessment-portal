"""Course material vector index: Chroma on disk.

Layers: slides (primary, compact) then textbook (detail / cohesion).
Does not delete existing index files.
"""
import gc
import os
import threading

from llama_index.embeddings.ollama import OllamaEmbedding

from app.config import (
    CHROMA_PATH,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBED_BATCH_SIZE,
    EMBED_MODEL,
    INDEX_INCLUDE_TEXTBOOK,
    OLLAMA_URL,
    SLIDES_DIR,
    SLIDES_TOP_K,
    TEXTBOOK_DIR,
    TEXTBOOK_TOP_K,
)
from app.briefs import load_grading_policy, read_file

_index = None
_status = "idle"
_status_detail = ""
_lock = threading.Lock()

LAYERS = (
    ("slides", SLIDES_DIR),
    ("textbook", TEXTBOOK_DIR),
)


class CourseIndex:
    def __init__(self, collections, embed_model):
        self.collections = collections
        self.embed_model = embed_model

    def count(self, layer=None):
        if layer:
            col = self.collections.get(layer)
            return col.count() if col else 0
        return sum(c.count() for c in self.collections.values())


def get_index():
    return _index


def get_index_status():
    with _lock:
        return {"state": _status, "detail": _status_detail, "ready": _index is not None}


def _set_status(state, detail=""):
    global _status, _status_detail
    with _lock:
        _status = state
        _status_detail = detail
    print(f"--- Index: {state}" + (f" — {detail}" if detail else "") + " ---")


def _embed_model():
    return OllamaEmbedding(model_name=EMBED_MODEL, base_url=OLLAMA_URL)


def _chroma_client():
    import chromadb

    os.makedirs(CHROMA_PATH, exist_ok=True)
    return chromadb.PersistentClient(path=CHROMA_PATH)


def _get_or_create_collection(client, name):
    try:
        return client.get_collection(name)
    except Exception:
        return client.create_collection(name=name, metadata={"hnsw:space": "cosine"})


def _chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    text = (text or "").strip()
    if not text:
        return []
    step = max(size - overlap, 1)
    chunks = []
    start = 0
    while start < len(text):
        piece = text[start : start + size].strip()
        if piece:
            chunks.append(piece)
        start += step
    return chunks


def _load_layer_chunks(layer, directory):
    if not os.path.isdir(directory):
        return []
    names = sorted(
        n for n in os.listdir(directory)
        if os.path.isfile(os.path.join(directory, n)) and not n.startswith(".")
    )
    chunks = []
    print(f"Reading {layer} from {directory} ({len(names)} files)")
    for name in names:
        path = os.path.join(directory, name)
        try:
            text = read_file(path)
        except Exception as exc:
            print(f"Skipping {path}: {exc}")
            continue
        for i, piece in enumerate(_chunk_text(text)):
            chunks.append({
                "id": f"{layer}/{name}::{i}",
                "text": piece,
                "source": name,
                "layer": layer,
            })
    return chunks


def _embed_batch(embed_model, texts):
    if hasattr(embed_model, "get_text_embedding_batch"):
        return embed_model.get_text_embedding_batch(texts, show_progress=False)
    return [embed_model.get_text_embedding(t) for t in texts]


def _fill_collection(collection, layer, directory, embed_model):
    if collection.count() > 0:
        print(f"Keeping existing {layer} collection ({collection.count()} chunks)")
        return
    chunks = _load_layer_chunks(layer, directory)
    if not chunks:
        print(f"No files for layer {layer}")
        return
    total = len(chunks)
    _set_status("building", f"Embedding {total} {layer} chunks")
    for start in range(0, total, EMBED_BATCH_SIZE):
        batch = chunks[start : start + EMBED_BATCH_SIZE]
        vectors = _embed_batch(embed_model, [c["text"] for c in batch])
        collection.add(
            ids=[c["id"] for c in batch],
            embeddings=vectors,
            documents=[c["text"] for c in batch],
            metadatas=[{"source": c["source"], "layer": c["layer"]} for c in batch],
        )
        print(f"{layer}: {min(start + EMBED_BATCH_SIZE, total)}/{total}")
    gc.collect()


def initialize_index():
    """Load or build Chroma collections for slides and textbook. Never deletes index files."""
    global _index
    _set_status("starting", "Opening Chroma store")
    try:
        embed_model = _embed_model()
        client = _chroma_client()
        collections = {}
        layers = [("slides", SLIDES_DIR)]
        if INDEX_INCLUDE_TEXTBOOK:
            layers.append(("textbook", TEXTBOOK_DIR))
        for layer, directory in layers:
            col = _get_or_create_collection(client, layer)
            _fill_collection(col, layer, directory, embed_model)
            collections[layer] = col
        _index = CourseIndex(collections, embed_model)
        parts = [f"{name}={col.count()}" for name, col in collections.items()]
        _set_status("ready", "Chroma layers " + ", ".join(parts))
        return _index
    except Exception as exc:
        _index = None
        _set_status("unavailable", str(exc))
        print(f"--- Index unavailable ({exc}). Grading will use assignment + rubric only. ---")
        return None


def initialize_index_background():
    thread = threading.Thread(target=initialize_index, name="chroma-index", daemon=True)
    thread.start()
    return thread


def rebuild_layer(layer: str):
    """Recreate one Chroma collection from the live slides or textbook folder."""
    global _index
    if layer == "slides":
        directory = SLIDES_DIR
    elif layer == "textbook":
        directory = TEXTBOOK_DIR
    else:
        raise ValueError("layer must be slides or textbook")
    _set_status("building", f"Rebuilding {layer} collection")
    embed_model = _embed_model()
    client = _chroma_client()
    try:
        client.delete_collection(layer)
    except Exception:
        pass
    collection = client.create_collection(name=layer, metadata={"hnsw:space": "cosine"})
    _fill_collection(collection, layer, directory, embed_model)
    if _index is None:
        collections = {layer: collection}
        if layer != "slides":
            collections["slides"] = _get_or_create_collection(client, "slides")
        _index = CourseIndex(collections, embed_model)
    else:
        _index.collections[layer] = collection
        _index.embed_model = embed_model
    parts = [f"{name}={col.count()}" for name, col in _index.collections.items()]
    _set_status("ready", "Chroma layers " + ", ".join(parts))
    return {"layer": layer, "count": collection.count()}


def _query_embedding(embed_model, text):
    if hasattr(embed_model, "get_query_embedding"):
        return embed_model.get_query_embedding(text)
    return embed_model.get_text_embedding(text)


def retrieve_layer(index_instance, query_text, layer, top_k):
    if not index_instance:
        return ""
    collection = index_instance.collections.get(layer)
    if not collection or collection.count() == 0 or top_k < 1:
        return ""
    try:
        vector = _query_embedding(index_instance.embed_model, query_text)
        result = collection.query(
            query_embeddings=[vector],
            n_results=min(top_k, collection.count()),
        )
        docs = (result.get("documents") or [[]])[0]
        return "\n\n[Reference Snippet]\n".join(d.strip() for d in docs if d)
    except Exception as exc:
        print(f"Retrieval failed ({layer}): {exc}")
        return ""


def retrieve_context(index_instance, query_text, top_k=None):
    """Backward compatible: slides then textbook concatenated."""
    k = top_k if top_k is not None else SLIDES_TOP_K
    slides = retrieve_layer(index_instance, query_text, "slides", k)
    book = retrieve_layer(index_instance, query_text, "textbook", TEXTBOOK_TOP_K)
    parts = [p for p in (slides, book) if p]
    return "\n\n".join(parts) if parts else "No reference context available."


def retrieve_grading_context(index_instance, student_text, assignment_text="", rubric_text=""):
    sections = []
    if assignment_text:
        sections.append(f"[ASSIGNMENT QUESTIONS]\n{assignment_text}")
    if rubric_text:
        sections.append(f"[GRADING RUBRIC]\n{rubric_text}")

    policy = load_grading_policy()
    if policy:
        sections.append(f"[COURSE MATERIAL POLICY]\n{policy}")

    course_query = f"Course material relevant to: {student_text[:300]}"
    slides = retrieve_layer(index_instance, course_query, "slides", SLIDES_TOP_K)
    if slides:
        sections.append(f"[COURSE SLIDES — PRIMARY FACTS]\n{slides[:6000]}")
    textbook = retrieve_layer(index_instance, course_query, "textbook", TEXTBOOK_TOP_K)
    if textbook:
        sections.append(
            f"[COURSE TEXTBOOK — DETAIL AND REASONING]\n{textbook[:6000]}"
        )
    return "\n\n".join(sections)
