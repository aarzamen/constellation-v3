#!/usr/bin/env python3
"""Constellation Export Fetcher — Automate AI conversation history downloads.

Fetches conversation exports from supported AI providers and saves them
to a local directory for Constellation ingestion.

Usage:
    python tools/export_fetcher.py --status
    python tools/export_fetcher.py --provider anthropic
    python tools/export_fetcher.py --provider openai
    python tools/export_fetcher.py --all

Supported providers:
    anthropic  — Requests data export via Claude account settings
    openai     — Requests data export via ChatGPT account settings
    xai        — Requests data export via accounts.x.ai/data
    google     — Copies from Google Drive AI Studio folder (local sync)

Note: Most providers require manual confirmation (email link, etc.).
This script automates the REQUEST, not the download itself, except
for Google AI Studio which syncs locally via Google Drive.
"""

import argparse
import glob
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def find_claude_exports():
    """Find Claude export files in ~/Downloads."""
    downloads = os.path.expanduser('~/Downloads')
    if not os.path.isdir(downloads):
        return []

    pattern = re.compile(r'data-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-batch-\d{4}')
    found = []

    for entry in os.listdir(downloads):
        full = os.path.join(downloads, entry)
        if pattern.match(entry):
            if entry.endswith('.zip') and os.path.isfile(full):
                size = os.path.getsize(full) / (1024 * 1024)
                mtime = datetime.fromtimestamp(os.path.getmtime(full))
                found.append(('zip', full, size, mtime))
            elif os.path.isdir(full):
                conv_path = os.path.join(full, 'conversations.json')
                if os.path.isfile(conv_path):
                    size = os.path.getsize(conv_path) / (1024 * 1024)
                    mtime = datetime.fromtimestamp(os.path.getmtime(conv_path))
                    found.append(('dir', conv_path, size, mtime))

    found.sort(key=lambda x: x[3], reverse=True)
    return found


def find_chatgpt_exports():
    """Find ChatGPT export files."""
    search_paths = [
        os.path.expanduser('~/Downloads'),
        os.path.expanduser('~/Desktop'),
    ]

    found = []
    for search_dir in search_paths:
        if not os.path.isdir(search_dir):
            continue

        for entry in os.listdir(search_dir):
            full = os.path.join(search_dir, entry)

            # Check for conversations.json from ChatGPT (typically large, 20MB+)
            if entry == 'conversations.json' and os.path.isfile(full):
                size = os.path.getsize(full) / (1024 * 1024)
                if size > 5:  # Likely ChatGPT if > 5MB
                    mtime = datetime.fromtimestamp(os.path.getmtime(full))
                    found.append(('json', full, size, mtime))

    # Check Google Drive
    gdrive_paths = glob.glob(os.path.expanduser(
        '~/Library/CloudStorage/GoogleDrive-*/My Drive/Google AI Studio/conversations.json'
    ))
    for gp in gdrive_paths:
        if os.path.isfile(gp):
            size = os.path.getsize(gp) / (1024 * 1024)
            mtime = datetime.fromtimestamp(os.path.getmtime(gp))
            found.append(('json', gp, size, mtime))

    found.sort(key=lambda x: x[3], reverse=True)
    return found


def find_gemini_studio_folder():
    """Find Google AI Studio folder via Google Drive sync."""
    patterns = [
        os.path.expanduser('~/Library/CloudStorage/GoogleDrive-*/My Drive/Google AI Studio'),
        os.path.expanduser('~/Google Drive/My Drive/Google AI Studio'),
        os.path.expanduser('~/GoogleDrive/My Drive/Google AI Studio'),
    ]

    for pattern in patterns:
        matches = glob.glob(pattern)
        for match in matches:
            if os.path.isdir(match):
                # Count chat files
                count = 0
                for entry in os.listdir(match):
                    filepath = os.path.join(match, entry)
                    if os.path.isfile(filepath):
                        _, ext = os.path.splitext(entry)
                        if not ext:  # extensionless files are likely chats
                            count += 1
                return match, count

    return None, 0


def find_grok_exports():
    """Find Grok export files in ~/Downloads."""
    downloads = os.path.expanduser('~/Downloads')
    if not os.path.isdir(downloads):
        return []

    found = []
    for entry in os.listdir(downloads):
        full = os.path.join(downloads, entry)
        if 'grok' in entry.lower() or 'xai' in entry.lower():
            if os.path.isfile(full):
                size = os.path.getsize(full) / (1024 * 1024)
                mtime = datetime.fromtimestamp(os.path.getmtime(full))
                found.append(('file', full, size, mtime))

    found.sort(key=lambda x: x[3], reverse=True)
    return found


