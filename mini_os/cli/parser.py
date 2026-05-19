from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass
class Segment:
    argv: list[str]
    pipe: bool = False
    redirect: str | None = None
    append: bool = False
    background: bool = False
    op: str = ";"


def strip_comment(line: str) -> str:
    out = []
    quoted = False
    quote = ""
    for ch in line:
        if ch in {"'", '"'}:
            quoted = not quoted if not quoted or quote == ch else quoted
            quote = ch if quoted else ""
        if ch == "#" and not quoted:
            break
        out.append(ch)
    return "".join(out)


def split_chains(line: str) -> list[tuple[str, str]]:
    lexer = shlex.shlex(strip_comment(line), posix=True, punctuation_chars=";&|<>")
    lexer.whitespace_split = True
    tokens = list(lexer)
    result: list[tuple[str, str]] = []
    current: list[str] = []
    op = ";"
    for token in tokens:
        if token in {";", "&&", "||"}:
            if current:
                result.append((op, " ".join(shlex.quote(x) for x in current)))
                current = []
            op = token
        else:
            current.append(token)
    if current:
        result.append((op, " ".join(shlex.quote(x) for x in current)))
    return result


def parse_pipeline(line: str, op: str = ";") -> list[Segment]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars="|&<>")
    lexer.whitespace_split = True
    tokens = list(lexer)
    segments: list[Segment] = []
    current: list[str] = []
    redirect: str | None = None
    append = False
    background = False
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok == "|":
            segments.append(Segment(current, pipe=True, redirect=redirect, append=append, op=op))
            current, redirect, append = [], None, False
        elif tok in {">", ">>"}:
            if idx + 1 >= len(tokens):
                raise ValueError("missing redirect target")
            redirect = tokens[idx + 1]
            append = tok == ">>"
            idx += 1
        elif tok == "&" and idx == len(tokens) - 1:
            background = True
        else:
            current.append(tok)
        idx += 1
    if current:
        segments.append(Segment(current, redirect=redirect, append=append, background=background, op=op))
    return segments
