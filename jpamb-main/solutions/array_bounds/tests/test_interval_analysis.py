import io
import itertools
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

import static as analysis
from abstractions import INT_MAX, INT_MIN, Interval, wrap_int
from array_bounds.difference_bounds import DifferenceBounds
from array_bounds.evaluate import evaluate
from static import AbstractInterpreter, State, Value

import jpamb
import jvm


def op(name, **fields):
    return {"opr": name, "offset": 0, **fields}


def load(index, kind="int"):
    return op("load", index=index, type=kind)


def push(value):
    return op("push", value={"type": "integer", "value": value})


def program(code, signature="([II)I", max_locals=3):
    methodid = jvm.AbsMethodID.decode(f"Example.check:{signature}")
    raw = {"access": ["public", "static"], "code": {"max_stack": 6, "exceptions": []}}
    method = jvm.Method(
        methodid, [jvm.Opcode.from_json(item) for item in code], max_locals
    )
    suite = SimpleNamespace(findmethod=lambda *args, **kwargs: raw)
    return jpamb.Bytecode(suite, None, {methodid: method}), methodid


def guarded(lower=True, upper="ge", change=False, rebind=False):
    code = [load(0, "ref"), op("ifz", condition="eq", target=-1)]
    if lower:
        code += [load(1), op("ifz", condition="lt", target=-1)]
    code += [
        load(1),
        load(0, "ref"),
        op("arraylength"),
        op("if", condition=upper, target=-1),
    ]
    if change:
        code += [op("incr", index=1, amount=1)]
    if rebind:
        code += [
            push(1),
            op("newarray", type="int", dim=1),
            op("store", type="ref", index=0),
        ]
    code += [
        load(0, "ref"),
        load(1),
        op("array_load", type="int"),
        op("return", type="int"),
    ]
    target = len(code)
    code += [push(0), op("return", type="int")]
    for item in code:
        if item.get("target") == -1:
            item["target"] = target
    return code


