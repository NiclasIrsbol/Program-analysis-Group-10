import itertools
import unittest
from math import inf
from unittest.mock import patch

from hypothesis import given
from hypothesis import strategies as st

from solutions.array_bounds.difference_bounds import INT_MAX, INT_MIN, DifferenceBounds


def contains(state, values):
    numbers = [0] + [values[name] for name in state.variables[1:]]
    return not state.is_bottom and all(
        bound == inf or numbers[i] - numbers[j] <= bound
        for i, row in enumerate(state.dbm)
        for j, bound in enumerate(row)
    )


class DifferenceBoundsTests(unittest.TestCase):
    def test_copy_skips_constructor_and_owns_its_containers(self):
        state = DifferenceBounds(["x", "y"])
        with patch.object(DifferenceBounds, "__init__", side_effect=AssertionError):
            copied = state.copy()
        copied.variables.append("z")
        copied.index["z"] = 3
        copied.dbm[1][2] = 7
        self.assertEqual(state.variables, ["0", "x", "y"])
        self.assertNotIn("z", state.index)
        self.assertEqual(state.dbm[1][2], inf)

    def test_batched_constraints_and_validation(self):
        state = DifferenceBounds(["x", "y"])
        constraints = [("x", "y", -1), ("y", "0", 8), ("0", "x", 0)]
        expected = state
        for constraint in constraints:
            expected = expected.assume(*constraint)
        self.assertEqual(state.assume_many(constraints).dbm, expected.dbm)
        self.assertTrue(state.assume_many([("x", "x", -1)]).is_bottom)
        for bound in (True, 1.5, inf):
            with self.assertRaises(TypeError):
                state.assume_many([("x", "y", bound)])
            with self.assertRaises(TypeError):
                state.reduce_with_intervals({"x": (bound, None)})
        for operation in (
            state.assume_interval,
            lambda name: state.reduce_with_intervals({name: (None, None)}),
        ):
            with self.assertRaises(KeyError):
                operation("missing")
            with self.assertRaises(ValueError):
                operation("0")

    def test_reduction_closes_once_and_matches_sequential_constraints(self):
        state = DifferenceBounds(["x", "y", "z"]).assume("x", "y", -1)
        intervals = {"x": (0, 5), "y": (2, 8), "z": (None, 10)}
        expected = state
        for variable, bounds in intervals.items():
            expected = expected.assume_interval(variable, *bounds)
        close = DifferenceBounds.close
        with patch.object(
            DifferenceBounds, "close", autospec=True, side_effect=close
        ) as calls:
            reduced, projections = state.reduce_with_intervals(intervals)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(reduced.dbm, expected.dbm)
        self.assertEqual(
            projections, {name: expected.interval(name) for name in state.variables[1:]}
        )
        bottom, projections = state.reduce_with_intervals({"x": (9, 10), "y": (0, 2)})
        self.assertTrue(bottom.is_bottom)
        self.assertEqual(projections, dict.fromkeys(["x", "y", "z"], (1, 0)))

    def test_initial_matrix(self):
        state = DifferenceBounds(["i", "length"])
        self.assertEqual(state.dbm, [[0, inf, inf], [inf, 0, inf], [inf, inf, 0]])
        state.dbm[1][2] = -1
        self.assertEqual(state.dbm[0][2], inf)
        for names in (["x", "x"], ["0"], [""]):
            with self.assertRaises(ValueError):
                DifferenceBounds(names)

    def test_closure_and_contradiction(self):
        state = DifferenceBounds(["x", "y", "z"])
        state = state.assume("x", "y", 2).assume("y", "z", 3)
        self.assertEqual(state.dbm[1][3], 5)
        self.assertTrue(state.assume("z", "x", -6).is_bottom)

    def test_both_bounds_are_required(self):
        top = DifferenceBounds(["i", "length"])
        upper_only = top.assume("i", "length", -1)
        self.assertFalse(upper_only.is_safe("i", "length"))
        self.assertTrue(upper_only.assume("0", "i", 0).is_safe("i", "length"))
        off_by_one = top.assume("0", "i", 0).assume("i", "length", 0)
        self.assertFalse(off_by_one.is_safe("i", "length"))

    def test_empty_array_guard_is_unreachable(self):
        state = DifferenceBounds(["i", "length"]).assign("length", offset=0)
        state = state.assume("0", "i", 0).assume("i", "length", -1)
        self.assertTrue(state.is_bottom)
        self.assertFalse(state.is_safe("i", "length"))

    def test_join_retains_weaker_bounds_and_contains_both_paths(self):
        top = DifferenceBounds(["x", "y"])
        left = top.assume("x", "y", 1)
        right = top.assume("x", "y", 4)
        joined = left.join(right)
        self.assertEqual(joined.dbm[1][2], 4)
        self.assertTrue(left.is_subset_of(joined))
        self.assertTrue(right.is_subset_of(joined))
        self.assertFalse(joined.is_subset_of(left))
        for x, y in itertools.product(range(-5, 6), repeat=2):
            values = {"x": x, "y": y}
            if contains(left, values) or contains(right, values):
                self.assertTrue(contains(joined, values))

    def test_join_does_not_keep_a_guard_from_only_one_path(self):
        top = DifferenceBounds(["i", "length"])
        upper_only = top.assume("i", "length", -1)
        safe = upper_only.assume("0", "i", 0)
        self.assertFalse(safe.join(upper_only).is_safe("i", "length"))

    def test_forget_preserves_consequences_for_other_variables(self):
        state = DifferenceBounds(["x", "y", "z"])
        state = state.assume("x", "y", 2).assume("y", "z", 3)
        result = state.forget("y")
        self.assertEqual(result.dbm[1][3], 5)
        self.assertEqual(result.dbm[1][2], inf)
        self.assertEqual(result.dbm[2][3], inf)

    def test_assignment_replaces_old_value(self):
        state = DifferenceBounds(["x", "y"]).assign("x", offset=100)
        state = state.assign("y", offset=2)
        result = state.assign("x", "y", 3)
        self.assertEqual(result.interval("x"), (5, 5))
        self.assertEqual(state.interval("x"), (100, 100))

    def test_increment_invalidates_old_safety(self):
        state = DifferenceBounds(["i", "length"]).assume_interval("length", 1, 8)
        state = state.assume("0", "i", 0).assume("i", "length", -1)
        result = state.assign("i", "i", 1)
        self.assertEqual(result.interval("i"), (1, 8))
        self.assertFalse(result.is_safe("i", "length"))
        self.assertTrue(state.is_safe("i", "length"))

    def test_rebinding_array_length_drops_old_relation(self):
        state = DifferenceBounds(["i", "length_a", "length_b"])
        state = state.assume("0", "i", 0).assume("i", "length_a", -1)
        self.assertFalse(state.assign("length_a", "length_b").is_safe("i", "length_a"))

    def test_java_overflow(self):
        state = DifferenceBounds(["i"]).assign("i", offset=INT_MAX)
        self.assertEqual(state.assign("i", "i", 1).interval("i"), (INT_MIN, INT_MIN))
        state = state.assign("i", offset=INT_MIN)
        self.assertEqual(state.assign("i", "i", -1).interval("i"), (INT_MAX, INT_MAX))
        state = state.assume_interval("i", INT_MAX - 1, INT_MAX)
        self.assertTrue(state.is_bottom)
        state = DifferenceBounds(["i"]).assume_interval("i", INT_MAX - 1, INT_MAX)
        result = state.assign("i", "i", 1)
        self.assertEqual(result.interval("i"), (INT_MIN, INT_MAX))
        self.assertTrue(contains(result, {"i": INT_MIN}))
        self.assertTrue(contains(result, {"i": INT_MAX}))

    def test_interval_reduction_in_both_directions(self):
        state = DifferenceBounds(["i", "length"])
        state = state.assume("0", "i", 0).assume("i", "length", -1)
        reduced, intervals = state.reduce_with_intervals({"length": (1, 8)})
        self.assertEqual(intervals, {"i": (0, 7), "length": (1, 8)})
        self.assertTrue(reduced.is_safe("i", "length"))
        _, intervals = state.reduce_with_intervals({"i": (3, None)})
        self.assertEqual(intervals["length"], (4, None))
        bottom, intervals = state.reduce_with_intervals(
            {"i": (8, 10), "length": (1, 8)}
        )
        self.assertTrue(bottom.is_bottom)
        self.assertTrue(all(bounds == (1, 0) for bounds in intervals.values()))

    def test_widening_preserves_both_inputs_and_drops_growing_bound(self):
        old = DifferenceBounds(["i"]).assume_interval("i", 0, 0)
        new = DifferenceBounds(["i"]).assume_interval("i", 0, 1)
        widened = old.widen(new)
        self.assertEqual(widened.interval("i"), (0, None))
        self.assertTrue(old.is_subset_of(widened))
        self.assertTrue(new.is_subset_of(widened))

    def test_widening_does_not_reclose_the_accumulator(self):
        new = DifferenceBounds(["x", "y"]).assume("x", "0", 0).assume("0", "y", 0)
        old = new.assume("x", "y", -1)
        widened = old.widen(new)
        self.assertEqual(widened.dbm[1][2], inf)
        self.assertEqual(widened.close().dbm[1][2], 0)
        self.assertEqual(widened.dbm[1][2], inf)
        self.assertEqual(widened.widen(new).dbm, widened.dbm)

    def test_guarded_loop_converges(self):
        initial = DifferenceBounds(["i", "length"]).assume_interval("length", 0, 100)
        initial = initial.assign("i", offset=0)
        head = initial
        for _ in range(10):
            body = head.assume("i", "length", -1)
            self.assertTrue(body.is_safe("i", "length"))
            next_head = head.widen(initial.join(body.assign("i", "i", 1)))
            if next_head.dbm == head.dbm:
                break
            head = next_head
        else:
            self.fail("Widening failed to stabilize")
        self.assertEqual(head.close().dbm[1][2], 0)

    def test_transfer_contains_all_small_concrete_successors(self):
        state = DifferenceBounds(["x", "y"]).assume("x", "y", 2)
        for target in ("x", "y"):
            for source in ("0", "x", "y"):
                for offset in (-2, 0, 2):
                    result = state.assign(target, source, offset)
                    for x, y in itertools.product(range(-3, 4), repeat=2):
                        values = {"x": x, "y": y}
                        if contains(state, values):
                            values[target] = (
                                0 if source == "0" else values[source]
                            ) + offset
                            self.assertTrue(contains(result, values))

    def test_bottom_and_schema_handling(self):
        state = DifferenceBounds(["x"]).assume("x", "x", -1)
        top = DifferenceBounds(["x"])
        self.assertTrue(state.assign("x", offset=1).is_bottom)
        self.assertTrue(state.is_subset_of(top))
        self.assertEqual(state.join(top).dbm, top.dbm)
        self.assertEqual(top.widen(state).dbm, top.dbm)
        for operation in (top.join, top.widen, top.is_subset_of):
            with self.assertRaises(ValueError):
                operation(DifferenceBounds(["y"]))


