import os
import sys
import traceback
from collections import deque
from dataclasses import dataclass, replace

from interval import INT_MAX, Interval
from difference_bounds import DifferenceBounds

import jpamb
import sexpr
from jpamb import jvm
from jvm.state import PC


@dataclass(frozen=True)
class Value:
    interval: Interval
    kind: str = "int"
    nullness: int = 0  
    origin: str | None = None

    def merge(self, other, widening=False):
        if self.kind != other.kind:
            return Value(Interval.top(), "unknown")
        interval = (
            self.interval.widen(other.interval)
            if widening
            else self.interval | other.interval
        )
        return Value(
            interval,
            self.kind,
            self.nullness | other.nullness,
            self.origin if self.origin == other.origin else None,
        )


@dataclass
class State(sexpr.AsSExpr):
    locals: list[Value]
    stack: list[Value]
    bounds: DifferenceBounds | None = None
    exchange: bool = True

    def __sexpr__(self):
        return str(self)

    def __str__(self):
        return f"{self.locals}/{self.stack}"

    def copy(self):
        return State(
            self.locals[:],
            self.stack[:],
            self.bounds.copy() if self.bounds else None,
            self.exchange,
        )

    def slots(self):
        return [(f"l{i}", value) for i, value in enumerate(self.locals)] + [
            (f"s{i}", value) for i, value in enumerate(self.stack)
        ]

    def value(self, symbol):
        return (self.locals if symbol[0] == "l" else self.stack)[int(symbol[1:])]

    def set_value(self, symbol, value):
        (self.locals if symbol[0] == "l" else self.stack)[int(symbol[1:])] = value

    def reduce(self):
        if self.bounds and not self.exchange:
            self.bounds = self.bounds.close()
            if self.bounds.is_bottom:
                return False
        elif self.bounds:
            intervals = {
                name: (value.interval.min, value.interval.max)
                for name, value in self.slots()
                if value.kind in ("int", "array")
            }
            self.bounds, tightened = self.bounds.reduce_with_intervals(intervals)
            if self.bounds.is_bottom:
                return False
            for name, value in self.slots():
                if value.kind in ("int", "array"):
                    self.set_value(
                        name,
                        replace(
                            value, interval=value.interval & Interval(*tightened[name])
                        ),
                    )
        return all(not value.interval.is_bottom for _, value in self.slots())

    def write(self, symbol, value, source=None, offset=0):
        if symbol.startswith("l"):
            self.stack = [
                replace(v, origin=None) if v.origin == symbol else v for v in self.stack
            ]
        self.set_value(symbol, value)
        if self.bounds:
            if source is not None:
                self.bounds = self.bounds.assign(symbol, source, offset)
            elif value.interval.singleton:
                self.bounds = self.bounds.assign(symbol, offset=value.interval.min)
            else:
                self.bounds = self.bounds.forget(symbol)

    def push(self, value, source=None, offset=0):
        symbol = f"s{len(self.stack)}"
        self.stack.append(value)
        self.write(symbol, value, source, offset)

    def pop(self):
        symbol = f"s{len(self.stack) - 1}"
        return symbol, self.stack.pop()

    def narrow(self, symbol, interval):
        value = self.value(symbol)
        origin = value.origin
        self.set_value(symbol, replace(value, interval=value.interval & interval))
        if origin is not None:
            original = self.value(origin)
            self.set_value(
                origin, replace(original, interval=original.interval & interval)
            )
            for name, alias in self.slots():
                if alias.origin == origin:
                    self.set_value(
                        name, replace(alias, interval=alias.interval & interval)
                    )

    def nonnull(self, symbol):
        value = self.value(symbol)
        self.set_value(symbol, replace(value, nullness=value.nullness & 2))
        if value.origin:
            original = self.value(value.origin)
            self.set_value(
                value.origin, replace(original, nullness=original.nullness & 2)
            )

    def merge(self, other, widening=False):
        if len(self.stack) != len(other.stack):
            raise NotImplementedError("Incompatible operand stacks")
        bounds = None
        if self.bounds:
            bounds = (
                self.bounds.widen(other.bounds)
                if widening
                else self.bounds.join(other.bounds)
            )
        return State(
            [a.merge(b, widening) for a, b in zip(self.locals, other.locals)],
            [a.merge(b, widening) for a, b in zip(self.stack, other.stack)],
            bounds,
            self.exchange,
        )

    def same_as(self, other):
        return (
            self.locals == other.locals
            and self.stack == other.stack
            and (
                self.bounds is None
                or (
                    self.bounds.dbm == other.bounds.dbm
                    and self.bounds.is_bottom == other.bounds.is_bottom
                )
            )
        )


