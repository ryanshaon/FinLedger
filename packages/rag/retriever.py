from typing import List, Dict, Any
from rag.indexer import Indexer
from rag.isolation import build_client_namespace

class Retriever:
    def __init__(self, indexer: Indexer):
        self.indexer = indexer

    def retrieve(self, client_id: str, gstin: str, vendor_name: str, descriptions: List[str], hsn: str, amount_band: str, doc_type: str, top_k: int = 8) -> List[Dict[str, Any]]:
        # This will simulate an embedding search by just fetching all items from the correct namespace and returning a slice
        # The key constraint is hard isolation by client_id.
        namespace = build_client_namespace(client_id)
        
        # In a real app we'd build a query string and do cosine similarity over the vector DB namespace
        # query_str = f"{gstin} {vendor_name} {' '.join(descriptions)} {hsn} {amount_band} {doc_type}"
        
        chunks = self.indexer.store.get(namespace, [])
        
        # simple mock filtering based on gstin if it's in the content
        # otherwise return anything (since it's mocked)
        scored_chunks = []
        for c in chunks:
            score = 1.0 if gstin in c["content"] else 0.5
            scored_chunks.append((score, c))
            
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        
        # Return top_k
        return [c for score, c in scored_chunks[:top_k]]
