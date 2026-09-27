from enum import Enum

class ChunkType(str, Enum):
    LEDGER = "ledger"
    VENDOR = "vendor"
    POSTED_BILL = "posted_bill"
    MEMORY = "memory"
    POLICY = "policy"
    HARD_RULE = "hard_rule"