NEGATE = {
    jvm.CmpOpr.Eq: jvm.CmpOpr.Ne,
    jvm.CmpOpr.Ne: jvm.CmpOpr.Eq,
    jvm.CmpOpr.Lt: jvm.CmpOpr.Ge,
    jvm.CmpOpr.Ge: jvm.CmpOpr.Lt,
    jvm.CmpOpr.Le: jvm.CmpOpr.Gt,
    jvm.CmpOpr.Gt: jvm.CmpOpr.Le,
}


def branch(state, left, right, condition):
    result = state.copy()
    a, b = (
        result.value(left),
        result.value(right) if right != "0" else Value(Interval(0, 0)),
    )
    if a.kind == "array":
        if right != "0" or condition not in (jvm.CmpOpr.Eq, jvm.CmpOpr.Ne):
            raise NotImplementedError(
                "Only reference comparisons with null are supported"
            )
        mask = 1 if condition == jvm.CmpOpr.Eq else 2
        if not a.nullness & mask:
            return None
        result.set_value(left, replace(a, nullness=mask))
        if a.origin:
            result.set_value(a.origin, replace(result.value(a.origin), nullness=mask))
    else:
        if a.kind != "int" or b.kind not in ("int", "array"):
            raise NotImplementedError("Unsupported comparison operands")
        x, y = a.interval.refine(b.interval, condition)
        result.narrow(left, x)
        if right != "0":
            result.narrow(right, y)
        if result.bounds:
            if condition in (jvm.CmpOpr.Lt, jvm.CmpOpr.Le, jvm.CmpOpr.Eq):
                result.bounds = result.bounds.assume(
                    left, right, -int(condition == jvm.CmpOpr.Lt)
                )
            if condition in (jvm.CmpOpr.Gt, jvm.CmpOpr.Ge, jvm.CmpOpr.Eq):
                result.bounds = result.bounds.assume(
                    right, left, -int(condition == jvm.CmpOpr.Gt)
                )
    return result if result.reduce() else None