def show_status():
    """Print status of all available exports."""
    print("\nExport Status:")
    print("=" * 60)

    # Claude
    claude = find_claude_exports()
    if claude:
        ftype, path, size, mtime = claude[0]
        print(f"  Claude:     \u2713 Found ({size:.1f}MB, {mtime.strftime('%Y-%m-%d')})")
        print(f"              {path}")
    else:
        print("  Claude:     \u2717 No export found")
        print("              Request at: https://claude.ai/settings")

    # ChatGPT
    chatgpt = find_chatgpt_exports()
    if chatgpt:
        ftype, path, size, mtime = chatgpt[0]
        print(f"  ChatGPT:    \u2713 Found ({size:.1f}MB, {mtime.strftime('%Y-%m-%d')})")
        print(f"              {path}")
    else:
        print("  ChatGPT:    \u2717 No export found")
        print("              Request at: https://chatgpt.com/#settings/DataControls")

    # Gemini
    gemini_path, gemini_count = find_gemini_studio_folder()
    if gemini_path:
        print(f"  Gemini:     \u2713 {gemini_count} chat files found in Google Drive")
        print(f"              {gemini_path}")
    else:
        print("  Gemini:     \u2717 No Google AI Studio folder found")
        print("              Sync via Google Drive for Desktop")

    # Grok
    grok = find_grok_exports()
    if grok:
        ftype, path, size, mtime = grok[0]
        print(f"  Grok:       \u2713 Found ({size:.1f}MB, {mtime.strftime('%Y-%m-%d')})")
        print(f"              {path}")
    else:
        print("  Grok:       \u2717 No export found")
        print("              Request at: https://accounts.x.ai/data")

    print()


def handle_provider(provider):
    """Print instructions for requesting export from a specific provider."""
    instructions = {
        'anthropic': {
            'name': 'Claude (Anthropic)',
            'url': 'https://claude.ai/settings',
            'steps': [
                'Go to claude.ai -> Settings -> Account',
                'Click "Request data export"',
                'Check your email for the download link',
                'Download the ZIP to ~/Downloads',
                'Run: python launch.py --source ~/Downloads/<export>.zip',
            ],
        },
        'openai': {
            'name': 'ChatGPT (OpenAI)',
            'url': 'https://chatgpt.com/#settings/DataControls',
            'steps': [
                'Go to chatgpt.com -> Settings -> Data controls',
                'Click "Export data"',
                'Check your email for the download link (may take hours)',
                'Download and extract the ZIP',
                'Run: python launch.py --chatgpt-source /path/to/conversations.json',
            ],
        },
        'xai': {
            'name': 'Grok (xAI)',
            'url': 'https://accounts.x.ai/data',
            'steps': [
                'Go to accounts.x.ai/data',
                'Click "Download account data"',
                'Download the ZIP to ~/Downloads',
                'Run: python launch.py --add-source grok /path/to/export.json',
            ],
        },
        'google': {
            'name': 'Gemini (Google AI Studio)',
            'url': 'https://aistudio.google.com/',
            'steps': [
                'Install Google Drive for Desktop (if not already)',
                'Ensure AI Studio folder is syncing to local drive',
                'Run: python launch.py --add-source gemini "/path/to/Google AI Studio"',
            ],
        },
    }

    info = instructions.get(provider)
    if not info:
        print(f"Unknown provider: {provider}")
        print(f"Available: {', '.join(instructions.keys())}")
        return

    print(f"\n{info['name']} Export Instructions:")
    print("=" * 60)
    print(f"URL: {info['url']}")
    print()
    for i, step in enumerate(info['steps'], 1):
        print(f"  {i}. {step}")
    print()


def main():
    parser = argparse.ArgumentParser(
        description='Constellation Export Fetcher — locate and manage AI conversation exports'
    )
    parser.add_argument('--status', action='store_true',
                        help='Show status of all available exports')
    parser.add_argument('--provider', type=str,
                        help='Show instructions for a specific provider (anthropic, openai, xai, google)')
    parser.add_argument('--all', action='store_true',
                        help='Show instructions for all providers')
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.provider:
        handle_provider(args.provider)
    elif args.all:
        for p in ['anthropic', 'openai', 'google', 'xai']:
            handle_provider(p)
    else:
        show_status()


if __name__ == '__main__':
    main()
