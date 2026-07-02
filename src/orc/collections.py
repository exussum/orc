import threading
from collections import defaultdict, namedtuple

Trie = namedtuple("Trie", ["word", "children"])


def build_trie(words):
    def build(words, depth):
        groups, word = defaultdict(list), None
        for w in words:
            parts = w.split("_")
            if depth >= len(parts):
                word = w
            else:
                groups[parts[depth]].append(w)
        return Trie(word, {seg: build(ws, depth + 1) for seg, ws in groups.items()})

    return build(words, 0)


def _all_words(node):
    words = {node.word} if node.word else set()
    for child in node.children.values():
        words |= _all_words(child)
    return words


def prefix_groups(trie):
    def dfs(node, path):
        if not node.children:
            result.append((path, {node.word}))
        elif len(node.children) == 1:
            ((seg, child),) = node.children.items()
            dfs(child, [*path, seg])
        else:
            result.append((path, _all_words(node)))

    result = []
    for seg, child in trie.children.items():
        dfs(child, [seg])
    return result


class LockedDict:
    def __init__(self, initial=None):
        self._lock = threading.Lock()
        self._data = dict(initial) if initial else {}

    def __contains__(self, key):
        with self._lock:
            return key in self._data

    def __getitem__(self, key):
        with self._lock:
            return self._data[key]

    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def get_or_set(self, key, factory):
        with self._lock:
            if key in self._data:
                return self._data[key]
            value = factory()
            self._data[key] = value
            return value

    def update(self, key, fn):
        with self._lock:
            new = fn(self._data.get(key))
            if new is None:
                return None
            self._data[key] = new
            return new

    def copy(self):
        with self._lock:
            return dict(self._data)
