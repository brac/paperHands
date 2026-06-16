"""Shared kernel: configuration, data contracts, and logging.

Not one of the original PROJECT.md module boundaries, but the specs mandate typed
dataclass contracts and a dependency-injected config object. `core/` is the honest,
import-cycle-free home for both — every other package may import from `core`, and `core`
imports from none of them.
"""
