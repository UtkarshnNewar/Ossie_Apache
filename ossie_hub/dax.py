"""DAX measure -> ANSI SQL aggregate expression.

Design rules (they exist so a translation is never silently wrong):

  1. Anything not understood is REJECTED with a reason (DaxUnsupported). Nothing is guessed.
  2. A filter condition must be row-level. A condition that contains an aggregate depends on
     the viewer's selection and is rejected.
  3. Constructs whose SQL meaning differs subtly from DAX are translated but reported as
     caveats (`DaxParser.notes`), so the report shows them.

Supported: SUM/AVERAGE/MIN/MAX/COUNT/DISTINCTCOUNT, SUMX/AVERAGEX/MINX/MAXX/COUNTX over a
plain table, COUNTROWS of the fact table (optionally filtered), DIVIDE, IF, SWITCH, AND/OR/
NOT and &&/||, COALESCE, ISBLANK/BLANK/TRUE/FALSE, ABS/ROUND, YEAR/MONTH/DAY, DATE(y,m,d),
CALCULATE with column filters / FILTER(table, cond) / FILTER(ALL(col), cond) / KEEPFILTERS,
comparison operators, IN { ... }, [Measure] references, and VAR / RETURN (inlined).
"""
from __future__ import annotations

import re


class DaxUnsupported(Exception):
    pass


AGGREGATE_CALL = re.compile(r"\b(SUM|AVG|MIN|MAX|COUNT)\(")


def paren(sql: str) -> str:
    """Wrap in parentheses unless already a single function call or plain reference."""
    if re.fullmatch(r"[\w.]+", sql):
        return sql
    m = re.match(r"^[A-Za-z_]+\(", sql)
    if m:
        depth = 0
        for i, ch in enumerate(sql[m.end() - 1:], m.end() - 1):
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0:
                if i == len(sql) - 1:
                    return sql
                break
    return f"({sql})"


def apply_filter(sql: str, cond: str) -> str:
    """DAX CALCULATE(<aggregate>, <filter>) -> SQL `AGG(...) FILTER (WHERE cond)`.
    The filter is attached to every aggregate call in `sql` (e.g. inside COALESCE)."""
    if AGGREGATE_CALL.search(cond):
        raise DaxUnsupported(
            "a filter condition contains an aggregate (e.g. MAX of a column): its value depends "
            "on the viewer's selection, so it is not a row-level filter")
    out, i, found = [], 0, False
    for m in AGGREGATE_CALL.finditer(sql):
        if m.start() < i:
            continue
        depth, j = 0, m.end() - 1
        for j in range(m.end() - 1, len(sql)):
            depth += sql[j] == "("
            depth -= sql[j] == ")"
            if depth == 0:
                break
        if sql[j + 1:].lstrip().startswith("FILTER"):
            raise DaxUnsupported("CALCULATE nested inside another filter")
        out += [sql[i:j + 1], f" FILTER (WHERE {cond})"]
        i, found = j + 1, True
    out.append(sql[i:])
    if not found:
        raise DaxUnsupported("CALCULATE over a non-aggregate expression")
    return "".join(out)