@st.composite
def witnessed_state(draw):
    variables = ["x", "y", "z"]
    integer = st.one_of(st.integers(-20, 20), st.sampled_from([INT_MIN, INT_MAX]))
    values = draw(st.fixed_dictionaries({name: integer for name in variables}))
    concrete = {"0": 0, **values}
    pairs = draw(
        st.lists(
            st.tuples(
                st.sampled_from(["0"] + variables),
                st.sampled_from(["0"] + variables),
                st.integers(0, 5),
            ),
            max_size=12,
        )
    )
    constraints = [(x, y, concrete[x] - concrete[y] + slack) for x, y, slack in pairs]
    return DifferenceBounds(variables).assume_many(constraints), values


class DifferenceBoundsPropertyTests(unittest.TestCase):
    @given(witnessed_state())
    def test_closure_preserves_witness(self, example):
        state, values = example
        self.assertTrue(contains(state.close(), values))

    @given(
        witnessed_state(),
        st.sampled_from(["x", "y", "z"]),
        st.sampled_from(["0", "x", "y", "z"]),
        st.integers(-5, 5),
    )
    def test_assignment_preserves_concrete_successor(
        self, example, target, source, offset
    ):
        state, values = example
        successor = values.copy()
        value = (0 if source == "0" else values[source]) + offset
        successor[target] = (value - INT_MIN) % (2**32) + INT_MIN
        self.assertTrue(contains(state.assign(target, source, offset), successor))
        self.assertTrue(contains(state, values))

    @given(witnessed_state(), witnessed_state())
    def test_join_and_widen_preserve_both_witnesses(self, left_example, right_example):
        left, left_values = left_example
        right, right_values = right_example
        for result in (left.join(right), left.widen(right)):
            self.assertTrue(contains(result, left_values))
            self.assertTrue(contains(result, right_values))

    @given(st.lists(witnessed_state(), min_size=1, max_size=12))
    def test_raw_widening_stabilizes_on_increasing_sequence(self, examples):
        increasing = examples[0][0]
        widened = increasing.copy()
        size = len(widened.dbm)
        for state, _ in examples[1:]:
            increasing = increasing.join(state)
            previous = widened
            widened = widened.widen(increasing)
            for i, j in itertools.product(range(size), repeat=2):
                self.assertIn(widened.dbm[i][j], (previous.dbm[i][j], inf))
        # Repeating the last input must be a fixed point, without reclosing the result.
        self.assertEqual(widened.widen(increasing).dbm, widened.dbm)


if __name__ == "__main__":
    unittest.main()
