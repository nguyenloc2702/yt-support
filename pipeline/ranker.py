from typing import List, Dict, Any, Optional
import math
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np


class HeuristicContentRanker:
    """Rank candidate segments using heuristic scoring."""

    def __init__(self, topic: Optional[str] = None, keywords: List[str] = None):
        self.topic = topic or ""
        self.keywords = keywords or []

    def rank(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Add scores to each candidate and return ranked list (sorted by overall descending)."""
        if not candidates:
            return []

        # Compute TF-IDF for relevance (if topic/keywords provided)
        relevance_scores = self._compute_relevance(candidates)

        for i, cand in enumerate(candidates):
            # Information density
            duration = cand["duration"]
            word_count = cand["word_count"]
            silence_ratio = cand["silence_ratio"]
            speech_ratio = 1.0 - silence_ratio
            words_per_second = word_count / duration if duration > 0 else 0
            word_rate_score = min(words_per_second / 3.0, 1.0)  # normalize
            info_density = 0.6 * speech_ratio + 0.4 * word_rate_score

            # Hook score: higher if early in video or has question marks
            start = cand["source_start"]
            hook = 0.0
            if start < 30:  # first 30s
                hook += 0.5
            if start < 10:
                hook += 0.3
            if "?" in cand["text"]:
                hook += 0.2
            hook = min(hook, 1.0)

            # Clarity: penalize low confidence, mid-sentence cuts, high silence
            clarity = 1.0
            # Simple heuristic: if text ends with incomplete punctuation, penalize
            if cand["text"] and not cand["text"][-1] in ".!?":
                clarity *= 0.9
            # Penalize high silence ratio
            if silence_ratio > 0.3:
                clarity *= (1.0 - (silence_ratio - 0.3) * 0.5)
            clarity = max(0.0, min(clarity, 1.0))

            # Relevance from TF-IDF
            relevance = relevance_scores.get(i, 0.5)

            # Overall score (heuristic)
            overall = (
                0.30 * relevance +
                0.25 * info_density +
                0.20 * clarity +
                0.15 * hook +
                0.10 * 0.5  # visual_score default 0.5
            )
            # Penalize redundancy later (after computing similarity)
            overall = max(0.0, min(overall, 1.0))

            cand["scores"] = {
                "relevance": relevance,
                "information_density": info_density,
                "clarity": clarity,
                "hook": hook,
                "visual": 0.5,
                "overall": overall,
            }

        # Compute redundancy and adjust overall
        self._adjust_for_redundancy(candidates)

        # Sort by overall descending
        sorted_candidates = sorted(candidates, key=lambda x: x["scores"]["overall"], reverse=True)
        return sorted_candidates

    def _compute_relevance(self, candidates: List[Dict[str, Any]]) -> Dict[int, float]:
        """Compute TF-IDF similarity between candidate text and topic/keywords."""
        if not self.topic and not self.keywords:
            return {i: 0.5 for i in range(len(candidates))}

        texts = [c["text"] for c in candidates]
        if not texts:
            return {}

        # Combine topic and keywords into a single query string
        query_parts = []
        if self.topic:
            query_parts.append(self.topic)
        if self.keywords:
            query_parts.extend(self.keywords)
        query = " ".join(query_parts)

        # Add query to corpus
        corpus = texts + [query]
        vectorizer = TfidfVectorizer(stop_words=None, token_pattern=r"(?u)\b\w+\b")
        tfidf_matrix = vectorizer.fit_transform(corpus)
        # Similarity of each candidate to query (last vector)
        similarities = cosine_similarity(tfidf_matrix[:-1], tfidf_matrix[-1:]).flatten()
        # Normalize to 0-1
        if similarities.size > 0:
            max_sim = similarities.max()
            if max_sim > 0:
                similarities = similarities / max_sim
        return {i: sim for i, sim in enumerate(similarities)}

    def _adjust_for_redundancy(self, candidates: List[Dict[str, Any]]):
        """Penalize candidates that are too similar to higher-scoring ones."""
        if len(candidates) < 2:
            return

        texts = [c["text"] for c in candidates]
        if not any(texts):
            return

        vectorizer = TfidfVectorizer(stop_words=None, token_pattern=r"(?u)\b\w+\b")
        try:
            tfidf = vectorizer.fit_transform(texts)
            similarities = cosine_similarity(tfidf)
        except:
            return

        # Sort by overall descending
        sorted_indices = sorted(range(len(candidates)), key=lambda i: candidates[i]["scores"]["overall"], reverse=True)

        for i, idx in enumerate(sorted_indices):
            # Compare with previous (higher scored) candidates
            for j in range(i):
                prev_idx = sorted_indices[j]
                sim = similarities[idx][prev_idx]
                if sim > 0.6:
                    # Penalize this candidate's overall score
                    penalty = 0.25 * (sim - 0.6) / 0.4  # 0 to 0.25
                    candidates[idx]["scores"]["overall"] = max(0.0, candidates[idx]["scores"]["overall"] - penalty)
                    candidates[idx]["scores"]["redundancy"] = penalty
                    break