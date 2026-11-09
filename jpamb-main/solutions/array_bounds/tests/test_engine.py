"""Engine tests: the real JPAMB array cases plus hand-assembled bytecode.

Run from jpamb-main/:  python -m unittest discover -s solutions/array_bounds/tests -t .
"""

import unittest

from solutions.array_bounds.engine import (
    SAFE,
    UNRESOLVED,
    WARNING,
    Analysis,
    analyse,
    method_from_json,
)

import jvm

INT, ARR = jvm.Int(), jvm.Array(jvm.Int())


# -- a tiny assembler: jvm2json-style dicts, jump targets are instruction indices
def push(v):
    return {"opr": "push", "value": {"type": "integer", "value": v}}


def load(i, t="int"):
    return {"opr": "load", "index": i, "type": t}


def store(i, t="int"):
    return {"opr": "store", "index": i, "type": t}


def ifz(cond, target):
    return {"opr": "ifz", "condition": cond, "target": target}


def if_(cond, target):
    return {"opr": "if", "condition": cond, "target": target}


def goto(t):
    return {"opr": "goto", "target": t}


def binary(op):
    return {"opr": "binary", "type": "int", "operant": op}


ARRLEN = {"opr": "arraylength"}
IALOAD = {"opr": "array_load", "type": "int"}
RET_INT = {"opr": "return", "type": "int"}
RET = {"opr": "return", "type": None}


def method(params, ops, max_locals):
    code = [{**op, "offset": i} for i, op in enumerate(ops)]
    m = {"access": ["static"], "code": {"bytecode": code, "max_locals": max_locals, "exceptions": []}}
    return method_from_json("Test.m", params, m)


def guarded_read(cmp="lt", lower=True, after_guard=()):
    """static int read(int[] a, int i) {
         if (a == null || i < 0 || i >= a.length) return -1;   // Listing 1
         <after_guard>
         return a[i]; }

    `cmp` is the jump to the access: "lt" is correct, "le" is off by one.
    """
    head = [load(0, "ref"), ifz("is", "RET")]
    if lower:
        head += [load(1), ifz("lt", "RET")]
    head += [load(1), load(0, "ref"), ARRLEN, if_(cmp, "ACC")]
    ret = [push(-1), RET_INT]
    acc = list(after_guard) + [load(0, "ref"), load(1), IALOAD, RET_INT]
    ops = head + ret + acc
    labels = {"RET": len(head), "ACC": len(head) + len(ret)}
    ops = [{**o, "target": labels[o["target"]]} if o.get("target") in labels else o for o in ops]
    return method([ARR, INT], ops, 2)


def sum_loop(cmp="ge"):
    """int s = 0; for (int i = 0; i < a.length; i++) s += a[i];
    With cmp="gt" the guard is i <= a.length (faulty)."""
    ops = [
        push(0), store(2),  # 0: s = 0
        push(0), store(3),  # 2: i = 0
        load(3), load(0, "ref"), ARRLEN, if_(cmp, 16),  # 4: loop head
        load(2), load(0, "ref"), load(3), IALOAD, binary("add"), store(2),  # 8
        {"opr": "incr", "index": 3, "amount": 1},  # 14
        goto(4),  # 15
        load(2), RET_INT,  # 16: exit
    ]
    return method([ARR], ops, 4)


def verdicts(m, domain, **kw):
    r = Analysis(m, domain, **kw).run()
    return [a.verdict for a in r.accesses], r


