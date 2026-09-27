# Writing Pickles Scenarios


## Black-box thinking

Pickles models the system as a black box: it only cares about its interfaces with an external user, and not about its internal implementation. In particular:

- **Inputs** are actions executed on the system by an external actor (a user, another component, an external sensor).
- **Outputs** are things the system reveals about itself (a displayed status, a response, a reported value).

In this sense, a good first exercise before writing scenarios is defining the bounds of the system under test. The granularity is not a restriction; we may choose to test a single module of a larger piece of software, or a huge system with both software and physical actuators, as long as we have clear interfaces defined.

## Writing specifications with Pickles

Pickles scenarios use the Given/When/Then structure of Behavior-Driven Development:

- **Given** — the state the system starts in.
- **When** — the single action or event being tested.
- **Then** — the observable result.

A `.pickles` file has two parts:
1. One **Variable Settings** block. It declares the variables, their types, domains and, optionally, their initial valuations.
2. One or more **Scenario** blocks. They describe the behavior of the system.

For example:

```
Variable Settings
"state" is a string with range {IDLE, BREWING, DONE, OUT OF STOCK} and initial value 'IDLE'
"beans level" is an integer with range [0,12] and initial value 12
"max beans level" is the integer 12

Scenario 03: ordering is rejected when there are no beans
Documentation: there should be beans to brew coffee
Given the machine has a current "beans level" equal to 0
When the user clicks Brew
Then the screen shows an error banner indicating no beans
And the screen shows "state" equal to 'OUT OF STOCK'  # no brewing after this

Scenario 05: the user refills coffee beans
Documentation: Refills the beans level to its maximum.
When the user clicks on beans refill
Then the machine has a current "beans level" equal to "max beans level"
```

Note that the specification admits optional Documentation blocks right after the Scenario title, and inline comments introduced with `#`. A full spec example, in this case for a coffee machine, can be found in `system-tests/resources/coffee_machine_specification.pickles`.

### From scenario to model

The `sts` command makes one STS (Symbolic Transition System) from each scenario. An STS is an automata, so you can read a scenario as a path through it:
- A **location** is a state of the model, and a **switch** is a transition from one location to the next.
- Each switch has a **gate**, which is its action. `When` steps give input gates (performed by an external actor), while `Then` steps give output gates (performed by the SUT).
- An STS has different types of **variables**. **Location variables** that are global to the STS, and **gate parameters** are local to switches. In the Pickles language, there is no distinction between them: every time a variable is declared, internally it generates both a location variable and a parameter.
- **Guards** are the conditions that must hold to take a switch. These can be both over parameters or location variables. In the Pickles language, guards are usually introduced by `such that`.
- **Assignments** change location variable values when the switch is taken. Internally, after a switch that has some parameter, the value of the parameter is assigned to the corresponding location variable.

### Declaring variables

Declare each variable one time, with a name and a type:

```
"<name>" is a <type> with range <range>
```

The range is optional, so a variable without one can have any value, e.g. `"available" is a boolean`. However, we do not recommend this.

**Ranges** use three types of brackets, and each one has a different meaning:
- `(lo,hi)` is an **exclusive** range: the value is more than `lo` and less than `hi`.
- `[lo,hi]` is an **inclusive** range: the value can also be `lo` or `hi`.
- `{v1, v2, ...}` is a **set** of allowed values. For example, `{IDLE, BREWING, DONE}` allows only these three strings, and `{1, 10}` allows only `1` and `10`, not the numbers between them.

The first two need exactly two values, but a set can have any number of values.

**Initial values**: add `and initial value <value>` to set the value of a variable when the system starts:

```
"water level" is an integer with range [0,210] and initial value 210
"extras" is an array of at most 3 unique elements where each element is a string with range {Milk, Sugar, Cream} and initial value {Milk}
```

If the variable has no range, write `with initial value <value>` instead:

```
"available" is a boolean with initial value true
```

The initial value must fit the declaration. That is, it must be in range, respect the cardinality of an array, and have no duplicates if the array is `unique`; otherwise, the spec has an error. A variable without an initial value can start with any value.

#### Primitive datatypes

| Type | Example |
|---|---|
| `boolean` | `"available" is a boolean` |
| `string` | `"state" is a string with range {IDLE, BREWING, DONE}` |
| `integer` | `"water level" is an integer with range [0,210]` |
| `float` | `"temperature" is a float with range (80.0,95.0)` |

#### Arrays

An array is a list of elements of the same type:

```
"recent orders" is an array of at most 5 elements where each element is an integer with range [1,99]
```

The cardinality can be `at most N`, `exactly N` or `between N and M`. An element can be a primitive type, another array or a structure.

##### Unique arrays

Add `unique` before `elements` when the array must not have duplicate values:

```
"extras" is an array of at most 3 unique elements where each element is a string with range {Milk, Sugar, Cream}
```

To make sure of this, Pickles adds a `uniqueElem` guard to each switch that uses the variable. You must still declare the cardinality, and it must not be more than the number of allowed values. In the example above, the maximum is 3 because the range has only three values.

`unique` also works when the elements are structures:

```
"order queue" is an array of at most 4 unique elements where each element is a structure with attributes "type", "size" such that:
    "type" is a string with range {Espresso, Americano}
    "size" is a string with range {Small, Large}
```

#### Constants