class IntervalTests(unittest.TestCase):
    def test_lattice_and_widening(self):
        intervals = [
            Interval.bot(),
            Interval.top(),
            Interval(None, 2),
            Interval(-1, None),
            Interval(0, 3),
        ]
        for a, b in itertools.product(intervals, repeat=2):
            self.assertLessEqual(a, a | b)
            self.assertLessEqual(b, a | b)
            self.assertLessEqual(a & b, a)
            self.assertLessEqual(a & b, b)
            self.assertLessEqual(a, a.widen(b))
            self.assertLessEqual(b, a.widen(b))
        self.assertEqual(Interval(0, 3).widen(Interval(-1, 4)), Interval.top())

    def test_arithmetic_contains_concrete_results(self):
        ranges = [
            (-5, -2),
            (-1, 1),
            (0, 3),
            (2, 5),
            (INT_MIN, INT_MIN + 1),
            (INT_MAX - 1, INT_MAX),
        ]
        for (a, b), (c, d), operation in itertools.product(
            ranges, ranges, list(jvm.BinaryOpr)
        ):
            interval, errors = Interval(a, b).arithmetic(Interval(c, d), operation)
            self.assertEqual(
                "divide by zero" in errors,
                operation in (jvm.BinaryOpr.Div, jvm.BinaryOpr.Rem) and c <= 0 <= d,
            )
            for x, y in itertools.product(range(a, b + 1), range(c, d + 1)):
                if operation in (jvm.BinaryOpr.Div, jvm.BinaryOpr.Rem) and y == 0:
                    continue
                quotient = (
                    (abs(x) // abs(y)) * (-1 if (x < 0) != (y < 0) else 1) if y else 0
                )
                concrete = {
                    jvm.BinaryOpr.Add: x + y,
                    jvm.BinaryOpr.Sub: x - y,
                    jvm.BinaryOpr.Mul: x * y,
                    jvm.BinaryOpr.Div: quotient,
                    jvm.BinaryOpr.Rem: x - quotient * y,
                }[operation]
                self.assertIn(wrap_int(concrete), interval, (x, y, operation, interval))

    def test_comparison_refinement_preserves_satisfying_pairs(self):
        comparisons = {
            jvm.CmpOpr.Eq: lambda x, y: x == y,
            jvm.CmpOpr.Ne: lambda x, y: x != y,
            jvm.CmpOpr.Lt: lambda x, y: x < y,
            jvm.CmpOpr.Le: lambda x, y: x <= y,
            jvm.CmpOpr.Gt: lambda x, y: x > y,
            jvm.CmpOpr.Ge: lambda x, y: x >= y,
        }
        ranges = [Interval(a, b) for a in range(-2, 3) for b in range(a, 3)]
        for a, b, comparison in itertools.product(ranges, ranges, comparisons):
            left, right = a.refine(b, comparison)
            for x, y in itertools.product(
                range(a.min, a.max + 1), range(b.min, b.max + 1)
            ):
                if comparisons[comparison](x, y):
                    self.assertIn(x, left)
                    self.assertIn(y, right)


class InterpreterTests(unittest.TestCase):
    def analyse(
        self, code, relational=True, signature="([II)I", max_steps=10000, exchange=True
    ):
        bc, methodid = program(code, signature)
        interpreter = AbstractInterpreter.initial(
            bc, methodid, relational=relational, exchange=exchange
        ).run(max_steps)
        finals, accesses = interpreter.results()
        return interpreter, finals, accesses

    def test_symbolic_guard_improves_baseline(self):
        for relational in (False, True):
            interpreter, finals, accesses = self.analyse(guarded(), relational)
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertNotIn("null pointer", finals)
            self.assertEqual("out of bounds" in finals, not relational)
            self.assertEqual(
                list(accesses.values()), ["safe" if relational else "warning"]
            )

    def test_incomplete_guards_and_overwrites_warn(self):
        for code in (
            guarded(lower=False),
            guarded(upper="gt"),
            guarded(change=True),
            guarded(rebind=True),
        ):
            interpreter, finals, accesses = self.analyse(code)
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertIn("out of bounds", finals)
            self.assertIn("warning", accesses.values())

    def test_constants_and_array_store(self):
        for relational, index in itertools.product((False, True), (-1, 0, 1, 2)):
            code = [
                push(2),
                op("newarray", type="int", dim=1),
                push(index),
                push(42),
                op("array_store", type="int"),
                op("return", type=None),
            ]
            interpreter, finals, accesses = self.analyse(code, relational, "()V")
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertEqual("out of bounds" in finals, index not in (0, 1))
            self.assertEqual(
                list(accesses.values()), ["safe" if index in (0, 1) else "warning"]
            )

    def test_empty_array_guard_is_unreachable(self):
        code = [
            push(0),
            op("newarray", type="int", dim=1),
            op("store", type="ref", index=0),
        ] + guarded()
        for item in code[3:]:
            if "target" in item:
                item["target"] += 3
        for relational in (False, True):
            _, finals, accesses = self.analyse(code, relational)
            self.assertNotIn("out of bounds", finals)
            self.assertEqual(list(accesses.values()), ["unreachable"])

    def test_null_access_is_not_reported_as_proven_bounds_safety(self):
        code = [
            op("push", value=None),
            push(0),
            op("array_load", type="int"),
            op("return", type="int"),
        ]
        _, finals, accesses = self.analyse(code, signature="()I")
        self.assertEqual(finals, {"null pointer"})
        self.assertEqual(accesses, {2: "unreachable"})

    def test_guarded_loop_converges_and_proves_bounds(self):
        code = [
            push(0),
            op("store", type="int", index=1),
            load(1),
            load(0, "ref"),
            op("arraylength"),
            op("if", condition="ge", target=12),
            load(0, "ref"),
            load(1),
            op("array_load", type="int"),
            op("store", type="int", index=2),
            op("incr", index=1, amount=1),
            op("goto", target=2),
            op("return", type=None),
        ]
        for relational in (False, True):
            interpreter, finals, accesses = self.analyse(code, relational, "([I)V")
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertLess(interpreter.steps, 150)
            self.assertEqual("out of bounds" in finals, not relational)
            self.assertEqual(accesses[8], "safe" if relational else "warning")

    def test_step_limit_and_unsupported_instruction_are_unresolved(self):
        interpreter, _, accesses = self.analyse(guarded(), max_steps=1)
        self.assertTrue(interpreter.incomplete)
        self.assertEqual(set(accesses.values()), {"unresolved"})
        code = guarded()
        code[0] = op("new", **{"class": "java/lang/Object"})
        interpreter, _, accesses = self.analyse(code)
        self.assertTrue(interpreter.incomplete)
        self.assertEqual(set(accesses.values()), {"unresolved"})

    def test_same_state_comparison_uses_dbm_contents(self):
        bc, methodid = program(guarded())
        state = next(iter(AbstractInterpreter.initial(bc, methodid).states.values()))
        self.assertTrue(state.same_as(state.copy()))
        changed = state.copy()
        changed.bounds = changed.bounds.assume("l1", "l0", -1)
        self.assertFalse(state.same_as(changed))

    def test_join_drops_a_relation_from_only_one_path(self):
        code = [
            load(2),
            op("ifz", condition="eq", target=6),
            load(1),
            load(0, "ref"),
            op("arraylength"),
            op("if", condition="ge", target=10),
            load(0, "ref"),
            load(1),
            op("array_load", type="int"),
            op("return", type="int"),
            push(0),
            op("return", type="int"),
        ]
        for relational in (False, True):
            interpreter, finals, accesses = self.analyse(code, relational, "([IIZ)I")
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertIn("out of bounds", finals)
            self.assertEqual(accesses[8], "warning")

    def test_transfer_does_not_modify_a_stored_state(self):
        bc, methodid = program(guarded())
        interpreter = AbstractInterpreter.initial(bc, methodid)
        initial = next(iter(interpreter.states.values()))
        snapshot = initial.copy()
        interpreter.step()
        self.assertTrue(initial.same_as(snapshot))

    def test_java_overflow_invalidates_guard(self):
        code = [
            push(2),
            op("newarray", type="int", dim=1),
            op("store", type="ref", index=0),
            push(INT_MAX),
            push(1),
            op("binary", type="int", operant="add"),
            op("store", type="int", index=1),
            load(0, "ref"),
            load(1),
            op("array_load", type="int"),
            op("return", type="int"),
        ]
        for relational in (False, True):
            _, finals, accesses = self.analyse(code, relational, "()I")
            self.assertIn("out of bounds", finals)
            self.assertEqual(accesses[9], "warning")

    def test_descending_loop(self):
        code = [
            load(0, "ref"),
            op("arraylength"),
            push(1),
            op("binary", type="int", operant="sub"),
            op("store", type="int", index=1),
            load(1),
            op("ifz", condition="lt", target=13),
            load(0, "ref"),
            load(1),
            op("array_load", type="int"),
            op("store", type="int", index=2),
            op("incr", index=1, amount=-1),
            op("goto", target=5),
            op("return", type=None),
        ]
        for relational, exchange in ((False, False), (True, True), (True, False)):
            interpreter, finals, accesses = self.analyse(
                code, relational, "([I)V", exchange=exchange
            )
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            if relational and exchange:
                self.assertNotIn("out of bounds", finals)
                self.assertEqual(accesses[9], "safe")
            elif not relational:
                self.assertIn("out of bounds", finals)

    def test_off_by_one_loop_warns(self):
        code = [
            push(0),
            op("store", type="int", index=1),
            load(1),
            load(0, "ref"),
            op("arraylength"),
            op("if", condition="gt", target=12),
            load(0, "ref"),
            load(1),
            op("array_load", type="int"),
            op("store", type="int", index=2),
            op("incr", index=1, amount=1),
            op("goto", target=2),
            op("return", type=None),
        ]
        for relational, exchange in ((False, False), (True, True), (True, False)):
            interpreter, finals, accesses = self.analyse(
                code, relational, "([I)V", exchange=exchange
            )
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertIn("out of bounds", finals)
            self.assertEqual(accesses[8], "warning")

    def test_transitive_loop_bound(self):
        code = [
            load(1),
            load(0, "ref"),
            op("arraylength"),
            op("if", condition="gt", target=16),
            push(0),
            op("store", type="int", index=2),
            load(2),
            load(1),
            op("if", condition="ge", target=16),
            load(0, "ref"),
            load(2),
            op("array_load", type="int"),
            op("store", type="int", index=3),
            op("incr", index=2, amount=1),
            op("goto", target=6),
            op("return", type=None),
            op("return", type=None),
        ]
        bc, methodid = program(code, "([II)V", max_locals=4)
        for relational, exchange in ((False, False), (True, True), (True, False)):
            interpreter = AbstractInterpreter.initial(
                bc, methodid, relational=relational, exchange=exchange
            ).run()
            finals, accesses = interpreter.results()
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertEqual("out of bounds" in finals, not relational)
            self.assertEqual(accesses[11], "safe" if relational else "warning")

    def test_allocation_boundaries(self):
        for length in (-1, 0, 1, 8):
            code = [
                push(length),
                op("store", type="int", index=0),
                load(0),
                op("newarray", type="int", dim=1),
                load(0),
                push(1),
                op("binary", type="int", operant="sub"),
                op("array_load", type="int"),
                op("return", type="int"),
            ]
            for relational, exchange in ((False, False), (True, True), (True, False)):
                interpreter, finals, accesses = self.analyse(
                    code, relational, "()I", exchange=exchange
                )
                self.assertFalse(interpreter.incomplete, interpreter.reason)
                self.assertEqual("negative array size" in finals, length < 0)
                self.assertEqual("out of bounds" in finals, length == 0)
                self.assertEqual(
                    accesses[7],
                    "unreachable"
                    if length < 0
                    else "warning"
                    if length == 0
                    else "safe",
                )
        # Variable n establishes length = n, so n-1 is safe on n > 0.
        code = [
            load(0),
            op("ifz", condition="le", target=9),
            load(0),
            op("newarray", type="int", dim=1),
            load(0),
            push(1),
            op("binary", type="int", operant="sub"),
            op("array_load", type="int"),
            op("return", type="int"),
            push(0),
            op("return", type="int"),
        ]
        interpreter, finals, accesses = self.analyse(code, signature="(I)I")
        self.assertFalse(interpreter.incomplete, interpreter.reason)
        self.assertNotIn("out of bounds", finals)
        self.assertEqual(accesses[7], "safe")

    def test_decreasing_index_without_lower_guard_warns(self):
        code = [
            load(0, "ref"),
            op("arraylength"),
            push(1),
            op("binary", type="int", operant="sub"),
            op("store", type="int", index=1),
            load(0, "ref"),
            load(1),
            op("array_load", type="int"),
            op("store", type="int", index=2),
            op("incr", index=1, amount=-1),
            op("goto", target=5),
        ]
        for relational, exchange in ((False, False), (True, True), (True, False)):
            interpreter, finals, accesses = self.analyse(
                code, relational, "([I)V", exchange=exchange
            )
            self.assertFalse(interpreter.incomplete, interpreter.reason)
            self.assertIn("out of bounds", finals)
            self.assertEqual(accesses[7], "warning")


class ExchangeAndFailureTests(unittest.TestCase):
    def test_exchange_switch_skips_import_and_export(self):
        state = State(
            [Value(Interval(0, 10)), Value(Interval(1, 8))],
            [],
            DifferenceBounds(["l0", "l1"]).assume("l0", "l1", -1),
            False,
        )
        with patch.object(
            DifferenceBounds, "reduce_with_intervals", side_effect=AssertionError
        ):
            self.assertTrue(state.reduce())
        self.assertEqual(state.bounds.interval("l0"), (None, None))
        self.assertEqual(state.locals[1].interval, Interval(1, 8))
        self.assertFalse(state.copy().exchange)
        self.assertFalse(state.merge(state.copy()).exchange)
        enabled = state.copy()
        enabled.exchange = True
        self.assertTrue(enabled.reduce())
        self.assertEqual(enabled.bounds.interval("l0"), (0, 7))
        self.assertEqual(enabled.locals[0].interval, Interval(0, 7))
        self.assertEqual(enabled.locals[1].interval, Interval(1, 8))
        state.bounds = state.bounds.assume("l0", "l0", -1)
        self.assertFalse(state.reduce())

    def test_branch_constraints_still_work_without_exchange(self):
        bc, methodid = program(guarded())
        interpreter = AbstractInterpreter.initial(bc, methodid, exchange=False).run()
        finals, accesses = interpreter.results()
        self.assertFalse(interpreter.incomplete, interpreter.reason)
        self.assertNotIn("out of bounds", finals)
        self.assertEqual(set(accesses.values()), {"safe"})
        self.assertTrue(
            all(not state.exchange for state in interpreter.states.values())
        )

    def test_processing_and_result_errors_become_unresolved(self):
        for phase in ("step", "results"):
            bc, methodid = program(guarded())
            interpreter = AbstractInterpreter.initial(bc, methodid)
            if phase == "results":
                interpreter.run()
            stderr = io.StringIO()
            with (
                redirect_stderr(stderr),
                patch.object(analysis, "manystep", side_effect=KeyError("injected")),
            ):
                if phase == "step":
                    interpreter.step()
                _, accesses = interpreter.results()
            self.assertTrue(interpreter.incomplete)
            self.assertIn("internal error", interpreter.reason)
            self.assertEqual(set(accesses.values()), {"unresolved"})
            self.assertIn("Traceback", stderr.getvalue())

    def test_cli_errors_always_emit_all_maybe_predictions(self):
        for phase in ("setup", "processing", "results"):
            bc, methodid = program(guarded())
            target = {
                "setup": (jpamb, "setup"),
                "processing": (analysis, "manystep"),
                "results": (AbstractInterpreter, "results"),
            }[phase]
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch.object(jpamb, "getmethodid", return_value=methodid),
                patch.object(jpamb, "setup", return_value=(bc.suite, None)),
                patch.object(jpamb, "Bytecode", return_value=bc),
                patch.object(*target, side_effect=KeyError("injected")),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                analysis.analyse()
            self.assertEqual(
                stdout.getvalue().splitlines(), [f"{q};maybe" for q in jpamb.QUERIES]
            )
            self.assertIn("Traceback", stderr.getvalue())

    def test_exchange_configuration_metadata(self):
        for exchange in ("on", "off"):
            with patch.dict(
                analysis.os.environ, {"JPAMB_DOMAIN": "dbm", "JPAMB_EXCHANGE": exchange}
            ):
                relational, enabled, tags = analysis.configuration()
            self.assertTrue(relational)
            self.assertEqual(enabled, exchange == "on")
            self.assertIn(f"exchange-{exchange}", tags)

    def test_coverage_counts_each_configuration_on_same_methods(self):
        bc, methodid = program(guarded())
        report = evaluate(bc, [methodid, methodid])
        self.assertEqual(len(report["configurations"]), 3)
        for configuration in report["configurations"]:
            self.assertEqual(
                configuration["methods"], {"total": 1, "completed": 1, "unresolved": 0}
            )
            self.assertEqual(configuration["unknown_access_count_methods"], 0)
            expected = "warning" if configuration["name"] == "interval" else "safe"
            self.assertEqual(configuration["accesses"][expected], 1)
            self.assertEqual(configuration["results"][0]["method"], methodid.encode())

    def test_coverage_reports_unsupported_methods_and_unknown_access_counts(self):
        for phase in ("initial", "load", "processing", "results"):
            bc, methodid = program(guarded())
            target = {
                "initial": (AbstractInterpreter, "initial"),
                "load": (bc, "getmethod"),
                "processing": (analysis, "manystep"),
                "results": (AbstractInterpreter, "results"),
            }[phase]
            with (
                patch.object(*target, side_effect=KeyError("injected")),
                redirect_stderr(io.StringIO()),
            ):
                report = evaluate(bc, [methodid])
            for configuration in report["configurations"]:
                self.assertEqual(configuration["methods"]["unresolved"], 1)
                self.assertEqual(configuration["methods"]["completed"], 0)
                self.assertEqual(
                    configuration["unknown_access_count_methods"], int(phase == "load")
                )
                result = configuration["results"][0]
                self.assertIn("injected", result["reason"])
                if phase != "load":
                    self.assertEqual(configuration["accesses"]["unresolved"], 1)


if __name__ == "__main__":
    unittest.main()
