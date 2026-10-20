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

            # Visual scene activity: candidates spanning multiple fast cuts indicate action
            scene_count = len(cand.get("scene_ids", []))
            visual_score = min(scene_count / 3.0, 1.0)

            # Action and emotional arousal keywords (Vietnamese & English)
            text_lower = (cand.get("text") or "").lower()
            action_cues = (
                "snake", "snakes", "rắn", "trăn", "python", "bite", "bit", "cắn",
                "blood", "máu", "dead", "death", "chết", "danger", "nguy hiểm",
                "attack", "tấn công", "hét", "sợ", "chạy", "run", "boat", "thuyền",
                "lost", "mất", "god", "chúa", "trời", "holy", "look", "nhìn", "wow",
            )
            action_hits = sum(1 for cue in action_cues if cue in text_lower)
            action_score = min(action_hits * 0.15, 1.0)

            # Relevance from TF-IDF
            relevance = relevance_scores.get(i, 0.5)

            # Overall score (heuristic)
            overall = (
                0.30 * relevance +
                0.20 * info_density +
                0.15 * clarity +
                0.15 * hook +
                0.10 * action_score +
                0.10 * visual_score
            )
            # Penalize redundancy later (after computing similarity)
            overall = max(0.0, min(overall, 1.0))

            cand["scores"] = {
                "relevance": relevance,
                "information_density": info_density,
                "clarity": clarity,
                "hook": hook,
                "action": action_score,
                "visual": visual_score,
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
            # Auto-extract top thematic keywords from the entire corpus (Global Topic Discovery)
            texts = [c.get("text", "") for c in candidates if c.get("text")]
            if not texts or len(texts) < 2:
                return {i: 0.5 for i in range(len(candidates))}
            try:
                vectorizer = TfidfVectorizer(max_features=40, stop_words="english", token_pattern=r"(?u)\b\w+\b")
                tfidf_matrix = vectorizer.fit_transform(texts)
                centroid = np.asarray(tfidf_matrix.mean(axis=0))
                similarities = cosine_similarity(tfidf_matrix, centroid).flatten()
                min_s = float(similarities.min())
                max_s = float(similarities.max())
                if max_s > min_s:
                    normalized = 0.3 + 0.6 * (similarities - min_s) / (max_s - min_s)
                else:
                    normalized = np.full_like(similarities, 0.5)

                idx_map = {}
                text_idx = 0
                for i, c in enumerate(candidates):
                    if c.get("text"):
                        idx_map[i] = float(normalized[text_idx])
                        text_idx += 1
                    else:
                        idx_map[i] = 0.3
                return idx_map
            except Exception:
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