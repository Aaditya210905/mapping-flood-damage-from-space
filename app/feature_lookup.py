"""Bounded, source-invalidated alias tries for exact saved feature names/IDs."""
from collections import OrderedDict
import hashlib
import threading
import unicodedata

_cache = OrderedDict()
_lock = threading.RLock()
_END = object()
_GENERIC = {"road", "bridge", "building", "village", "settlement", "hospital", "town"}


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def word(character):
    return bool(character) and (character.isalnum() or character in "_/-")


def _trie(context):
    entries = []
    digest = hashlib.blake2b(digest_size=16)
    for subject, feature in context.index.items():
        name = feature["properties"].get("name")
        entries.append((subject, name))
        # Include actual names: in-place changes in tests/imported runs cannot
        # reuse stale aliases, nor can a changed result.json with the same UUID.
        digest.update(repr((subject, name)).encode())
    key = (context.run_id, digest.digest())
    with _lock:
        trie = _cache.get(key)
        if trie is None:
            trie = {}
            for subject, name in entries:
                for value in (subject[1], name):
                    if not isinstance(value, str) or not value.strip():
                        continue
                    alias = normalized(value)
                    if value != subject[1] and alias in _GENERIC:
                        continue
                    node = trie
                    for character in alias:
                        node = node.setdefault(character, {})
                    node.setdefault(_END, set()).add(subject)
            _cache[key] = trie
            while len(_cache) > 2:
                _cache.popitem(last=False)
        _cache.move_to_end(key)
    return trie


def lookup(context, text, layers=None):
    trie = getattr(context, '_alias_trie', None)
    if trie is None:
        trie = context._alias_trie = _trie(context)
    text = normalized(text)
    hits = []
    for start in range(len(text)):
        if start and word(text[start - 1]):
            continue
        node = trie
        for end in range(start, len(text)):
            node = node.get(text[end])
            if node is None:
                break
            following = text[end + 1:end + 2]
            if word(following) or (following == "." and word(text[end + 2:end + 3])):
                continue
            for subject in sorted(node.get(_END, ())):
                if not layers or subject[0] in layers:
                    hits.append((start, end + 1, subject))
    matches = []
    for start, end, subject in hits:
        if any(a <= start and b >= end and (a < start or b > end) for a, b, _ in hits):
            continue
        if subject not in matches:
            matches.append(subject)
    return matches
