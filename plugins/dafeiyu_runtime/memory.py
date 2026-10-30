"""Select automatic recall excerpts without mutating stored memories."""
import json
import math
import re


def select_memories(memories, query=None):
    from .profile_quality import reason, social_memory, QUERY, SECRET, INSTRUCTION
    if query is None:query=QUERY.get()
    technical=bool(reason(query) or re.search(r'调试|插件|日志|预算|缓存|账单|token|API|AstrBot',query or '',re.I))
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
        if SECRET.search(content) or INSTRUCTION.search(content):continue
        if any(SECRET.search(str(meta.get(field) or '')) or INSTRUCTION.search(str(meta.get(field) or '')) for field in ('persona_summary','key_facts')):continue
        if not technical:
            content=social_memory(content)
            if not content:continue
            item['content']=content
            meta=dict(meta)
            if meta.get('persona_summary'):
                meta['persona_summary']=social_memory(meta['persona_summary']) or content
            for field in ('key_facts','topics'):
                if isinstance(meta.get(field),list):
                    meta[field]=[value for value in meta[field] if not reason(str(value),summary=True)]
            item['metadata']=meta
        elif reason(content,summary=True) or any(reason(str(meta.get(field) or ''),summary=True) for field in ('persona_summary','key_facts')):
            note='[历史技术操作，仅用于当前技术问题，不代表人物性格或长期兴趣] '
            item['content']=note+content
            meta=dict(meta)
            if meta.get('persona_summary'):meta['persona_summary']=note+str(meta['persona_summary'])
            item['metadata']=meta
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
