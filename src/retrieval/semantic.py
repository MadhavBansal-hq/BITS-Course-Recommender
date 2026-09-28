"""
Meaning-based matching of a free-text topic ("AI", "sustainable energy")
against a course's title and handout description, locally, with no API key
and no network: spaCy's en_core_web_md word vectors (requirements-nlp.txt).

Matching is word by word: each content word of the topic takes its best
match among the course's words (cosine similarity), and the topic score is
their mean. Averaging whole phrases instead let generic words dominate
("Principles of Economics" outscored "Data Mining" for "artificial
intelligence and machine learning"). Without the model it falls back to
exact word overlap and reports that in `mode`.

Abbreviations are expanded before matching, using long forms learned from
the dataset's own titles and handouts: the vector for "AI" is ambiguous in
general text and matched "Fuzzy" and "garbage", while "Artificial
Intelligence" appears in the course titles themselves.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

import numpy as np

FILLER = set("""a an and the of for in on to with without related relating about around courses course
subject subjects some any me i my want need suggest recommend find show give list that which is are be
based like similar also please electives elective""".split())


FLOOR = 0.45


class Matcher:
    def __init__(self):
        self.nlp = None
        try:
            import spacy
            self.nlp = spacy.load("en_core_web_md", exclude=["parser", "ner", "tagger", "lemmatizer",
                                                             "attribute_ruler", "senter"])
            self.mode = "word vectors (spaCy en_core_web_md, local)"
        except (ImportError, OSError):
            self.mode = "exact word overlap (spaCy model not installed; see requirements-nlp.txt)"
        self._cache: dict[str, tuple] = {}
        self.acronyms: dict[str, str] = {}

    def learn_acronyms(self, texts) -> None:
        """Acronym -> long form, from phrases in the data whose capitalised
        initials spell it ("Artificial Intelligence" -> AI) or that are
        followed by it in brackets ("Machine Learning (ML)")."""
        seen: dict[str, Counter] = defaultdict(Counter)
        small = {"of", "and", "in", "for", "the", "to", "&"}
        for text in texts:
            for m in re.finditer(r"((?:[A-Z][a-z]+\s+(?:(?:of|and|in|for|the|&)\s+)?){1,3}[A-Z][a-z]+)\s*\(([A-Z]{2,5})s?\)", text):
                seen[m[2]][m[1]] += 3                      # explicit "(ML)" is strong evidence
            for m in re.finditer(r"\b(?:[A-Z][a-z]+\s+){1,3}[A-Z][a-z]+\b", text):
                words = [w for w in m.group().split() if w.lower() not in small]
                for n in range(2, min(4, len(words)) + 1):
                    for i in range(len(words) - n + 1):
                        phrase = words[i:i + n]
                        seen["".join(w[0] for w in phrase)][" ".join(phrase)] += 1
        self.acronyms = {a: c.most_common(1)[0][0] for a, c in seen.items() if c.most_common(1)[0][1] >= 2}

    def expand(self, topic: str) -> str:
        return re.sub(r"\b([A-Z]{2,5})s?\b", lambda m: self.acronyms.get(m[1], m[0]), topic)

    def _prepare(self, text: str):
        if text in self._cache:
            return self._cache[text]
        if self.nlp is None:
            words = [w for w in re.findall(r"[a-z]+", text.lower()) if w not in FILLER]
            out = (words, None)
        else:
            toks = [t for t in self.nlp(text) if t.is_alpha and not t.is_stop and t.lower_ not in FILLER]
            words = [t.text for t in toks]
            vecs = np.array([t.vector if t.has_vector else np.zeros(self.nlp.vocab.vectors_length) for t in toks]) \
                if toks else np.zeros((0, self.nlp.vocab.vectors_length))
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            out = (words, np.divide(vecs, norms, out=np.zeros_like(vecs), where=norms > 0))
        self._cache[text] = out
        return out

    def topic_words(self, topic: str) -> list[str]:
        return self._prepare(self.expand(topic))[0]

    def score(self, topic: str, text: str) -> tuple[float, list[str]]:
        """(score in [0, 1], the course words that matched each topic word)."""
        qw, qv = self._prepare(self.expand(topic))
        cw, cv = self._prepare(text or "")
        if not qw or not cw:
            return 0.0, []
        if qv is None:
            hits = [w for w in qw if w in cw]
            return len(hits) / len(qw), hits
        sims = qv @ cv.T                                   # topic words x course words
        exact = np.array([[1.0 if a.lower() == b.lower() else 0.0 for b in cw] for a in qw])
        sims = np.maximum(sims, exact)                     # words without vectors still match exactly
        best = sims.argmax(axis=1)
        # Weak similarities (< FLOOR) are what any long text offers for any
        # word ("machine" ~ "tool"); rescale so only real matches count.
        top = np.clip((sims.max(axis=1) - FLOOR) / (1 - FLOOR), 0, 1)
        return float(top.mean()), [cw[i] for i, t in zip(best, top) if t > 0]