def manystep(bc, pc, state):
    state = state.copy()
    if not state.reduce():
        return
    op = bc[pc]
    if isinstance(op, (jvm.If, jvm.Ifz)):
        left = f"s{len(state.stack) - (2 if isinstance(op, jvm.If) else 1)}"
        right = f"s{len(state.stack) - 1}" if isinstance(op, jvm.If) else "0"
        for condition, successor in (
            (op.condition, pc % op.target),
            (NEGATE[op.condition], pc + 1),
        ):
            after = branch(state, left, right, condition)
            if after:
                after.pop()
                if isinstance(op, jvm.If):
                    after.pop()
                yield successor, after
        return
    match op:
        case jvm.Push(type=jvm.Int(), value=value):
            state.push(Value(Interval(value, value)))
        case jvm.Push(type=jvm.Reference(), value=0):
            state.push(Value(Interval(0, INT_MAX), "array", 1))
        case jvm.Load(index=index):
            value = state.locals[index]
            if value.kind == "unknown":
                raise NotImplementedError(
                    "Read of an uninitialized or incompatible local"
                )
            state.push(replace(value, origin=f"l{index}"), f"l{index}")
        case jvm.Store(index=index):
            source, value = state.pop()
            state.write(f"l{index}", replace(value, origin=None), source)
        case jvm.Incr(index=index, amount=amount):
            value = state.locals[index]
            if value.kind != "int":
                raise NotImplementedError("Increment of a non-integer")
            interval, _ = value.interval.arithmetic(
                Interval(amount, amount), jvm.BinaryOpr.Add
            )
            state.write(f"l{index}", Value(interval), f"l{index}", amount)
        case jvm.Binary(type=jvm.Int(), operant=operation):
            right, b = state.pop()
            left, a = state.pop()
            if a.kind != "int" or b.kind != "int":
                raise NotImplementedError("Non-integer arithmetic")
            interval, errors = a.interval.arithmetic(b.interval, operation)
            yield from errors
            if interval.is_bottom:
                return
            source, offset = None, 0
            if (
                operation in (jvm.BinaryOpr.Add, jvm.BinaryOpr.Sub)
                and b.interval.singleton
            ):
                offset = b.interval.min * (-1 if operation == jvm.BinaryOpr.Sub else 1)
                source = left
            elif operation == jvm.BinaryOpr.Add and a.interval.singleton:
                source, offset = right, a.interval.min
            state.push(Value(interval), source, offset)
        case jvm.Negate(type=jvm.Int()):
            _, value = state.pop()
            interval, _ = Interval(0, 0).arithmetic(value.interval, jvm.BinaryOpr.Sub)
            state.push(Value(interval))
        case jvm.Dup(words=1):
            source = f"s{len(state.stack) - 1}"
            state.push(state.stack[-1], source)
        case jvm.NewArray(type=jvm.Int(), dim=1):
            source, value = state.pop()
            interval = value.interval & Interval(0, INT_MAX)
            if value.interval.min is None or value.interval.min < 0:
                yield "negative array size"
            if interval.is_bottom:
                return
            state.push(Value(interval, "array", 2), source)
        case jvm.ArrayLength():
            symbol = f"s{len(state.stack) - 1}"
            value = state.value(symbol)
            if value.kind != "array":
                raise NotImplementedError("Length of a non-array")
            if value.nullness & 1:
                yield "null pointer"
            if not value.nullness & 2:
                return
            state.nonnull(symbol)
            source, value = state.pop()
            state.push(Value(value.interval, origin=value.origin), source)
        case jvm.ArrayLoad(type=jvm.Int()) | jvm.ArrayStore(type=jvm.Int()):
            if isinstance(op, jvm.ArrayStore):
                state.pop()
            index, i = f"s{len(state.stack) - 1}", state.stack[-1]
            array, a = f"s{len(state.stack) - 2}", state.stack[-2]
            if a.kind != "array" or i.kind != "int":
                raise NotImplementedError("Unsupported array access")
            if a.nullness & 1:
                yield "null pointer"
            if not a.nullness & 2:
                return
            safe = (
                i.interval.min is not None
                and i.interval.min >= 0
                and i.interval.max is not None
                and a.interval.min is not None
                and i.interval.max < a.interval.min
            )
            if state.bounds:
                safe = safe or state.bounds.is_safe(index, array)
            if not safe:
                yield "out of bounds"
            state.nonnull(array)
            normal = branch(state, index, "0", jvm.CmpOpr.Ge)
            if normal:
                normal = branch(normal, index, array, jvm.CmpOpr.Lt)
            if normal is None:
                return
            state = normal
            state.pop()
            state.pop()
            if isinstance(op, jvm.ArrayLoad):
                state.push(Value(Interval.java_int()))
        case jvm.Get(static=True, field=field) if (
            field.extension.name == "$assertionsDisabled"
        ):
            state.push(Value(Interval(0, 0)))
        case jvm.New(classname=jvm.ClassName("java.lang.AssertionError")):
            state.push(Value(Interval.top(), "assertion"))
        case jvm.InvokeSpecial(method=method) if (
            method.classname == jvm.ClassName("java.lang.AssertionError")
            and method.extension.name == "<init>"
        ):
            for _ in method.extension.params:
                state.pop()
            _, receiver = state.pop()
            if receiver.kind != "assertion":
                raise NotImplementedError("Unsupported constructor receiver")
        case jvm.Throw():
            _, value = state.pop()
            if value.kind != "assertion":
                raise NotImplementedError("Unsupported thrown object")
            yield "assertion error"
            return
        case jvm.Goto(target=target):
            yield pc % target, state
            return
        case jvm.Return():
            yield "ok"
            return
        case _:
            raise NotImplementedError(str(op))
    if state.reduce():
        yield pc + 1, state


