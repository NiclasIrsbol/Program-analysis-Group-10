"""Tests for the standalone array-bounds interpreter.

Run from jpamb-main/:
    uv run --project solutions/array_bounds --no-sync python -m unittest discover \
        -s solutions/array_bounds/tests -t .
"""

import unittest
from dataclasses import dataclass

import jpamb
import jvm
from interpreter import AbstractInterpreter

SAFE = "safe"
WARNING = "warning"
UNRESOLVED = "unresolved"
INT, ARR = jvm.Int(), jvm.Array(jvm.Int())


class FixtureSuite:
    def __init__(self, raw_method):
        self.raw_method = raw_method

    def findmethod(self, methodid, *, eff):
        return self.raw_method

    def method_opcodes(self, methodid, *, eff):
        return (
            jvm.Opcode.from_json(op)
            for op in self.raw_method["code"]["bytecode"]
        )

    def method_max_locals(self, methodid, *, eff):
        return self.raw_method["code"]["max_locals"]


@dataclass
class MethodFixture:
    bytecode: jpamb.Bytecode
    methodid: jvm.AbsMethodID


def push(value):
    return {"opr": "push", "value": {"type": "integer", "value": value}}


def load(index, type_="int"):
    return {"opr": "load", "index": index, "type": type_}


def store(index, type_="int"):
    return {"opr": "store", "index": index, "type": type_}


def ifz(condition, target):
    return {"opr": "ifz", "condition": condition, "target": target}


def if_(condition, target):
    return {"opr": "if", "condition": condition, "target": target}


def goto(target):
    return {"opr": "goto", "target": target}


def binary(operation):
    return {"opr": "binary", "type": "int", "operant": operation}


ARRLEN = {"opr": "arraylength"}
IALOAD = {"opr": "array_load", "type": "int"}
RET_INT = {"opr": "return", "type": "int"}


def method(params, ops, max_locals):
    descriptor = "".join(param.encode() for param in params)
    methodid = jvm.AbsMethodID.decode(f"Test.m:({descriptor})V")
    bytecode = [
        {**opcode, "offset": offset} for offset, opcode in enumerate(ops)
    ]
    raw_method = {
        "access": ["static"],
        "code": {
            "bytecode": bytecode,
            "max_locals": max_locals,
            "max_stack": 8,
            "exceptions": [],
        },
    }
    suite = FixtureSuite(raw_method)
    return MethodFixture(
        jpamb.Bytecode(suite, jpamb.Effect(None), {}),
        methodid,
    )


def guarded_read(cmp="lt", lower=True, after_guard=()):
    head = [load(0, "ref"), ifz("eq", "RET")]
    if lower:
        head.extend([load(1), ifz("lt", "RET")])
    head.extend([load(1), load(0, "ref"), ARRLEN, if_(cmp, "ACC")])
    returns = [push(-1), RET_INT]
    access = list(after_guard) + [load(0, "ref"), load(1), IALOAD, RET_INT]
    ops = head + returns + access
    labels = {"RET": len(head), "ACC": len(head) + len(returns)}
    return method(
        [ARR, INT],
        [
            {**opcode, "target": labels[opcode["target"]]}
            if opcode.get("target") in labels
            else opcode
            for opcode in ops
        ],
        2,
    )


def sum_loop(cmp="ge"):
    ops = [
        push(0),
        store(2),
        push(0),
        store(3),
        load(3),
        load(0, "ref"),
        ARRLEN,
        if_(cmp, 16),
        load(2),
        load(0, "ref"),
        load(3),
        IALOAD,
        binary("add"),
        store(2),
        {"opr": "incr", "index": 3, "amount": 1},
        goto(4),
        load(2),
        RET_INT,
    ]
    return method([ARR], ops, 4)


def verdicts(fixture, domain, exchange=True):
    analysis = AbstractInterpreter.initial(
        fixture.bytecode,
        fixture.methodid,
        domain=domain,
        exchange=exchange,
    ).run()
    _, accesses = analysis.results()
    return [accesses[offset] for offset in sorted(accesses)], analysis


class GuardedAccessTests(unittest.TestCase):
    def test_interval_warns_relational_proves(self):
        fixture = guarded_read()
        self.assertEqual(verdicts(fixture, "interval")[0], [WARNING])
        self.assertEqual(verdicts(fixture, "dbm")[0], [SAFE])

    def test_off_by_one_still_detected(self):
        for domain in ("interval", "dbm"):
            self.assertEqual(
                verdicts(guarded_read(cmp="le"), domain)[0],
                [WARNING],
                domain,
            )

    def test_missing_lower_bound_detected(self):
        for domain in ("interval", "dbm"):
            self.assertEqual(
                verdicts(guarded_read(lower=False), domain)[0],
                [WARNING],
                domain,
            )

    def test_index_changed_after_guard(self):
        increment = [{"opr": "incr", "index": 1, "amount": 1}]
        self.assertEqual(
            verdicts(guarded_read(after_guard=increment), "dbm")[0],
            [WARNING],
        )

    def test_decrement_after_guard_can_break_lower_bound(self):
        decrement = [{"opr": "incr", "index": 1, "amount": -1}]
        self.assertEqual(
            verdicts(guarded_read(after_guard=decrement), "dbm")[0],
            [WARNING],
        )

    def test_array_reassigned_after_guard(self):
        reallocation = [
            push(0),
            {"opr": "newarray", "type": "int", "dim": 1},
            store(0, "ref"),
        ]
        self.assertEqual(
            verdicts(guarded_read(after_guard=reallocation), "dbm")[0],
            [WARNING],
        )

    def test_no_exchange_ablation_runs(self):
        statuses, analysis = verdicts(guarded_read(), "dbm", exchange=False)
        self.assertIsNone(analysis.reason)
        self.assertEqual(len(statuses), 1)


class LoopTests(unittest.TestCase):
    def test_sum_loop(self):
        self.assertEqual(verdicts(sum_loop(), "interval")[0], [WARNING])
        statuses, analysis = verdicts(sum_loop(), "dbm")
        self.assertEqual(statuses, [SAFE])
        self.assertIsNone(analysis.reason)

    def test_sum_loop_off_by_one(self):
        for domain in ("interval", "dbm"):
            self.assertEqual(verdicts(sum_loop("gt"), domain)[0], [WARNING], domain)

    def test_terminates_without_step_limit(self):
        _, analysis = verdicts(sum_loop(), "dbm")
        self.assertLess(analysis.steps, 200)


class UnresolvedTests(unittest.TestCase):
    def test_unsupported_array_type_is_unresolved(self):
        fixture = method(
            [ARR, INT],
            [load(0, "ref"), load(1), {"opr": "array_load", "type": "char"}, RET_INT],
            2,
        )
        statuses, analysis = verdicts(fixture, "dbm")
        self.assertIsNotNone(analysis.reason)
        self.assertEqual(statuses, [UNRESOLVED])


if __name__ == "__main__":
    unittest.main()
