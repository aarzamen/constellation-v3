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
    return engine.search(query, top_k)


@mcp.tool()
def get_conversation(conversation_id: str) -> dict:
    """Retrieve the full text of a specific conversation by ID.

    Args:
        conversation_id: UUID of the conversation.

    Returns:
        Full conversation with all messages.
    """
    return engine.get_conversation(conversation_id)


if __name__ == '__main__':
    mcp.run()
