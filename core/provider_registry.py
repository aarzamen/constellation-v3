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
        from core.notebooklm_parser import parse_notebooklm_export
        register_parser('notebooklm', parse_notebooklm_export)
    except ImportError:
        pass

    # 'gemini' = Gemini Apps activity log (MyActivity.html). The older
    # core.gemini_parser targets the AI Studio chunkedPrompt format, which is now
    # served by the dedicated 'aistudio' provider (carries model identity).
    try:
        from core.gemini_activity_parser import parse_gemini_activity
        register_parser('gemini', parse_gemini_activity)
    except ImportError:
        pass

    try:
        from core.aistudio_parser import parse_aistudio_export
        register_parser('aistudio', parse_aistudio_export)
    except ImportError:
        pass

    try:
        from core.grok_parser import parse_grok_export
        register_parser('grok', parse_grok_export)
    except ImportError:
        pass


_register_builtins()
