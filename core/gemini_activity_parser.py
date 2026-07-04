"""Gemini Apps activity-log parser (Phase 2 / D4).

Google Takeout exports Gemini Apps history as `MyActivity.html` — a flat list of
activity entries, each: `Prompted <prompt> … <timestamp> [<response>]`. There is
no native conversation grouping, id, or model. This parser:

  * extracts (timestamp, prompt, response) per entry (responses are present for
    many entries; captured best-effort);
  * filters by era (default: 2023-03 onward — the Bard launch; earlier records
    are likely Google-Assistant-era and are returned separately to HOLD);
  * sessionizes by a time-gap threshold (default 30 min) into conversations,
    titled from the first prompt, flagged `inferred_grouping: True`.

provider: `gemini`. Roles preserved (user prompt / assistant response) though
only user messages embed today.
"""

import datetime
import hashlib
import html
import re

_TS_RE = re.compile(
    r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2}, \d{4}, '
    r'\d{1,2}:\d{2}:\d{2})[\s \xa0]*(AM|PM)[\s \xa0]*([A-Z]{2,4})')

BARD_LAUNCH = datetime.datetime(2023, 3, 1)


def _clean(s: str) -> str:
    s = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s).replace('\xa0', ' ').replace(' ', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def _parse_ts(datepart: str, ampm: str):
    try:
        return datetime.datetime.strptime(f'{datepart} {ampm}', '%b %d, %Y, %I:%M:%S %p')
    except ValueError:
        return None


def extract_entries(html_path: str) -> list:
    """Return [{dt, iso, tz, prompt, response}] for every activity entry."""
    raw = open(html_path, encoding='utf-8', errors='replace').read()
    entries = []
    for chunk in raw.split('<div class="outer-cell'):
        p = chunk.find('Prompted')
        if p == -1:
            continue
        m = _TS_RE.search(chunk, p)
        if not m:
            continue
        dt = _parse_ts(m.group(1), m.group(2))
        if dt is None:
            continue
        prompt = _clean(chunk[p + len('Prompted'):m.start()])
        # response = text after the timestamp, up to the caption/meta cell
        tail = chunk[m.end():]
        cut = tail.find('class="caption')
        if cut != -1:
            cut = tail.rfind('<', 0, cut)   # back up to the caption's opening tag
        response = _clean(tail[:cut] if cut != -1 else tail)
        # Belt-and-suspenders: strip Takeout caption boilerplate if any leaks.
        response = re.split(r'Products:\s*Gemini Apps', response)[0].strip()
        if not prompt:
            continue
        entries.append({'dt': dt, 'iso': dt.isoformat(), 'tz': m.group(3),
                        'prompt': prompt, 'response': response})
    entries.sort(key=lambda e: e['dt'])
    return entries


def sessionize(entries: list, gap_minutes: int = 30) -> list:
    """Group time-sorted entries into conversations by a gap threshold."""
    convs, cur = [], []

    def flush(batch):
        if not batch:
            return
        messages, user_messages = [], []
        for e in batch:
            messages.append({'role': 'user', 'text': e['prompt'], 'timestamp': e['iso']})
            user_messages.append(e['prompt'])
            if e['response']:
                messages.append({'role': 'assistant', 'text': e['response'],
                                 'timestamp': e['iso']})
        first = batch[0]
        title = first['prompt'][:60] + ('…' if len(first['prompt']) > 60 else '')
        sid = hashlib.sha1((first['iso'] + '|' + first['prompt']).encode()).hexdigest()[:12]
        convs.append({
            'id': f'gemini_{sid}',
            'name': title or 'Gemini chat',
            'created_at': first['iso'],
            'provider': 'gemini',
            'inferred_grouping': True,
            'messages': messages,
            'user_messages': user_messages,
        })

    prev = None
    for e in entries:
        if prev is not None and (e['dt'] - prev).total_seconds() > gap_minutes * 60:
            flush(cur)
            cur = []
        cur.append(e)
        prev = e['dt']
    flush(cur)
    return convs


def parse_gemini_activity(path: str, since: str = '2023-03-01',
                          gap_minutes: int = 30) -> list:
    """Registry entry point: parse MyActivity.html, keep entries on/after `since`,
    sessionize into conversations (provider 'gemini'). Pre-`since` records are
    intentionally excluded here (held for review)."""
    import os
    ma = path
    if os.path.isdir(path):
        ma = os.path.join(path, 'MyActivity.html')
    entries = extract_entries(ma)
    cutoff = datetime.datetime.strptime(since, '%Y-%m-%d')
    kept = [e for e in entries if e['dt'] >= cutoff]
    return sessionize(kept, gap_minutes=gap_minutes)
