---
name: as-agreed
description: >
  Use this skill when a person gives you an order with must terms, for example a limit or a date.
  Use it before your tool call does the work, for example a payment.
  The skill records what was agreed. Then the As-Agreed hook checks each step against that record.
---

# As-Agreed: record what was agreed, then let the hook check each step

The hook checks a tool call only when the folder of the work has `.as-agreed/binding.json`. You write the record and the binding. The person agrees to them before the hook uses them.

## 1. Write the record: `.as-agreed/record.json`

```json
{"order": "<the order, as the person gave it>",
 "commitments": [{"commitment_id": "pay-invoice", "act": "pay the October invoice",
                  "obligor": "finance-ops", "obligee": "northwind-freight", "modality": "MUST",
                  "limit": {"amount": {"max": 18400, "unit": "USD"}},
                  "carried_by": {"act": "act", "limit.amount": "amount"}}],
 "confirmed_by": "", "confirmed_at": ""}
```

- Write one commitment for each item that must occur. A commitment must have `act`, `obligor`, `obligee` and `modality`.
- Write each number as a limit with a direction: `max`, `min`, `until`, `from`, `in` or `not_in`.
- Write a date as `YYYY-MM-DD`.
- In `carried_by`, give the key of the output that has each field. As-Agreed does not check a field with no key.
- Use only what the person gives. If a value is not clear, stop and tell the person. Do not add a value.

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

## 3. Show the record and the binding to the person

Tell the person each commitment, its value, and its key. Tell the person which tool call the hook checks. Write `confirmed_by` and `confirmed_at` only after the person agrees. After that, the hook stops each file tool that changes the record or the binding. Only the person can then agree to a change.

## 4. When the hook stops a call

The reason gives the field, the agreed value, and the value in your call. Correct the output, and do the call again. Do not change the record or the binding to let a call pass. If you cannot do the work as agreed, stop and tell the person.

## Its limits

The hook compares only the keys that you declare. It does not read text. It checks only the calls in the binding. It does not stop a change to the record or the binding that a file tool does not make.
