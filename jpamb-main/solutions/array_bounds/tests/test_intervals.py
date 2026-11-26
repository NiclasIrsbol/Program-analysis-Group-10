"""Randomised soundness tests for intervals against Java int semantics."""

import random
import unittest

import jvm
from interval import INT_MAX, INT_MIN, Interval

ARITHMETIC = {
    "add": jvm.BinaryOpr.Add,
    "sub": jvm.BinaryOpr.Sub,
    "mul": jvm.BinaryOpr.Mul,
    "div": jvm.BinaryOpr.Div,
    "rem": jvm.BinaryOpr.Rem,
}
CMP = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}
NEGATE = {
    "lt": "ge",
    "le": "gt",
    "gt": "le",
    "ge": "lt",
    "eq": "ne",
    "ne": "eq",
}
EDGES = [INT_MIN, INT_MIN + 1, -1000, -2, -1, 0, 1, 2, 7, 1000, INT_MAX - 1, INT_MAX]


def wrap(value):
    return (value - INT_MIN) % 2**32 + INT_MIN


def jdiv(a, b):
    quotient = abs(a) // abs(b)
    return quotient if (a >= 0) == (b > 0) else -quotient


def jrem(a, b):
    return a - jdiv(a, b) * b


CONCRETE = {
    "add": lambda a, b: wrap(a + b),
    "sub": lambda a, b: wrap(a - b),
    "mul": lambda a, b: wrap(a * b),
    "div": lambda a, b: wrap(jdiv(a, b)),
    "rem": jrem,
}


def rand_interval(rng):
    a = rng.choice(EDGES + [rng.randint(-50, 50)])
    b = rng.choice(EDGES + [rng.randint(-50, 50)])
    return Interval(min(a, b), max(a, b))


def samples(interval, rng, n=6):
    points = {interval.min, interval.max}
    points.update(value for value in EDGES if value in interval)
    points.update(rng.randint(interval.min, interval.max) for _ in range(n))
    return points


class IntervalSoundness(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(2242)

    def test_arithmetic_contains_concrete_results(self):
        for _ in range(3000):
            left, right = rand_interval(self.rng), rand_interval(self.rng)
            for name, operation in ARITHMETIC.items():
                result, _ = left.arithmetic(right, operation)
                for a in samples(left, self.rng):
                    for b in samples(right, self.rng):
                        if name in ("div", "rem") and b == 0:
                            continue
                        self.assertIn(
                            CONCRETE[name](a, b),
                            result,
                            f"{a} {name} {b} not in {left}.{name}({right}) = {result}",
                        )

    def test_refine_keeps_all_satisfying_pairs(self):
        for _ in range(3000):
            left, right = rand_interval(self.rng), rand_interval(self.rng)
            for name, predicate in CMP.items():
                refined_left, refined_right = left.refine(
                    right, jvm.CmpOpr.from_str(name)
                )
                for a in samples(left, self.rng):
                    for b in samples(right, self.rng):
                        if predicate(a, b):
                            self.assertIn(a, refined_left, (left, name, right))
                            self.assertIn(b, refined_right, (left, name, right))

    def test_negation_table(self):
        for operation, negated in NEGATE.items():
            for value in (-1, 0, 1):
                self.assertNotEqual(
                    CMP[operation](value, 0), CMP[negated](value, 0)
                )

    def test_overflow_uses_java_integer_range(self):
        wrapped_add, _ = Interval(INT_MAX, INT_MAX).arithmetic(
            Interval(1, 1), jvm.BinaryOpr.Add
        )
        self.assertEqual(wrapped_add, Interval(INT_MIN, INT_MIN))

        range_overflow, _ = Interval(INT_MAX - 1, INT_MAX).arithmetic(
            Interval(1, 1), jvm.BinaryOpr.Add
        )
        self.assertEqual(range_overflow, Interval.java_int())

        wrapped, _ = Interval(INT_MIN, INT_MIN).arithmetic(
            Interval(-1, -1), jvm.BinaryOpr.Mul
        )
        self.assertEqual(wrapped, Interval(INT_MIN, INT_MIN))

        ordinary, _ = Interval(0, 10).arithmetic(
            Interval(1, 1), jvm.BinaryOpr.Add
        )
        self.assertEqual(ordinary, Interval(1, 11))

    def test_lattice(self):
        for _ in range(2000):
            left, right = rand_interval(self.rng), rand_interval(self.rng)
            joined = left | right
            met = left & right
            widened = left.widen(joined)
            self.assertTrue(left <= joined and right <= joined)
            self.assertTrue(met <= left and met <= right)
            self.assertTrue(joined <= widened)

    def test_widening_stabilises(self):
        interval = Interval(0, 0)
        for upper in range(1, 10):
            widened = interval.widen(interval | Interval(0, upper))
            if widened == interval:
                break
            interval = widened
        self.assertEqual(interval, Interval(0, None))


if __name__ == "__main__":
    unittest.main()
