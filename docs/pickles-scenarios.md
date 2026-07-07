# Writing Pickles Scenarios

## Black-box thinking

Pickles models the system as a black box: it only cares about its interfaces with an externar user, and not about its internal implementation. In particular: 

- **Inputs** are actions excecuted on the system by an external actor (a user, another component, an external sensor).
- **Outputs** are things the system reveals about itself (a displayed status, a response, a reported value).

In this sense, a good first exercise before writing scenarios is defining the bounds of the system under test. The granularity is not a restriction; ee may choose to test a single module of a larger piece of software, or a huge system with both software and physical actuators, as long as we have clear interfaces defined.

## Writing specifications with Pickles

Pickles scenarios follow the Given/When/Then structure common to Behavior-Driven Development:

- **Given** — the state the system starts in.
- **When** — the single action or event being tested.
- **Then** — the observable result.

A `.pickles` file has two parts: a **Variable Settings** block declaring the system's variables, followed by one or more **Scenario** blocks describing behavior.

```
Variable Settings
"water level" is an integer with range [0,100]
...

Scenario 01: brewing starts when sufficient resources are available
Given ...
When ...
Then ...
```

### Declaring variables

Every variable is declared once, with a name and a type:

```
"<name>" is a <type> with range <range>
```
**Ranges:**
- For numbers, giving exactly two values declares an inclusive range, e.g. `[0,100]` allows 0 to 100.
- Giving more than two values, or any values for a `string`, declares the exact set of allowed values, e.g. `{READY, BREWING, ERROR}`.

#### Primitive datatypes

| Type | Example |
|---|---|
| `boolean` | `"brew request" is a boolean with range {true, false}` |
| `string` | `"machine status" is a string with range {READY, BREWING, ERROR}` |
| `integer` | `"water level" is an integer with range [0,100]` |
| `decimal` | `"critical section start" is a decimal with range (1.0,3.0)` |


#### Arrays
A list of up to/exactly/between N elements, each of a given type:

```
"current measuments" is an array of at most 5 elements where each element is an integer with range [0,10]
```

Cardinality can be `at most N`, `exactly N`, or `between N and M`. Elements can be a primitive type, another array or a structure.

#### Structures
A map of key values where each value can be a primitive type, array or another structure.

```
"faulty detector" is a structure with attributes "lane", "lenght position" such that:
  "lane" is an integer with range [1,3]
  "length position" is a decimal with range (1.0,3.0)
```

### Writing a scenario

Once variables are defined, the basic structure for writing a scenario is:
```
Scenario <id>: <short description>
Given <initial guard>
And <another initial guard>
When <the action being tested> such that:
    <action guard>
And <another action executed sequentially> such that:
    <action guard>
Then <the observable result> such that:
    <action guard>
And <another result observed afterwards> such that:
    <action guard>
```

- `Given`, `When`, `Then` each start a step; `And` continues the previous one.
- A scenario needs at least a `When` and a `Then`. `Given` is optional.
- Start a scenario with `Given the system is in its initial state` when it doesn't depend on any prior state, i.e., can be executed as soon as the system under test is initialized.
- The description after `Scenario <id>:` is free text — use it to say what's being tested.
- Guards are optional in all cases.

### Guards on a step

To constrain a step to specific variable values, add `such that:` followed by one condition per line, joined with `AND` / `OR`:

```
When the user issues a "brew request" such that:
    "brew request" is equal to true
And the machine has a current "water level", "coffee beans level" such that:
    "water level" is greater than 20 AND
    "coffee beans level" is greater than 10
```

If a step only checks a single variable, you can skip `such that:` and write the condition inline:

```
Then the user interface displays "machine status" equal to 'BREWING'
```

**Comparison operators:** `is equal to`, `is not equal to`, `is greater than`, `is lower than`, `is greater or equal than`, `is lower or equal than`, `is between X and Y`.

Values are numbers, `true`/`false`, `'quoted strings'`, or another variable's name in quotes (to compare two variables).

**Arrays** — constrain how many elements match a condition:

```
"current measurements" has at least 2 elements where such that each element is equal to 2
```

Quantifiers: `has at least N`, `has at most N`, `has exactly N`, or `has all elements where each element ...` (every element must match).

**Structs** — for a struct-typed variable with several attributes, condition each attribute with `has attributes such that:`:

```
"faulty detectors" has exactly 1 elements where each element has attributes such that:
    "lane" is equal to "critical section lane" AND
    "length position" is greater than "critical section start"
```

### Good practices when writing scenarios

A few practices that keep specs easy to model and to read:

- One behavior per scenario. If a scenario needs more than one when/then set, it's probably two scenarios. Remember that composition will be done automatically!
- Describe *what* happens, not *how* it's implemented; specs describe the system's interface, not its code.
- Give every scenario a short, descriptive title (what is being tested, not which steps it runs).
- Reuse the same variable names and keywords across scenarios; the tool matches steps by wording, so consistency matters. In many cases, a handful of keywords can be replaced by a single, parameterized one. This will mean easier automation of test execution as well.