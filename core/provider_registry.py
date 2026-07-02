"""Provider registry for multi-format conversation ingestion.

Maps provider names to parser functions so adding new providers is a matter
of dropping in a new parser module — not refactoring the pipeline.
"""


# Registry: provider_name -> parse function
_PARSERS = {}


def register_parser(provider, parse_fn):
    """Register a parser function for a provider."""
    _PARSERS[provider] = parse_fn


def get_parser(provider):
    """Get the parser function for a provider."""
    if provider not in _PARSERS:
        available = ', '.join(_PARSERS.keys())
        raise ValueError(f"Unknown provider '{provider}'. Available: {available}")
    return _PARSERS[provider]


def list_providers():
    """List all registered provider names."""
    return list(_PARSERS.keys())


def _register_builtins():
    """Auto-register built-in parsers on import."""
    from core.parser import parse_claude_export
    from core.chatgpt_parser import parse_chatgpt_export
    register_parser('claude', parse_claude_export)
    register_parser('chatgpt', parse_chatgpt_export)

    try:
        from core.claude_code_parser import parse_claude_code_export
        register_parser('claude-code', parse_claude_code_export)
    except ImportError:
        pass

    try:
        from core.gemini_parser import parse_gemini_export
        register_parser('gemini', parse_gemini_export)
    except ImportError:
        pass

    try:
        from core.grok_parser import parse_grok_export
        register_parser('grok', parse_grok_export)
    except ImportError:
        pass


_register_builtins()