def initialstate(bc, methodid, inputs, relational=True, exchange=True):
    method = bc.getmethod(methodid)
    raw = bc.suite.findmethod(methodid, eff=bc.eff)
    if "static" not in raw["access"] or raw["code"]["exceptions"]:
        raise NotImplementedError(
            "Only static methods without exception handlers are supported"
        )
    max_stack = raw["code"]["max_stack"]
    variables = [f"l{i}" for i in range(method.max_locals)] + [
        f"s{i}" for i in range(max_stack)
    ]
    state = State(
        [Value(Interval.top(), "unknown") for _ in range(method.max_locals)],
        [],
        DifferenceBounds(variables) if relational else None,
        exchange,
    )
    for i, parameter in enumerate(methodid.extension.params):
        if isinstance(parameter, (jvm.Int, jvm.Boolean)):
            interval = (
                Interval(0, 1)
                if isinstance(parameter, jvm.Boolean)
                else Interval.java_int()
            )
            value = Value(interval)
        elif isinstance(parameter, jvm.Array) and isinstance(
            parameter.contains, jvm.Int
        ):
            value = Value(Interval(0, INT_MAX), "array", 3)
        else:
            raise NotImplementedError(f"Unsupported parameter {parameter}")
        if inputs is not None:
            concrete = inputs.values[i]
            if isinstance(concrete, (jpamb.case.Int, jpamb.case.Boolean)):
                value = Value(Interval(int(concrete.value), int(concrete.value)))
            elif isinstance(concrete, jpamb.case.Array):
                value = Value(
                    Interval(len(concrete.values), len(concrete.values)), "array", 2
                )
            else:
                raise NotImplementedError(f"Unsupported input {concrete}")
        state.write(f"l{i}", value)
    state.reduce()
    return {PC(methodid, 0): state}


