from math import inf

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1


class DifferenceBounds:
    """dbm[x][y] is an upper bound on x - y; '0' always denotes zero.

    Operations return new states. Assignments model Java signed 32-bit ints.
    """

    def __init__(self, variables):
        variables = list(variables)
        if any(
            not isinstance(name, str) or not name or name == "0" for name in variables
        ):
            raise ValueError("Use nonempty, unique variable names; '0' is reserved")
        if len(set(variables)) != len(variables):
            raise ValueError("Variable names must be unique")
        self.variables = ["0"] + variables
        self.index = {name: position for position, name in enumerate(self.variables)}
        size = len(self.variables)
        self.dbm = [[inf for _ in range(size)] for _ in range(size)]
        for i in range(size):
            self.dbm[i][i] = 0
        self.is_bottom = False

    def copy(self):
        result = object.__new__(DifferenceBounds)
        result.variables = self.variables[:]
        result.index = self.index.copy()
        result.dbm = [row[:] for row in self.dbm]
        result.is_bottom = self.is_bottom
        return result

    def _check_variables(self, other):
        if self.variables != other.variables:
            raise ValueError("States must use the same variables in the same order")

    def close(self):
        result = self.copy()
        if result.is_bottom:
            return result
        m = result.dbm
        size = len(m)
        for k in range(size):
            for i in range(size):
                if m[i][k] == inf:
                    continue
                for j in range(size):
                    if m[k][j] != inf:
                        m[i][j] = min(m[i][j], m[i][k] + m[k][j])
            if any(m[i][i] < 0 for i in range(size)):
                result.is_bottom = True
                break
        return result

    def assume(self, x, y, bound):
        """Add the branch constraint x - y <= bound."""
        return self.assume_many([(x, y, bound)])

    def assume_many(self, constraints):
        """Insert (x, y, bound) constraints and close the matrix once."""
        result = self.copy()
        for x, y, bound in constraints:
            if type(bound) is not int:
                raise TypeError("A difference constraint needs an integer bound")
            i, j = self.index[x], self.index[y]
            result.dbm[i][j] = min(result.dbm[i][j], bound)
        return result.close()

    def assume_interval(self, variable, lower=None, upper=None):
        if variable == "0":
            raise ValueError("Cannot change the distinguished zero symbol")
        self.index[variable]
        return self.assume_many(self._interval_constraints(variable, lower, upper))

    def _interval_constraints(self, variable, lower, upper):
        for endpoint in (lower, upper):
            if endpoint is not None and type(endpoint) is not int:
                raise TypeError("An interval endpoint must be an integer or None")
        constraints = []
        if lower is not None:
            constraints.append(("0", variable, -lower))
        if upper is not None:
            constraints.append((variable, "0", upper))
        return constraints

    def _closed_interval(self, variable):
        if self.is_bottom:
            return (1, 0)
        i = self.index[variable]
        lower, upper = self.dbm[0][i], self.dbm[i][0]
        return (None if lower == inf else -lower, None if upper == inf else upper)

    def interval(self, variable):
        """Return (lower, upper), using None for infinity and (1, 0) for bottom."""
        self.index[variable]
        return self.close()._closed_interval(variable)

    def forget(self, variable):
        if variable == "0":
            raise ValueError("Cannot forget the distinguished zero symbol")
        k = self.index[variable]
        result = self.close()
        for i in range(len(result.dbm)):
            result.dbm[k][i] = result.dbm[i][k] = 0 if i == k else inf
        return result

    def assign(self, target, source="0", offset=0):
        """Assign target := source + offset; omitted source means a constant."""
        if type(offset) is not int:
            raise TypeError("An assignment offset must be an integer")
        if target == "0":
            raise ValueError("Cannot assign the distinguished zero symbol")
        k, source_index = self.index[target], self.index[source]
        old = self.close()
        if old.is_bottom:
            return old
        if source == "0":
            value = (offset - INT_MIN) % (2**32) + INT_MIN
            return old.forget(target).assume_interval(target, value, value)

        old = old.assume_interval(source, INT_MIN, INT_MAX)
        if old.is_bottom:
            return old
        lower, upper = old._closed_interval(source)
        if lower == upper:
            value = (lower + offset - INT_MIN) % (2**32) + INT_MIN
            return old.forget(target).assume_interval(target, value, value)
        if lower + offset < INT_MIN or upper + offset > INT_MAX:
            return old.forget(target).assume_interval(target, INT_MIN, INT_MAX)

        if k != source_index:
            return old.forget(target).assume_many(
                [(target, source, offset), (source, target, -offset)]
            )
        # For i := i+c, shift the OLD row and column instead of forgetting i.
        result = old.copy()
        for i in range(len(result.dbm)):
            if i != k:
                if result.dbm[k][i] != inf:
                    result.dbm[k][i] += offset
                if result.dbm[i][k] != inf:
                    result.dbm[i][k] -= offset
        return result.close()

    def join(self, other):
        self._check_variables(other)
        left, right = self.close(), other.close()
        if left.is_bottom:
            return right
        if right.is_bottom:
            return left
        result = self.copy()
        result.dbm = [
            [max(a, b) for a, b in zip(row_a, row_b)]
            for row_a, row_b in zip(left.dbm, right.dbm)
        ]
        return result

    def widen(self, other):
        self._check_variables(other)
        if self.is_bottom:
            return other.copy()
        if other.is_bottom:
            return self.copy()
        right = other.close()
        result = self.copy()
        result.dbm = [
            [old if new <= old else inf for old, new in zip(row_a, row_b)]
            for row_a, row_b in zip(self.dbm, right.dbm)
        ]
        # Store this RAW result at the loop head; refine only outgoing copies.
        return result

    def is_subset_of(self, other):
        self._check_variables(other)
        left, right = self.close(), other.close()
        if left.is_bottom:
            return True
        if right.is_bottom:
            return False
        return all(
            a <= b
            for row_a, row_b in zip(left.dbm, right.dbm)
            for a, b in zip(row_a, row_b)
        )

    def reduce_with_intervals(self, intervals):
        """Import {name: (lower, upper)}; return (new DBM, tighter intervals)."""
        constraints = []
        for variable, (lower, upper) in intervals.items():
            if variable == "0":
                raise ValueError("Cannot change the distinguished zero symbol")
            self.index[variable]
            constraints.extend(self._interval_constraints(variable, lower, upper))
        result = self.assume_many(constraints)
        return result, {
            name: result._closed_interval(name) for name in self.variables[1:]
        }

    def is_safe(self, index, length):
        """Numerical bounds only; the shared engine checks nullness separately."""
        i, n = self.index[index], self.index[length]
        state = self.close()
        return not state.is_bottom and state.dbm[0][i] <= 0 and state.dbm[i][n] <= -1