class ListingOne(unittest.TestCase):
    def test_interval_warns_relational_proves(self):
        m = guarded_read()
        self.assertEqual(verdicts(m, "interval")[0], [WARNING])
        v, r = verdicts(m, "dbm")
        self.assertEqual(v, [SAFE])
        self.assertEqual(r.accesses[0].null, "safe")  # a == null was ruled out

    def test_off_by_one_still_detected(self):
        for d in ("interval", "dbm"):
            self.assertEqual(verdicts(guarded_read(cmp="le"), d)[0], [WARNING], d)

    def test_missing_lower_bound_detected(self):
        for d in ("interval", "dbm"):
            self.assertEqual(verdicts(guarded_read(lower=False), d)[0], [WARNING], d)

    def test_index_changed_after_guard(self):
        bump = [{"opr": "incr", "index": 1, "amount": 1}]
        self.assertEqual(verdicts(guarded_read(after_guard=bump), "dbm")[0], [WARNING])

    def test_decrement_after_guard_stays_safe_for_upper_but_not_lower(self):
        dec = [{"opr": "incr", "index": 1, "amount": -1}]
        self.assertEqual(verdicts(guarded_read(after_guard=dec), "dbm")[0], [WARNING])

    def test_array_reassigned_after_guard(self):
        realloc = [push(0), {"opr": "newarray", "type": "int", "dim": 1}, store(0, "ref")]
        self.assertEqual(verdicts(guarded_read(after_guard=realloc), "dbm")[0], [WARNING])

    def test_no_exchange_ablation_runs(self):
        v, r = verdicts(guarded_read(), "dbm", exchange=False)
        self.assertIsNone(r.unresolved)
        self.assertEqual(len(v), 1)


class Loops(unittest.TestCase):
    def test_sum_loop(self):
        self.assertEqual(verdicts(sum_loop(), "interval")[0], [WARNING])
        v, r = verdicts(sum_loop(), "dbm")
        self.assertEqual(v, [SAFE])
        self.assertIsNone(r.unresolved)

    def test_sum_loop_off_by_one(self):
        for d in ("interval", "dbm"):
            self.assertEqual(verdicts(sum_loop("gt"), d)[0], [WARNING], d)

    def test_terminates_without_step_limit(self):
        _, r = verdicts(sum_loop(), "dbm")
        self.assertLess(r.steps, 200)


class Soundness(unittest.TestCase):
    def test_unsupported_opcode_is_unresolved(self):
        m = method([ARR, INT], [load(0, "ref"), load(1), {"opr": "weird"}, IALOAD, RET_INT], 2)
        v, r = verdicts(m, "dbm")
        self.assertIsNotNone(r.unresolved)
        self.assertEqual(v, [UNRESOLVED])


class JPAMBArrays(unittest.TestCase):
    """Known faulty accesses must stay detected in every configuration."""

    FAULTY = {
        "jpamb.cases.Arrays.arrayOutOfBounds:()V",
        "jpamb.cases.Arrays.arraySometimesNull:(I)V",
        "jpamb.cases.Arrays.arraySpellsHello:([C)V",
    }
    RELATIONAL_WINS = {
        "jpamb.cases.Arrays.arraySumIsLarge:([I)V",
        "jpamb.cases.Calls.allPrimesArePositive:(I)V",
    }

    def test_faulty_detected(self):
        for m in self.FAULTY:
            for d in ("interval", "dbm"):
                self.assertEqual(analyse(m, d).summary, WARNING, (m, d))

    def test_relational_removes_false_warnings(self):
        for m in self.RELATIONAL_WINS:
            self.assertEqual(analyse(m, "interval").summary, WARNING, m)
            self.assertEqual(analyse(m, "dbm").summary, SAFE, m)


if __name__ == "__main__":
    unittest.main()


class DBMAdapter(unittest.TestCase):
    def test_fast_reduce_matches_reference(self):
        import random

        from solutions.array_bounds.difference_bounds import DifferenceBounds
        from solutions.array_bounds.domains import DBMDomain

        rng = random.Random(7)
        names = ["L0", "#L0", "L1", "#L1"]
        for _ in range(300):
            d = DifferenceBounds(names)
            for _ in range(rng.randint(0, 4)):
                x, y = rng.sample(["0"] + names, 2)
                d = d.assume(x, y, rng.randint(-5, 5))
            itv = {}
            for n in rng.sample(names, rng.randint(0, 4)):
                lo = rng.choice([None, rng.randint(-5, 5)])
                hi = rng.choice([None, rng.randint(-5, 5)])
                itv[n] = (lo, hi)
            ref, ref_bounds = d.reduce_with_intervals(itv)
            fast, fast_bounds = DBMDomain(d).reduce(itv)
            self.assertEqual(ref.close().is_bottom, fast.is_bottom)
            if not fast.is_bottom:
                self.assertEqual(ref_bounds, fast_bounds)
                self.assertEqual(ref.close().dbm, fast.closed().dbm)
