"""Randomised soundness tests for the interval domain against Java int semantics."""

import random
import unittest

from solutions.array_bounds.intervals import (
    INT_MAX,
    INT_MIN,
    NEGATE,
    TOP,
    Interval,
)


def wrap(x):
    return (x - INT_MIN) % 2**32 + INT_MIN


def jdiv(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def jrem(a, b):
    return a - jdiv(a, b) * b


CONCRETE = {
    "add": lambda a, b: wrap(a + b),
    "sub": lambda a, b: wrap(a - b),
    "mul": lambda a, b: wrap(a * b),
    "div": lambda a, b: wrap(jdiv(a, b)),
    "rem": lambda a, b: jrem(a, b),
}
CMP = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}

EDGES = [INT_MIN, INT_MIN + 1, -1000, -2, -1, 0, 1, 2, 7, 1000, INT_MAX - 1, INT_MAX]


def rand_interval(rng):
    a, b = rng.choice(EDGES + [rng.randint(-50, 50)]), rng.choice(EDGES + [rng.randint(-50, 50)])
    return Interval(min(a, b), max(a, b))


def samples(i, rng, n=6):
    pts = {i.lo, i.hi}
    pts |= {v for v in EDGES if v in i}
    pts |= {rng.randint(i.lo, i.hi) for _ in range(n)}
    return pts


class IntervalSoundness(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(2242)

    def test_arithmetic_contains_concrete_results(self):
        for _ in range(3000):
            a, b = rand_interval(self.rng), rand_interval(self.rng)
            for name, f in CONCRETE.items():
                r = getattr(a, name)(b)
                for x in samples(a, self.rng):
                    for y in samples(b, self.rng):
                        if name in ("div", "rem") and y == 0:
                            continue
                        self.assertIn(f(x, y), r, f"{x} {name} {y} not in {a}.{name}({b}) = {r}")

    def test_refine_keeps_all_satisfying_pairs(self):
        for _ in range(3000):
            a, b = rand_interval(self.rng), rand_interval(self.rng)
            for op, f in CMP.items():
                ra, rb = Interval.refine(a, op, b)
                for x in samples(a, self.rng):
                    for y in samples(b, self.rng):
                        if f(x, y):
                            self.assertIn(x, ra, (a, op, b))
                            self.assertIn(y, rb, (a, op, b))

    def test_negation_table(self):
        for op, neg in NEGATE.items():
            for x in (-1, 0, 1):
                self.assertNotEqual(CMP[op](x, 0), CMP[neg](x, 0))

    def test_overflow_goes_to_top(self):
        self.assertEqual(Interval(INT_MAX, INT_MAX).add(Interval.const(1)), TOP)
        self.assertEqual(Interval(INT_MIN, INT_MIN).neg(), TOP)
        self.assertEqual(Interval(0, 10).add(Interval.const(1)), Interval(1, 11))

    def test_lattice(self):
        for _ in range(2000):
            a, b = rand_interval(self.rng), rand_interval(self.rng)
            j, m, w = a.join(b), a.meet(b), a.widen(a.join(b))
            self.assertTrue(a.leq(j) and b.leq(j))
            self.assertTrue(m.leq(a) and m.leq(b))
            self.assertTrue(j.leq(w))

    def test_widening_stabilises(self):
        x = Interval.const(0)
        for k in range(1, 10):
            new = x.widen(x.join(Interval(0, k)))
            if new == x:
                break
            x = new
        self.assertEqual(x, Interval(0, INT_MAX))


if __name__ == "__main__":
    unittest.main()
