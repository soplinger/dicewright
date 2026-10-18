# 🎲 Dicewright

**Exact dice odds, seeded rolls and head-to-head comparisons for tabletop expressions, fully offline.**

Dicewright reads expressions like `4d6kh3`, `2d20kl1`, `5d10>=7` or `d6!` and tells you the precise probability of every result: the mean, the spread, percentiles, the chance of hitting a target, and who wins between two rolls. Every probability is an exact fraction, not a rounded guess. It is a single Python file with no dependencies, and it never touches the network.

## Features

- **Expression parser** with case-insensitive, space-tolerant input (`  3D6 + 2 `) and an implicit count (`d20`).
- **Strict errors**: every character is either understood or reported with its column, so `3d6x` fails with `column 4` rather than being silently dropped.
- **Exact distributions** computed with `fractions.Fraction`, enumerated by multiset so 4d6 and friends are instant.
- **Keep highest / keep lowest** (`4d6kh3` for stat rolls, `2d20kl1` for disadvantage), with validation that the keep count fits the dice.
- **Success counting** with `=`, `==`, `<`, `<=`, `>`, `>=` (e.g. `5d10>=7`).
- **Exploding dice** (`d6!`) with a fixed cap of six extra rolls, so the distribution always sums to exactly 1.
- **Statistics**: exact mean and variance, standard deviation, minimum and maximum.
- **Percentiles** at p10, p25, p50, p75 and p90.
- **Probability tests** such as `--test ">=15"`, reported as both a decimal and an exact fraction.
- **Head-to-head comparison**: exact P(A > B), P(A = B) and P(A < B) for two expressions.
- **Seeded rolling** with `--seed` for reproducible results, and `--count` for multiple rolls.
- **Monte Carlo check** (`simulate`) that compares simulated frequencies against the exact odds, so you can see the engine agrees with reality.
- **Output formats**: readable text, JSON (with exact fractions as strings), or CSV for a spreadsheet.
- **ASCII histogram** of the distribution.
- **Plain-text file linting** (`check`): one expression per line, optional `name:` labels, `#` comments, and `file:line:column` error messages with exit codes for CI.

## Quick start

```console
$ python3 dicewright.py odds 2d6 --test ">=10"
Expression   2d6
Mean         7.0000
Std dev      2.4152
Range        2 .. 12
Percentiles  p10 4  p25 5  p50 7  p75 9  p90 10
P(result >= 10)  0.1667  (exact 1/6)
```

Compare two attacks, rolling each one fresh:

```console
$ python3 dicewright.py compare d6 d6
A  d6   mean 3.5000
B  d6   mean 3.5000
P(A > B)  0.4167  (exact 5/12)
P(A = B)  0.1667  (exact 1/6)
P(A < B)  0.4167  (exact 5/12)
```

Lint a file of named rolls, with a line-numbered error:

```console
$ python3 dicewright.py check examples/attacks.dice
```

`examples/attacks.dice` contains:

```text
# Fighter attack rolls
longsword: 1d20+5
greatsword: 2d6+3
sneak: 1d6!
advantage: 2d20kh1+5
stat: 4d6kh3
```

## Expression syntax

| Form | Meaning | Example |
| --- | --- | --- |
| `NdS` | Sum of N dice with S sides | `3d6` |
| `dS` | One die (implicit count) | `d20` |
| `NdSkhK` / `NdSklK` | Keep the K highest / lowest dice | `4d6kh3`, `2d20kl1` |
| `NdS!` | Exploding: a top face rolls again (max 6 extra) | `d6!` |
| `NdS>=T` | Count dice meeting the test (`=`, `<`, `<=`, `>`, `>=`) | `5d10>=7` |
| `+M` / `-M` | Modifier added to the result | `2d8+3` |

Limits: up to 100 dice and 1000 sides for plain and keep rolls, with at most 200,000 enumerated outcomes. Exploding dice are limited to 10 dice with up to 20 sides. Keep and success tests cannot be combined with each other or with `!`, and each combination is rejected with a clear message.

## Commands

```text
dicewright odds EXPR [--test OP N] [--histogram] [--format text|json|csv]
dicewright roll EXPR [--count N] [--seed S]
dicewright simulate EXPR [--trials N] [--seed S]
dicewright compare EXPR_A EXPR_B
dicewright check FILE
```

Exit codes: `0` on success, `2` for an invalid expression or option, and `1` from `check` when any line has an error.

## How to run

Dicewright needs Python 3.8 or newer and nothing else.

```console
$ python3 dicewright.py odds 4d6kh3 --histogram
$ python3 dicewright.py roll 2d20kl1 --seed 42 --count 3
$ python3 dicewright.py odds d20 --format json
```

## How to test

The tests use the standard `unittest` module, so they run with or without pytest.

```console
$ python3 -m unittest discover -s tests
$ python3 -m pytest -q
```

The test suite covers the parser (valid forms, rejected forms and column numbers), exact distributions checked against hand-computed values, percentiles, comparisons, the exploding-die sum, seeded reproducibility, simulation convergence, file linting with line numbers, and the command-line exit codes.

## Project layout

```text
dicewright.py          the whole program: parser, exact engine, rolls, CLI
examples/attacks.dice  sample expression file for `check`
tests/test_dicewright.py
README.md
```

## Notes on accuracy

- Exact odds assume fair dice and independent rolls.
- Exploding dice stop after six extra rolls. The last face counts as-is, so the probabilities still total exactly one, and the cap is part of the answer.
- `simulate` is a sanity check on the engine, not a replacement for the exact odds.
