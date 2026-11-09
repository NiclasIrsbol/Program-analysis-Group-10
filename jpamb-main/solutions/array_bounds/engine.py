"""Shared abstract interpreter for the array-bounds analyses."""

from __future__ import annotations

import heapq
import json
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # .../jpamb-main
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import jvm  # noqa: E402

from solutions.array_bounds.domains import RELATIONAL_DOMAINS  # noqa: E402
from solutions.array_bounds.intervals import (  # noqa: E402
    BOTTOM,
    INT_MAX,
    NEGATE,
    TOP,
    Interval,
)

Sym = tuple[str, int] | None
ZERO = "0"
NONNEG = Interval(0, INT_MAX)

SAFE, WARNING, UNRESOLVED = "SAFE", "WARNING", "UNRESOLVED"


# ---------------------------------------------------------------------------
# Abstract values
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntVal:
    itv: Interval
    sym: Sym = None  # value == var + offset, if known

    def __str__(self):
        s = f"{self.itv}"
        if self.sym and self.sym[0] != ZERO:
            s += f"={self.sym[0]}{self.sym[1]:+d}"
        return s


@dataclass(frozen=True)
class RefVal:
    may_null: bool
    may_obj: bool  # may be a non-null reference
    length: Interval = BOTTOM  # array length if non-null (BOTTOM: not an array)
    len_sym: Sym = None  # length == var + offset, if known
    origin: int | None = None  # local slot this value was loaded from
    klass: str | None = None  # class of `new` objects (for throw outcomes)

    def __str__(self):
        n = {(True, True): "null?", (True, False): "null", (False, True): "obj"}
        s = n.get((self.may_null, self.may_obj), "bot")
        if not self.length.is_bottom:
            s += f" len{self.length}"
        return s


Val = IntVal | RefVal


def _sym_join(a, b):
    return a if a == b else None


def join_val(a: Val | None, b: Val | None, widen=False) -> Val | None:
    """None is 'unusable' (uninitialised or type-mismatch) and acts as top."""
    if a is None or b is None or type(a) is not type(b):
        return None
    if isinstance(a, IntVal):
        itv = a.itv.widen(a.itv.join(b.itv)) if widen else a.itv.join(b.itv)
        return IntVal(itv, _sym_join(a.sym, b.sym))
    j = a.length.join(b.length)
    return RefVal(
        a.may_null or b.may_null,
        a.may_obj or b.may_obj,
        a.length.widen(j) if widen else j,
        _sym_join(a.len_sym, b.len_sym),
        _sym_join(a.origin, b.origin),
        _sym_join(a.klass, b.klass),
    )


def leq_val(a: Val | None, b: Val | None) -> bool:
    if b is None:
        return True
    if a is None or type(a) is not type(b):
        return False
    if isinstance(a, IntVal):
        return a.itv.leq(b.itv) and (b.sym is None or a.sym == b.sym)
    return (
        (b.may_null or not a.may_null)
        and (b.may_obj or not a.may_obj)
        and a.length.leq(b.length)
        and (b.len_sym is None or a.len_sym == b.len_sym)
        and (b.origin is None or a.origin == b.origin)
        and (b.klass is None or a.klass == b.klass)
    )


@dataclass(frozen=True)
class AState:
    locals: tuple[Val | None, ...]
    stack: tuple[Val, ...]
    rel: object | None = None  # a RelationalDomain, or None for intervals only

    def push(self, *vals: Val) -> AState:
        return replace(self, stack=self.stack + vals)

    def pop(self, n: int) -> tuple[list[Val], AState]:
        if n == 0:
            return [], self
        return list(self.stack[-n:]), replace(self, stack=self.stack[:-n])

    def __str__(self):
        loc = ", ".join("_" if v is None else str(v) for v in self.locals)
        return f"[{loc}] | {' : '.join(map(str, self.stack))}"


