def build_client_namespace(client_id: str) -> str:
    """Enforce hard isolation by namespace."""
    if not client_id or not client_id.strip():
        raise ValueError("client_id cannot be empty")
    return f"client_{client_id.strip()}"
