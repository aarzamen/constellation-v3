"""MCP memory server for Constellation V3.

Standalone process — Claude Desktop launches this as a subprocess via stdio.
Loads its own copy of embeddings and conversation index from disk.
"""

import os
import sys

# Suppress tokenizer fork warning
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

# Ensure project root is on sys.path regardless of cwd
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Set DATA_DIR relative to project root
os.environ.setdefault('CONSTELLATION_DATA_DIR', os.path.join(PROJECT_ROOT, 'data'))

from fastmcp import FastMCP
from core.logger import get_logger
from server.api import SearchEngine

logger = get_logger('mcp')

mcp = FastMCP("Constellation Memory")
engine = SearchEngine()


@mcp.tool()
def search_conversations(query: str, top_k: int = 5, provider: str = None) -> list:
    """Search conversation history by semantic similarity.

    Searches across all ingested AI providers (Claude, ChatGPT) in a unified
    semantic embedding space. Conversations cluster by topic, not by provider.
    Results include a 'provider' field ('claude' or 'chatgpt') indicating
    which AI the conversation was with.

    Args:
        query: Natural language description of what you're looking for.
        top_k: Number of results to return (default 5).
        provider: Optional filter — 'claude', 'chatgpt', or None for all.

    Returns:
        List of matching conversations with title, date, relevance score,
        provider, and message excerpts.
    """
    if not query or not query.strip():
        return [{"error": "Search query cannot be empty"}]
    try:
        top_k = int(top_k)
        return engine.search(query, top_k, provider=provider)
    except Exception as e:
        logger.error("search_conversations failed", extra={'error': str(e)})
        return [{"error": str(e)}]


@mcp.tool()
def get_conversation(conversation_id: str) -> dict:
    """Retrieve the full text of a specific conversation by ID.

    Works for conversations from any ingested provider (Claude or ChatGPT).
    The response includes a 'provider' field indicating the source.

    Args:
        conversation_id: UUID of the conversation.

    Returns:
        Full conversation with all messages and provider info.
    """
    if not conversation_id or not conversation_id.strip():
        return {"error": "conversation_id cannot be empty"}
    try:
        return engine.get_conversation(conversation_id)
    except Exception as e:
        logger.error("get_conversation failed", extra={'error': str(e)})
        return {"error": str(e)}


@mcp.tool()
def add_conversation_note(conversation_id: str, note_text: str) -> dict:
    """Append a note to a conversation. Notes are stored in a sidecar file
    and survive pipeline rebuilds (re-embedding, re-clustering).

    Args:
        conversation_id: UUID of the conversation.
        note_text: The string content of the note.

    Returns:
        Status dict with the new note and current notes list.
    """
    if not conversation_id or not conversation_id.strip():
        return {"error": "conversation_id cannot be empty"}
    if not note_text or not note_text.strip():
        return {"error": "note_text cannot be empty"}

    try:
        return engine.add_note(conversation_id, note_text)
    except Exception as e:
        logger.error("add_conversation_note failed", extra={'error': str(e)})
        return {"error": str(e)}


@mcp.tool()
def delete_conversation_note(conversation_id: str, note_id: str) -> dict:
    """Delete a specific note from a conversation by note ID.

    Args:
        conversation_id: UUID of the conversation.
        note_id: The 8-character hex ID of the note to delete.

    Returns:
        Status dict with remaining notes list, or error if not found.
    """
    if not conversation_id or not conversation_id.strip():
        return {"error": "conversation_id cannot be empty"}
    if not note_id or not note_id.strip():
        return {"error": "note_id cannot be empty"}

    try:
        return engine.delete_note(conversation_id, note_id)
    except Exception as e:
        logger.error("delete_conversation_note failed", extra={'error': str(e)})
        return {"error": str(e)}


@mcp.tool()
def list_conversations(offset: int = 0, limit: int = 20, sort_by: str = "date",
                       provider: str = None) -> dict:
    """Browse conversations with pagination.

    Lists conversations from all ingested providers. Each result includes
    a 'provider' field. Use sort_by to order results.

    Args:
        offset: Starting index for pagination (default 0).
        limit: Number of results to return, max 50 (default 20).
        sort_by: Sort order — "date" (newest first), "title" (alphabetical),
                 or "message_count" (most messages first).
        provider: Optional filter — 'claude', 'chatgpt', or None for all.

    Returns:
        Dict with conversations list, total count, offset, and limit.
    """
    try:
        return engine.list_conversations(offset=offset, limit=limit,
                                         sort_by=sort_by, provider=provider)
    except Exception as e:
        logger.error("list_conversations failed", extra={'error': str(e)})
        return {"error": str(e)}


@mcp.tool()
def get_stats() -> dict:
    """Get summary statistics about the conversation memory index.

    Returns total counts, date range, embedding info, and per-provider
    breakdown (e.g., {'claude': 975, 'chatgpt': 1247}).

    Returns:
        Dict with total conversations, messages, date range, embedding info,
        and providers breakdown.
    """
    try:
        return engine.get_stats()
    except Exception as e:
        logger.error("get_stats failed", extra={'error': str(e)})
        return {"error": str(e)}


if __name__ == '__main__':
    # Pre-load embedding model to avoid cold-start latency on first search
    try:
        engine.load()
        import time
        t0 = time.time()
        engine._ensure_embedder()
        # Trigger actual model load by accessing the model
        if engine.embedder:
            _ = engine.embedder.model
        logger.info("Model pre-loaded", extra={'duration_ms': round((time.time() - t0) * 1000, 1)})
    except Exception as e:
        logger.warning(f"Could not pre-load model: {e}")

    mcp.run()