def join_state(a: AState, b: AState, widen=False) -> AState:
    assert len(a.stack) == len(b.stack), "stack heights must agree at merge points"
    locs = tuple(join_val(x, y, widen) for x, y in zip(a.locals, b.locals))
    stack = tuple(join_val(x, y, widen) for x, y in zip(a.stack, b.stack))
    rel = None
    if a.rel is not None:
        rel = a.rel.join(b.rel)
        if widen:
            rel = a.rel.widen(rel)
    return AState(locs, stack, rel)


def leq_state(a: AState, b: AState) -> bool:
    return (
        all(leq_val(x, y) for x, y in zip(a.locals, b.locals))
        and all(leq_val(x, y) for x, y in zip(a.stack, b.stack))
        and (a.rel is None or a.rel.leq(b.rel))
    )


# ---------------------------------------------------------------------------
# Method loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UnsupportedOp:
    """Placeholder for an opcode the jvm package cannot parse."""

    offset: int
    opr: str

    def __str__(self):
        return f"unsupported {self.opr}"


@dataclass
class MethodCode:
    methodid: str
    params: list  # jvm types
    opcodes: list
    max_locals: int
    is_static: bool = True
    has_handlers: bool = False


def load_method(methodid: str | jvm.AbsMethodID, root: Path = ROOT) -> MethodCode:
    if isinstance(methodid, str):
        methodid = jvm.AbsMethodID.decode(methodid)
    cn = methodid.classname
    path = root / "target" / "decompiled" / Path(*cn.packages) / f"{cn.name}.json"
    with open(path, encoding="utf-8") as f:
        cls = json.load(f)
    for m in cls["methods"]:
        if m["name"] != methodid.extension.name:
            continue
        params = jvm.ParameterType.from_json(m["params"], annotated=True)
        if params == methodid.extension.params:
            break
    else:
        raise KeyError(f"method {methodid.encode()} not found in {path}")
    return method_from_json(methodid.encode(), list(methodid.extension.params), m)


def method_from_json(name: str, params: list, m: dict) -> MethodCode:
    ops = []
    for op in m["code"]["bytecode"]:
        # ifnull / ifnonnull / if_acmp* may be encoded as "is" / "isnot".
        if op.get("condition") in ("is", "isnot"):
            op = {**op, "condition": "eq" if op["condition"] == "is" else "ne"}
        try:
            ops.append(jvm.Opcode.from_json(op))
        except (NotImplementedError, KeyError, AssertionError):
            ops.append(UnsupportedOp(op["offset"], op["opr"]))
    return MethodCode(
        methodid=name,
        params=params,
        opcodes=ops,
        max_locals=m["code"]["max_locals"],
        is_static="static" in m.get("access", ["static"]),
        has_handlers=bool(m["code"].get("exceptions")),
    )


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class AccessResult:
    index: int  # instruction index in the method
    offset: int  # bytecode offset
    kind: str  # "load" | "store"
    verdict: str  # SAFE | WARNING | UNRESOLVED
    definite: bool = False  # the access fails whenever it is reached
    reachable: bool = True
    null: str = "safe"  # "safe" | "possible" | "definite" (recorded separately)
    index_itv: str = ""
    length_itv: str = ""


@dataclass
class AnalysisResult:
    method: str
    domain: str
    exchange: bool
    accesses: list[AccessResult] = field(default_factory=list)
    unresolved: str | None = None  # reason, if the analysis gave up
    outcomes: set[str] = field(default_factory=set)
    steps: int = 0
    program_points: int = 0
    rel_size: int = 0
    runtime: float = 0.0

    def to_dict(self):
        d = asdict(self)
        d["outcomes"] = sorted(self.outcomes)
        return d

    @property
    def summary(self) -> str:
        """Method-level verdict for array bounds."""
        if self.unresolved:
            return UNRESOLVED
        if any(a.verdict == WARNING for a in self.accesses):
            return WARNING
        return SAFE


class Unsupported(Exception):
    pass


# ---------------------------------------------------------------------------
# The analysis
# ---------------------------------------------------------------------------

