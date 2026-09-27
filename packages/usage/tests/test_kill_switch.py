import pytest
from usage.kill_switch import KillSwitch, TokenCapExceededException

def test_under_budget():
    ks = KillSwitch(limit=20000)
    # First call uses 10K
    ks.check_and_add("doc-1", tokens_in=8000, tokens_out=2000)
    # Second call uses 5K
    ks.check_and_add("doc-1", tokens_in=4000, tokens_out=1000)
    
    # Check total is tracked correctly
    assert ks._usage_by_doc["doc-1"] == 15000

def test_exceed_budget():
    ks = KillSwitch(limit=20000)
    # First call uses 15K
    ks.check_and_add("doc-2", tokens_in=10000, tokens_out=5000)
    
    # Second call asks for 6K -> should fail
    with pytest.raises(TokenCapExceededException):
        ks.check_and_add("doc-2", tokens_in=4000, tokens_out=2000)
    
    # State should remain at 15K
    assert ks._usage_by_doc["doc-2"] == 15000

def test_different_docs_isolated():
    ks = KillSwitch(limit=20000)
    ks.check_and_add("doc-a", 15000, 0)
    ks.check_and_add("doc-b", 15000, 0) # Should pass because it's a different doc
    
    assert ks._usage_by_doc["doc-a"] == 15000
    assert ks._usage_by_doc["doc-b"] == 15000
