class TokenCapExceededException(Exception):
    pass

class KillSwitch:
    def __init__(self, limit: int = 20000):
        self.limit = limit
        self._usage_by_doc = {}

    def check_and_add(self, doc_id: str, tokens_in: int, tokens_out: int):
        current = self._usage_by_doc.get(doc_id, 0)
        total_requested = tokens_in + tokens_out
        
        if current + total_requested > self.limit:
            raise TokenCapExceededException(
                f"Token cap exceeded for doc {doc_id}. Limit: {self.limit}, "
                f"Current: {current}, Requested: {total_requested}"
            )
        
        self._usage_by_doc[doc_id] = current + total_requested
        return self._usage_by_doc[doc_id]