@dataclass
class AbstractInterpreter:
    bc: jpamb.Bytecode
    worklist: deque
    states: dict
    loop_heads: set
    updates: dict
    incomplete: bool = False
    reason: str | None = None
    steps: int = 0

    @staticmethod
    def initial(bc, methodid, inputs=None, relational=True, exchange=True):
        states = initialstate(bc, methodid, inputs, relational, exchange)
        heads = {
            PC(methodid, op.target)
            for position, op in enumerate(bc.getmethod(methodid).opcodes)
            if isinstance(op, (jvm.If, jvm.Ifz, jvm.Goto)) and op.target <= position
        }
        return AbstractInterpreter(bc, deque(states), states, heads, {})

    def step(self):
        pc = self.worklist.popleft()
        finals = set()
        try:
            for result in manystep(self.bc, pc, self.states[pc]):
                if isinstance(result, str):
                    finals.add(result)
                    continue
                successor, incoming = result
                before = self.states.get(successor)
                widening = (
                    successor in self.loop_heads and self.updates.get(successor, 0) >= 2
                )
                after = incoming if before is None else before.merge(incoming, widening)
                # Widened loop-head DBMs stay raw. Transfer reduces an outgoing copy.
                if before is None or not after.same_as(before):
                    self.states[successor] = after
                    self.updates[successor] = self.updates.get(successor, 0) + 1
                    if successor not in self.worklist:
                        self.worklist.append(successor)
        except Exception as error:  # noqa: BLE001 - Failed analysis must stay unresolved.
            self.incomplete = True
            self.reason = error_reason(error)
        self.steps += 1
        return pc, finals

    def run(self, max_steps=10000):
        while self.worklist and self.steps < max_steps:
            self.step()
        if self.worklist:
            self.incomplete = True
            self.reason = "Analysis step limit reached"
        return self

    def results(self):
        finals, accesses = set(), {}
        try:
            methodid = next(iter(self.states)).method
            for position, op in enumerate(self.bc.getmethod(methodid).opcodes):
                if isinstance(op, (jvm.ArrayLoad, jvm.ArrayStore)):
                    accesses[position] = "unreachable"
            for pc, state in self.states.items():
                outcomes = {
                    result
                    for result in manystep(self.bc, pc, state)
                    if isinstance(result, str)
                }
                finals |= outcomes
                if pc.offset in accesses:
                    array_position = (
                        -3 if isinstance(self.bc[pc], jvm.ArrayStore) else -2
                    )
                    if state.stack[array_position].nullness & 2:
                        accesses[pc.offset] = (
                            "warning" if "out of bounds" in outcomes else "safe"
                        )
        except Exception as error:  # noqa: BLE001 - Failed analysis must stay unresolved.
            self.incomplete = True
            self.reason = error_reason(error)
        if self.incomplete:
            accesses = dict.fromkeys(accesses, "unresolved")
        return finals, accesses


def error_reason(error):
    if isinstance(error, NotImplementedError):
        return str(error)
    traceback.print_exc(file=sys.stderr)
    return f"internal error: {error!r}"


def configuration():
    relational = os.environ.get("JPAMB_DOMAIN", "dbm") != "interval"
    exchange = os.environ.get("JPAMB_EXCHANGE", "on") != "off"
    tags = ["static", "interval"]
    if relational:
        tags += ["dbm", "exchange-on" if exchange else "exchange-off"]
    return relational, exchange, tags


def interpret():
    relational, exchange, tags = configuration()
    methodid, inputs, steps = jpamb.getcase(
        "static", "2.0", "Group 10", tags, for_science=True
    )
    suite, eff = jpamb.setup()
    bc = jpamb.Bytecode(suite, eff, {})
    ai = AbstractInterpreter.initial(bc, methodid, inputs, relational, exchange)
    previous = jpamb.emit_init(ai.states)
    while steps > 0 and ai.worklist:
        pc, finals = ai.step()
        for outcome in finals:
            previous = jpamb.emit_step(previous, pc, outcome, depth=1)
        previous = jpamb.emit_step(previous, pc, ai.states, depth=1)
        steps -= 1


def analyse():
    try:
        relational, exchange, tags = configuration()
        methodid = jpamb.getmethodid(
            "static", "2.0", "Group 10", tags, for_science=True
        )
        suite, eff = jpamb.setup()
        bc = jpamb.Bytecode(suite, eff, {})
        ai = AbstractInterpreter.initial(
            bc, methodid, relational=relational, exchange=exchange
        ).run()
        finals, accesses = ai.results()
        if ai.reason:
            print(ai.reason, file=sys.stderr)
        for offset, status in sorted(accesses.items()):
            print(f"array access {offset}: {status}", file=sys.stderr)
        predictions = [
            f"{query};{'maybe' if ai.incomplete or query in finals or query == '*' else 'no'}"
            for query in jpamb.QUERIES
        ]
    except Exception as error:  # noqa: BLE001 - Failed analysis must stay unresolved.
        print(error_reason(error), file=sys.stderr)
        predictions = [f"{query};maybe" for query in jpamb.QUERIES]
    print("\n".join(predictions))