A constant is a variable that always has the same value. This is an alternative to having harcoded values in the specification; this way, we ensure consisteny and explainability. To declare a constant, use **the** instead of **a**/**an**:

```
"max water level" is the integer 210
"default size" is the string 'Small'
"available drinks" is the array of unique strings {Espresso, Americano}
```

You can use a constant in a guard, as the subject or as the value. However, you cannot list it as a step parameter:

```
Then the machine has a current "water level" such that:
    "water level" is lower or equal than "max water level"
And the screen shows some "drink type" in "available drinks"
```

#### Structures

A structure is a map of named attributes. Each attribute can be a primitive type, an array or another structure:

```
"drink" is a structure with attributes "type", "size" such that:
    "type" is a string with range {Espresso, Americano}
    "size" is a string with range {Small, Large}
```

### Writing a scenario

When the variables are declared, you can write the scenarios. The basic structure is:

```
Scenario <id>: <short description>
Given <initial guard>
And <another initial guard>
When <the action to test> such that:
    <action guard>
And <next action> such that:
    <action guard>
Then <the result> such that:
    <action guard>
And <next result> such that:
    <action guard>
```

- `Given`, `When` and `Then` start a step, and `And` continues the previous one.
- A scenario must have a `When` and a `Then`, but `Given` is optional.
- If the scenario can start as soon as the system starts, use `Given the system is in its initial state`.
- The text after `Scenario <id>:` is free text, so use it to say what you test.
- Guards are always optional. For example, `Then the screen shows an error banner indicating no beans` is a valid step without variables.

### Guards on a step

To set conditions on the variables of a step, add `such that:` and write one condition on each line. Join the conditions with `AND` / `OR`:

```
When the user selects a "drink" such that:
    "drink" has attributes such that:
        "type" is equal to 'Americano' AND
        "size" is equal to 'Small'
And the machine has a current "water level", "beans level" such that:
    "water level" is greater or equal than 105 AND
    "beans level" is greater or equal than 6
```

The variables before `such that:` are the parameters of the step, which means that the step sends or observes their values.

If the step has only one variable and one condition, you can write it on the same line. In this case, do not write `is` before the operator:

```
Then the screen shows "state" equal to 'BREWING'
```

**Comparison operators:** `is equal to`, `is not equal to`, `is greater than`, `is lower than`, `is greater or equal than`, `is lower or equal than` and `is between X and Y`:

```
Then the machine has a current "water level" such that:
    "water level" is between 105 and "max water level"
```

A value can be a number, `true`/`false`, a `'quoted string'` or the name of another variable in double quotes, when you want to compare two variables.

**Arithmetic and `stored`**: a value can also use `+`, `-`, `*`, `/` and parentheses. 

```
Then the machine has a current "water level" such that:
    "water level" is equal to "max water level" - 105
```

**Stored**: `stored "x"` refers to the value that `"x"` had before the step, so you can describe how a value changes:
```
Then the machine has a current "water level" such that:
    "water level" is equal to stored "water level" - 105
```
On the STS level, with `stored 'x'` we refer to the location variable `x`, and not the parameter `x`.

**Membership**: `is in` / `is not in` check if a value is in an array. The array can be a constant or a regular variable, and you can also write the short form `in` / `not in`:

```
Then the screen shows "drink type" is in "available drinks"
And the screen shows "drink type" is not in "sold out drinks"
```

**Booleans** have a short form, where you write only the variable name:

```
Then the machine is "available"
And the machine is not "available"
```

`"available"` alone means `"available" is equal to true`. However, if the word just before the quote is a negation word (`not` / `no` / `niet`), it means `is equal to false`. A negation word in other positions of the step text has no effect.

**Arrays**: a quantifier sets how many elements must match a condition:

```
"recent orders" has at least 2 elements where each element is equal to 7
```

The quantifiers are `has at least N`, `has at most N`, `has exactly N` and `has all elements where each element ...`, where all elements must match.

To check if an array has a value, use `contains` / `does not contain`:

```
Then the screen shows "extras" contains 'Milk'
And the screen shows "extras" does not contain 'Cream'
```

`"X" contains Y` is a short form of `"X" has at least 1 elements where each element is equal to Y`. There are more checks of this type:

```
Then the screen shows "extras" is empty
And the screen shows "extras" is not empty
And the screen shows "extras" contains only 'Milk'
And the screen shows "extras" contains all possible elements
```

- `"X" is empty` means that `X` has no elements, and `"X" is not empty` means that it has one or more.
- `"X" contains only Y` means that `X` has exactly one element, and this element is `Y`.
- `"X" contains all possible elements` means that `X` has each value of its range. For this reason, the element type must have a range.

**Length**: to compare the length of an array, use `has length` with the usual comparison operators, `between` included:

```
Then the screen shows "extras" such that:
    "extras" has length greater than 1
And the machine reports "recent orders" such that:
    "recent orders" has length between 1 and 3
```

**Array to array**: to compare two arrays, use `is equal to` / `is not equal to`, as with two primitive variables.

**Structures**: use `has attributes such that:` to set conditions on the attributes of a structure:

```
"order queue" has exactly 1 elements where each element has attributes such that:
    "type" is equal to 'Espresso' AND
    "size" is equal to "default size"
```

A boolean attribute also has a short form, as with boolean variables:

```
Then the screen shows the "drink" is "decaf"
And the screen shows the "drink" is not "decaf"
```

### Good practices when writing scenarios

A few practices that keep specs easy to model and to read:

- One behavior per scenario. If a scenario needs more than one when/then set, it's probably two scenarios. Remember that composition will be done automatically!
- Describe *what* happens, not *how* it's implemented; specs describe the system's interface, not its code.
- Give every scenario a short, descriptive title (what is being tested, not which steps it runs). If you want to add more information, you can use the Documentation block, although the scenario should be descriptive in itself.
- Reuse the same variable names and keywords across scenarios; the tool matches steps by wording, so consistency matters. In many cases, a handful of keywords can be replaced by a single, parameterized one. This will mean easier automation of test execution as well.