from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Self

import jvm
import jvm.state as jvms


class Poset(ABC):
    @abstractmethod
    def __eq__(self, other: Self) -> bool: ...

    @abstractmethod
    def __lt__(self, other: Self) -> bool: ...

    def __le__(self, other: Self) -> bool:
        return self == other or self < other

    @abstractmethod
    def __gt__(self, other: Self) -> bool: ...

    def __ge__(self, other: Self) -> bool:
        return self == other or self > other


def is_reflexive(a: Poset):
    # Checking if the relation is reflexive (a == a)
    assert a == a, f"Reflexivity check failed: Element {a} is not related to itself."  # noqa: PLR0124


def is_antisymmetric(a: Poset, b: Poset):
    if a != b:
        # Checking that if a <= b and b <= a, then a must equal b
        assert not (a <= b <= a), (
            f"Antisymmetry violation: Found elements where {a} <= {b} and {b} <= {a}, "
            f"but {a} != {b}."
        )


def is_transitive(a: Poset, b: Poset, c: Poset):
    if a <= b <= c:
        # Checking that if a <= b and b <= c, then a <= c
        assert a <= c, (
            f"Transitivity violation: Found path {a} <= {b} and {b} <= {c}, "
            f"but {a} is not related to {c}."
        )


def is_poset(a: Poset, b: Poset, c: Poset):
    for x in [a, b, c]:
        is_reflexive(x)

    for x in [b, c]:
        is_antisymmetric(a, x)

    is_transitive(a, b, c)


class Lattice(Poset):
    @classmethod
    @abstractmethod
    def top(cls) -> Self:
        raise NotImplementedError(f"top: {cls}")

    @classmethod
    def bot(cls) -> Self:
        return cls.abstract([])

    @abstractmethod
    def __or__(self, other: Self) -> Self: ...

    @abstractmethod
    def __and__(self, other: Self) -> Self: ...


def is_lattice_identities(a: Lattice):
    # Top element identity checks
    assert (a | a.top()) == a.top(), (
        f"Top identity fail: ({a} | {a.top()}) -> {a | a.top()} != {a.top()}"
    )
    assert (a.top() | a) == a.top(), (
        f"Top identity fail (commute): ({a.top()} | {a}) != {a.top()}"
    )
    assert (a & a.top()) == a, f"Top identity fail (meet): ({a} & {a.top()}) != {a}"
    assert (a.top() & a) == a, (
        f"Top identity fail (meet commute): ({a.top()} & {a}) != {a}"
    )

    # Bottom element identity checks
    assert (a & a.bot()) == a.bot(), (
        f"Bottom identity fail: ({a} & {a.bot()}) != {a.bot()}"
    )
    assert (a.bot() & a) == a.bot(), (
        f"Bottom identity fail (commute): ({a.bot()} & {a}) != {a.bot()}"
    )
    assert (a.bot() | a) == a, f"Bottom identity fail (join): ({a.bot()} | {a}) != {a}"
    assert (a | a.bot()) == a, (
        f"Bottom identity fail (join commute): ({a} | {a.bot()}) != {a}"
    )


def is_lattice(a: Lattice, b: Lattice, c: Lattice):
    for x in [a, b, c]:
        is_lattice_identities(x)

    if not (a | b <= c):
        # Calculate values for the error message
        assert not (a <= c and b <= c), (
            f"Upper bound check failed: ({a} | {b}) -> {a | b} is not <= {c}. "
            f"Check individual bounds: {a <= c=}, {b <= c=}."
        )

    if not (c <= a & b):
        # Calculate values for the error message
        assert not (c <= a and c <= b), (
            f"Lower bound check failed: {c} is not <= ({a} & {b}) -> {a & b}. "
            f"Check individual bounds: {c <= a=}, {c <= b=}."
        )


class Abstraction(ABC):
    """This is the minimal interface for an abstraction."""

    @classmethod
    @abstractmethod
    def abstract(cls, values: Iterable[jvms.StackValue]) -> Self:
        raise NotImplementedError(f"abstract: {cls}")

    @abstractmethod
    def __contains__(self, value: jvms.StackValue) -> bool: ...


def is_galoi(a: set[jvms.StackValue], b: Abstraction):
    alpha_a = b.abstract(a)

    for value in a:
        assert value in alpha_a, f"{value} not in {a}"

    if alpha_a <= b:
        for value in a:
            assert value in b, f"{value} not in {b}"


