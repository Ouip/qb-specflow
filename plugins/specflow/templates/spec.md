# [FILL: ID] [FILL: component name]

> Status: Not started. Based on: [FILL: decision IDs]

## 1 Purpose

[FILL: one paragraph: what this component is responsible for, and what it is not]

## 2 Dependencies

| Dependency | Types only / calls | Contract version |
|---|---|---|
| [FILL] | [FILL] | [FILL] |

Types only: work can start once the other side's contract exists. Calls: the other side must be done first.

## 3 Interface

### 3.1 `[FILL: contract path]` (contract v1, designed before work starts)

<!-- header: [FILL: contract path] -->
```c
/* [FILL: the full contract. After the user approves, mark it "frozen v1"] */
```

## 4 Behavior rules

### 4.1 Rules

| # | Rule | Source | Verify |
|---|---|---|---|
| 1 | [FILL: one rule; for numbers, strings and lengths, say what happens at max, min, negative and empty] | [FILL: source marker; `(new)` if none] | [FILL: `5.1 G1` / `I01` / `5.5` / `untested: <reason>`] |

Rules written as a numbered list end with a Verify marker:

1. [FILL: rule] [verify: 5.1 G1]

- IDs are stable; a deleted ID is never reused. Cite as `<ID> 4.1#<n>`.
- Verify values: `5.1 G<n>` (a group in 5.1), a scenario ID (e.g. `I01`), `5.5` (manual), `untested: <reason>`. `coverage.py` must return 0.
- A value that holds for only one hardware variant names that variant. A variant not decided yet fails the build instead of getting a default: `#error "<value> for <variant> not decided"`.
- Claims about drivers, libraries or hardware cite source `file:line` or a datasheet page; if none can be found, write "pending hardware" and add it to 5.5.
- State machines list the "B happens while A is in progress" combinations.

## 5 Tests

### 5.1 Unit

One heading per group; each table row is "input → expected", never "test the XX feature".

#### G1 [FILL: group name]

| Input | Expected |
|---|---|
| [FILL] | [FILL] |

### 5.2 Integration

[FILL: scenario IDs only; their contents live in the scenario catalog; "none" with the reason if none]

### 5.4 End-to-end

[FILL: scenario IDs only; "none" with the reason if none]

### 5.5 Manual

| Step | Expected | If not, check first |
|---|---|---|
| [FILL] | [FILL] | [FILL] |

## 6 Done when

Each line is one command and its result; sentences like "works correctly" belong in 5.5.

- [FILL: e.g. `<unit test command> <component>` returns 0]
- The product's consistency lint returns 0; `coverage.py <this spec>` returns 0.

## 7 Prohibited

Each line closes one degree of freedom that can really cause harm.

- Do not change the contract or frozen tests.
- [FILL]
- Paths you may change: [FILL]
