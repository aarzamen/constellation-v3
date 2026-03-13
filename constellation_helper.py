#!/usr/bin/env python3
"""Constellation Helper — macOS control panel for managing Constellation servers.

Launch:
    python constellation_helper.py

Features:
- Start/stop REST and MCP servers as background subprocesses
- View server status, uptime, and corpus stats
- See and copy MCP connection URLs and config JSON
- Add data sources (Claude, ChatGPT, Gemini, Grok)
- Re-embed data with progress indicator
- Start/stop Cloudflare Tunnel for remote access
- Closing the helper does NOT stop the servers
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

# Ensure project root on sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

import customtkinter as ctk

from core.config import DATA_DIR, ensure_config, save_config

# --- Paths ---
VENV_PYTHON = os.path.join(PROJECT_ROOT, '.venv', 'bin', 'python')
LAUNCH_SCRIPT = os.path.join(PROJECT_ROOT, 'launch.py')
MCP_SCRIPT = os.path.join(PROJECT_ROOT, 'server', 'mcp_server.py')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.yaml')
PID_FILE = os.path.join(DATA_DIR, 'helper.pid')
LOG_DIR = os.path.join(DATA_DIR, 'logs')

# If .venv python doesn't exist, fall back to current interpreter
if not os.path.exists(VENV_PYTHON):
    VENV_PYTHON = sys.executable

# --- Design Tokens (Constellation design system) ---
COLORS = {
    'bg': '#0b0b14',
    'panel': '#12121f',
    'surface': '#1a1a2e',
    'border': '#2a2a3e',
    'text': '#e8e6f0',
    'text_secondary': '#9896a8',
    'text_dim': '#5c5a6e',
    'violet': '#7957d9',
    'violet_bright': '#9b7bef',
    'green': '#57d98b',
    'red': '#d95757',
    'amber': '#d9a857',
}

VERSION = '4.5'


# --- Server Probes ---

def probe_rest_server(port=8420):
    """Check if REST/visualization server is running."""
    try:
        req = urllib.request.urlopen(f'http://127.0.0.1:{port}/api/stats', timeout=2)
        return req.status == 200
    except Exception:
        return False


def probe_mcp_server(port=8000):
    """Check if MCP HTTP server is running."""
    try:
        req = urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2)
        return req.status == 200
    except Exception:
        return False


def fetch_stats(port=8420):
    """Fetch stats from REST server."""
    try:
        req = urllib.request.urlopen(f'http://127.0.0.1:{port}/api/stats', timeout=2)
        return json.loads(req.read())
    except Exception:
        return None


def fetch_server_info(port=8420):
    """Fetch server info from REST server (if endpoint exists)."""
    try:
        req = urllib.request.urlopen(f'http://127.0.0.1:{port}/api/server-info', timeout=2)
        return json.loads(req.read())
    except Exception:
        return None


# --- PID Management ---

def save_pids(rest_pid=None, mcp_pid=None):
    """Save server PIDs to file for reconnection on next helper launch."""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    data = {}
    if rest_pid:
        data['rest_pid'] = rest_pid
    if mcp_pid:
        data['mcp_pid'] = mcp_pid
    data['saved_at'] = time.time()
    with open(PID_FILE, 'w') as f:
        json.dump(data, f)


def load_pids():
    """Load saved PIDs."""
    if not os.path.exists(PID_FILE):
        return {}
    try:
        with open(PID_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {}


def clear_pids():
    """Remove PID file."""
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def is_pid_alive(pid):
    """Check if a process is still running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False
    except Exception:
        return False


# --- Helper App ---

