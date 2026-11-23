# Set up As-Agreed for your agent

This page has five steps. They start with the install and end on the Board.

You must have Python 3.9 or higher and Claude Code. You do not use an account. The band and the Board need version 0.1.1 or later of the plugin, and Claude Code 2.1.290 or later.

## 1. Install the library and see one hold

```
python3 -m pip install as-agreed
python3 -m as_agreed demo --no-retry
```

If `pip` says that the environment is externally managed, make a virtual environment first:

```
python3 -m venv .venv
. .venv/bin/activate
```

The demo uses an invented order: "up to 18,400 dollars". The step gives 19,800. One line starts with `gate: HOLD`: the step stops, and the demo does not call the payment system.

## 2. Add the plugin to Claude Code

In Claude Code, type:

```
/plugin marketplace add as-agreed/as-agreed
/plugin install as-agreed@as-agreed
```

Then start Claude Code again in your project folder. The hook checks no call in a project that has no `.as-agreed` files.

## 3. Give your task to your agent

Copy the prompt in [The prompt](#the-prompt) below. Write your task in it, in your own words, and paste it. Your agent then does five things:

- It breaks your task into numbered terms, in your words. For each term, it tells you how code can check it, or that only you can.
- It writes `.as-agreed/record.json`: one commitment for each term, with your words beside it.
- It writes `.as-agreed/binding.json`: the call that makes the task take effect.
- It checks the two files with the library, and corrects them until the check passes.
- It tests that the hook is on: the hook must stop a test write until you confirm the record.

Then it shows you a table: each term, your words, the value, how it is checked, and the output key.

## 4. Confirm the record, then let the agent work

If the table is correct, confirm the record. Only you confirm it. The hook stops each bound call until you do this.

- If your plugin has the `confirm` command, run the line that your agent gives you, in your own terminal. It starts with `python3 -m as_agreed confirm`. It shows the terms and asks for your name. Then it writes your name, the time and two hashes in the record.
- If your plugin has no `confirm` command, open `.as-agreed/record.json` and write your name in `confirmed_by`.

The hook stops an agent that runs `confirm`, and an agent's file tool (Edit or Write) that writes `confirmed_by`. It does not read shell commands. If you find a name there that you did not write, stop. With `confirm`, a name without the two hashes was not written by `confirm`.

Then tell the agent to continue. Before the bound call runs, the hook compares it with the record. If a term changed or is missing, the hook stops the call and gives the reason. The agent corrects the output and does the call again.

Above the prompt, the band shows the flag line: kept as agreed, held, and unchecked.

## 5. Open the Board

Click Board on the band. The button makes a new page from the records of the task. The page lists each term: what was agreed, what arrived, and the result. The page loads nothing and sends nothing.

Then your agent gives you one row for each term: kept, held, unchecked, or not in the record. For each term that only you can check, it tells you what to read.

## How to read the result

- Kept: the value in the call agrees with the record. It does not tell you that the work is good.
- Held: the hook stopped the call, and gave the reason.
- Unchecked: code could not decide. Read that term yourself.
- Not checked by code (0.1.2): fields that no step declares, for example the act. Read them yourself.
- The band counts the values of the terms, not the terms. A term with two limits counts two times.
- If the call was stopped, its kept rows show what the call carried. No file was written.
- A term "by a script" is as correct as its script. Run the command yourself, and compare the hash.

## What each version gives you

| | 0.1.0 | 0.1.1 | 0.1.2 |
|---|---|---|---|
| Steps 1 to 4 | yes | yes | yes |
| The stop, with its reason | yes | yes | yes |
| The flag line | in the message of the hook | in the band, above the prompt | in the band, above the prompt |
| What the flag line counts | each field of a binding term | each field of a binding term | only the fields that code can check; the other fields are on a line "Not checked by code" |
| The Board | no | yes: one page for each task, on your machine | yes |
| What the hook writes | `.as-agreed/hook.jsonl`: each decision, with no values | also `.as-agreed/runs/<session>/`: `ledger.jsonl`, `band.json` and `board.html`, with the values | as 0.1.1 |

`python3 -m as_agreed --version` shows the version of the library. The installs in steps 1 and 2 give you the band and the Board.

## Its limits

The hook compares only the keys that you declare. It does not read text. It checks only the calls that the binding names. It does not read shell commands, and an agent with a shell can change the files in `.as-agreed/`. After `confirm`, such a change stops every call until you confirm again. What code cannot decide does not stop the call: the Board marks it unchecked. The band counts every checked call of a session, so use one session for each task. A task over more than one session has one Board for each session. A script is not frozen: only its hash in the record is. For work that is not JSON, for example code, the hook checks the summary of your agent, not the work. Read the work yourself. The files in `.as-agreed/runs/` stay on your machine until you delete them. The commands in the prompt are for macOS and Linux.

To see the band and the Board on an invented example first, read `docs/FIRST-RUN.md`.

## The prompt

```
Do my task below with As-Agreed. As-Agreed is an open library and a Claude Code plugin.
Its hook checks the call that makes my task take effect, against a record of what I agreed.
It stops that call when a term that I agreed is missing or changed.
Do these steps in this sequence. Do not skip a step.

MY TASK:
<write your task here, in your own words>

1. Install the plugin.
- Run `claude plugin list --json`. Look for an entry whose `id` starts with `as-agreed@` and whose `enabled` is true.
  Keep its `version` and its `installPath`.
- Run `PYTHONPATH=<installPath>/src python3 -m as_agreed show --help`.
  If it prints a usage line, the plugin has `show` and `confirm`. Steps 3, 5 and 6 say what changes.
- If there is none, run `claude plugin marketplace add as-agreed/as-agreed`, then `claude plugin install as-agreed@as-agreed`.
- If you installed or enabled the plugin now, stop. Tell me to start Claude Code again in this folder.
  Then I paste this prompt again.
- If `confirmed_by` in `.as-agreed/record.json` has a name from an earlier task, stop.
  Tell me to clear it by hand and start a new session.

2. Break my task into terms. A term is one item that must be true when the task is done.
- Number the terms. Write each term in my words, exactly. Use only what I wrote.
- Give each term one way to check it:
  - "at the call": a value that the call that does the work uses, for example an amount, a date or a recipient;
  - "by a script": a count or a value that a script reads from the work itself, not from the input of my task;
  - "by you": a decision that only I can make, for example about quality or fit. Code cannot check it.
- If a term has no clear value, ask me. Do not add a value.
- If my task changes code, tell me now: the hook checks the summary that you write, not the code.
  I read the diff myself.

3. Write the record, in `.as-agreed/record.json`, in this shape. One commitment for each term:
  {"order": "<my task, word for word>",
   "commitments": [
    {"commitment_id": "t1-<short name>", "act": "<the term, in my words>",
     "obligor": "agent", "obligee": "me", "modality": "MUST",
     "limit": {"<value name>": {"max": 160, "unit": "EUR"}},
     "carried_by": {"limit.<value name>": "<output key>"},
     "source": {"artifact": "order", "words": "<my words, exactly>"}},
    {"commitment_id": "t2-<short name>", "act": "<the term, in my words>",
     "obligor": "agent", "obligee": "me", "modality": "MUST",
     "acceptance_criteria": [{"text": "<my words>", "verification": "human_judged"}],
     "source": {"artifact": "order", "words": "<my words, exactly>"}}],
   "confirmed_by": "", "confirmed_at": ""}
- The first commitment is a term "at the call" or "by a script". The second is a term "by you".
- A limit has one direction: `max`, `min`, `until`, `from`, `in` or `not_in`. Write a date as `YYYY-MM-DD`.
- Use `MUST` or `MUST_NOT` when I said that it must occur. Use `SHOULD` when I said that I prefer it.
  The band does not count a `SHOULD` term.
- If the plugin has `confirm`, give my words in `said`, not in `source`. One entry for each field that I said:
  "said": {"act": "<my words>", "limit.<value name>": "<my words>"}. Give no words to a field that I did not say.
- For a term "by a script", write the script now, and add to `source`: "script": "<path>", "sha256": "<hash>".
  Get the hash with `shasum -a 256 <path>`.

4. Write the binding, in `.as-agreed/binding.json`:
  {"steps": [
    {"tool": "Write", "path": "*/outbox/<name>.json", "json": "content",
     "binding": {"serves": ["t1-<short name>", "t2-<short name>"], "kind": "takes_effect", "author": "agent"}},
    {"tool": "Write", "path": "*/as-agreed-probe.json", "json": "content",
     "binding": {"serves": [], "kind": "mentions", "author": "agent"}}]}
- The first step is the one call that makes the task take effect. `serves` names every commitment.
- That call always writes JSON. If the work is not JSON (a list in text, a document, code),
  the call writes a JSON summary of the work, with the values that the scripts printed.
- For a tool that sends, books or pays, put the name of that tool in `tool`, and remove `path` and `json`.
- In `carried_by`, give a key only to a value that this call uses, or that a script printed.
- Do not copy my words into the output to make a term pass. Thus give no key to `act` or `obligor`.
- The second step is a test step. Keep it as it is.

5. Check the two files, then test the hook.
- Run this check. If `python3` cannot import `as_agreed`, put `PYTHONPATH=<installPath>/src` before it.
  python3 -c 'import json; from as_agreed.commitment import Commitment; r = json.load(open(".as-agreed/record.json")); ids = {Commitment.from_mapping({k: v for k, v in c.items() if k != "said"}).commitment_id for c in r["commitments"]}; b = json.load(open(".as-agreed/binding.json")); bad = sorted({s for st in b["steps"] for s in st["binding"]["serves"] if s not in ids}); print("unknown ids in serves: %s" % bad if bad else "record ok")'
- If it does not print "record ok", correct the files and run it again.
- If the plugin has `confirm`, also run `PYTHONPATH=<installPath>/src python3 -m as_agreed show`.
  Tell me each mark. The test step always has one mark, because it serves no commitment. That mark is expected.
- Then, with the Write tool, write {} to `as-agreed-probe.json`.
- The hook must stop this write, because I did not confirm the record yet.
- If the write runs, the hook is not on. Delete the file, stop, and tell me. Do not continue.

6. Show me a table with one row for each term: the number, my words, the value, how it is checked, and the output key.
- For each term "by a script", give the command and the hash, so that I can run them myself.
- Under the table, name the call that the hook checks.
- Then tell me how to confirm:
  - if the plugin has `confirm`: "If this is correct, run this in your own terminal, in this folder:
    `PYTHONPATH=<installPath>/src python3 -m as_agreed confirm`. It asks for your name."
  - if not: "If this is correct, write your name in `confirmed_by` in `.as-agreed/record.json`, by hand."
- Wait for me. Do not confirm yourself, with any tool. Only I confirm. The hook stops an agent's `confirm`.

7. Do the work.
- After I confirm, do not change a script, the record or the binding. If a script is wrong, stop and tell me.
- Write the work to a draft file first. Run each script on that draft.
- Then the bound call writes `outbox/<name>.json`: the work, if it is JSON, or a summary of it,
  with the values that the scripts printed.
- Move a draft that is not JSON to its final place only after the bound call ran.
  If the hook stops the call, the draft stays a draft.
- Put in the output only a value that you got from the work: from a script, a tool or a reply.
- If you have no real value, leave the key out. The hook then stops the call. Tell me why.
- Make the effect only through the bound call. Make no other call that sends, books, pays or publishes.
- If the hook stops the call, read its reason: the field, the agreed value and your value.
- Correct the output, and do the call again.
- Do not change the record or the binding to make a call pass.
- If you cannot do the task as agreed, do steps 8 to 10, then stop.

8. Read the flag line. Run `ls -td .as-agreed/runs/*/ | head -1` to find the folder of this task.
Read `band.json` in that folder, and tell me its labels.
The band counts values, not terms: a term with two limits counts two times.

9. Tell me to click Board on the band. The button makes a new page from the records of this task.
If I see no band, open `<that folder>/board.html`. Tell me that it is your copy: a file tool can change it.

10. Give me one table, with one row for each term of step 2:
- kept, held or unchecked, as the Board gives it, or "not on the Board", and why;
- for each term "by you": what I must read, and where;
- for each term "by a script": run `shasum -a 256` on the script again. If the hash is not the one in the record, say so first.
  Then run the script on the work as written, and give its value.
If the hook stopped the bound call, say first: "The call was stopped. No file was written."
The rows of that call show what it carried, not what took effect.
Under the table, name each call or effect of my task that the binding does not name.

If the hook gave an error, show me the error, word for word, and stop.
If there is no error and no `.as-agreed/runs/` folder, look at the `version` of step 1.
Version 0.1.0 has no band and no Board. Then read the last lines of `.as-agreed/hook.jsonl`,
and tell me the decision for each call.
```

## The result

- Your task as numbered terms, in your words, each with how it is checked.
- Before you confirm: a check of the two files with the library, and a test of the hook.
- A record that only you confirm. After you confirm it, a file tool cannot change it.
- With `confirm`: your name, the time and two hashes in the record. After that, a change by any program stops every call until you confirm again.
- A stop, with the reason, when the bound call does not carry a term as agreed.
- The flag line: kept, held, unchecked. The Board: one page on your machine, for this task.
- One row for each of your terms. The terms that code cannot check are in it too.
