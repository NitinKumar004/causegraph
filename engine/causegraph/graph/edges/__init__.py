"""Edge-rule registry. The builder runs DEFAULT_RULES; adding a heuristic that
works on existing node types is a new file + one line here (the §6 seam)."""
from causegraph.graph.edges.base import EdgeRule  # noqa: F401
from causegraph.graph.edges.file_watch import FileWatchRule
from causegraph.graph.edges.spawn import SpawnRule

# The certain edge first, then inferred rules.
DEFAULT_RULES: list = [SpawnRule(), FileWatchRule()]
