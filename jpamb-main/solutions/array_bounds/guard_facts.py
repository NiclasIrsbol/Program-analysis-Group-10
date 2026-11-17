from __future__ import annotations
from typing import Iterable

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1

Fact = tuple[str, str, str] 

class GuardFacts:
    def __init__(self, variables: Iterable[str]):
        variables = list(variables)
        if (
            any(not isinstance(v, str) or not v or v == "0" for v in variables)
            or len(set(variables)) != len(variables)
        ):
            raise ValueError( "Variables must be unique, nonempty strings other than '0'")
        self.variables = ["0", *variables]
        self._names = set(self.variables)
        self.facts: set[Fact] = set()
        self.is_bottom = False

    def __repr__(self):
        if self.is_bottom:
            return "GuardFacts(bottom)"
        return f"GuardFacts({sorted(self.facts)!r})"

    def __eq__(self, other):
        return (
            isinstance(other, GuardFacts)
            and self.variables == other.variables
            and self.is_bottom == other.is_bottom
            and self.facts == other.facts
        )

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

    def _normalize(self):
        if self.is_bottom:
            return

        atoms = self.facts | {
            ("le", x, y) for relation, x, y in self.facts if relation == "lt"
        }

        parent = {name: name for name in self.variables}

        def find(name):
            while parent[name] != name:
                parent[name] = parent[parent[name]]
                name = parent[name]
            return name
        for relation, x, y in atoms:
            if relation == "le" and ("le", y, x) in atoms:
                parent[find(x)] = find(y)

        groups: dict[str, set[str]] = {}
        for name in self.variables:
            root = find(name)
            groups.setdefault(root, set()).add(name)
        aliases = {name: groups[find(name)] for name in self.variables}

        normalized: set[Fact] = set()
        for relation, x, y in atoms:
            for ax in aliases[x]:
                for ay in aliases[y]:
                    if ax == ay:
                        if relation == "lt":
                            self.is_bottom = True
                            return
                        continue  # x <= x is a tautology
                    normalized.add((relation, ax, ay))

        for relation, x, y in normalized:
            if relation == "lt" and ("le", y, x) in normalized:
                self.is_bottom = True
                return

        self.facts = normalized

    def _add_comparison(self, x: str, y: str, bound: int):
        if bound < 0:
            self.facts.add(("lt", x, y))
        elif bound == 0:
            self.facts.add(("le", x, y))

    def assume(self, x, y, bound):
        self._check(x, y)
        if type(bound) is not int:
            raise TypeError("Difference-constraint bound must be an integer")
        out = self.copy()
        if not out.is_bottom:
            out._add_comparison(x, y, bound)
            out._normalize()
        return out

    def assume_nonnegative(self, index):
        return self.assume("0", index, 0)

    def assume_below_length(self, index, array_length):
        return self.assume(index, array_length, -1)

    def assume_interval(self, variable, lower=None, upper=None):
        self._check(variable)
        if variable == "0":
            raise ValueError("Cannot constrain distinguished zero")
        out = self.copy()
        if out.is_bottom:
            return out
        if lower is not None and upper is not None and lower > upper:
            out.is_bottom = True
            return out
        if lower is not None:
            if lower > 0:
                out._add_comparison("0", variable, -1)
            elif lower == 0:
                out._add_comparison("0", variable, 0)
        if upper is not None:
            if upper < 0:
                out._add_comparison(variable, "0", -1)
            elif upper == 0:
                out._add_comparison(variable, "0", 0)
        out._normalize()
        return out

    def interval(self, variable):
        self._check(variable)
        if self.is_bottom:
            return (1, 0)
        if variable == "0":
            return (0, 0)
        lower = None
        upper = None
        if ("le", "0", variable) in self.facts:
            lower = 0
        if ("lt", "0", variable) in self.facts:
            lower = 1
        if ("le", variable, "0") in self.facts:
            upper = 0
        if ("lt", variable, "0") in self.facts:
            upper = -1
        return lower, upper

    def forget(self, variable):
        self._check(variable)
        if variable == "0":
            raise ValueError("Cannot forget distinguished zero")
        out = self.copy()
        out.facts = {
            (relation, x, y)
            for relation, x, y in out.facts
            if x != variable and y != variable
        }
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
            constant = (offset - INT_MIN) % (2**32) + INT_MIN
            return self.forget(target).assume_interval(
                target, lower=constant, upper=constant
            )

        if source == target:
            return self.copy() if offset == 0 else self.forget(target)

        out = self.forget(target)
        if offset == 0:
            out.facts.add(("le", target, source))
            out.facts.add(("le", source, target))
            out._normalize()
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
        out.facts = self.facts & other.facts
        return out

    def widen(self, other):
        return self.join(other)

    def is_safe(self, index, length):
      
        self._check(index, length)
        if self.is_bottom:
            return False
        nonnegative = ("le", "0", index) in self.facts
        below_length = ("lt", index, length) in self.facts
        return nonnegative and below_length

    def reduce_with_intervals(self, intervals):
        out = self.copy()
        for variable, (lower, upper) in intervals.items():
            out._check(variable)
            if variable == "0":
                raise ValueError("Cannot constrain distinguished zero")
            if lower is not None and upper is not None and lower > upper:
                out.is_bottom = True
                break
            if lower is not None:
                if lower > 0:
                    out._add_comparison("0", variable, -1)
                elif lower == 0:
                    out._add_comparison("0", variable, 0)
            if upper is not None:
                if upper < 0:
                    out._add_comparison(variable, "0", -1)
                elif upper == 0:
                    out._add_comparison(variable, "0", 0)

        if not out.is_bottom:
            out._normalize()
        tightened = {name: out.interval(name) for name in self.variables[1:]}
        if not out.is_bottom:
            for variable, (incoming_lower, incoming_upper) in intervals.items():
                lower, upper = tightened[variable]
                lower = max(incoming_lower, lower) if incoming_lower is not None and lower is not None else (incoming_lower if lower is None else lower)
                upper = min(incoming_upper, upper) if incoming_upper is not None and upper is not None else (incoming_upper if upper is None else upper)
                if lower is not None and upper is not None and lower > upper:
                    out.is_bottom = True
                    break
        return out, tightened
