"""smolagents conversation loop for AI voice qualification."""


async def run_conversation(call_session: dict, kb_collection: str) -> dict:
    """Main agent loop: STT → smolagents reasoning → RAG retrieval → TTS response."""
    # ponytail: Will use smolagents ToolCallingAgent with Qdrant retrieval tool
    raise NotImplementedError
