"""tvastar.contrib.ltm.graphify — NetworkX bridge for LTM relationships.

Optional bridge that converts the relationships table into a NetworkX DiGraph
for graph algorithms (PageRank, community detection, centrality, etc.).

NetworkX is NOT a required dependency. Import raises ImportError with install
instructions if missing.

Usage:
    from tvastar.contrib.ltm.graphify import to_networkx
    from tvastar.contrib.ltm.store import LTMStore

    store = LTMStore("memory.db")
    G = to_networkx(store)  # DiGraph of active relationships
    G = to_networkx(store, include_expired=True)  # include expired edges
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import LTMStore


def to_networkx(store: "LTMStore", include_expired: bool = False):
    """Return a networkx.DiGraph built from the relationships table.

    Each relationship becomes a directed edge with attributes:
        edge_type, valid_from, valid_until, confidence.

    Args:
        store: An LTMStore instance with a relationships table.
        include_expired: If False (default), skip relationships where
            valid_until is not NULL. If True, include all relationships.

    Returns:
        networkx.DiGraph with nodes as fact keys and edges as relationships.

    Raises:
        ImportError: If networkx is not installed.
    """
    try:
        import networkx as nx
    except ImportError:
        raise ImportError(
            "Install networkx: pip install tvastar[graph]"
        ) from None

    G = nx.DiGraph()

    # ponytail: direct SQL query is simpler and faster than iterating via
    # relationships_of() for every known key.
    query = "SELECT source_key, edge_type, target_key, valid_from, valid_until, confidence FROM relationships"
    if not include_expired:
        query += " WHERE valid_until IS NULL"

    rows = store._conn.execute(query).fetchall()
    for source, edge_type, target, valid_from, valid_until, confidence in rows:
        G.add_edge(
            source,
            target,
            edge_type=edge_type,
            valid_from=valid_from,
            valid_until=valid_until,
            confidence=confidence,
        )

    return G
