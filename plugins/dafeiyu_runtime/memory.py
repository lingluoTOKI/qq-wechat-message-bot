"""Select automatic recall excerpts without mutating stored memories."""
import json
import math
import re


def select_memories(memories):
    candidates = []
    for index, memory in enumerate(memories):
        if not isinstance(memory, dict):
            continue
        item = dict(memory)
        meta = item.get('metadata') or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (ValueError, TypeError):
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        if str(meta.get('summary_quality', '')).lower() == 'low':
            continue
        item['metadata'] = meta
        content = str(item.get('content') or '').strip()
        if not content:
            continue
        normalized = re.sub(r'\s+', '', content).casefold()
        try:
            score = float(item.get('score') or 0)
            if not math.isfinite(score): score = 0
        except (TypeError, ValueError):
            score = 0
        candidates.append((score, index, normalized, item))
    seen = set(); selected = []
    for _, _, content, item in sorted(candidates, key=lambda x: (-x[0], x[1])):
        if content not in seen:
            seen.add(content); selected.append(item)
    return selected
