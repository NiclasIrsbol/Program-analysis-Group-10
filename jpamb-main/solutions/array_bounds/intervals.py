"""Interval domain over Java 32-bit ints (overflow -> TOP)."""

from dataclasses import dataclass

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1


@dataclass(frozen=True)
class Interval:
    lo: int
    hi: int

    # -- construction -----------------------------------------------------
    @staticmethod
    def top() -> "Interval":
        return TOP

    @staticmethod
    def bottom() -> "Interval":
        return BOTTOM

    @staticmethod
    def const(value: int) -> "Interval":
        return Interval.of(value, value)

    @staticmethod
    def of(lo: int | None, hi: int | None) -> "Interval":
        """Clamp to the int range; None means unbounded on that side."""
        lo = INT_MIN if lo is None else max(lo, INT_MIN)
        hi = INT_MAX if hi is None else min(hi, INT_MAX)
        return Interval(lo, hi) if lo <= hi else BOTTOM

    # -- queries ----------------------------------------------------------
    @property
    def is_bottom(self) -> bool:
        return self.lo > self.hi

    @property
    def is_const(self) -> bool:
        return self.lo == self.hi

    def __contains__(self, value: int) -> bool:
        return self.lo <= value <= self.hi

    def __str__(self) -> str:
        if self.is_bottom:
            return "bot"
        lo = "-inf" if self.lo == INT_MIN else str(self.lo)
        hi = "inf" if self.hi == INT_MAX else str(self.hi)
        return f"[{lo}, {hi}]"

    # -- lattice ----------------------------------------------------------
    def leq(self, other: "Interval") -> bool:
        if self.is_bottom:
            return True
        if other.is_bottom:
            return False
        return other.lo <= self.lo and self.hi <= other.hi

    def join(self, other: "Interval") -> "Interval":
        if self.is_bottom:
            return other
        if other.is_bottom:
            return self
        return Interval(min(self.lo, other.lo), max(self.hi, other.hi))

    def meet(self, other: "Interval") -> "Interval":
        if self.is_bottom or other.is_bottom:
            return BOTTOM
        return Interval.of(max(self.lo, other.lo), min(self.hi, other.hi))

    def widen(self, new: "Interval") -> "Interval":
        """Standard interval widening: unstable bounds jump to the type bound."""
        if self.is_bottom:
            return new
        if new.is_bottom:
            return self
        lo = self.lo if new.lo >= self.lo else INT_MIN
        hi = self.hi if new.hi <= self.hi else INT_MAX
        return Interval(lo, hi)

    # -- arithmetic (int32 semantics, overflow -> TOP) ----------------------
    @staticmethod
    def _checked(lo: int, hi: int) -> "Interval":
        if lo < INT_MIN or hi > INT_MAX:
            return TOP
        return Interval(lo, hi)

    def add(self, other: "Interval") -> "Interval":
        if self.is_bottom or other.is_bottom:
            return BOTTOM
        return self._checked(self.lo + other.lo, self.hi + other.hi)

    def sub(self, other: "Interval") -> "Interval":
        if self.is_bottom or other.is_bottom:
            return BOTTOM
        return self._checked(self.lo - other.hi, self.hi - other.lo)

    def neg(self) -> "Interval":
        if self.is_bottom:
            return BOTTOM
        return self._checked(-self.hi, -self.lo)

    def mul(self, other: "Interval") -> "Interval":
        if self.is_bottom or other.is_bottom:
            return BOTTOM
        products = [a * b for a in (self.lo, self.hi) for b in (other.lo, other.hi)]
        return self._checked(min(products), max(products))

    def div(self, other: "Interval") -> "Interval":
        """Java truncating division; the caller removes 0 from the divisor."""
        if self.is_bottom or other.is_bottom:
            return BOTTOM
        quotients = []
        for b in _nonzero_parts(other):
            for a in (self.lo, self.hi):
                for d in (b.lo, b.hi):
                    quotients.append(_jdiv(a, d))
        if not quotients:
            return BOTTOM
        return self._checked(min(quotients), max(quotients))

    def rem(self, other: "Interval") -> "Interval":
        """Java remainder: sign follows the dividend, |r| < |divisor|."""
        if self.is_bottom or other.is_bottom:
            return BOTTOM
        parts = _nonzero_parts(other)
        if not parts:
            return BOTTOM
        m = max(max(abs(p.lo), abs(p.hi)) for p in parts) - 1
        lo = 0 if self.lo >= 0 else max(-m, self.lo)
        hi = 0 if self.hi <= 0 else min(m, self.hi)
        return Interval.of(lo, hi)

    def cast(self, lo: int, hi: int) -> "Interval":
        """Narrowing cast (i2s, i2c, i2b): exact if it fits, else the type range."""
        if self.is_bottom:
            return BOTTOM
        if lo <= self.lo and self.hi <= hi:
            return self
        return Interval(lo, hi)

    # -- comparison refinement --------------------------------------------
    @staticmethod
    def refine(a: "Interval", op: str, b: "Interval") -> tuple["Interval", "Interval"]:
        """Return the largest (a', b') within (a, b) consistent with `a op b`."""
        if a.is_bottom or b.is_bottom:
            return BOTTOM, BOTTOM
        match op:
            case "lt":
                a, b = a.meet(Interval.of(None, b.hi - 1)), b.meet(Interval.of(a.lo + 1, None))
            case "le":
                a, b = a.meet(Interval.of(None, b.hi)), b.meet(Interval.of(a.lo, None))
            case "gt":
                b, a = Interval.refine(b, "lt", a)
            case "ge":
                b, a = Interval.refine(b, "le", a)
            case "eq":
                a = b = a.meet(b)
            case "ne":
                if b.is_const:
                    a = _shave(a, b.lo)
                if a.is_const:
                    b = _shave(b, a.lo)
            case _:
                raise ValueError(op)
        if a.is_bottom or b.is_bottom:
            return BOTTOM, BOTTOM
        return a, b


NEGATE = {"lt": "ge", "le": "gt", "gt": "le", "ge": "lt", "eq": "ne", "ne": "eq"}

TOP = Interval(INT_MIN, INT_MAX)
BOTTOM = Interval(1, 0)


def _shave(a: Interval, value: int) -> Interval:
    if a.lo == value:
        return Interval.of(value + 1, a.hi)
    if a.hi == value:
        return Interval.of(a.lo, value - 1)
    return a


def _nonzero_parts(i: Interval) -> list[Interval]:
    parts = [i.meet(Interval.of(None, -1)), i.meet(Interval.of(1, None))]
    return [p for p in parts if not p.is_bottom]


def _jdiv(a: int, b: int) -> int:
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q
