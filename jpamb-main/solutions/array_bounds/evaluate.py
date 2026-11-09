import json
from collections import Counter

from static import AbstractInterpreter, error_reason

import jpamb
from jpamb import jvm

CONFIGURATIONS = (
    ("interval", False, False),
    ("dbm-exchange-on", True, True),
    ("dbm-exchange-off", True, False),
)


def evaluate(bc, methods):
    methods = list({method.encode(): method for method in methods}.values())
    configurations = []
    for name, relational, exchange in CONFIGURATIONS:
        method_counts = Counter(total=len(methods), completed=0, unresolved=0)
        access_counts = Counter(safe=0, warning=0, unresolved=0, unreachable=0)
        unknown_access_counts = 0
        results = []
        for methodid in methods:
            positions = None
            complete, reason, accesses = False, None, None
            try:
                positions = [
                    i
                    for i, op in enumerate(bc.getmethod(methodid).opcodes)
                    if isinstance(op, (jvm.ArrayLoad, jvm.ArrayStore))
                ]
                interpreter = AbstractInterpreter.initial(
                    bc, methodid, relational=relational, exchange=exchange
                ).run()
                _, accesses = interpreter.results()
                complete = not interpreter.incomplete
                reason = interpreter.reason
                accesses = {
                    position: accesses.get(position, "unresolved")
                    if complete
                    else "unresolved"
                    for position in positions
                }
            except Exception as error:  # noqa: BLE001 - Failed analysis must stay unresolved.
                complete = False
                reason = error_reason(error)
                if positions is not None:
                    accesses = dict.fromkeys(positions, "unresolved")
            method_counts["completed" if complete else "unresolved"] += 1
            if accesses is None:
                unknown_access_counts += 1
            else:
                access_counts.update(accesses.values())
            results.append(
                {
                    "method": methodid.encode(),
                    "completed": complete,
                    "reason": reason,
                    "accesses": accesses,
                }
            )
        configurations.append(
            {
                "name": name,
                "methods": dict(method_counts),
                "accesses": dict(access_counts),
                "unknown_access_count_methods": unknown_access_counts,
                "results": results,
            }
        )
    return {"configurations": configurations}


def main():
    suite, eff = jpamb.setup()
    methods = sorted(
        suite.benchmark(eff=eff).entries(), key=lambda method: method.encode()
    )
    report = evaluate(jpamb.Bytecode(suite, eff, {}), methods)
    print(json.dumps(report, indent=2))
