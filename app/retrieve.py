"""app/retrieve.py — the retrieval half of grounded answering.

A walkthrough's evidence is a handful of short page captures, not a corpus:
five or six steps of visible text plus the live page. Embeddings and a vector
store would be more machinery than documents, so scoring is lexical — inverse
document frequency over word overlap, which is enough to tell "what does that
button do?" from "what plan am I on?" and is what keeps a local model's prompt
small enough to answer well.

Usage:
    hits = top_k("what does the label button do", chunks_of(evidence), k=6)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# ponytail: lexical scoring, swap in embeddings only if answers start missing
# evidence that shares no words with the question.
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset("""a an and are as at be but by do does for from how i if in is it its of on or
that the this to was what when where which who why will with you your""".split())

CHUNK_CHARS = 400


@dataclass(frozen=True)
class Chunk:
    """One retrievable span of evidence, tagged with the source it may be cited as."""

    source: str
    text: str


def _terms(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


def split(source: str, text: str, size: int = CHUNK_CHARS) -> list[Chunk]:
    """Break one page capture into chunks of at most ``size`` characters on line breaks."""
    chunks: list[Chunk] = []
    current = ""
    for line in (text or "").splitlines():
        if len(current) + len(line) + 1 > size and current:
            chunks.append(Chunk(source, current.strip()))
            current = ""
        current += line + "\n"
        while len(current) > size:  # a single very long line still has to be cut
            chunks.append(Chunk(source, current[:size].strip()))
            current = current[size:]
    if current.strip():
        chunks.append(Chunk(source, current.strip()))
    return chunks


def top_k(query: str, chunks: list[Chunk], k: int = 6) -> list[Chunk]:
    """The ``k`` chunks sharing the most distinctive words with ``query``, best first.

    Chunks with no overlap are dropped: passing evidence about nothing the
    question mentions is what makes a small model answer confidently and wrongly.
    """
    wanted = set(_terms(query))
    if not wanted or not chunks:
        return chunks[:k]
    seen = [set(_terms(chunk.text)) for chunk in chunks]
    total = len(chunks)
    idf = {
        term: math.log(1 + total / (1 + sum(term in words for words in seen)))
        for term in wanted
    }
    scored = [
        (sum(idf[term] for term in wanted & words), index, chunk)
        for index, (chunk, words) in enumerate(zip(chunks, seen))
    ]
    hits = sorted((s for s in scored if s[0] > 0), key=lambda s: (-s[0], s[1]))
    return [chunk for _, _, chunk in hits[:k]]


def demo() -> None:
    """Self-check: retrieval finds the right page and drops the unrelated ones."""
    chunks = [
        Chunk("1-open-issues", "Issues  New issue  Labels  Milestones"),
        Chunk("2-name-it", "Title field. Add a description of the bug here."),
        Chunk("3-label-it", "Apply labels to this issue: bug, enhancement, question"),
    ]
    hits = top_k("which labels can I apply", chunks, k=2)
    assert hits and hits[0].source == "3-label-it", hits
    assert all(hit.source != "2-name-it" for hit in hits), hits
    assert top_k("pricing and billing tiers", chunks) == [], "unrelated query must retrieve nothing"
    assert top_k("", chunks, k=2) == chunks[:2], "empty query falls back to first k"
    long_page = split("live_page", "\n".join(f"line {n} of the page" for n in range(200)))
    assert len(long_page) > 1 and all(len(c.text) <= CHUNK_CHARS for c in long_page)
    print("retrieve: ok")


if __name__ == "__main__":
    demo()
