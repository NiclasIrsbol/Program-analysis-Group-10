from abstractions import Lattice, Abstraction
from dataclasses import dataclass
import jvm.state as jvms
import jvm

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