class ConstellationHelper(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window setup
        self.title('Constellation Helper')
        self.geometry('600x780')
        self.minsize(550, 700)
        self.configure(fg_color=COLORS['bg'])

        # State
        self.rest_process = None
        self.mcp_process = None
        self.tunnel_process = None
        self.tunnel_url = None
        self._running = True
        self._start_time = None
        self._rest_running = False
        self._mcp_running = False

        # Try to reconnect to existing servers
        self._reconnect_existing()

        # Build UI
        self._build_ui()

        # Start background polling
        self._poll_thread = threading.Thread(target=self._poll_status, daemon=True)
        self._poll_thread.start()

        # Window close handler — does NOT stop servers
        self.protocol('WM_DELETE_WINDOW', self._on_closing)

    def _reconnect_existing(self):
        """Check if servers are already running (from a previous helper session)."""
        pids = load_pids()
        rest_pid = pids.get('rest_pid')
        mcp_pid = pids.get('mcp_pid')

        if rest_pid and is_pid_alive(rest_pid):
            self._rest_running = True
            self._start_time = pids.get('saved_at', time.time())
        if mcp_pid and is_pid_alive(mcp_pid):
            self._mcp_running = True

        # Also probe directly in case PIDs are stale but servers are up
        if not self._rest_running and probe_rest_server():
            self._rest_running = True
            self._start_time = time.time()
        if not self._mcp_running and probe_mcp_server():
            self._mcp_running = True

    def _build_ui(self):
        """Construct all UI elements."""
        # Main scrollable frame
        self.main_frame = ctk.CTkScrollableFrame(
            self, fg_color=COLORS['bg'],
            scrollbar_button_color=COLORS['border'],
            scrollbar_button_hover_color=COLORS['violet'],
        )
        self.main_frame.pack(fill='both', expand=True, padx=0, pady=0)

        # --- Header ---
        header = ctk.CTkFrame(self.main_frame, fg_color=COLORS['panel'], corner_radius=0)
        header.pack(fill='x', padx=0, pady=(0, 10))

        header_inner = ctk.CTkFrame(header, fg_color='transparent')
        header_inner.pack(fill='x', padx=16, pady=10)

        ctk.CTkLabel(
            header_inner, text='C\u2726nstellation Helper',
            font=('Helvetica', 20, 'bold'), text_color=COLORS['text'],
        ).pack(side='left')

        ctk.CTkLabel(
            header_inner, text=f'v{VERSION}',
            font=('Helvetica', 12), text_color=COLORS['text_dim'],
        ).pack(side='right')

        # --- Server Status Section ---
        self._build_section_label('SERVER STATUS')
        status_frame = self._build_card()

        # REST server row
        self.rest_dot = ctk.CTkLabel(status_frame, text='\u25cf', font=('Helvetica', 14),
                                      text_color=COLORS['text_dim'], width=20)
        self.rest_dot.grid(row=0, column=0, padx=(10, 5), pady=5, sticky='w')
        ctk.CTkLabel(status_frame, text='REST Server', font=('Helvetica', 13),
                     text_color=COLORS['text']).grid(row=0, column=1, padx=5, pady=5, sticky='w')
        self.rest_status = ctk.CTkLabel(status_frame, text='Checking...',
                                         font=('Helvetica', 12), text_color=COLORS['text_secondary'])
        self.rest_status.grid(row=0, column=2, padx=5, pady=5, sticky='w')
        self.rest_port_label = ctk.CTkLabel(status_frame, text='',
                                             font=('Helvetica', 12), text_color=COLORS['text_dim'])
        self.rest_port_label.grid(row=0, column=3, padx=5, pady=5, sticky='e')

        # MCP server row
        self.mcp_dot = ctk.CTkLabel(status_frame, text='\u25cf', font=('Helvetica', 14),
                                     text_color=COLORS['text_dim'], width=20)
        self.mcp_dot.grid(row=1, column=0, padx=(10, 5), pady=5, sticky='w')
        ctk.CTkLabel(status_frame, text='MCP Server', font=('Helvetica', 13),
                     text_color=COLORS['text']).grid(row=1, column=1, padx=5, pady=5, sticky='w')
        self.mcp_status = ctk.CTkLabel(status_frame, text='Checking...',
                                        font=('Helvetica', 12), text_color=COLORS['text_secondary'])
        self.mcp_status.grid(row=1, column=2, padx=5, pady=5, sticky='w')
        self.mcp_port_label = ctk.CTkLabel(status_frame, text='',
                                            font=('Helvetica', 12), text_color=COLORS['text_dim'])
        self.mcp_port_label.grid(row=1, column=3, padx=5, pady=5, sticky='e')

        # Tunnel row
        self.tunnel_dot = ctk.CTkLabel(status_frame, text='\u25cf', font=('Helvetica', 14),
                                        text_color=COLORS['text_dim'], width=20)
        self.tunnel_dot.grid(row=2, column=0, padx=(10, 5), pady=5, sticky='w')
        ctk.CTkLabel(status_frame, text='Tunnel', font=('Helvetica', 13),
                     text_color=COLORS['text']).grid(row=2, column=1, padx=5, pady=5, sticky='w')
        self.tunnel_status = ctk.CTkLabel(status_frame, text='Inactive',
                                           font=('Helvetica', 12), text_color=COLORS['text_secondary'])
        self.tunnel_status.grid(row=2, column=2, padx=5, pady=5, sticky='w')

        # Separator
        ctk.CTkFrame(status_frame, fg_color=COLORS['border'], height=1).grid(
            row=3, column=0, columnspan=4, sticky='ew', padx=10, pady=5)

        # Stats row
        self.stats_label = ctk.CTkLabel(
            status_frame, text='Loading stats...',
            font=('Helvetica', 12), text_color=COLORS['text_secondary'],
            anchor='w', justify='left',
        )
        self.stats_label.grid(row=4, column=0, columnspan=4, padx=10, pady=(2, 8), sticky='w')

        status_frame.columnconfigure(2, weight=1)

        # --- Data Sources Section ---
        self._build_section_label('DATA SOURCES')
        sources_frame = self._build_card()

        self.sources_labels = {}
        config = ensure_config()
        providers = [
            ('Claude', config.get('source', {}).get('path', '')),
            ('ChatGPT', config.get('sources', {}).get('chatgpt', {}).get('path', '') if isinstance(config.get('sources', {}).get('chatgpt'), dict) else ''),
            ('Gemini', config.get('sources', {}).get('gemini', {}).get('path', '') if isinstance(config.get('sources', {}).get('gemini'), dict) else ''),
            ('Grok', config.get('sources', {}).get('grok', {}).get('path', '') if isinstance(config.get('sources', {}).get('grok'), dict) else ''),
        ]

        for i, (name, path) in enumerate(providers):
            ctk.CTkLabel(sources_frame, text=f'{name}:', font=('Helvetica', 12, 'bold'),
                         text_color=COLORS['text'], width=70, anchor='e').grid(
                row=i, column=0, padx=(10, 5), pady=3, sticky='e')
            display = self._truncate_path(path) if path else 'Not configured'
            color = COLORS['text_secondary'] if path else COLORS['text_dim']
            lbl = ctk.CTkLabel(sources_frame, text=display, font=('Helvetica', 11),
                               text_color=color, anchor='w')
            lbl.grid(row=i, column=1, padx=5, pady=3, sticky='w')
            self.sources_labels[name.lower()] = lbl

        sources_frame.columnconfigure(1, weight=1)

        add_btn = ctk.CTkButton(
            sources_frame, text='Add Source...', font=('Helvetica', 12),
            fg_color=COLORS['surface'], hover_color=COLORS['border'],
            text_color=COLORS['text_secondary'], corner_radius=6, width=110, height=28,
            command=self._add_source_dialog,
        )
        add_btn.grid(row=len(providers), column=1, padx=5, pady=(5, 8), sticky='e')

        # --- MCP Connection Info ---
        self._build_section_label('MCP CONNECTION INFO')
        mcp_frame = self._build_card()

        # Claude Desktop (stdio)
        ctk.CTkLabel(mcp_frame, text='Claude Desktop (stdio):',
                     font=('Helvetica', 12, 'bold'), text_color=COLORS['text'],
                     anchor='w').pack(padx=10, pady=(8, 2), anchor='w')

        self.mcp_config_json = json.dumps({
            "mcpServers": {
                "constellation": {
                    "command": VENV_PYTHON,
                    "args": [MCP_SCRIPT]
                }
            }
        }, indent=2)

        config_display = ctk.CTkTextbox(
            mcp_frame, height=80, font=('Courier', 11),
            fg_color=COLORS['bg'], text_color=COLORS['text_secondary'],
            border_width=1, border_color=COLORS['border'], corner_radius=4,
        )
        config_display.pack(padx=10, pady=2, fill='x')
        config_display.insert('1.0', self.mcp_config_json)
        config_display.configure(state='disabled')

        ctk.CTkButton(
            mcp_frame, text='Copy JSON', font=('Helvetica', 11),
            fg_color=COLORS['violet'], hover_color=COLORS['violet_bright'],
            text_color='white', corner_radius=4, width=90, height=26,
            command=lambda: self._copy_to_clipboard(self.mcp_config_json, 'JSON copied!'),
        ).pack(padx=10, pady=(2, 8), anchor='e')

        # Claude.ai / iPhone (HTTP)
        ctk.CTkLabel(mcp_frame, text='Claude.ai / iPhone (HTTP):',
                     font=('Helvetica', 12, 'bold'), text_color=COLORS['text'],
                     anchor='w').pack(padx=10, pady=(8, 2), anchor='w')

        local_url_frame = ctk.CTkFrame(mcp_frame, fg_color='transparent')
        local_url_frame.pack(padx=10, fill='x')
        ctk.CTkLabel(local_url_frame, text='Local URL:',
                     font=('Helvetica', 11), text_color=COLORS['text_dim'],
                     width=75, anchor='e').pack(side='left')
        self.local_url = 'http://localhost:8000/mcp'
        ctk.CTkLabel(local_url_frame, text=self.local_url,
                     font=('Courier', 11), text_color=COLORS['text_secondary']).pack(side='left', padx=5)
        ctk.CTkButton(
            local_url_frame, text='Copy', font=('Helvetica', 10),
            fg_color=COLORS['surface'], hover_color=COLORS['border'],
            text_color=COLORS['text_secondary'], corner_radius=4, width=50, height=22,
            command=lambda: self._copy_to_clipboard(self.local_url, 'URL copied!'),
        ).pack(side='right')

        tunnel_url_frame = ctk.CTkFrame(mcp_frame, fg_color='transparent')
        tunnel_url_frame.pack(padx=10, fill='x', pady=2)
        ctk.CTkLabel(tunnel_url_frame, text='Tunnel URL:',
                     font=('Helvetica', 11), text_color=COLORS['text_dim'],
                     width=75, anchor='e').pack(side='left')
        self.tunnel_url_label = ctk.CTkLabel(
            tunnel_url_frame, text='Not active',
            font=('Courier', 11), text_color=COLORS['text_dim'])
        self.tunnel_url_label.pack(side='left', padx=5)
        self.tunnel_copy_btn = ctk.CTkButton(
            tunnel_url_frame, text='Copy', font=('Helvetica', 10),
            fg_color=COLORS['surface'], hover_color=COLORS['border'],
            text_color=COLORS['text_secondary'], corner_radius=4, width=50, height=22,
            command=lambda: self._copy_to_clipboard(self.tunnel_url or '', 'Tunnel URL copied!'),
            state='disabled',
        )
        self.tunnel_copy_btn.pack(side='right')

        # Install instructions
        instructions = ctk.CTkLabel(
            mcp_frame,
            text='How to install on claude.ai:\n'
                 '1. Go to claude.ai \u2192 Settings \u2192 Connectors\n'
                 '2. Click "Add Connector" \u2192 Name: Constellation\n'
                 '3. Paste the URL above',
            font=('Helvetica', 11), text_color=COLORS['text_dim'],
            anchor='w', justify='left',
        )
        instructions.pack(padx=10, pady=(4, 10), anchor='w')

        # --- Embedding Progress Section (hidden by default) ---
        self.progress_frame = ctk.CTkFrame(
            self.main_frame, fg_color=COLORS['panel'],
            corner_radius=8, border_width=1, border_color=COLORS['border'],
        )
        # Not packed initially — shown only during re-embed

        self.progress_stage_label = ctk.CTkLabel(
            self.progress_frame, text='Preparing...',
            font=('Helvetica', 13, 'bold'), text_color=COLORS['text'],
            anchor='w',
        )
        self.progress_stage_label.pack(padx=12, pady=(10, 2), anchor='w')

        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame, fg_color=COLORS['surface'],
            progress_color=COLORS['violet'], height=14, corner_radius=4,
        )
        self.progress_bar.pack(padx=12, pady=2, fill='x')
        self.progress_bar.set(0)

        progress_detail_row = ctk.CTkFrame(self.progress_frame, fg_color='transparent')
        progress_detail_row.pack(padx=12, pady=(0, 4), fill='x')

        self.progress_detail_label = ctk.CTkLabel(
            progress_detail_row, text='',
            font=('Helvetica', 11), text_color=COLORS['text_secondary'],
            anchor='w',
        )
        self.progress_detail_label.pack(side='left')

        self.progress_speed_label = ctk.CTkLabel(
            progress_detail_row, text='',
            font=('Helvetica', 11), text_color=COLORS['text_dim'],
            anchor='e',
        )
        self.progress_speed_label.pack(side='right')

        self.progress_eta_label = ctk.CTkLabel(
            self.progress_frame, text='',
            font=('Helvetica', 11), text_color=COLORS['text_dim'],
            anchor='w',
        )
        self.progress_eta_label.pack(padx=12, pady=(0, 8), anchor='w')

        # Tracking for speed/ETA calculation
        self._embed_start_time = None
        self._embed_last_done = 0

        # --- Action Buttons ---
        button_frame = ctk.CTkFrame(self.main_frame, fg_color='transparent')
        button_frame.pack(fill='x', padx=16, pady=(10, 5))

        # Row 1: Start, Stop, Open Visualization
        row1 = ctk.CTkFrame(button_frame, fg_color='transparent')
        row1.pack(fill='x', pady=2)

        self.start_btn = ctk.CTkButton(
            row1, text='Start', font=('Helvetica', 14, 'bold'),
            fg_color=COLORS['green'], hover_color='#4bc878',
            text_color=COLORS['bg'], corner_radius=6, height=38,
            command=self._start_servers,
        )
        self.start_btn.pack(side='left', padx=(0, 5), expand=True, fill='x')

        self.stop_btn = ctk.CTkButton(
            row1, text='Stop', font=('Helvetica', 14, 'bold'),
            fg_color=COLORS['red'], hover_color='#c44a4a',
            text_color='white', corner_radius=6, height=38,
            command=self._stop_servers,
        )
        self.stop_btn.pack(side='left', padx=5, expand=True, fill='x')

        self.viz_btn = ctk.CTkButton(
            row1, text='Open Visualization', font=('Helvetica', 13),
            fg_color=COLORS['violet'], hover_color=COLORS['violet_bright'],
            text_color='white', corner_radius=6, height=38,
            command=lambda: webbrowser.open('http://127.0.0.1:8420'),
        )
        self.viz_btn.pack(side='left', padx=(5, 0), expand=True, fill='x')

        # Row 2: Re-embed, Start Tunnel
        row2 = ctk.CTkFrame(button_frame, fg_color='transparent')
        row2.pack(fill='x', pady=2)

        self.reembed_btn = ctk.CTkButton(
            row2, text='Re-embed', font=('Helvetica', 13),
            fg_color=COLORS['surface'], hover_color=COLORS['border'],
            text_color=COLORS['text'], corner_radius=6, height=34,
            command=self._reembed,
        )
        self.reembed_btn.pack(side='left', padx=(0, 5), expand=True, fill='x')

        self.tunnel_btn = ctk.CTkButton(
            row2, text='Start Tunnel', font=('Helvetica', 13),
            fg_color=COLORS['surface'], hover_color=COLORS['border'],
            text_color=COLORS['text'], corner_radius=6, height=34,
            command=self._toggle_tunnel,
        )
        self.tunnel_btn.pack(side='left', padx=(5, 0), expand=True, fill='x')

        # --- Status Bar ---
        self.status_bar = ctk.CTkLabel(
            self, text='Checking server status...',
            font=('Helvetica', 11), text_color=COLORS['text_dim'],
            fg_color=COLORS['panel'], height=30, anchor='w',
        )
        self.status_bar.pack(fill='x', side='bottom', padx=0, pady=0)

    def _build_section_label(self, text):
        """Build a section header label."""
        ctk.CTkLabel(
            self.main_frame, text=text,
            font=('Helvetica', 11, 'bold'), text_color=COLORS['text_dim'],
            anchor='w',
        ).pack(padx=20, pady=(10, 3), anchor='w')

    def _build_card(self):
        """Build a card-style frame."""
        card = ctk.CTkFrame(
            self.main_frame, fg_color=COLORS['panel'],
            corner_radius=8, border_width=1, border_color=COLORS['border'],
        )
        card.pack(fill='x', padx=16, pady=(0, 5))
        return card

    def _truncate_path(self, path, max_len=45):
        """Truncate a path for display."""
        if not path:
            return ''
        if len(path) <= max_len:
            return path
        return '...' + path[-(max_len - 3):]

    def _copy_to_clipboard(self, text, msg='Copied!'):
        """Copy text to clipboard and flash status bar."""
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status(msg)

    def _set_status(self, text):
        """Update the status bar."""
        self.status_bar.configure(text=f'  {text}')

    # --- Server Management ---

    def _start_servers(self):
        """Start both REST and MCP HTTP servers as background subprocesses."""
        if self._rest_running and self._mcp_running:
            self._set_status('Servers already running.')
            return

        self._set_status('Starting servers...')
        os.makedirs(LOG_DIR, exist_ok=True)

        if not self._rest_running:
            try:
                rest_log = open(os.path.join(LOG_DIR, 'rest_server.log'), 'a')
                self.rest_process = subprocess.Popen(
                    [VENV_PYTHON, LAUNCH_SCRIPT, '--headless'],
                    stdout=subprocess.DEVNULL,
                    stderr=rest_log,
                    cwd=PROJECT_ROOT,
                    start_new_session=True,
                )
                self._start_time = time.time()
            except Exception as e:
                self._set_status(f'Failed to start REST server: {e}')
                return

        if not self._mcp_running:
            try:
                mcp_log = open(os.path.join(LOG_DIR, 'mcp_server.log'), 'a')
                # Check if mcp_server.py supports --transport flag
                self.mcp_process = subprocess.Popen(
                    [VENV_PYTHON, MCP_SCRIPT],
                    stdout=subprocess.DEVNULL,
                    stderr=mcp_log,
                    cwd=PROJECT_ROOT,
                    start_new_session=True,
                )
            except Exception as e:
                self._set_status(f'Failed to start MCP server: {e}')
                return

        # Save PIDs
        rest_pid = self.rest_process.pid if self.rest_process else None
        mcp_pid = self.mcp_process.pid if self.mcp_process else None
        save_pids(rest_pid, mcp_pid)

        self._set_status('Starting servers... (waiting for readiness)')

    def _stop_servers(self):
        """Gracefully stop servers."""
        self._set_status('Stopping servers...')

        processes_to_stop = []
        if self.rest_process and self.rest_process.poll() is None:
            processes_to_stop.append(('REST', self.rest_process))
        if self.mcp_process and self.mcp_process.poll() is None:
            processes_to_stop.append(('MCP', self.mcp_process))

        # Also try to stop by saved PID if we don't have process handles
        if not processes_to_stop:
            pids = load_pids()
            for name, key in [('REST', 'rest_pid'), ('MCP', 'mcp_pid')]:
                pid = pids.get(key)
                if pid and is_pid_alive(pid):
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except Exception:
                        pass

        for name, proc in processes_to_stop:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        self.rest_process = None
        self.mcp_process = None
        self._rest_running = False
        self._mcp_running = False
        self._start_time = None
        clear_pids()
        self._set_status('Servers stopped.')

    def _reembed(self):
        """Run re-embedding in a background subprocess with real-time progress."""
        self._set_status('Re-embedding...')
        self.reembed_btn.configure(state='disabled', text='Embedding...')

        # Show progress panel
        self._build_section_label_for_progress()
        self.progress_frame.pack(fill='x', padx=16, pady=(0, 5),
                                  before=self.progress_frame.master.winfo_children()[-1]
                                  if False else None)
        # Pack after the data sources section
        self.progress_frame.pack(fill='x', padx=16, pady=(0, 5))
        self.progress_bar.set(0)
        self.progress_stage_label.configure(text='Starting pipeline...')
        self.progress_detail_label.configure(text='')
        self.progress_speed_label.configure(text='')
        self.progress_eta_label.configure(text='')
        self._embed_start_time = None
        self._embed_last_done = 0

        def run():
            try:
                proc = subprocess.Popen(
                    [VENV_PYTHON, LAUNCH_SCRIPT, '--reembed', '--headless'],
                    cwd=PROJECT_ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for line in proc.stdout:
                    line = line.strip()
                    if line.startswith('PROGRESS:'):
                        self.after(0, lambda l=line: self._handle_progress_line(l))
                proc.wait()
                if proc.returncode == 0:
                    self.after(0, lambda: self._finish_reembed(True, ''))
                else:
                    stderr_out = proc.stderr.read() if proc.stderr else ''
                    self.after(0, lambda: self._finish_reembed(
                        False, f'exit {proc.returncode}'))
            except Exception as e:
                self.after(0, lambda: self._finish_reembed(False, str(e)))

        threading.Thread(target=run, daemon=True).start()

    def _build_section_label_for_progress(self):
        """Ensure EMBEDDING PROGRESS section label exists."""
        if not hasattr(self, '_progress_section_label'):
            self._progress_section_label = ctk.CTkLabel(
                self.main_frame, text='EMBEDDING PROGRESS',
                font=('Helvetica', 11, 'bold'), text_color=COLORS['text_dim'],
                anchor='w',
            )
        self._progress_section_label.pack(padx=20, pady=(10, 3), anchor='w')

    def _handle_progress_line(self, line):
        """Parse a PROGRESS protocol line and update the progress panel.

        Format: PROGRESS:<stage>:<done>:<total>:<detail>
        """
        parts = line.split(':', 4)
        if len(parts) < 5:
            return
        _, stage, done_str, total_str, detail = parts

        try:
            done = int(done_str)
            total = int(total_str)
        except ValueError:
            return

        stage_labels = {
            'init': 'Initializing',
            'embedding': 'Embedding messages',
            'clustering': 'Clustering',
            'edges': 'Building edges',
            'saving': 'Saving data',
            'complete': 'Complete',
        }
        stage_text = stage_labels.get(stage, stage.title())

        if stage == 'complete':
            self.progress_bar.set(1.0)
            self.progress_stage_label.configure(
                text='Pipeline complete', text_color=COLORS['green'])
            self.progress_detail_label.configure(text=detail)
            self.progress_speed_label.configure(text='')
            self.progress_eta_label.configure(text='')
            return

        # Update progress bar
        fraction = done / total if total > 0 else 0
        self.progress_bar.set(fraction)
        self.progress_stage_label.configure(
            text=stage_text, text_color=COLORS['text'])

        if stage == 'embedding' and total > 0:
            self.progress_detail_label.configure(
                text=f'{done:,} / {total:,} messages')

            # Speed and ETA
            if self._embed_start_time is None and done > 0:
                self._embed_start_time = time.time()
                self._embed_last_done = done
            elif self._embed_start_time and done > self._embed_last_done:
                elapsed = time.time() - self._embed_start_time
                processed = done - self._embed_last_done
                if elapsed > 0:
                    speed = (done - 0) / elapsed  # total since start
                    remaining = total - done
                    eta_secs = remaining / speed if speed > 0 else 0
                    self.progress_speed_label.configure(
                        text=f'{speed:.0f} msg/s')
                    if eta_secs < 60:
                        self.progress_eta_label.configure(
                            text=f'ETA: {eta_secs:.0f}s')
                    else:
                        mins = int(eta_secs // 60)
                        secs = int(eta_secs % 60)
                        self.progress_eta_label.configure(
                            text=f'ETA: {mins}m {secs:02d}s')
        else:
            self.progress_detail_label.configure(text=detail)
            self.progress_speed_label.configure(text='')
            self.progress_eta_label.configure(text='')

    def _finish_reembed(self, success, error_msg):
        """Clean up after re-embed completes."""
        self.reembed_btn.configure(state='normal', text='Re-embed')
        if success:
            self._set_status('Re-embedding complete!')
            self.progress_stage_label.configure(
                text='Pipeline complete', text_color=COLORS['green'])
            self.progress_bar.set(1.0)
        else:
            self._set_status(f'Re-embed failed: {error_msg}')
            self.progress_stage_label.configure(
                text=f'Failed: {error_msg}', text_color=COLORS['red'])
        # Hide progress panel after 10 seconds on success
        if success:
            self.after(10000, self._hide_progress_panel)

    def _hide_progress_panel(self):
        """Hide the progress panel and its section label."""
        self.progress_frame.pack_forget()
        if hasattr(self, '_progress_section_label'):
            self._progress_section_label.pack_forget()

    def _toggle_tunnel(self):
        """Start or stop Cloudflare Tunnel."""
        if self.tunnel_process and self.tunnel_process.poll() is None:
            # Stop tunnel
            self.tunnel_process.terminate()
            try:
                self.tunnel_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.tunnel_process.kill()
            self.tunnel_process = None
            self.tunnel_url = None
            self.tunnel_btn.configure(text='Start Tunnel')
            self.tunnel_url_label.configure(text='Not active', text_color=COLORS['text_dim'])
            self.tunnel_copy_btn.configure(state='disabled')
            self.tunnel_dot.configure(text_color=COLORS['text_dim'])
            self.tunnel_status.configure(text='Inactive', text_color=COLORS['text_secondary'])
            self._set_status('Tunnel stopped.')
            return

        # Start tunnel
        self._set_status('Starting tunnel...')
        self.tunnel_btn.configure(text='Starting...')

        def run():
            try:
                self.tunnel_process = subprocess.Popen(
                    ['cloudflared', 'tunnel', '--url', 'http://localhost:8000'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
                # Read output to find the tunnel URL
                import re
                for line in self.tunnel_process.stdout:
                    # Only match actual tunnel URLs, not Cloudflare terms/policy pages
                    match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com[^\s]*', line)
                    if match:
                        self.tunnel_url = match.group(0)
                        self.after(0, self._update_tunnel_display)
                        break
            except FileNotFoundError:
                self.after(0, lambda: self._set_status(
                    'cloudflared not found. Install: brew install cloudflared'))
                self.after(0, lambda: self.tunnel_btn.configure(text='Start Tunnel'))
            except Exception as e:
                self.after(0, lambda: self._set_status(f'Tunnel error: {e}'))
                self.after(0, lambda: self.tunnel_btn.configure(text='Start Tunnel'))

        threading.Thread(target=run, daemon=True).start()

    def _update_tunnel_display(self):
        """Update UI after tunnel URL is captured."""
        if self.tunnel_url:
            self.tunnel_url_label.configure(text=self.tunnel_url, text_color=COLORS['green'])
            self.tunnel_copy_btn.configure(state='normal')
            self.tunnel_btn.configure(text='Stop Tunnel')
            self.tunnel_dot.configure(text_color=COLORS['green'])
            self.tunnel_status.configure(text='Active', text_color=COLORS['green'])
            self._set_status(f'Tunnel active: {self.tunnel_url}')

    def _add_source_dialog(self):
        """Dialog for adding a new data source."""
        dialog = ctk.CTkToplevel(self)
        dialog.title('Add Data Source')
        dialog.geometry('400x200')
        dialog.configure(fg_color=COLORS['bg'])
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text='Provider:', font=('Helvetica', 13),
                     text_color=COLORS['text']).pack(padx=20, pady=(15, 5), anchor='w')

        provider_var = ctk.StringVar(value='claude')
        provider_menu = ctk.CTkOptionMenu(
            dialog, values=['claude', 'chatgpt', 'gemini', 'grok'],
            variable=provider_var,
            fg_color=COLORS['surface'], button_color=COLORS['violet'],
            button_hover_color=COLORS['violet_bright'],
            text_color=COLORS['text'],
        )
        provider_menu.pack(padx=20, fill='x')

        ctk.CTkLabel(dialog, text='File or folder path:',
                     font=('Helvetica', 13), text_color=COLORS['text']).pack(
            padx=20, pady=(10, 5), anchor='w')

        path_var = ctk.StringVar()
        path_entry = ctk.CTkEntry(dialog, textvariable=path_var,
                                   fg_color=COLORS['surface'], text_color=COLORS['text'],
                                   border_color=COLORS['border'])
        path_entry.pack(padx=20, fill='x')

        def browse():
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                title='Select data file',
                filetypes=[('JSON files', '*.json'), ('All files', '*')],
            )
            if path:
                path_var.set(path)

        btn_frame = ctk.CTkFrame(dialog, fg_color='transparent')
        btn_frame.pack(padx=20, pady=10, fill='x')

        ctk.CTkButton(
            btn_frame, text='Browse...', font=('Helvetica', 12),
            fg_color=COLORS['surface'], hover_color=COLORS['border'],
            text_color=COLORS['text'], corner_radius=4, width=80,
            command=browse,
        ).pack(side='left')

        def add():
            provider = provider_var.get()
            path = path_var.get().strip()
            if not path:
                return
            config = ensure_config()
            abs_path = os.path.abspath(path)
            if provider == 'claude':
                # Claude uses the top-level source.path key
                config['source']['path'] = abs_path
            else:
                if 'sources' not in config:
                    config['sources'] = {}
                source_entry = {'path': abs_path}
                if os.path.isdir(path):
                    source_entry['type'] = 'directory'
                config['sources'][provider] = source_entry
            save_config(config)

            # Update display
            if provider in self.sources_labels:
                self.sources_labels[provider].configure(
                    text=self._truncate_path(path),
                    text_color=COLORS['text_secondary'],
                )
            self._set_status(f'Added {provider} source. Re-embed to index.')
            dialog.destroy()

        ctk.CTkButton(
            btn_frame, text='Add', font=('Helvetica', 12, 'bold'),
            fg_color=COLORS['violet'], hover_color=COLORS['violet_bright'],
            text_color='white', corner_radius=4, width=80,
            command=add,
        ).pack(side='right')

    # --- Background Polling ---

    def _poll_status(self):
        """Background thread: probe servers and update UI every 3 seconds."""
        while self._running:
            rest_ok = probe_rest_server()
            mcp_ok = probe_mcp_server()

            stats = None
            server_info = None
            if rest_ok:
                server_info = fetch_server_info()
                if not server_info:
                    stats = fetch_stats()

            self.after(0, self._update_status_display, rest_ok, mcp_ok, stats, server_info)
            time.sleep(3)

    def _update_status_display(self, rest_ok, mcp_ok, stats, server_info=None):
        """Update all status indicators on the main thread."""
        self._rest_running = rest_ok
        self._mcp_running = mcp_ok

        # REST status
        if rest_ok:
            self.rest_dot.configure(text_color=COLORS['green'])
            self.rest_status.configure(text='Running', text_color=COLORS['green'])
            self.rest_port_label.configure(text='port 8420')
        else:
            self.rest_dot.configure(text_color=COLORS['red'])
            self.rest_status.configure(text='Stopped', text_color=COLORS['red'])
            self.rest_port_label.configure(text='')

        # MCP status
        if mcp_ok:
            self.mcp_dot.configure(text_color=COLORS['green'])
            self.mcp_status.configure(text='Running', text_color=COLORS['green'])
            self.mcp_port_label.configure(text='port 8000')
        else:
            self.mcp_dot.configure(text_color=COLORS['text_dim'])
            self.mcp_status.configure(text='Stopped', text_color=COLORS['text_secondary'])
            self.mcp_port_label.configure(text='')

        # Button states
        both_running = rest_ok and mcp_ok
        neither_running = not rest_ok and not mcp_ok
        self.start_btn.configure(
            state='disabled' if both_running else 'normal',
            fg_color=COLORS['border'] if both_running else COLORS['green'],
        )
        self.stop_btn.configure(
            state='disabled' if neither_running else 'normal',
            fg_color=COLORS['border'] if neither_running else COLORS['red'],
        )

        # Stats display
        if server_info:
            uptime = server_info.get('uptime_formatted', '')
            pid = server_info.get('pid', '')
            convs = server_info.get('total_conversations', 0)
            msgs = server_info.get('total_messages', 0)
            providers = server_info.get('providers', {})
            prov_str = ', '.join(f'{k.title()} ({v})' for k, v in providers.items())
            self.stats_label.configure(
                text=f'Uptime: {uptime}   PID: {pid}\n'
                     f'Conversations: {convs:,}   Messages: {msgs:,}\n'
                     f'Providers: {prov_str}',
            )
        elif stats:
            convs = stats.get('totalConversations', 0)
            msgs = stats.get('totalMessages', 0)
            providers = stats.get('providers', {})
            prov_str = ', '.join(f'{k.title()} ({v})' for k, v in providers.items())
            uptime_str = ''
            if self._start_time:
                elapsed = time.time() - self._start_time
                hours = int(elapsed // 3600)
                mins = int((elapsed % 3600) // 60)
                uptime_str = f'Uptime: {hours}h {mins:02d}m   '
            self.stats_label.configure(
                text=f'{uptime_str}Conversations: {convs:,}   Messages: {msgs:,}\n'
                     f'Providers: {prov_str}',
            )
        elif not rest_ok:
            self.stats_label.configure(text='Server not running')

        # Status bar
        if rest_ok:
            self._set_status('Servers running. Visualization at http://127.0.0.1:8420')
        elif self.rest_process and self.rest_process.poll() is None:
            self._set_status('Starting servers...')
        else:
            self._set_status('Servers stopped.')

    def _on_closing(self):
        """Close helper window without stopping servers."""
        self._running = False
        self.destroy()


def center_view():
    """Placeholder for compatibility — actual center_view is in graph.js."""
    pass


if __name__ == '__main__':
    ctk.set_appearance_mode('dark')
    ctk.set_default_color_theme('dark-blue')

    app = ConstellationHelper()
    app.mainloop()
