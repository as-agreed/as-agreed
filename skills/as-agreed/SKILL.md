---
name: as-agreed
description: >
  Use this skill when a person gives you an order with must terms. For example, the order sets a limit or a date.
  Use it before your tool call does the work, for example a payment.
  The skill records what was agreed. Then the As-Agreed hook checks each step against that record.
---

# As-Agreed: record what was agreed, then let the hook check each step

The hook checks a tool call only when the folder where Claude Code starts has `.as-agreed/binding.json`. You write the record and the binding. The person agrees to them before the hook uses them.

## 1. Write the record: `.as-agreed/record.json`

```json
{"order": "<the order, as the person gave it>",
 "commitments": [{"commitment_id": "pay-invoice", "act": "pay the October invoice from Northwind Freight",
                  "obligor": "finance-ops", "obligee": "northwind-freight", "modality": "MUST",
                  "limit": {"amount": {"max": 18400, "unit": "USD"}},
                  "said": {"act": "Pay the October invoice from Northwind Freight",
                           "obligee": "Northwind Freight", "limit.amount": "up to 18,400 dollars"},
                  "carried_by": {"limit.amount": "amount"}}]}
```

- Write one commitment for each item that must occur. A commitment must have `act`, `obligor`, `obligee` and `modality`.
- Write each number as a limit with a direction: `max`, `min`, `until`, `from`, `in` or `not_in`.
- Write a date as `YYYY-MM-DD`.
- In `said`, give each term the person's words for it. Copy the words exactly from the order. Give no words to a term that the person did not say.
- In `carried_by`, give a key only to a value that the call uses, for example the amount that it pays. Do not copy the words of the order into the output: a copied value counts as kept and checks nothing.
- Use only what the person gives. If a value is not clear, stop and tell the person. Do not add a value.
- Do not write `confirmed_by`, `confirmed_at` or a hash. The person's confirm writes them.

## 2. Write the binding: `.as-agreed/binding.json`

```json
{"steps": [{"tool": "Write", "path": "*/outbox/payment.json", "json": "content",
            "binding": {"serves": ["pay-invoice"], "kind": "takes_effect", "author": "finance-ops"}}]}
```

- `tool` is the name of the tool. For a file tool, `path` gives the file, and `json` gives the input that has the output.
- Set `kind` to `takes_effect` for the call that does the work.
- Set `kind` to `sets_terms` for a call that gives the task to a different agent.
- Set `kind` to `mentions` for a call that must have no commitment.
- `serves` gives the commitments that this step must have. `author` is the person or agent who writes the step.

## 3. Show the record to the person, then let the person confirm it

Run `as-agreed show`. It prints each term with its value and the person's words. It marks three things. A quote is not in the order. A value differs from its words. A number or a date has no words.

Tell the person each term and each mark. Tell the person which tool call the hook checks. Correct a mark only with the person's words.

Then ask the person to run `as-agreed confirm` in their own terminal. Do not run it yourself: the hook stops that call. The command reads the name from the terminal. It writes `confirmed_by`, `confirmed_at` and two hashes. After that, the hook stops each file tool that changes the record or the binding. While either file differs from its hash, the hook stops every call until the person confirms again.

## 4. When the hook stops a call

The reason gives the field, the agreed value, and the value in your call. Correct the output, and do the call again. Do not change the record or the binding to let a call pass. If you cannot do the work as agreed, stop and tell the person.

## Its limits

The hook compares only the keys that you declare. It does not read text. It checks only the calls in the binding. It sees a change that a file tool does not make only through the two hashes. It misses a program that writes the hashes again, or removes both.
