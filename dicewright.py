"""Dicewright: exact odds, seeded rolls and head-to-head comparisons for dice expressions.

Expressions look like ``3d6``, ``d20+5``, ``4d6kh3``, ``2d20kl1``, ``d6!``, ``5d10>=7``.
Distributions are computed with exact fractions, so every probability is precise.
Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import operator
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations_with_replacement

MAX_DICE = 100
MAX_SIDES = 1000
MAX_OUTCOMES = 200_000
EXPLODE_CAP = 6
EXPLODE_MAX_DICE = 10
EXPLODE_MAX_SIDES = 20
HISTOGRAM_MAX_ROWS = 80
PERCENTILES = (10, 25, 50, 75, 90)

OPERATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "=": operator.eq,
}

_NUM = re.compile(r"\d+")
_KEEP = re.compile(r"k([hl])", re.IGNORECASE)
_OP = re.compile(r"(>=|<=|==|=|>|<)")
_SIGNED = re.compile(r"[+-]?\d+")
_MOD = re.compile(r"([+-])\s*(\d+)")
_TEST = re.compile(r"\s*(>=|<=|==|=|>|<)\s*(-?\d+)\s*$")


class ExprError(ValueError):
    """Raised for any invalid expression. ``column`` is 1-based when known."""

    def __init__(self, message, column=None):
        super().__init__(message)
        self.column = column


@dataclass(frozen=True)
class Expr:
    text: str
    count: int
    sides: int
    explode: bool = False
    keep: str | None = None  # "h" (highest) or "l" (lowest)
    keep_n: int = 0
    op: str | None = None  # success test: counts dice where value <op> target
    target: int = 0
    mod: int = 0


def _skip(s, pos):
    while pos < len(s) and s[pos].isspace():
        pos += 1
    return pos


def _fail(s, pos, what):
    if pos < len(s):
        raise ExprError(f"{what} at column {pos + 1}, found {s[pos]!r}", pos + 1)
    raise ExprError(f"{what}, but the expression ended", len(s) + 1)


def parse(text):
    """Parse one dice expression. Every character is consumed or an error is raised."""
    s = text
    if not s.strip():
        raise ExprError("empty dice expression", 1)

    pos = _skip(s, 0)
    count = None
    m = _NUM.match(s, pos)
    if m:
        count = int(m.group())
        pos = _skip(s, m.end())
    if pos >= len(s) or s[pos] not in "dD":
        _fail(s, pos, "expected 'd'")

    pos = _skip(s, pos + 1)
    m = _NUM.match(s, pos)
    if not m:
        _fail(s, pos, "expected number of sides")
    sides = int(m.group())
    pos = m.end()

    explode = pos < len(s) and s[pos] == "!"
    if explode:
        pos += 1

    keep, keep_n = None, 0
    m = _KEEP.match(s, pos)
    if m:
        keep = m.group(1).lower()
        pos = m.end()
        m = _NUM.match(s, pos)
        if not m:
            _fail(s, pos, f"expected keep count after 'k{keep}'")
        keep_n = int(m.group())
        pos = m.end()

    op, target = None, 0
    pos = _skip(s, pos)
    m = _OP.match(s, pos)
    if m:
        op = "=" if m.group() == "==" else m.group()
        pos = _skip(s, m.end())
        m = _SIGNED.match(s, pos)
        if not m:
            _fail(s, pos, "expected a target number after the comparison")
        target = int(m.group())
        pos = m.end()

    mod = 0
    pos = _skip(s, pos)
    m = _MOD.match(s, pos)
    if m:
        mod = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
        pos = m.end()
    pos = _skip(s, pos)
    if pos < len(s):
        _fail(s, pos, "unexpected text")

    expr = Expr(
        text=text.strip(),
        count=1 if count is None else count,
        sides=sides,
        explode=explode,
        keep=keep,
        keep_n=keep_n,
        op=op,
        target=target,
        mod=mod,
    )
    _validate(expr)
    return expr


def _validate(e):
    if not 1 <= e.count <= MAX_DICE:
        raise ExprError(f"dice count must be between 1 and {MAX_DICE}, got {e.count}", 1)
    if not 2 <= e.sides <= MAX_SIDES:
        raise ExprError(f"sides must be between 2 and {MAX_SIDES}, got {e.sides}", 1)
    if e.keep and not 1 <= e.keep_n <= e.count:
        raise ExprError(f"keep count {e.keep_n} must be between 1 and the {e.count} dice", 1)
    if e.keep and e.op:
        raise ExprError("cannot combine keep (kh/kl) with a success test", 1)
    if e.explode and (e.keep or e.op):
        raise ExprError("exploding dice (!) cannot be combined with keep or success tests", 1)
    if e.explode and (e.count > EXPLODE_MAX_DICE or e.sides > EXPLODE_MAX_SIDES):
        raise ExprError(
            f"exploding dice are limited to {EXPLODE_MAX_DICE} dice with up to "
            f"{EXPLODE_MAX_SIDES} sides",
            1,
        )
    if not e.explode and math.comb(e.count + e.sides - 1, e.count) > MAX_OUTCOMES:
        raise ExprError("too many outcomes to enumerate exactly; use fewer dice or sides", 1)


def parse_test(text):
    """Parse a probability test such as ``>=15`` into (operator, number)."""
    m = _TEST.match(text)
    if not m:
        raise ExprError(f"invalid test {text!r}; use an operator and a number, e.g. >=15")
    op = "=" if m.group(1) == "==" else m.group(1)
    return op, int(m.group(2))


# ---------------------------------------------------------------- exact odds

def _multinomial(combo):
    ways = math.factorial(len(combo))
    for c in Counter(combo).values():
        ways //= math.factorial(c)
    return ways


def _score(expr, combo):
    """Result of one multiset of faces (combo is sorted ascending)."""
    if expr.keep == "h":
        total = sum(combo[-expr.keep_n:])
    elif expr.keep == "l":
        total = sum(combo[:expr.keep_n])
    elif expr.op:
        return sum(1 for v in combo if OPERATORS[expr.op](v, expr.target))
    else:
        total = sum(combo)
    return total


def _explode_die(sides, cap):
    """Distribution of one die that re-rolls on its top face, up to ``cap`` extra rolls."""
    dist = {v: Fraction(1, sides) for v in range(1, sides + 1)}
    for _ in range(cap):
        nxt = {v: Fraction(1, sides) for v in range(1, sides)}
        for v, p in dist.items():
            nxt[v + sides] = nxt.get(v + sides, Fraction(0)) + p / sides
        dist = nxt
    return dist


def _convolve(a, b):
    out = defaultdict(Fraction)
    for x, px in a.items():
        for y, py in b.items():
            out[x + y] += px * py
    return out


def distribution(expr):
    """Exact probability of each result, as a sorted dict of value -> Fraction."""
    if expr.explode:
        dist = {0: Fraction(1)}
        die = _explode_die(expr.sides, EXPLODE_CAP)
        for _ in range(expr.count):
            dist = _convolve(dist, die)
    else:
        weights = defaultdict(int)
        for combo in combinations_with_replacement(range(1, expr.sides + 1), expr.count):
            weights[_score(expr, combo)] += _multinomial(combo)
        total = expr.sides ** expr.count
        dist = {v: Fraction(w, total) for v, w in weights.items()}
    shifted = defaultdict(Fraction)
    for v, p in dist.items():
        shifted[v + expr.mod] += p
    return dict(sorted(shifted.items()))


def stats(dist):
    mean = sum(v * p for v, p in dist.items())
    variance = sum(v * v * p for v, p in dist.items()) - mean * mean
    return {
        "mean": mean,
        "variance": variance,
        "stdev": math.sqrt(variance),
        "min": min(dist),
        "max": max(dist),
    }


def percentile(dist, pct):
    """Smallest value whose cumulative probability reaches pct percent."""
    q = Fraction(pct, 100)
    cumulative = Fraction(0)
    for v, p in dist.items():
        cumulative += p
        if cumulative >= q:
            return v
    return max(dist)


def probability(dist, op, target):
    return sum((p for v, p in dist.items() if OPERATORS[op](v, target)), Fraction(0))


def compare(dist_a, dist_b):
    """Exact P(A > B), P(A = B), P(A < B) for independent results A and B."""
    result = {"greater": Fraction(0), "equal": Fraction(0), "less": Fraction(0)}
    for a, pa in dist_a.items():
        for b, pb in dist_b.items():
            p = pa * pb
            if a > b:
                result["greater"] += p
            elif a == b:
                result["equal"] += p
            else:
                result["less"] += p
    return result


def histogram_lines(dist, width=40):
    if len(dist) > HISTOGRAM_MAX_ROWS:
        raise ExprError(
            f"histogram needs at most {HISTOGRAM_MAX_ROWS} outcomes, this has {len(dist)}; "
            "use --format csv instead"
        )
    peak = max(float(p) for p in dist.values())
    lines = []
    for v, p in dist.items():
        bar = "#" * max(1, round(float(p) / peak * width)) if p else ""
        lines.append(f"{v:>6} | {bar:<{width}} {float(p) * 100:6.2f}%")
    return lines


# ------------------------------------------------------------ seeded rolling

@dataclass
class Roll:
    dice: list  # one list of faces per die (more than one face when exploding)
    kept: list
    dropped: list
    total: int


def roll_once(expr, rng):
    chains = []
    for _ in range(expr.count):
        chain = [rng.randint(1, expr.sides)]
        while expr.explode and chain[-1] == expr.sides and len(chain) - 1 < EXPLODE_CAP:
            chain.append(rng.randint(1, expr.sides))
        chains.append(chain)
    values = [sum(chain) for chain in chains]
    ordered = sorted(values)
    if expr.keep == "h":
        cut = len(ordered) - expr.keep_n
        kept, dropped = ordered[cut:], ordered[:cut]
        total = sum(kept)
    elif expr.keep == "l":
        kept, dropped = ordered[:expr.keep_n], ordered[expr.keep_n:]
        total = sum(kept)
    elif expr.op:
        kept = [v for v in values if OPERATORS[expr.op](v, expr.target)]
        dropped = [v for v in values if not OPERATORS[expr.op](v, expr.target)]
        total = len(kept)
    else:
        kept, dropped = values, []
        total = sum(values)
    return Roll(dice=chains, kept=kept, dropped=dropped, total=total + expr.mod)


def simulate(expr, trials, seed=None):
    rng = random.Random(seed)
    return Counter(roll_once(expr, rng).total for _ in range(trials))


# ----------------------------------------------------------- file linting

def lint_lines(lines):
    """Check a plain-text file of expressions, one per line.

    Each line is ``expression`` or ``name: expression``; ``#`` starts a comment.
    Returns one result dict per expression, including the line number and any error.
    """
    results = []
    for lineno, raw in enumerate(lines, start=1):
        body = raw.split("#", 1)[0].rstrip("\n")
        if not body.strip():
            continue
        name, offset, expr_part = None, 0, body
        if ":" in body:
            head, expr_part = body.split(":", 1)
            name, offset = head.strip() or None, len(head) + 1
        result = {"line": lineno, "name": name, "expression": expr_part.strip()}
        try:
            expr = parse(expr_part)
            dist = distribution(expr)
            result["mean"] = float(stats(dist)["mean"])
            result["error"] = None
        except ExprError as exc:
            column = offset + exc.column if exc.column else None
            result["error"] = str(exc)
            result["column"] = column
        results.append(result)
    return results


# ------------------------------------------------------------------ output

def _fmt_frac(p):
    return str(p)


def _odds_document(expr, dist, test):
    info = stats(dist)
    doc = {
        "expression": expr.text,
        "mean": float(info["mean"]),
        "mean_exact": _fmt_frac(info["mean"]),
        "variance": float(info["variance"]),
        "variance_exact": _fmt_frac(info["variance"]),
        "stdev": info["stdev"],
        "min": info["min"],
        "max": info["max"],
        "percentiles": {str(q): percentile(dist, q) for q in PERCENTILES},
        "probability": None,
        "distribution": [
            {"value": v, "probability": float(p), "exact": _fmt_frac(p)} for v, p in dist.items()
        ],
    }
    if test:
        op, target = test
        p = probability(dist, op, target)
        doc["probability"] = {
            "test": f"{op}{target}",
            "value": float(p),
            "exact": _fmt_frac(p),
        }
    return doc


def _odds_text(expr, dist, test, histogram):
    info = stats(dist)
    pct = "  ".join(f"p{q} {percentile(dist, q)}" for q in PERCENTILES)
    lines = [
        f"Expression   {expr.text}",
        f"Mean         {float(info['mean']):.4f}",
        f"Std dev      {info['stdev']:.4f}",
        f"Range        {info['min']} .. {info['max']}",
        f"Percentiles  {pct}",
    ]
    if test:
        op, target = test
        p = probability(dist, op, target)
        lines.append(f"P(result {op} {target})  {float(p):.4f}  (exact {_fmt_frac(p)})")
    if histogram:
        lines.append("")
        lines.extend(histogram_lines(dist))
    return "\n".join(lines)


def _die_text(chain):
    return "+".join(str(v) for v in chain)


def _roll_text(expr, roll):
    dice = ", ".join(_die_text(c) for c in roll.dice)
    line = f"{expr.text}: dice [{dice}]"
    if expr.op:
        line += f"  successes {len(roll.kept)}"
    else:
        line += f"  kept {roll.kept}"
        if roll.dropped:
            line += f"  dropped {roll.dropped}"
    if expr.mod:
        line += f"  modifier {expr.mod:+d}"
    return line + f"  total {roll.total}"


# --------------------------------------------------------------- commands

def cmd_odds(args, out):
    expr = parse(args.expression)
    dist = distribution(expr)
    test = parse_test(args.test) if args.test else None
    if args.format == "json":
        out.write(json.dumps(_odds_document(expr, dist, test), indent=2) + "\n")
    elif args.format == "csv":
        writer = csv.writer(out)
        writer.writerow(["value", "probability", "exact"])
        for v, p in dist.items():
            writer.writerow([v, f"{float(p):.10f}", _fmt_frac(p)])
    else:
        out.write(_odds_text(expr, dist, test, args.histogram) + "\n")
    return 0


def cmd_roll(args, out):
    expr = parse(args.expression)
    if args.count < 1:
        raise ExprError("--count must be at least 1")
    rng = random.Random(args.seed)
    for _ in range(args.count):
        out.write(_roll_text(expr, roll_once(expr, rng)) + "\n")
    return 0


def cmd_simulate(args, out):
    expr = parse(args.expression)
    if args.trials < 1:
        raise ExprError("--trials must be at least 1")
    dist = distribution(expr)
    counts = simulate(expr, args.trials, args.seed)
    sim_mean = sum(v * c for v, c in counts.items()) / args.trials
    exact_mean = float(stats(dist)["mean"])
    values = set(dist) | set(counts)
    worst = max(abs(counts.get(v, 0) / args.trials - float(dist.get(v, 0))) for v in values)
    out.write(f"Expression       {expr.text}\n")
    out.write(f"Trials           {args.trials}  (seed {args.seed})\n")
    out.write(f"Simulated mean   {sim_mean:.4f}\n")
    out.write(f"Exact mean       {exact_mean:.4f}\n")
    out.write(f"Worst deviation  {worst:.4f}  (largest gap between a simulated and exact probability)\n")
    return 0


def cmd_compare(args, out):
    a, b = parse(args.a), parse(args.b)
    dist_a, dist_b = distribution(a), distribution(b)
    result = compare(dist_a, dist_b)
    mean_a, mean_b = stats(dist_a)["mean"], stats(dist_b)["mean"]
    out.write(f"A  {a.text}   mean {float(mean_a):.4f}\n")
    out.write(f"B  {b.text}   mean {float(mean_b):.4f}\n")
    out.write(f"P(A > B)  {float(result['greater']):.4f}  (exact {_fmt_frac(result['greater'])})\n")
    out.write(f"P(A = B)  {float(result['equal']):.4f}  (exact {_fmt_frac(result['equal'])})\n")
    out.write(f"P(A < B)  {float(result['less']):.4f}  (exact {_fmt_frac(result['less'])})\n")
    return 0


def cmd_check(args, out):
    try:
        with open(args.file, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        raise ExprError(f"cannot read {args.file}: {exc.strerror}") from exc
    results = lint_lines(lines)
    if not results:
        out.write(f"{args.file}: no expressions found\n")
        return 1
    errors = 0
    for r in results:
        label = f"{r['name']}: " if r["name"] else ""
        if r["error"]:
            errors += 1
            out.write(f"{args.file}:{r['line']}:{r['column'] or 1}: error: {r['error']}\n")
        else:
            out.write(f"{args.file}:{r['line']}: ok  {label}{r['expression']}  mean {r['mean']:.2f}\n")
    out.write(f"{len(results)} expressions, {errors} error(s)\n")
    return 1 if errors else 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="dicewright",
        description="Exact dice odds, seeded rolls and comparisons, fully offline.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    odds = sub.add_parser("odds", help="exact distribution, statistics and probability tests")
    odds.add_argument("expression", help="e.g. 4d6kh3, 2d20kl1, 5d10>=7, d6!, 2d8+3")
    odds.add_argument("--test", metavar="OP N", help='probability test such as ">=15"')
    odds.add_argument("--histogram", action="store_true", help="ASCII histogram (text output)")
    odds.add_argument("--format", choices=["text", "json", "csv"], default="text")
    odds.set_defaults(func=cmd_odds)

    roll = sub.add_parser("roll", help="roll an expression with an optional seed")
    roll.add_argument("expression")
    roll.add_argument("--count", type=int, default=1, help="number of rolls")
    roll.add_argument("--seed", type=int, help="seed for reproducible rolls")
    roll.set_defaults(func=cmd_roll)

    sim = sub.add_parser("simulate", help="Monte Carlo check against the exact odds")
    sim.add_argument("expression")
    sim.add_argument("--trials", type=int, default=10_000)
    sim.add_argument("--seed", type=int, default=1)
    sim.set_defaults(func=cmd_simulate)

    cmp_ = sub.add_parser("compare", help="P(A > B), P(A = B), P(A < B) for two expressions")
    cmp_.add_argument("a")
    cmp_.add_argument("b")
    cmp_.set_defaults(func=cmd_compare)

    check = sub.add_parser("check", help="lint a plain-text file of expressions")
    check.add_argument("file", help="one expression per line, optionally 'name: expression'")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv=None, out=None):
    out = out or sys.stdout
    args = build_parser().parse_args(argv)
    try:
        return args.func(args, out)
    except ExprError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
