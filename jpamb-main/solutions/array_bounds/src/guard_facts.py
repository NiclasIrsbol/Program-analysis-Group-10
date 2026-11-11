

from __future__ import annotations

from collections import defaultdict

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1


class GuardFacts:
    def __init__(self, variables):
        variables = list(variables)
        if (any(not isinstance(v, str) or not v or v == "0" for v in variables)
                or len(set(variables)) != len(variables)):
            raise ValueError("Variables must be unique, nonempty strings other than '0'")
        self.variables = ["0", *variables]
        self._names = set(self.variables)
        self.facts: dict[tuple[str, str], int] = {}
        self.is_bottom = False

    def __repr__(self):
        if self.is_bottom:
            return "GuardFacts(bottom)"
        return f"GuardFacts({self.facts!r})"

    def __eq__(self, other):
        return (isinstance(other, GuardFacts)
                and self.variables == other.variables
                and self.is_bottom == other.is_bottom
                and self.facts == other.facts)

    def copy(self):
        out = GuardFacts(self.variables[1:])
        out.facts = self.facts.copy()
        out.is_bottom = self.is_bottom
        return out

    def close(self):
        return self.copy()

    def _check(self, *names):
        for name in names:
            if name not in self._names:
                raise KeyError(f"Unknown guard-fact symbol: {name}")

    def _put(self, x, y, bound):
        if x == y:
            if bound < 0:
                self.is_bottom = True
            return 
        key = (x, y)
        self.facts[key] = min(self.facts.get(key, bound), bound)

    def _equality_groups(self):
        parent = {v: v for v in self.variables}

        def find(v):
            while parent[v] != v:
                parent[v] = parent[parent[v]]
                v = parent[v]
            return v

        for (x, y), bound in list(self.facts.items()):
            if (bound <= 0 and self.facts.get((y, x), 1) <= 0):
                parent[find(x)] = find(y)
        groups = defaultdict(set)
        for v in self.variables:
            groups[find(v)].add(v)
        return {v: groups[find(v)] for v in self.variables}

    def _normalize(self):
      
        groups = self._equality_groups()
        old_facts = list(self.facts.items())
        for (x, y), bound in old_facts:
            for ax in groups[x]:
                for ay in groups[y]:
                    self._put(ax, ay, bound)
        # A contradictory pair of direct facts also makes the state empty.
        for (x, y), bound in self.facts.items():
            opposite = self.facts.get((y, x))
            if opposite is not None and bound + opposite < 0:
                self.is_bottom = True
                break

    def assume(self, x, y, bound):
        self._check(x, y)
        if type(bound) is not int:
            raise TypeError("Difference-constraint bound must be an integer")
        out = self.copy()
        if not out.is_bottom:
            out._put(x, y, bound)
            if not out.is_bottom:
                out._normalize()
        return out

    def assume_interval(self, variable, lower=None, upper=None):
        self._check(variable)
        if variable == "0":
            raise ValueError("Cannot constrain distinguished zero")
        out = self.copy()
        if lower is not None:
            out = out.assume("0", variable, -lower)
        if upper is not None:
            out = out.assume(variable, "0", upper)
        return out

    def interval(self, variable):
        self._check(variable)
        if self.is_bottom:
            return (1, 0)
        if variable == "0":
            return (0, 0)
        lower = self.facts.get(("0", variable))
        upper = self.facts.get((variable, "0"))
        return (-lower if lower is not None else None, upper)

    def forget(self, variable):
        self._check(variable)
        if variable == "0":
            raise ValueError("Cannot forget distinguished zero")
        out = self.copy()
        out.facts = {(x, y): b for (x, y), b in out.facts.items()
                     if x != variable and y != variable}
        return out

    def assign(self, target, source="0", offset=0):
       
        self._check(target, source)
        if target == "0":
            raise ValueError("Cannot assign distinguished zero")
        if type(offset) is not int:
            raise TypeError("Assignment offset must be an integer")
        if self.is_bottom:
            return self.copy()
        if source == "0":
            value = (offset - INT_MIN) % (2**32) + INT_MIN
            return self.forget(target).assume_interval(target, value, value)
        if source == target:
            return self.copy() if offset == 0 else self.forget(target)
        out = self.forget(target)
        if offset == 0:
            out = out.assume(target, source, 0)
            out = out.assume(source, target, 0)
        return out

    def _check_other(self, other):
        if not isinstance(other, GuardFacts) or self.variables != other.variables:
            raise ValueError("GuardFacts must use the same variables and ordering")

    def join(self, other):
        self._check_other(other)
        if self.is_bottom:
            return other.copy()
        if other.is_bottom:
            return self.copy()
        out = GuardFacts(self.variables[1:])
        out.facts = {(x, y): max(bound, other.facts[(x, y)])
                     for (x, y), bound in self.facts.items()
                     if (x, y) in other.facts}
        return out

    def widen(self, other):
        self._check_other(other)
        if self.is_bottom:
            return other.copy()
        if other.is_bottom:
            return self.copy()
        out = GuardFacts(self.variables[1:])
        out.facts = {(x, y): old for (x, y), old in self.facts.items()
                     if other.facts.get((x, y), float("inf")) <= old}
        return out

    def is_safe(self, index, length):
     
        self._check(index, length)
        if self.is_bottom:
            return False
        groups = self._equality_groups()
        indexes, lengths = groups[index], groups[length]
        nonnegative = any(self.facts.get(("0", i), float("inf")) <= 0
                          for i in indexes)
        below_length = any(self.facts.get((i, a), float("inf")) <= -1
                           for i in indexes for a in lengths)
        return nonnegative and below_length

    def reduce_with_intervals(self, intervals):
    
        out = self.copy()
        for variable, (lower, upper) in intervals.items():
            if lower is not None and upper is not None and lower > upper:
                out.is_bottom = True
                break
            out = out.assume_interval(variable, lower, upper)
            if out.is_bottom:
                break
        return out, {v: out.interval(v) for v in self.variables[1:]}