type Sign = Literal[-1, 0, 1]


def to_sign(value: jvms.StackValue) -> Sign:
    match value:
        case jvms.StackInt(v):
            return (v > 0) - (v < 0)
        case a:
            raise NotImplementedError(f"Unsupported value {value!r}")


@dataclass(frozen=True, order=True)
class SignSet(Abstraction, Lattice):
    signs: frozenset[Sign]

    def __str__(self):
        return f"{{{''.join(z for z, b in zip('-0+', [-1, 0, 1]) if b in self.signs)}}}"

    def __sexpr__(self):
        return str(self)

    @classmethod
    def from_sign(cls, code):
        return cls(frozenset(b for z, b in zip("-0+", [-1, 0, 1]) if z in code))

    @classmethod
    def abstract(cls, values: Iterable[jvms.StackValue]) -> Self:
        signs = set()
        for value in values:
            signs.add(to_sign(value))
        return cls(frozenset(signs))

    def __contains__(self, value: jvms.StackValue) -> bool:
        return to_sign(value) in self.signs

    @classmethod
    def top(cls) -> bool:
        return cls(frozenset([-1, 0, 1]))

    def __or__(self, other: "SignSet") -> "SignSet":
        if not isinstance(other, SignSet):
            return NotImplemented

        return SignSet(self.signs | other.signs)

    def __and__(self, other: "SignSet") -> "SignSet":
        if not isinstance(other, SignSet):
            return NotImplemented

        return SignSet(self.signs & other.signs)

    def arithmetic(
        self, other: "SignSet", opr: jvm.BinaryOpr
    ) -> tuple["SignSet", set[str]]:
        match opr:
            case jvm.BinaryOpr.Add:
                output = set()
                if 1 in self.signs:
                    output.add(1)
                    if -1 in other.signs:
                        output.update([0, -1])

                if -1 in self.signs:
                    output.add(-1)
                    if 1 in other.signs:
                        output.update([0, 1])

                if 0 in self.signs:
                    output.update(other.signs)

                return (SignSet(output), set())
            case _:
                raise NotImplementedError(f"TODO: {opr}")

    def compare(self, other: "SignSet", opr: jvm.CmpOpr) -> Iterable[bool]:
        match opr:
            case jvm.CmpOpr.Le:
                cases = set()
                for x in self.signs:
                    for y in other.signs:
                        if x == 0 or y == 0:
                            cases.add(x <= y)
                            continue
                        if x <= y:
                            cases.add(True)
                        if x >= y:
                            cases.add(False)
                return cases
            case _:
                raise NotImplementedError(f"TODO: {opr}")


INT_MIN = -(2**31)
INT_MAX = 2**31 - 1


def wrap_int(value):
    return (value - INT_MIN) % (2**32) + INT_MIN


