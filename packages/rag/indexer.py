from typing import Dict, Any, List
from rag.chunk_types import ChunkType
from rag.isolation import build_client_namespace

class Indexer:
    def __init__(self):
        # In-memory mock for now, storing chunks keyed by namespace
        self.store: Dict[str, List[Dict[str, Any]]] = {}

    def upsert(self, client_id: str, chunk_id: str, chunk_type: ChunkType, content: str, metadata: Dict[str, Any] = None):
        namespace = build_client_namespace(client_id)
        if namespace not in self.store:
            self.store[namespace] = []
        
        chunk = {
            "chunk_id": chunk_id,
            "chunk_type": chunk_type,
            "content": content,
            "metadata": metadata or {}
        }
        
        # Upsert logic (replace if chunk_id exists, else append)
        for i, existing in enumerate(self.store[namespace]):
            if existing["chunk_id"] == chunk_id:
                self.store[namespace][i] = chunk
                return chunk
        
        self.store[namespace].append(chunk)
        return chunk

    def get_namespace_chunks(self, client_id: str):
        namespace = build_client_namespace(client_id)
        return self.store.get(namespace, [])
