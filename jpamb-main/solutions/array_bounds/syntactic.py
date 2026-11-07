#!/usr/bin/env python3

import logging
import sys
from pathlib import Path

import tree_sitter
import tree_sitter_java

import jpamb


def main():
    methodid = jpamb.getmethodid(
        "syntaxer",
        "1.0",
        "The Rice Theorem Cookers",
        ["syntactic", "python"],
        for_science=True,
    )

    JAVA_LANGUAGE = tree_sitter.Language(tree_sitter_java.language())
    parser = tree_sitter.Parser(JAVA_LANGUAGE)

    log = logging
    log.basicConfig(level=logging.DEBUG)

    suite, _ = jpamb.setup()

    srcfile = suite.sourcefile(methodid.classname).relative_to(Path.cwd())

    with open(srcfile, "rb") as f:
        log.debug("parse sourcefile %s", srcfile)
        tree = parser.parse(f.read())

    simple_classname = str(methodid.classname.name)

    log.debug(f"{simple_classname}")

    # To figure out how to write these you can consult the
    # https://tree-sitter.github.io/tree-sitter/playground
    class_q = tree_sitter.Query(
        JAVA_LANGUAGE,
        f"""
        (class_declaration 
            name: ((identifier) @class-name 
                   (#eq? @class-name "{simple_classname}"))) @class
    """,
    )

    for node in tree_sitter.QueryCursor(class_q).captures(tree.root_node)["class"]:
        break
    else:
        log.error(f"could not find a class of name {simple_classname} in {srcfile}")

        sys.exit(-1)

    # log.debug("Found class %s", node.range)

    method_name = methodid.extension.name

    method_q = tree_sitter.Query(
        JAVA_LANGUAGE,
        f"""
        (method_declaration name: 
          ((identifier) @method-name (#eq? @method-name "{method_name}"))
        ) @method
    """,
    )

    for snode in tree_sitter.QueryCursor(method_q).captures(node)["method"]:
        if not (p := snode.child_by_field_name("parameters")):
            log.debug(f"Could not find parameteres of {method_name}")
            continue

        params = [c for c in p.children if c.type == "formal_parameter"]

        if len(params) != len(methodid.extension.params):
            continue

        # log.debug(methodid.extension.params)
        # log.debug(params)

        for tn, t in zip(methodid.extension.params, params):
            if (tp := t.child_by_field_name("type")) is None:
                break

            if tp.text is None:
                break
        else:
            break
    else:
        log.warning(
            f"could not find a method of name {method_name} in {simple_classname}"
        )
        sys.exit(-1)


    body = snode.child_by_field_name("body")
    assert body and body.text
   
    array_q = tree_sitter.Query( JAVA_LANGUAGE, "(array_access) @access" )
    captures = tree_sitter.QueryCursor(array_q).captures(body)

    array_accesses = captures.get("access", [])


    if_q = tree_sitter.Query( JAVA_LANGUAGE, "(if_statement) @if" )

    captures = tree_sitter.QueryCursor(if_q).captures(body)
    if_statements = captures.get("if", [])

    for access in array_accesses:
        array = access.child_by_field_name("array")
        index = access.child_by_field_name("index")

        if array is None or index is None:
            continue

        array_name = array.text.decode()
        index_name = index.text.decode()
        length_name = f"{array_name}.length"

        for statement in if_statements:
            condition = statement.child_by_field_name("condition")
            if condition is None:
                continue

            for expr in walk(condition):
                if expr.type != "binary_expression":
                    continue

                left = expr.child_by_field_name("left")
                right = expr.child_by_field_name("right")

                if left is None or right is None:
                    continue

                op = next(
                    (c.type for c in expr.children
                    if c.type in ("<", "<=", ">", ">=")),
                    None
                )

                if op is None:
                    continue

                lhs = "".join(left.text.decode().split())
                rhs = "".join(right.text.decode().split())

                if lhs == index_name and rhs == "0":
                    if op == ">=":
                        log.debug("Lower bound check: %s", expr.text.decode())
                    elif op == "<":
                        log.debug("Negative index check: %s", expr.text.decode())

                elif lhs == index_name and rhs == length_name:
                    if op == "<":
                        log.debug("Upper bound check: %s", expr.text.decode())
                    elif op == ">=":
                        log.debug("Out-of-bounds check: %s", expr.text.decode())

        


    for q in jpamb.QUERIES:
        print(f"{q};skip")    

    sys.exit(0)






def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)