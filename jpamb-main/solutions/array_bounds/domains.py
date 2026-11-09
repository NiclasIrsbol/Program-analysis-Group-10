"""Relational-domain interface used by the engine, plus the DBM adapter.

Variables: "0" (zero), "L<k>" (int in local k), "#L<k>" (length of array in local k).
"""

from math import inf
from typing import Protocol, Self

from solutions.array_bounds.difference_bounds import DifferenceBounds


class RelationalDomain(Protocol):
    @classmethod
    def top(cls, variables: list[str]) -> Self:
        """No relations known between the given variables."""
        ...

    @property
    def is_bottom(self) -> bool: ...

    # -- transfer ------------------------------------------------------------
    def assign(self, target: str, source: str, offset: int) -> Self:
        """target := source + offset (source may be "0" or target itself).

        Must be sound under Java int overflow: if source + offset may
        overflow, the domain must drop the facts about target instead.
        """
        ...

    def forget(self, variable: str) -> Self:
        """Drop all facts that mention `variable` (it got an unknown value)."""
        ...

    def assume(self, x: str, y: str, bound: int) -> Self:
        """Add the branch constraint x - y <= bound (x or y may be "0")."""
        ...

    # -- lattice -------------------------------------------------------------
    def join(self, other: Self) -> Self:
        """Keep only facts that hold on both incoming paths."""
        ...

    def widen(self, other: Self) -> Self:
        """Widening applied at loop heads; must guarantee termination."""
        ...

    def leq(self, other: Self) -> bool:
        """self is at least as precise as other (inclusion of concretisations)."""
        ...

    # -- queries / information exchange ---------------------------------------
    def upper(self, x: str, y: str) -> float:
        """Best known upper bound on x - y (math.inf if unknown)."""
        ...

    def reduce(
        self, intervals: dict[str, tuple[int, int]]
    ) -> tuple[Self, dict[str, tuple[int, int]]]:
        """Information exchange: import interval bounds, export tighter ones.

        Return the refined relational state and, for every variable, the
        (lo, hi) bounds the relational state now implies. Lower/upper may be
        None for "unbounded"; (1, 0) means bottom.
        """
        ...

    def size(self) -> int:
        """Rough measure of the state's size, for the abstract-state counts."""
        ...


class DBMDomain:
    """Adapter that exposes Person 3's DifferenceBounds via RelationalDomain."""

    __slots__ = ("dbm", "_closed")

    def __init__(self, dbm: DifferenceBounds):
        self.dbm = dbm
        self._closed = None

    @classmethod
    def top(cls, variables):
        return cls(DifferenceBounds(variables))

    def closed(self) -> DifferenceBounds:
        """Shortest-path closure, computed at most once per state."""
        if self._closed is None:
            self._closed = self.dbm.close()
        return self._closed

    @property
    def is_bottom(self):
        return self.closed().is_bottom

    def assign(self, target, source, offset):
        return DBMDomain(self.dbm.assign(target, source, offset))

    def forget(self, variable):
        return DBMDomain(self.dbm.forget(variable))

    def assume(self, x, y, bound):
        return DBMDomain(self.dbm.assume(x, y, bound))

    def join(self, other):
        return DBMDomain(self.dbm.join(other.dbm))

    def widen(self, other):
        return DBMDomain(self.dbm.widen(other.dbm))

    def leq(self, other):
        # Same as DifferenceBounds.is_subset_of, but reusing cached closures.
        left, right = self.closed(), other.closed()
        if left.is_bottom:
            return True
        if right.is_bottom:
            return False
        return all(
            a <= b for ra, rb in zip(left.dbm, right.dbm) for a, b in zip(ra, rb)
        )

    def upper(self, x, y):
        closed = self.closed()
        if closed.is_bottom:
            return -inf
        return closed.dbm[closed.index[x]][closed.index[y]]

    def reduce(self, intervals):
        """Same result as DifferenceBounds.reduce_with_intervals, but with one
        closure instead of one per imported bound and per exported variable."""
        d = self.dbm.copy()
        for var, (lo, hi) in intervals.items():
            i = d.index[var]
            if lo is not None:
                d.dbm[0][i] = min(d.dbm[0][i], -lo)
            if hi is not None:
                d.dbm[i][0] = min(d.dbm[i][0], hi)
        c = d.close()
        result = DBMDomain(c)
        result._closed = c
        if c.is_bottom:
            return result, {v: (1, 0) for v in c.variables[1:]}
        bounds = {}
        for var in c.variables[1:]:
            i = c.index[var]
            lo, hi = c.dbm[0][i], c.dbm[i][0]
            bounds[var] = (None if lo == inf else -lo, None if hi == inf else hi)
        return result, bounds

    def size(self):
        return sum(1 for row in self.dbm.dbm for b in row if b != inf)

    def __repr__(self):
        return f"DBMDomain({self.dbm.dbm})"


def _guard_domain():
    """Person 2's guard-fact domain, if it has been added yet."""
    try:
        from solutions.array_bounds.guard_facts import GuardFactDomain
    except ImportError:
        return None
    return GuardFactDomain


# name -> relational domain class (None = interval-only baseline)
RELATIONAL_DOMAINS = {
    "interval": None,
    "dbm": DBMDomain,
}
if (_guard := _guard_domain()) is not None:
    RELATIONAL_DOMAINS["guard"] = _guard
