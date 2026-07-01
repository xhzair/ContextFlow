"""Auto-discovery engine: finds workspace patterns via co-occurrence clustering.

Algorithm:
  1. Read co-occurrence matrix from DB
  2. Compute Jaccard distance matrix
  3. Agglomerative hierarchical clustering (Ward linkage)
  4. Cut dendrogram at dynamic threshold
  5. Name clusters heuristically
  6. Return suggested workspaces with confidence scores
"""

import time
import math
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.spatial.distance import squareform

from contextflow.storage.db import ContextFlowDB
from contextflow.engine.ai_namer import generate_workspace_name


# Minimum data requirements before clustering
MIN_OBSERVATIONS = 30  # at least 30 snapshots (15 min at 30s intervals)
MIN_CO_OCCURRENCE = 3  # apps must co-occur at least 3 times


@dataclass
class WorkspaceSuggestion:
    """An auto-discovered workspace pattern."""
    apps: list[str]
    cluster_id: int
    confidence: float          # 0-1, how "tight" the cluster is
    suggested_name: str
    color: str = "#4A90D9"


class DiscoveryEngine:
    """Finds workspace patterns from co-occurrence data."""

    def __init__(self, db: ContextFlowDB):
        self._db = db
        # App categories that are likely "noise" (always open / irrelevant)
        self._noise_categories = {"system", "other"}

    def analyze(self) -> list[WorkspaceSuggestion]:
        """Run the full discovery pipeline. Returns sorted suggestions."""
        # 1. Check if we have enough data
        co_occurrences = self._db.get_co_occurrence_matrix()
        if len(co_occurrences) < MIN_OBSERVATIONS:
            return []

        # 2. Build the app vocabulary and co-occurrence matrix
        apps, matrix = self._build_co_occurrence_matrix(co_occurrences)
        if len(apps) < 3:
            return []  # Need at least 3 distinct apps to cluster

        # 3. Compute Jaccard distance
        n = len(apps)
        dist_matrix = np.zeros((n, n))

        for i in range(n):
            for j in range(i + 1, n):
                co = matrix[i][j]
                a_alone = max(1, matrix[i][i] + matrix[i][j])
                b_alone = max(1, matrix[j][j] + matrix[i][j])
                jaccard_sim = co / (a_alone + b_alone - co) if (a_alone + b_alone - co) > 0 else 0
                dist_matrix[i][j] = 1.0 - jaccard_sim
                dist_matrix[j][i] = dist_matrix[i][j]

        # 4. Hierarchical clustering
        condensed = squareform(dist_matrix, checks=False)
        try:
            Z = linkage(condensed, method="ward")
        except Exception:
            return []

        # 5. Dynamic cluster cut
        clusters = self._cut_clusters(Z, n)

        # 6. Build suggestions
        suggestions = self._build_suggestions(apps, clusters, dist_matrix)

        return sorted(suggestions, key=lambda s: s.confidence, reverse=True)

    # ── internals ────────────────────────────────────────────────────

    def _build_co_occurrence_matrix(self, co_occurrences: list[dict]):
        """Build app vocabulary and co-occurrence matrix from DB records."""
        # Collect all unique apps and their co-occurrence counts
        app_set: set[str] = set()
        co_counts: dict[tuple[str, str], int] = {}

        for row in co_occurrences:
            a, b = row["app_a"], row["app_b"]
            app_set.add(a)
            app_set.add(b)
            co_counts[(a, b)] = row["co_count"]

        apps = sorted(app_set)
        app_idx = {app: i for i, app in enumerate(apps)}
        n = len(apps)
        matrix = np.zeros((n, n))

        for (a, b), count in co_counts.items():
            i, j = app_idx[a], app_idx[b]
            matrix[i][j] = count
            matrix[j][i] = count

        # Set diagonal to the total times each app appeared (co_count sum for row)
        for i in range(n):
            matrix[i][i] = max(1, matrix[i].sum())

        return apps, matrix

    def _cut_clusters(self, Z, n: int) -> dict[int, list[int]]:
        """Cut dendrogram at a dynamic height to produce meaningful clusters.

        Strategy: try multiple cut heights, pick the one that produces
        2-6 clusters of size 2-8 apps each.
        """
        heights = Z[:, 2]  # column 2 = linkage heights
        if len(heights) == 0:
            return {}

        best_clusters = None
        best_score = 0

        # Try cut at percentiles of the height range
        for percentile in [30, 40, 50, 60, 70, 80]:
            cutoff = np.percentile(heights, percentile) if len(heights) > 1 else heights[0]
            labels = fcluster(Z, cutoff, criterion="distance")
            clusters: dict[int, list[int]] = {}
            for i, label in enumerate(labels):
                if label not in clusters:
                    clusters[label] = []
                clusters[label].append(i)

            # Score: prefer clusters of size 2-8, penalize singletons and giants
            score = 0
            for members in clusters.values():
                size = len(members)
                if 2 <= size <= 8:
                    score += 3  # good size
                elif size == 1:
                    score -= 1  # singletons are useless
                elif size > 8:
                    score -= 2  # too large, not meaningful

            if 2 <= len(clusters) <= 6:
                score += 5  # good number of clusters

            if score > best_score:
                best_score = score
                best_clusters = clusters

        return best_clusters or {}

    def _build_suggestions(self, apps: list[str], clusters: dict[int, list[int]],
                           dist_matrix: np.ndarray) -> list[WorkspaceSuggestion]:
        """Convert clusters into user-facing suggestions."""
        suggestions = []
        used_apps: set[str] = set()

        # Predefined color palette
        colors = ["#4A90D9", "#E74C3C", "#27AE60", "#F39C12", "#9B59B6", "#1ABC9C"]
        color_idx = 0

        for cluster_id, indices in clusters.items():
            if len(indices) < 2:
                continue

            cluster_apps = [apps[i] for i in indices]

            # Remove system apps from the display set
            display_apps = [a for a in cluster_apps if a not in used_apps]
            if len(display_apps) < 2:
                continue

            # Compute intra-cluster similarity as confidence
            similarities = []
            for i in range(len(indices)):
                for j in range(i + 1, len(indices)):
                    sim = 1.0 - dist_matrix[indices[i]][indices[j]]
                    similarities.append(sim)
            confidence = float(np.mean(similarities)) if similarities else 0.5

            # Naming: try AI first, fall back to heuristic
            ai_name = generate_workspace_name(display_apps)
            suggested_name = ai_name if ai_name else self._name_cluster(display_apps)

            suggestions.append(WorkspaceSuggestion(
                apps=display_apps,
                cluster_id=cluster_id,
                confidence=round(min(1.0, confidence), 2),
                suggested_name=suggested_name,
                color=colors[color_idx % len(colors)],
            ))
            color_idx += 1

            for a in display_apps:
                used_apps.add(a)

        return suggestions

    def _name_cluster(self, apps: list[str]) -> str:
        """Heuristically generate a workspace name from app names."""
        # Prioritize editors/IDEs as workspace names
        editors = {"VS Code", "Visual Studio", "IntelliJ", "Sublime Text", "Notepad++"}
        office = {"Excel", "Word", "PowerPoint", "Outlook"}
        comm = {"WeChat", "DingTalk", "Slack", "Teams", "Discord", "QQ"}
        browsers = {"Chrome", "Edge", "Firefox", "Brave"}
        terminals = {"Windows Terminal", "Command Prompt", "PuTTY"}

        cats: dict[str, list[str]] = {
            "Coding": [], "Office": [], "Comm": [], "Browse": [],
            "Terminal": [], "Other": [],
        }

        for a in apps:
            if a in editors or any(e.lower() in a.lower() for e in editors):
                cats["Coding"].append(a)
            elif a in office or a in terminals:
                cats["Office"].append(a) if a in office else cats["Terminal"].append(a)
            elif a in comm:
                cats["Comm"].append(a)
            elif a in browsers:
                cats["Browse"].append(a)
            else:
                cats["Other"].append(a)

        # Build description from largest category
        priority = ["Coding", "Office", "Browse", "Comm", "Terminal", "Other"]
        for cat in priority:
            if cats[cat]:
                primary = cats[cat][0]
                if len(cats[cat]) > 1:
                    primary += f" +{len(cats[cat]) - 1}"
                # Add secondary
                secondary_cats = [c for c in priority if c != cat and cats[c]]
                if secondary_cats:
                    secondary = secondary_cats[0]
                    return f"{primary} & {secondary}"
                return primary

        return "Workspace"

    def needs_data(self) -> bool:
        """Check if we need more data before clustering is meaningful."""
        co_occurrences = self._db.get_co_occurrence_matrix()
        return len(co_occurrences) < MIN_OBSERVATIONS
