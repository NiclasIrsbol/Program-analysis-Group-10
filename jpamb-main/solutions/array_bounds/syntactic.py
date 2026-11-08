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
    
    required_facts = {"nonnull", "lower", "upper"}
    access_results = []

    for access in array_accesses:
        array = access.child_by_field_name("array")
        index = access.child_by_field_name("index")

        if array is None or index is None:
            continue
      

        array_name = array.text.decode()
        index_name = index.text.decode()

        guarded = False

        for statement in if_statements:

            condition = statement.child_by_field_name("condition")
            consequence = statement.child_by_field_name("consequence")

            if condition is None or consequence is None:
                continue

            current = access

            while current is not None and current != consequence:
                current = current.parent

            if current is None:
                continue

            facts = get_guard_facts( condition, array_name, index_name )

            if facts is None:
                continue

            if not required_facts.issubset(facts):
                continue

            changed = has_changes_before_access( consequence, access, array_name,  index_name
 )

            if changed:
                continue

            guarded = True
            break

        access_results.append("guarded" if guarded else "unknown")

    if not array_accesses:
        result = "noarray"

    elif all(r == "guarded" for r in access_results):
        result = "guarded"

    else:
        result = "unknown"

    for q in jpamb.QUERIES:
        if q == "out of bounds":
            print(f"{q};{result}")
        else:
            print(f"{q};skip") 

    sys.exit(0)






def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def has_changes_before_access( branch, access, array_name, index_name ):
    for node in walk(branch):

        if node.start_byte >= access.start_byte:
            continue

        target = None

        if node.type == "assignment_expression":
            target = node.child_by_field_name("left")

        elif node.type == "update_expression":
            if node.named_children:
                target = node.named_children[0]

        if target is None:
            continue

        name = target.text.decode()

        if name == index_name or name == array_name:
            return True

    return False


def get_guard_facts(node, array_name, index_name):
    if node.type == "parenthesized_expression":
        children = node.named_children

        if len(children) != 1:
            return None

        return get_guard_facts(
            children[0], array_name, index_name
        )

    if node.type != "binary_expression":
        return None

    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")

    if left is None or right is None:
        return None

    op = next(
        (c.type for c in node.children
         if c.type in ("&&", "||", "!=", ">=", "<")),
        None
    )

    if op == "&&":
        left_facts = get_guard_facts(
            left, array_name, index_name
        )

        right_facts = get_guard_facts(
            right, array_name, index_name
        )

        if left_facts is None or right_facts is None:
            return None

        return left_facts | right_facts

    if op is None or op == "||":
        return None

    lhs = "".join(left.text.decode().split())
    rhs = "".join(right.text.decode().split())

    if lhs == array_name and rhs == "null" and op == "!=":
        return {"nonnull"}

    if lhs == index_name and rhs == "0" and op == ">=":
        return {"lower"}

    if lhs == index_name and rhs == f"{array_name}.length" and op == "<":
        return {"upper"}

    return None