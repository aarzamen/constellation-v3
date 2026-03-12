# Constellation Tools

Standalone utility scripts that support the Constellation workflow
but are NOT part of the server itself.

## export_fetcher.py

Automates requesting and locating conversation exports from AI providers.

```bash
# Check what exports are available
python tools/export_fetcher.py --status

# Get instructions for a specific provider
python tools/export_fetcher.py --provider anthropic
python tools/export_fetcher.py --provider openai
python tools/export_fetcher.py --provider google
python tools/export_fetcher.py --provider xai

# Show all provider instructions
python tools/export_fetcher.py --all
```
