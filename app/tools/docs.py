"""Retrieval over the document corpus.

Two things about this module are deliberate and worth stating.

First, the hard prefilter runs *before* any scoring, and the index is built over
the surviving chunks. Filtering after ranking would mean a Northstar clause was
briefly a candidate for a LumenWorks answer, and a low-ranked leak is exactly as
wrong as a high-ranked one. Rebuilding a BM25 index over twenty short chunks
costs nothing, so there is no reason to trade that guarantee for speed.

Second, the corpus is six one-page documents. Hybrid retrieval here is arguably
overkill and BM25 alone would score nearly as well -- it is kept because clause
numbers and identifiers like "KI-208" are exactly where lexical matching wins,
and because the fusion step is where a real embedding arm would drop in. The
dense arm is a local character-n-gram vector space rather than a hosted
embedding model: it needs no second vendor key and no 500MB model layer, and it
catches the spelling-variant cases that BM25 misses. `Embedder` is the seam.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any, Protocol

from rank_bm25 import BM25Okapi

from app.authority import Clause, visible_clauses

RRF_K = 60
DEFAULT_TOP_K = 6
NGRAM_SIZE = 4

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, with hyphenated identifiers kept whole *and* split.

    'KI-208' has to match a query that says "KI 208" and one that says "KI-208",
    so both forms go into the index.
    """
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        token = match.group(0)
        tokens.append(token)
        if any(sep in token for sep in "-_."):
            tokens.extend(part for part in re.split(r"[-_.]", token) if part)
    return tokens


class Embedder(Protocol):
    """The seam for a real embedding model."""

    def vector(self, text: str) -> dict[str, float]: ...


class CharNgramEmbedder:
    """A local, deterministic stand-in for a hosted embedding model.

    Character n-grams with inverse-document-frequency weighting, L2-normalised.
    Not a semantic model -- it will not connect "credit" to "refund" -- but on a
    corpus this small its job is fuzzy surface matching, which it does without a
    network call, an API key, or a model download.
    """

    def __init__(self, documents: list[str], ngram_size: int = NGRAM_SIZE) -> None:
        self.ngram_size = ngram_size
        self._idf: dict[str, float] = {}
        total = len(documents) or 1
        document_frequency: Counter[str] = Counter()
        for document in documents:
            document_frequency.update(set(self._ngrams(document)))
        for gram, count in document_frequency.items():
            self._idf[gram] = math.log((total + 1) / (count + 1)) + 1.0

    def _ngrams(self, text: str) -> list[str]:
        normalised = re.sub(r"\s+", " ", text.lower()).strip()
        if len(normalised) < self.ngram_size:
            return [normalised] if normalised else []
        return [
            normalised[index : index + self.ngram_size]
            for index in range(len(normalised) - self.ngram_size + 1)
        ]

    def vector(self, text: str) -> dict[str, float]:
        counts = Counter(self._ngrams(text))
        weighted = {
            gram: count * self._idf.get(gram, 1.0) for gram, count in counts.items()
        }
        norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
        return {gram: value / norm for gram, value in weighted.items()}


def cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(gram, 0.0) for gram, value in left.items())