INT_TYPES = {
    jvm.Int: TOP,
    jvm.Boolean: Interval(0, 1),
    jvm.Char: Interval(0, 65535),
    jvm.Byte: Interval(-128, 127),
    jvm.Short: Interval(-32768, 32767),
}
CASTS = {jvm.Short: (-32768, 32767), jvm.Char: (0, 65535), jvm.Byte: (-128, 127)}
CMP = {
    jvm.CmpOpr.Lt: "lt",
    jvm.CmpOpr.Le: "le",
    jvm.CmpOpr.Gt: "gt",
    jvm.CmpOpr.Ge: "ge",
    jvm.CmpOpr.Eq: "eq",
    jvm.CmpOpr.Ne: "ne",
}


def top_of(t, may_null=True) -> Val:
    """The least precise value of a JVM type."""
    for cls, itv in INT_TYPES.items():
        if isinstance(t, cls):
            return IntVal(itv)
    if isinstance(t, jvm.Array):
        return RefVal(may_null, True, NONNEG)
    if isinstance(t, (jvm.Object, jvm.Reference)):
        return RefVal(may_null, True)
    raise Unsupported(f"type {t}")


def const(k: int) -> IntVal:
    return IntVal(Interval.const(k), (ZERO, k))


class Analysis:
    def __init__(
        self,
        method: MethodCode,
        domain: str = "interval",
        exchange: bool = True,
        nonnull_params: bool = False,
        max_steps: int = 50_000,
    ):
        self.m = method
        self.domain_name = domain
        self.rel_cls = RELATIONAL_DOMAINS[domain]
        self.exchange_on = exchange
        self.nonnull_params = nonnull_params
        self.max_steps = max_steps
        self.vars = [v for k in range(method.max_locals) for v in (f"L{k}", f"#L{k}")]
        self.heads = self._loop_heads()
        self.states: dict[int, AState] = {}
        self.outcomes: set[str] = set()
        self.steps = 0

    # -- setup ---------------------------------------------------------------
    def _loop_heads(self) -> set[int]:
        heads = set()
        for i, op in enumerate(self.m.opcodes):
            t = getattr(op, "target", None)
            if isinstance(t, int) and t <= i:
                heads.add(t)
        return heads

    def initial(self) -> AState:
        locs: list[Val | None] = [None] * self.m.max_locals
        slot = 0
        if not self.m.is_static:
            locs[0] = RefVal(False, True)
            slot = 1
        for p in self.m.params:
            if isinstance(p, (jvm.Long, jvm.Double, jvm.Float)):
                raise Unsupported(f"parameter type {p}")
            locs[slot] = top_of(p, may_null=not self.nonnull_params)
            slot += 1
        rel = self.rel_cls.top(self.vars) if self.rel_cls else None
        return AState(tuple(locs), (), rel)

    # -- variables & information exchange -------------------------------------
    def _slot(self, var: str) -> tuple[int, bool]:
        """'L3' -> (3, False), '#L3' -> (3, True)."""
        is_len = var.startswith("#")
        return int(var[2:] if is_len else var[1:]), is_len

    def refine_var(self, s: AState, var: str, itv: Interval) -> AState | None:
        """Meet the interval of a variable with itv (None if that is empty)."""
        if var == ZERO:
            return s if not itv.is_bottom and 0 in itv else None
        k, is_len = self._slot(var)
        v = s.locals[k]
        if is_len:
            if not isinstance(v, RefVal):
                return s
            new = v.length.meet(itv)
            if new.is_bottom:
                if not v.may_null:
                    return None
                nv = RefVal(True, False)
            else:
                nv = replace(v, length=new)
        else:
            if not isinstance(v, IntVal):
                return s
            new = v.itv.meet(itv)
            if new.is_bottom:
                return None
            nv = IntVal(new)
        return replace(s, locals=s.locals[:k] + (nv,) + s.locals[k + 1 :])

    def exchange(self, s: AState) -> AState | None:
        """Reduced product: intervals -> relational -> intervals."""
        if s.rel is None or not self.exchange_on:
            if s.rel is not None and s.rel.is_bottom:
                return None
            return s
        bounds = {}
        for k, v in enumerate(s.locals):
            if isinstance(v, IntVal):
                bounds[f"L{k}"] = (v.itv.lo, v.itv.hi)
            elif isinstance(v, RefVal) and v.may_obj and not v.length.is_bottom:
                bounds[f"#L{k}"] = (v.length.lo, v.length.hi)
        rel, implied = s.rel.reduce(bounds)
        if rel.is_bottom:
            return None
        s = replace(s, rel=rel)
        for var in bounds:
            lo, hi = implied[var]
            s = self.refine_var(s, var, Interval.of(lo, hi))
            if s is None:
                return None
        return s

    def refine_sym(self, s: AState, sym: Sym, itv: Interval) -> AState | None:
        """The value var+off now lies in itv, so var lies in itv - off."""
        if sym is None:
            return s
        var, off = sym
        return self.refine_var(s, var, Interval.of(itv.lo - off, itv.hi - off))

    def assume(self, s: AState, a: IntVal, op: str, b: IntVal) -> AState | None:
        """Refine s with the int comparison `a op b`; None if infeasible."""
        a2, b2 = Interval.refine(a.itv, op, b.itv)
        if a2.is_bottom:
            return None
        s = self.refine_sym(s, a.sym, a2)
        s = s and self.refine_sym(s, b.sym, b2)
        if s is None:
            return None
        if s.rel is not None and a.sym and b.sym:
            (x, c1), (y, c2) = a.sym, b.sym
            rel = s.rel
            # x + c1 OP y + c2   <=>   x - y OP c2 - c1
            if op in ("lt", "le", "eq"):
                rel = rel.assume(x, y, c2 - c1 - (op == "lt"))
            if op in ("gt", "ge", "eq"):
                rel = rel.assume(y, x, c1 - c2 - (op == "gt"))
            if rel.is_bottom:
                return None
            s = replace(s, rel=rel)
        return self.exchange(s)

    def assume_null(self, s: AState, ref: RefVal, is_null: bool) -> AState | None:
        if is_null and not ref.may_null or not is_null and not ref.may_obj:
            return None
        if ref.origin is not None:
            v = s.locals[ref.origin]
            if isinstance(v, RefVal):
                nv = RefVal(True, False) if is_null else replace(v, may_null=False)
                s = replace(s, locals=s.locals[: ref.origin] + (nv,) + s.locals[ref.origin + 1 :])
        return s

    # -- local writes (with invalidation of stale facts) ----------------------
    @staticmethod
    def _invalidate(stack, names: set[str], slot: int):
        out = []
        for v in stack:
            if isinstance(v, IntVal) and v.sym and v.sym[0] in names:
                v = IntVal(v.itv)
            elif isinstance(v, RefVal):
                if v.len_sym and v.len_sym[0] in names:
                    v = replace(v, len_sym=None)
                if v.origin == slot:
                    v = replace(v, origin=None)
            out.append(v)
        return tuple(out)

    def store(self, s: AState, k: int, v: Val) -> AState:
        lk, nk = f"L{k}", f"#L{k}"
        rel = s.rel
        # Assign first, then forget the slot's other variable: the stored value
        # may be defined in terms of it (e.g. slot k reused for `a.length`).
        if isinstance(v, IntVal):
            if rel is not None:
                rel = rel.assign(lk, *v.sym) if v.sym else rel.forget(lk)
                rel = rel.forget(nk)
            nv = IntVal(v.itv)
        else:
            if rel is not None:
                rel = rel.assign(nk, *v.len_sym) if v.len_sym else rel.forget(nk)
                rel = rel.forget(lk)
            nv = replace(v, len_sym=None, origin=None)
        stack = self._invalidate(s.stack, {lk, nk}, k)
        return AState(s.locals[:k] + (nv,) + s.locals[k + 1 :], stack, rel)

    # -- array accesses ---------------------------------------------------------
    def access(self, s: AState, ref: RefVal, idx: IntVal) -> AState | None:
        """State after a *successful* access ref[idx]."""
        if not isinstance(ref, RefVal) or not isinstance(idx, IntVal):
            raise Unsupported("ill-typed array access")
        s = self.assume_null(s, ref, is_null=False)
        if s is None:
            return None
        s = self.assume(s, idx, "ge", const(0))
        length = IntVal(ref.length.meet(NONNEG), ref.len_sym)
        return s and self.assume(s, idx, "lt", length)

    def check(self, s: AState, ref: RefVal, idx: IntVal) -> tuple[str, bool, str]:
        """(verdict, definite, null) for the access ref[idx] in state s."""
        null = "safe" if not ref.may_null else ("definite" if not ref.may_obj else "possible")
        if not ref.may_obj:
            return SAFE, False, null  # never gets to the bounds check
        i, n, rel = idx.itv, ref.length, s.rel
        lower = i.lo >= 0
        upper = i.hi < n.lo
        neg = i.hi < 0
        over = i.lo >= n.hi
        if rel is not None and idx.sym:
            x, c = idx.sym
            lower = lower or rel.upper(ZERO, x) <= c  # -x <= c
            neg = neg or rel.upper(x, ZERO) <= -c - 1  # x + c <= -1
            if ref.len_sym:
                y, d = ref.len_sym
                upper = upper or rel.upper(x, y) <= d - c - 1  # x+c < y+d
                over = over or rel.upper(y, x) <= c - d  # x+c >= y+d
        if lower and upper:
            return SAFE, False, null
        return WARNING, neg or over, null

    # -- transfer function ----------------------------------------------------
    def step(self, i: int, s: AState) -> list[tuple[int, AState]]:
        op = self.m.opcodes[i]
        nxt = i + 1
        match op:
            case jvm.Push(type=t, value=v):
                if isinstance(t, jvm.Reference):
                    return [(nxt, s.push(RefVal(True, False)))]
                if isinstance(t, jvm.Object):
                    return [(nxt, s.push(RefVal(False, True, klass=str(t))))]
                if isinstance(v, int) and isinstance(t, tuple(INT_TYPES)):
                    return [(nxt, s.push(const(int(v))))]
                raise Unsupported(f"push {t}")

            case jvm.Load(index=k):
                v = s.locals[k]
                if isinstance(v, IntVal):
                    return [(nxt, s.push(IntVal(v.itv, (f"L{k}", 0))))]
                if isinstance(v, RefVal):
                    return [(nxt, s.push(replace(v, len_sym=(f"#L{k}", 0), origin=k)))]
                raise Unsupported(f"load of unusable local {k}")

            case jvm.Store(index=k):
                [v], s = s.pop(1)
                return [(nxt, self.store(s, k, v))]

            case jvm.Incr(index=k, amount=c):
                v = s.locals[k]
                if not isinstance(v, IntVal):
                    raise Unsupported("iinc on non-int")
                lk = f"L{k}"
                rel = s.rel.assign(lk, lk, c) if s.rel is not None else None
                stack = self._invalidate(s.stack, {lk}, -1)
                nv = IntVal(v.itv.add(Interval.const(c)))
                return [(nxt, AState(s.locals[:k] + (nv,) + s.locals[k + 1 :], stack, rel))]

            case jvm.Dup(words=1):
                return [(nxt, s.push(s.stack[-1]))]

            case jvm.Binary(type=jvm.Int(), operant=opr):
                [a, b], s = s.pop(2)
                return self._binary(nxt, s, a, b, opr)

            case jvm.Negate(type=jvm.Int()):
                [a], s = s.pop(1)
                return [(nxt, s.push(IntVal(a.itv.neg())))]

            case jvm.Cast(from_=jvm.Int(), to_=t) if type(t) in CASTS:
                [a], s = s.pop(1)
                return [(nxt, s.push(IntVal(a.itv.cast(*CASTS[type(t)]))))]

            case jvm.NewArray(dim=1):
                [size], s = s.pop(1)
                s = self.assume(s, size, "ge", const(0))  # else NegativeArraySize
                if s is None:
                    return []
                n = size.itv.meet(NONNEG)
                return [(nxt, s.push(RefVal(False, True, n, size.sym)))]

            case jvm.ArrayLength():
                [ref], s = s.pop(1)
                if ref.may_null:
                    self.outcomes.add("null pointer")
                s = self.assume_null(s, ref, is_null=False)
                if s is None:
                    return []
                return [(nxt, s.push(IntVal(ref.length.meet(NONNEG), ref.len_sym)))]

            case jvm.ArrayLoad(type=t):
                [ref, idx], s = s.pop(2)
                s = self.access(s, ref, idx)
                if s is None:
                    return []
                elem = top_of(t) if not isinstance(t, jvm.Int) else IntVal(TOP)
                return [(nxt, s.push(elem))]

            case jvm.ArrayStore():
                [ref, idx, _], s = s.pop(3)
                s = self.access(s, ref, idx)
                return [] if s is None else [(nxt, s)]

            case jvm.Get(static=static, field=f):
                if not static:
                    _, s = s.pop(1)
                if static and f.extension.name == "$assertionsDisabled":
                    return [(nxt, s.push(const(0)))]  # run with -ea, as JPAMB does
                return [(nxt, s.push(top_of(f.extension.type)))]

            case jvm.Ifz(condition=c, target=t):
                [v], s = s.pop(1)
                if isinstance(v, RefVal):
                    if c not in (jvm.CmpOpr.Eq, jvm.CmpOpr.Ne):
                        raise Unsupported(f"ifz {c} on reference")
                    is_null = c == jvm.CmpOpr.Eq
                    taken = self.assume_null(s, v, is_null)
                    fall = self.assume_null(s, v, not is_null)
                else:
                    taken = self.assume(s, v, CMP[c], const(0))
                    fall = self.assume(s, v, NEGATE[CMP[c]], const(0))
                return self._branches(t, taken, nxt, fall)

            case jvm.If(condition=c, target=t):
                [a, b], s = s.pop(2)
                if isinstance(a, RefVal) or isinstance(b, RefVal):
                    return [(t, s), (nxt, s)]  # reference equality: no refinement
                taken = self.assume(s, a, CMP[c], b)
                fall = self.assume(s, a, NEGATE[CMP[c]], b)
                return self._branches(t, taken, nxt, fall)

            case jvm.Goto(target=t):
                return [(t, s)]

            case jvm.Return():
                self.outcomes.add("ok")
                return []

            case jvm.New(classname=cn):
                return [(nxt, s.push(RefVal(False, True, klass=cn.dotted())))]

            case jvm.Throw():
                [v], _ = s.pop(1)
                if isinstance(v, RefVal) and v.klass == "java.lang.AssertionError":
                    self.outcomes.add("assertion error")
                else:
                    self.outcomes.add("*")
                return []

            case jvm.InvokeStatic(method=m) | jvm.InvokeVirtual(method=m) | jvm.InvokeSpecial(
                method=m
            ) | jvm.InvokeInterface(method=m):
                # Callees cannot change our locals, and array lengths are
                # immutable, so forgetting the result is sound.
                nargs = len(m.extension.params) + (not isinstance(op, jvm.InvokeStatic))
                _, s = s.pop(nargs)
                rt = m.extension.return_type
                if rt is None:
                    return [(nxt, s)]
                return [(nxt, s.push(top_of(rt)))]

        raise Unsupported(str(op))

    def _branches(self, t, taken, nxt, fall):
        out = []
        if taken is not None:
            out.append((t, taken))
        if fall is not None:
            out.append((nxt, fall))
        return out

    def _binary(self, nxt, s, a: IntVal, b: IntVal, opr) -> list[tuple[int, AState]]:
        B = jvm.BinaryOpr
        sym = None
        match opr:
            case B.Add:
                r = a.itv.add(b.itv)
                sym = _shift(a, b, +1) or _shift(b, a, +1)
            case B.Sub:
                r = a.itv.sub(b.itv)
                sym = _shift(a, b, -1)
            case B.Mul:
                r = a.itv.mul(b.itv)
            case B.Div | B.Rem:
                if 0 in b.itv:
                    self.outcomes.add("divide by zero")
                    if b.itv.is_const:
                        return []
                r = a.itv.div(b.itv) if opr == B.Div else a.itv.rem(b.itv)
            case _:
                raise Unsupported(f"binary {opr}")
        return [(nxt, s.push(IntVal(r, sym)))]

    # -- fixpoint ---------------------------------------------------------------
    def run(self) -> AnalysisResult:
        start = time.perf_counter()
        res = AnalysisResult(self.m.methodid, self.domain_name, self.exchange_on)
        try:
            if self.m.has_handlers:
                raise Unsupported("exception handlers")
            init = self.exchange(self.initial())
            if init is not None:
                self._fixpoint(init)
        except Unsupported as e:
            res.unresolved = str(e)
        except Exception as e:  # a crash must never turn into a safety claim
            res.unresolved = f"internal error: {type(e).__name__}: {e}"
        self._report(res)
        res.steps = self.steps
        res.program_points = len(self.states)
        res.rel_size = sum(s.rel.size() for s in self.states.values() if s.rel is not None)
        res.runtime = time.perf_counter() - start
        return res

    def _fixpoint(self, init: AState):
        self.states = {0: init}
        work, queued = [0], {0}
        while work:
            i = heapq.heappop(work)
            queued.discard(i)
            self.steps += 1
            if self.steps > self.max_steps:
                raise Unsupported("step budget exhausted")
            s = self.exchange(self.states[i])
            if s is None:
                continue
            for t, new in self.step(i, s):
                old = self.states.get(t)
                if old is None:
                    merged = new
                else:
                    merged = join_state(old, new)
                    if t in self.heads:
                        merged = join_state(old, merged, widen=True)
                    if leq_state(merged, old):
                        continue
                self.states[t] = merged
                if t not in queued:
                    queued.add(t)
                    heapq.heappush(work, t)

    def _report(self, res: AnalysisResult):
        for i, op in enumerate(self.m.opcodes):
            if not isinstance(op, (jvm.ArrayLoad, jvm.ArrayStore)):
                continue
            kind = "load" if isinstance(op, jvm.ArrayLoad) else "store"
            a = AccessResult(i, op.offset, kind, SAFE)
            s = self.states.get(i)
            s = self.exchange(s) if s is not None and not res.unresolved else s
            if s is None:
                a.reachable = False
                a.verdict = UNRESOLVED if res.unresolved else SAFE
            else:
                depth = 3 if kind == "store" else 2
                ref, idx = s.stack[-depth], s.stack[-depth + 1]
                a.verdict, a.definite, a.null = self.check(s, ref, idx)
                a.index_itv, a.length_itv = str(idx.itv), str(ref.length)
                if res.unresolved:
                    a.verdict = UNRESOLVED
            res.accesses.append(a)
            if a.verdict != SAFE and a.reachable:
                self.outcomes.add("out of bounds")
            if a.null != "safe" and a.reachable:
                self.outcomes.add("null pointer")
        res.outcomes = set(self.outcomes)


def _shift(a: IntVal, b: IntVal, sign: int) -> Sym:
    """sym of a + sign*b when b is a constant and nothing overflows."""
    if a.sym is None or b.sym is None or b.sym[0] != ZERO:
        return None
    k = sign * b.sym[1]
    if a.itv.lo + k < -(2**31) or a.itv.hi + k > INT_MAX:
        return None
    return (a.sym[0], a.sym[1] + k)


def analyse(
    methodid: str | MethodCode,
    domain: str = "interval",
    exchange: bool = True,
    **kw,
) -> AnalysisResult:
    m = load_method(methodid) if isinstance(methodid, str) else methodid
    return Analysis(m, domain, exchange, **kw).run()