@dataclass(frozen=True)
class Interval(Abstraction, Lattice):
    min: int | None
    max: int | None

    def __post_init__(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            object.__setattr__(self, "min", 1)
            object.__setattr__(self, "max", 0)

    @property
    def is_bottom(self):
        return self.min is not None and self.max is not None and self.min > self.max

    @property
    def singleton(self):
        return not self.is_bottom and self.min is not None and self.min == self.max

    def __str__(self):
        if self.is_bottom:
            return "bottom"
        return f"{self.min if self.min is not None else '-inf'}:{self.max if self.max is not None else 'inf'}"

    def __sexpr__(self):
        return str(self)

    @classmethod
    def abstract(cls, values):
        numbers = []
        for value in values:
            if not isinstance(value, jvms.StackInt):
                raise TypeError(f"Expected an integer stack value, got {value!r}")
            numbers.append(value.value)
        return cls(min(numbers), max(numbers)) if numbers else cls.bot()

    def __contains__(self, value):
        number = value.value if isinstance(value, jvms.StackInt) else value
        return (
            not self.is_bottom
            and (self.min is None or self.min <= number)
            and (self.max is None or number <= self.max)
        )

    @classmethod
    def top(cls):
        return cls(None, None)

    @classmethod
    def java_int(cls):
        return cls(INT_MIN, INT_MAX)

    @classmethod
    def bot(cls):
        return cls(1, 0)

    def __or__(self, other):
        if self.is_bottom:
            return other
        if other.is_bottom:
            return self
        lower = (
            None if self.min is None or other.min is None else min(self.min, other.min)
        )
        upper = (
            None if self.max is None or other.max is None else max(self.max, other.max)
        )
        return Interval(lower, upper)

    def __and__(self, other):
        if self.is_bottom or other.is_bottom:
            return self.bot()
        lower = other.min if self.min is None else self.min
        if other.min is not None and lower is not None:
            lower = max(lower, other.min)
        upper = other.max if self.max is None else self.max
        if other.max is not None and upper is not None:
            upper = min(upper, other.max)
        return Interval(lower, upper)

    def __lt__(self, other):
        return self != other and (self & other) == self

    def __gt__(self, other):
        return other < self

    def widen(self, other):
        if self.is_bottom:
            return other
        if other.is_bottom:
            return self
        lower = (
            self.min
            if other.min is not None and self.min is not None and other.min >= self.min
            else None
        )
        upper = (
            self.max
            if other.max is not None and self.max is not None and other.max <= self.max
            else None
        )
        return Interval(lower, upper)

    def arithmetic(self, other, opr):
        if self.is_bottom or other.is_bottom:
            return self.bot(), set()
        left, right = self & self.java_int(), other & self.java_int()
        if left.is_bottom or right.is_bottom:
            return self.bot(), set()
        a, b, c, d = left.min, left.max, right.min, right.max
        errors = (
            {"divide by zero"}
            if opr in (jvm.BinaryOpr.Div, jvm.BinaryOpr.Rem) and 0 in right
            else set()
        )
        if opr in (jvm.BinaryOpr.Div, jvm.BinaryOpr.Rem) and c == d == 0:
            return self.bot(), errors
        if left.singleton and right.singleton:
            if opr == jvm.BinaryOpr.Add:
                value = a + c
            elif opr == jvm.BinaryOpr.Sub:
                value = a - c
            elif opr == jvm.BinaryOpr.Mul:
                value = a * c
            else:
                quotient = (abs(a) // abs(c)) * (-1 if (a < 0) != (c < 0) else 1)
                value = quotient if opr == jvm.BinaryOpr.Div else a - quotient * c
            value = wrap_int(value)
            return Interval(value, value), errors
        if opr == jvm.BinaryOpr.Add:
            lower, upper = a + c, b + d
        elif opr == jvm.BinaryOpr.Sub:
            lower, upper = a - d, b - c
        elif opr == jvm.BinaryOpr.Mul:
            values = [x * y for x in (a, b) for y in (c, d)]
            lower, upper = min(values), max(values)
        elif opr == jvm.BinaryOpr.Rem:
            limit = max(abs(c), abs(d)) - 1
            return Interval(
                max(a, -limit) if a < 0 else 0, min(b, limit) if b > 0 else 0
            ), errors
        elif opr == jvm.BinaryOpr.Div:
            divisors = {c, d} - {0}
            divisors.update(x for x in (-1, 1) if x in right)
            values = [
                (abs(x) // abs(y)) * (-1 if (x < 0) != (y < 0) else 1)
                for x in (a, b)
                for y in divisors
            ]
            lower, upper = min(values), max(values)
        else:
            raise NotImplementedError(opr)
        if lower < INT_MIN or upper > INT_MAX:
            return self.java_int(), errors
        return Interval(lower, upper), errors

    def refine(self, other, opr):
        """Project a successful integer comparison onto both operands."""
        if opr == jvm.CmpOpr.Gt:
            right, left = other.refine(self, jvm.CmpOpr.Lt)
            return left, right
        if opr == jvm.CmpOpr.Ge:
            right, left = other.refine(self, jvm.CmpOpr.Le)
            return left, right
        if opr == jvm.CmpOpr.Eq:
            common = self & other
            return common, common
        if opr == jvm.CmpOpr.Ne:
            if self.singleton and other.singleton and self.min == other.min:
                return self.bot(), self.bot()
            left, right = self, other
            if other.singleton:
                if self.min == other.min:
                    left = self & Interval(other.min + 1, None)
                elif self.max == other.min:
                    left = self & Interval(None, other.min - 1)
            if self.singleton and not other.singleton:
                right, _ = other.refine(self, opr)
            return left, right
        strict = int(opr == jvm.CmpOpr.Lt)
        left = self & Interval(None, None if other.max is None else other.max - strict)
        right = other & Interval(None if self.min is None else self.min + strict, None)
        return left, right