def _reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Fuse ranked id lists. Rank-based, so the two arms' scores never have to
    be made commensurable -- which is the whole appeal of RRF."""
    fused: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for position, identifier in enumerate(ranking):
            fused[identifier] += 1.0 / (k + position + 1)
    return fused


def _chunk_payload(clause: Clause, score: float | None = None) -> dict[str, Any]:
    payload = clause.to_source()
    payload.update(
        {
            "doc_title": clause.doc_title,
            "section": clause.section,
            "topics": clause.topics,
            "flags": clause.flags,
            "text": clause.text,
            "effective_from": clause.effective_from,
            "effective_to": clause.effective_to,
            "internal_only": clause.internal_only,
        }
    )
    if score is not None:
        payload["score"] = round(score, 6)
    return payload


def _topic_conflicts(clauses: list[Clause]) -> list[dict[str, str]]:
    """Surface tier disagreement among the retrieved set.

    Grouping by topic and comparing tiers is enough to raise the banner the UI
    needs. The calculators do the finer, outcome-level comparison; this is what
    catches conflicts on questions that never reach a calculator.
    """
    conflicts: list[dict[str, str]] = []
    by_topic: dict[str, list[Clause]] = defaultdict(list)
    for clause in clauses:
        for topic in clause.topics:
            by_topic[topic].append(clause)

    for topic, group in sorted(by_topic.items()):
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda clause: clause.tier)
        winner = ordered[0]
        for loser in ordered[1:]:
            if loser.doc_id == winner.doc_id:
                continue
            if loser.tier == winner.tier:
                conflicts.append(
                    {
                        "topic": topic,
                        "winner": "none - same-tier disagreement",
                        "loser": f"{winner.citation} and {loser.citation} (both tier {winner.tier})",
                        "why": (
                            "Two sources of equal authority both speak to "
                            f"{topic}; the system does not choose between them."
                        ),
                    }
                )
                continue
            conflicts.append(
                {
                    "topic": topic,
                    "winner": f"{winner.citation} (tier {winner.tier})",
                    "loser": f"{loser.citation} (tier {loser.tier})",
                    "why": (
                        f"{winner.tier_label} outranks {loser.tier_label} on {topic}"
                        + (
                            f" for {winner.scope}"
                            if winner.scope != "global"
                            else ""
                        )
                    ),
                }
            )
    return conflicts


def search_documents(
    conn: sqlite3.Connection,
    query: str,
    *,
    topics: list[str] | None = None,
    include_deprecated: bool = False,
    account_id: str | None = None,
    role: str = "customer",
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    """Hybrid search across everything this caller is allowed to see."""
    candidates = visible_clauses(
        conn, account_id, role=role, include_deprecated=include_deprecated
    )
    if topics:
        wanted = {topic.lower() for topic in topics}
        filtered = [
            clause
            for clause in candidates
            if wanted & {topic.lower() for topic in clause.topics}
        ]
        # A topic filter that matches nothing is a bad filter, not an answer.
        # Falling back to the unfiltered set beats returning a confident empty.
        candidates = filtered or candidates

    if not candidates:
        return {"chunks": [], "conflicts": [], "warnings": [], "query": query}

    corpus = [
        f"{clause.doc_title} {clause.section_title} {clause.text}" for clause in candidates
    ]
    identifiers = [clause.chunk_id for clause in candidates]

    bm25 = BM25Okapi([tokenize(document) for document in corpus])
    lexical_scores = bm25.get_scores(tokenize(query))
    lexical_ranking = [
        identifiers[index]
        for index in sorted(
            range(len(identifiers)), key=lambda i: lexical_scores[i], reverse=True
        )
    ]

    embedder = CharNgramEmbedder(corpus)
    query_vector = embedder.vector(query)
    dense_scores = [cosine(query_vector, embedder.vector(document)) for document in corpus]
    dense_ranking = [
        identifiers[index]
        for index in sorted(
            range(len(identifiers)), key=lambda i: dense_scores[i], reverse=True
        )
    ]

    fused = _reciprocal_rank_fusion([lexical_ranking, dense_ranking])
    by_id = {clause.chunk_id: clause for clause in candidates}
    ordered = sorted(
        fused.items(),
        # Tier is the tie-breaker, never the primary sort: a weakly matching
        # contract clause should not outrank a strongly matching SOP clause.
        key=lambda item: (-item[1], by_id[item[0]].tier),
    )[:top_k]

    selected = [by_id[identifier] for identifier, _ in ordered]
    return {
        "query": query,
        "chunks": [
            _chunk_payload(by_id[identifier], score) for identifier, score in ordered
        ],
        "conflicts": _topic_conflicts(selected),
        "warnings": [],
        "considered": len(candidates),
    }
