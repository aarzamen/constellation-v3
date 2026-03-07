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
from server.api import SearchEngine

mcp = FastMCP("Constellation Memory")
engine = SearchEngine()


@mcp.tool()
def search_conversations(query: str, top_k: int = 5) -> list:
    """Search conversation history by semantic similarity.

    Args:
        query: Natural language description of what you're looking for.
        top_k: Number of results to return (default 5).

    Returns:
        List of matching conversations with title, date, relevance score,
        and message excerpts.
    """
    if not query or not query.strip():
        return [{"error": "Search query cannot be empty"}]
    try:
        top_k = int(top_k)
        return engine.search(query, top_k)
    except Exception as e:
        import sys
        print(f"Error in search_conversations: {e}", file=sys.stderr)
        return [{"error": str(e)}]


@mcp.tool()
def get_conversation(conversation_id: str) -> dict:
    """Retrieve the full text of a specific conversation by ID.

    Args:
        conversation_id: UUID of the conversation.

    Returns:
        Full conversation with all messages.
    """
    if not conversation_id or not conversation_id.strip():
        return {"error": "conversation_id cannot be empty"}
    try:
        return engine.get_conversation(conversation_id)
    except Exception as e:
        import sys
        print(f"Error in get_conversation: {e}", file=sys.stderr)
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
        print(f"Error in add_conversation_note: {e}", file=sys.stderr)
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
        print(f"Error in delete_conversation_note: {e}", file=sys.stderr)
        return {"error": str(e)}


@mcp.tool()
def list_conversations(offset: int = 0, limit: int = 20, sort_by: str = "date") -> dict:
    """Browse conversations with pagination. Use this to discover conversations
    without needing a search query.

    Args:
        offset: Starting index for pagination (default 0).
        limit: Number of results to return, max 50 (default 20).
        sort_by: Sort order — "date" (newest first), "title" (alphabetical),
                 or "message_count" (most messages first).

    Returns:
        Dict with conversations list, total count, offset, and limit.
    """
    try:
        return engine.list_conversations(offset=offset, limit=limit, sort_by=sort_by)
    except Exception as e:
        print(f"Error in list_conversations: {e}", file=sys.stderr)
        return {"error": str(e)}


@mcp.tool()
def get_stats() -> dict:
    """Get summary statistics about the conversation memory index.

    Returns:
        Dict with total conversations, messages, date range, embedding info.
    """
    try:
        return engine.get_stats()
    except Exception as e:
        print(f"Error in get_stats: {e}", file=sys.stderr)
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
        print(f"Model loaded in {time.time() - t0:.1f}s", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Could not pre-load model: {e}", file=sys.stderr)

    mcp.run()