TOKEN = re.compile(r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?)|
    (?P<str>"(?:[^"]|"")*")|
    (?P<tq>'(?:[^']|'')+')|
    (?P<br>\[[^\]]+\])|
    (?P<id>[A-Za-z_][A-Za-z0-9_]*)|
    (?P<op>&&|\|\||[()+\-*/,<>=&{}])
)""", re.X)

COMMENT_OR_LITERAL = re.compile(
    r"\"(?:[^\"]|\"\")*\"|'(?:[^']|'')*'|--[^\n]*|//[^\n]*|/\*.*?\*/", re.S)


def strip_comments(text: str) -> str:
    """Remove DAX comments (-- ..., // ..., /* ... */) but leave string literals alone."""
    return COMMENT_OR_LITERAL.sub(
        lambda m: m.group(0) if m.group(0)[0] in "\"'" else " ", text)


AGG = {"SUM": "SUM", "AVERAGE": "AVG", "MIN": "MIN", "MAX": "MAX", "COUNT": "COUNT"}
ITERATORS = {"SUMX": "SUM", "AVERAGEX": "AVG", "MINX": "MIN", "MAXX": "MAX", "COUNTX": "COUNT"}
TABLE = "__TABLE__("      # marker for a bare table reference while parsing
ALLM = "__ALL__("         # marker for ALL(...) while parsing


class DaxParser:
    def __init__(self, text: str, measures: dict[str, str], ident=None, fact_table: str | None = None):
        self.ident = ident or (lambda table, col: f"{table}.{col}")
        self.fact = fact_table
        self.notes: list[str] = []
        self.vars: dict[str, str] = {}
        self.toks: list[tuple[str, str]] = []
        pos = 0
        text = strip_comments(text).strip()
        while pos < len(text):
            m = TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise DaxUnsupported(f"cannot tokenize near {text[pos:pos + 20]!r}")
            kind = m.lastgroup
            self.toks.append((kind, m.group(kind)))
            pos = m.end()
        self.i = 0
        self.measures = measures

    # ------------------------------------------------------------------ token helpers
    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def expect(self, val):
        _, v = self.take()
        if v != val:
            raise DaxUnsupported(f"expected {val!r}, got {v!r}")

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def is_kw(self, word: str) -> bool:
        k, v = self.peek()
        return k == "id" and v.upper() == word

    # ------------------------------------------------------------------ grammar
    def parse(self) -> str:
        while self.is_kw("VAR"):  # VAR name = expr ... RETURN expr  (variables are inlined)
            self.take()
            _, name = self.take()
            self.expect("=")
            self.vars[name] = paren(self.expr())
        if self.vars:
            if not self.is_kw("RETURN"):
                raise DaxUnsupported("VAR without RETURN")
            self.take()
        out = self.expr()
        if self.i != len(self.toks):
            raise DaxUnsupported("trailing tokens")
        if ALLM in out:
            raise DaxUnsupported("ALL() used outside FILTER(ALL(column), condition)")
        if TABLE in out:
            raise DaxUnsupported("a table reference is used where a value is expected")
        return out

    def additive(self) -> str:
        left = self.term()
        while self.peek()[1] in ("+", "-", "*", "/"):
            op = self.take()[1]
            left = f"{left} {op} {self.term()}"
        return left

    def concat(self) -> str:
        """DAX `a & b` (text concatenation; a blank counts as empty text)."""
        first = self.additive()
        if self.peek()[1] != "&":
            return first
        parts = [first]
        while self.peek()[1] == "&":
            self.take()
            parts.append(self.additive())

        def text(x: str) -> str:
            return x if re.fullmatch(r"'[^']*'", x) else f"COALESCE(CAST({x} AS STRING), '')"
        return "CONCAT(" + ", ".join(text(p) for p in parts) + ")"

    def comparison(self) -> str:
        left = self.concat()
        k, v = self.peek()
        if v in ("=", "<", ">"):  # = <> < > <= >=
            op = self.take()[1]
            if op in ("<", ">") and self.peek()[1] in ("=", ">"):
                op += self.take()[1]
            right = self.concat()
            if "'" in right or "'" in left:
                self.note("DAX compares text case-insensitively; SQL is case-sensitive")
            return f"{left} {op} {right}"
        if k == "id" and v.upper() == "IN":
            self.take()
            self.expect("{")
            items = []
            while True:
                items.append(self.additive())
                _, sep = self.take()
                if sep == "}":
                    break
                if sep != ",":
                    raise DaxUnsupported("bad IN list")
            if any("'" in x for x in items):
                self.note("DAX compares text case-insensitively; SQL is case-sensitive")
            return f"{left} IN ({', '.join(items)})"
        return left

    def and_expr(self) -> str:
        left = self.comparison()
        while self.peek()[1] == "&&":
            self.take()
            left = f"({left} AND {self.comparison()})"
        return left

    def expr(self) -> str:
        left = self.and_expr()
        while self.peek()[1] == "||":
            self.take()
            left = f"({left} OR {self.and_expr()})"
        return left

    def term(self) -> str:
        k, v = self.take()
        if k == "op" and v in ("-", "+"):  # unary sign
            inner = self.term()
            return f"-{paren(inner)}" if v == "-" else inner
        if k == "num":
            return v
        if k == "str":
            return "'" + v[1:-1].replace('""', '"') + "'"
        if v == "(":
            e = self.expr()
            self.expect(")")
            return f"({e})"
        if k == "br":  # [Measure] reference -> inline its SQL
            name = v[1:-1]
            if name not in self.measures:
                raise DaxUnsupported(f"reference to unknown measure [{name}]")
            return paren(self.measures[name])
        if k in ("tq", "id"):
            name = v[1:-1].replace("''", "'") if k == "tq" else v
            nk, nv = self.peek()
            if nk == "br":  # Table[Column]
                self.take()
                return self.ident(name, nv[1:-1])
            if nv == "(":  # function call
                self.take()
                return self.call(name.upper())
            if name in self.vars:
                return self.vars[name]
            if name.upper() in ("VAR", "RETURN"):
                raise DaxUnsupported("unsupported VAR / RETURN layout")
            return f"{TABLE}{name})"  # a bare table name (used by FILTER / SUMX / COUNTROWS)
        raise DaxUnsupported(f"unexpected token {v!r}")

    def args(self) -> list[str]:
        out = []
        if self.peek()[1] == ")":
            self.take()
            return out
        while True:
            out.append(self.expr())
            _, v = self.take()
            if v == ")":
                return out
            if v != ",":
                raise DaxUnsupported("bad argument list")

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def table_of(marker: str) -> str | None:
        m = re.fullmatch(re.escape(TABLE) + r"(.+?)\)(?: WHERE (.*))?", marker, re.S)
        return m.group(1) if m else None

    def same_table(self, expr: str, table: str) -> bool:
        want = self.ident(table, "x").split(".")[0]
        refs = {r.split(".")[0] for r in re.findall(r"\b[A-Za-z_]\w*\.[A-Za-z_]\w*", expr)}
        return refs <= {want}

    # ------------------------------------------------------------------ functions
    def call(self, fn: str) -> str:
        a = self.args()
        n = len(a)
        if fn in AGG and n == 1:
            return f"{AGG[fn]}({a[0]})"
        if fn in ("MIN", "MAX") and n == 2:
            return f"{'LEAST' if fn == 'MIN' else 'GREATEST'}({a[0]}, {a[1]})"
        if fn == "DISTINCTCOUNT" and n == 1:
            self.note("DAX DISTINCTCOUNT counts a blank value; SQL COUNT(DISTINCT) ignores NULL")
            return f"COUNT(DISTINCT {a[0]})"
        if fn == "DISTINCTCOUNTNOBLANK" and n == 1:  # exact equivalent
            return f"COUNT(DISTINCT {a[0]})"
        if fn == "COUNTA" and n == 1:  # counts non-blank values: same as SQL COUNT(col)
            return f"COUNT({a[0]})"
        if fn in ITERATORS and n == 2:
            tbl = self.table_of(a[0])
            if tbl is None:
                raise DaxUnsupported(f"{fn} over an expression that is not a plain table")
            if not self.same_table(a[1], tbl):
                raise DaxUnsupported(f"{fn} expression uses columns of another table")
            sql = f"{ITERATORS[fn]}({a[1]})"
            cond = re.fullmatch(re.escape(TABLE) + r".+?\) WHERE (.*)", a[0], re.S)
            return apply_filter(sql, cond.group(1)) if cond else sql
        if fn == "COUNTROWS" and n == 1:
            tbl = self.table_of(a[0])
            if tbl is None or self.fact is None or tbl != self.fact:
                raise DaxUnsupported("COUNTROWS is only translated for the fact table "
                                     "(on another table it would count joined rows)")
            cond = re.fullmatch(re.escape(TABLE) + r".+?\) WHERE (.*)", a[0], re.S)
            return apply_filter("COUNT(*)", cond.group(1)) if cond else "COUNT(*)"
        if fn == "DIVIDE" and n in (2, 3):
            core = f"{paren(a[0])} / NULLIF({a[1]}, 0)"
            return core if n == 2 else f"COALESCE({core}, {a[2]})"
        if fn == "COALESCE" and n >= 2:
            if n == 2 and a[0].startswith("COALESCE(") and a[0].endswith(f", {a[1]})") \
                    and paren(a[0]) == a[0]:
                return a[0]  # COALESCE(COALESCE(x, 0), 0) -> COALESCE(x, 0)
            return f"COALESCE({', '.join(a)})"
        if fn == "CALCULATE" and n >= 1:
            if n == 1:
                return a[0]
            conds = []
            for f in a[1:]:
                if f.startswith(TABLE):  # FILTER(table, cond) came back as a marked table
                    m = re.fullmatch(re.escape(TABLE) + r".+?\) WHERE (.*)", f, re.S)
                    if not m:
                        raise DaxUnsupported("a bare table is used as a CALCULATE filter")
                    f = m.group(1)
                conds.append(f)
            return apply_filter(a[0], " AND ".join(conds))
        if fn == "KEEPFILTERS" and n == 1:
            return a[0]
        if fn == "ALL":
            return ALLM + ", ".join(a) + ")"
        if fn == "FILTER" and n == 2:
            if a[0].startswith(ALLM):
                self.note("FILTER(ALL(col), cond) becomes a row filter: DAX also ignores any outer "
                          "filter on that column, which a plain SQL filter cannot do")
                return a[1]
            tbl = self.table_of(a[0])
            if tbl is not None:
                return f"{TABLE}{tbl}) WHERE {a[1]}"
            raise DaxUnsupported("FILTER over an expression that is not a plain table")
        if fn in ("YEAR", "MONTH", "DAY") and n == 1:
            return f"EXTRACT({fn} FROM {a[0]})"
        if fn == "DATE" and n == 3 and all(re.fullmatch(r"\d+", x) for x in a):
            return f"DATE '{int(a[0]):04d}-{int(a[1]):02d}-{int(a[2]):02d}'"
        if fn == "IF" and n in (2, 3):
            return f"CASE WHEN {a[0]} THEN {a[1]}" + (f" ELSE {a[2]}" if n == 3 else "") + " END"
        if fn == "SWITCH" and n >= 3:
            subject, rest = a[0], a[1:]
            else_ = rest.pop() if len(rest) % 2 == 1 else None
            if len(rest) < 2:
                raise DaxUnsupported("SWITCH with no cases")
            whens = []
            for c, r in zip(rest[0::2], rest[1::2]):
                whens.append(f"WHEN {c} THEN {r}" if subject == "TRUE" else f"WHEN {subject} = {c} THEN {r}")
            return "CASE " + " ".join(whens) + (f" ELSE {else_}" if else_ is not None else "") + " END"
        if fn == "TRUE" and n == 0:
            return "TRUE"
        if fn == "FALSE" and n == 0:
            return "FALSE"
        if fn == "BLANK" and n == 0:
            return "NULL"
        if fn == "ISBLANK" and n == 1:
            return f"({a[0]} IS NULL)"
        if fn in ("AND", "OR") and n == 2:
            return f"({a[0]} {fn} {a[1]})"
        if fn == "NOT" and n == 1:
            return f"(NOT {a[0]})"
        if fn == "ABS" and n == 1:
            return f"ABS({a[0]})"
        if fn == "ROUND" and n == 2:
            return f"ROUND({a[0]}, {a[1]})"
        if fn in ("ALLSELECTED", "VALUES", "SELECTEDVALUE", "HASONEVALUE", "ISFILTERED",
                  "CONCATENATEX", "EARLIER", "RANKX", "TOPN"):
            raise DaxUnsupported(f"{fn}: depends on the viewer's selection or grouping at "
                                 f"query time, so it has no model-level SQL equivalent")
        raise DaxUnsupported(f"function {fn}")


def dax_to_sql(dax: str, measures: dict[str, str], ident=None, fact_table: str | None = None) -> str:
    return DaxParser(dax, measures, ident, fact_table).parse()


def dax_to_sql_notes(dax: str, measures: dict[str, str], ident=None,
                     fact_table: str | None = None) -> tuple[str, list[str]]:
    """Like dax_to_sql, also returning the semantic caveats the translation introduced."""
    p = DaxParser(dax, measures, ident, fact_table)
    sql = p.parse()
    return sql, p.notes
