# Word list for public text

The controlled vocabulary for every public sentence about As-Agreed: this README, the site, a post, a message.
It follows the method of ASD-STE100 Simplified Technical English: approved words, one definition for each word,
short sentences. A checker prints each sentence over 20 words, each word not on this list, and each banned word.

Source of the general words: the STE word list at
https://raw.githubusercontent.com/kibook/ste-dic/master/sx_US.dic (1,255 words, fetched 2026-11-04 17:36 MSK).
Every general word below is on that list, except four basic words that the list does not have: it, what, under, line.

## Rules

1. Use only the words on this list. A word in code font is a name, not a word.
2. Use each word in one sense: a technical name as the table defines it, a general word as STE defines it.
3. Do not use two words for one thing. A step is a step: not a node, an agent, or a hop.
4. Write a sentence of 20 words or fewer. Write the first sentence of a page in fewer than 12 words.
5. Write one topic in each sentence, and one instruction in each sentence.
6. Use the active voice. Say who or what does the action.
7. Use the present tense only for what runs today. For a planned item, use "will" and give its version.
8. Write numbers as digits. Give each number with what it counts and the script that printed it.
9. Write no claim about data sets, about measurements, or about how much the tool finds.
10. Do not apologize and do not discount the tool. Give its limits in one line, in the present tense.
11. Put code, commands, file names, and output in code font. Put quoted data in double quotes.
12. Write each link as a full URL, because a package index does not show a relative link.

## Banned

Banned: meaning, semantic, understanding, intent, true_intent, real_intent, cascading, cascade, control plane
Banned: holds a step, hold a step, holds the step, reconcile, reconciliation, must-travel, as-briefed, as-delivered
Banned: restoration, seam, hub, spoke, hop, correction gap, answered rate, intact, substituted, guarantee

"Intent" stands only in code, as `intent_record`. "Cascade" names the project and its history, never a finding.

## General words

a, about, above, accept, accepted, access, accurate, across, add, added, after, again, against, agree, agreed, all,
almost, along, also, alternative, although, always, an, and, applicable, apply, approved, approximately, are, as, at,
automatic, automatically, available, back, bad, basic, be, because, become, before, below, best, better, between,
break, breaks, broken, but, by, calculate, can, cancel, cannot, careful, cause, change, changed, changes, check,
clear, clearly, close, code, collect, come, compare, compared, compatible, complete, condition, connect, connection,
constant, contain, contains, continue, control, copy, correct, correctly, count, cut, data, date, decision, decrease,
different, direction, directly, discard, discarded, display, divide, do, does, done, down, downstream, during, each,
easily, easy, effect, empty, end, equal, equivalent, error, estimate, examine, example, exit, expired, extend,
external, fact, far, fast, fill, find, finds, first, flow, follow, for, found, free, from, full, fully, function,
general, get, gets, give, given, gives, go, goes, gone, good, group, had, has, have, help, here, high, higher, hold,
how, identify, if, ignore, immediately, important, in, include, incorrect, increase, information, initial, input,
install, instruction, interface, internal, interval, into, is, isolate, item, its, keep, kept, know, known, large,
last, less, let, level, limit, local, locally, location, lock, long, look, low, made, make, makes, manual, manually,
many, mark, maximum, measure, minimum, missing, mode, monitor, more, most, move, much, must, name, near, necessary,
new, newest, next, no, none, not, number, occur, of, off, on, one, only, open, operate, operation, optional, or,
other, out, output, over, part, permitted, person, point, position, possible, possibly, prepare, prevent, primary,
problem, procedure, property, pull, push, put, quality, quantity, quickly, random, range, rate, read, receive,
record, recorded, refer, regular, reject, related, release, remaining, remove, repair, replace, report, result,
right, risk, row, safe, safety, same, sample, schedule, secondary, see, select, send, sends, sent, sequence, set,
short, show, shows, side, sign, simulate, since, slow, small, some, something, source, special, specified, speed,
stable, standard, start, stay, step, stop, stops, strong, structure, such, sufficient, sum, supply, sure, symbol,
system, tag, task, tell, tells, temporary, test, than, that, the, their, them, then, there, these, they, this, those,
through, thus, time, to, together, tool, top, total, try, turn, type, typical, unit, unknown, unless, until, up,
upstream, use, used, usual, usually, value, very, view, visual, wait, was, we, weak, when, where, which, while, who,
wide, with, without, work, write, writes, written, yes, you, your
will
it, what, under, line

## Technical names

| name | definition |
|---|---|
| As-Agreed | this library |
| agreed | what the parties accepted, as the order and its commitments record it |
| pipeline | steps in a sequence, where each step uses the output of a step before it |
| step | one function in a pipeline that makes one output |
| commitment | a typed record of what was agreed: who must do what, for whom, with which limits, by when |
| field | one named part of a commitment, for example `limit` or `deadline` |
| declare | to write a field into a commitment, so that As-Agreed can compare it |
| claim | a sentence in the work of a step that a later step can use as a fact |
| status | what a claim tells about itself: a hedge, a source, a date, or "not found" |
| hedge | a word that tells that the writer is not sure, for example "probably" |
| arrive | to be present in the output of a later step |
| order | the request, as it was given, before any work |
| gate | the check that stops a step and exits with a code that is not 0 |
| stage | one text file in a folder, the work of one step |
| text | words in a file, not a declared field |
| schema | the types that the output of a step must have |
| key | one name in the output of a step, for example `amount` |
| reason | the line that tells a step why As-Agreed stopped it |
| pass | to end a check with no problem found |
| call | to start a function from code |
| run | to start a program and let it go to its end |
| demo | the example that ships with As-Agreed |
| clone | a copy of the repository on your disk |
| repository | the files of As-Agreed, with their history, on GitHub |
| file | one named item of data on a disk |
| folder | a named group of files |
| disk | the storage of your computer |
| module | one Python file that code can import |
| import | to load a module into Python code |
| network | the connection from your computer to other computers |
| package | a set of modules that you install |
| account | a user name on a service |
| service | the As-Agreed service: one record for all the pipelines of a team, planned |
| trace | the record that another tool writes when a pipeline runs |
| version | one numbered release of As-Agreed |
| model | a language model |
| payment | money that the example pipeline sends for an invoice |
| map | the flow picture of a pipeline, without its text |
| Python | the programming language |
| GitHub | the site that keeps the repository |
| licence | the terms under which you can use the code |
| Apache | the Apache Software Foundation, the author of the Apache-2.0 licence |
| copyright | the right of the owner of the code |
| BizBuilder | BizBuilder, Inc., the owner of the code |
| Inc | part of the name BizBuilder, Inc. |
| sign-off | a line in a change that says that you wrote it and can send it |
| agent | a program that uses a model to do the work of one or more steps |
| agents | more than one agent |
| multi-agent | with more than one agent, where agents give work to agents |
| hand-off | the point where one step gives its output to the next step |
| term | one point of an agreement, for example a limit; a declared term is a field |
| agreement | what two or more parties agreed, before As-Agreed records it as commitments |
| obligor | the party that must do the act of a commitment |
| obligee | the party for whom the obligor does the act |
| pipeline step | a step; use "step" after the first time |
| commit | the last step, where the result goes into another system, for example a payment |
| decorator | a Python line, such as `@aa.step`, that puts As-Agreed on one function |
| adapter | the code that connects As-Agreed to one workflow tool |
| carrier | in freight, the trucking company that moves the load; in insurance, the company that takes the risk. Never the old vocabulary's "carrier of a claim" |
| PyPI | the public index where Python packages are published; `pip install` reads it |
| LangGraph | a Python tool for pipelines of agents |
| Temporal | a workflow tool that runs long tasks and tries a step again after an error |
| Langfuse | a tool that writes traces of a pipeline |
| LangSmith | a tool that writes traces of a pipeline |
| library | the As-Agreed code that you import into your pipeline |
| team | a group of persons who work together |
| today | on the date of the text |
| due | planned to be ready on the given date |
| sold | given for money; the As-Agreed service will be sold |
| computer | the machine where your code runs |
| contact | the address to which you write to the persons who make As-Agreed |
| research | the work that examines how agreed terms change across steps, and publishes the method |
| founder | the person who started As-Agreed |
| year | a unit of time, 12 months |
| engineer | a person who writes and runs pipelines |
| researcher | a person who does research |
| buyer | a team that pays for the As-Agreed service |
| community | the persons who send traces and read the results in public |
| public | open for all persons to see and use |
| publish | to make a text public |
| people | persons |
| consent | the approval of the persons whose data is in a trace |
| fidelity | in "commitment fidelity": the part of the checked fields that arrive as agreed |
| nonconformance | a result that does not agree with a specified requirement, in the words of quality standards |
| post-mortem | a public report on one error in a pipeline, step by step |
| organiser | a person who runs an event where a demo can be shown |
| client | a person or a team for whom a buyer does work |
| auditor | a person who examines the records of a team for another party |
| regulator | a public body that sets and checks the rules for a type of work |
| price | the money that a buyer gives for the service |
| loss | what a changed term costs, in the words of the task; the first of the three lines of a finding |
| planted | put into an example run on purpose, to show the hold; no company had it |
| brief | in "hand-off brief": what one step gives the next step as its task |
| company | a business, for example a shipper, an insurer or a supplier |
| freight | goods that a trucking company moves for a shipper; also the market for this work |
| shipper | the company that gives its goods to a trucking company, often through a broker |
| broker | a company that finds a trucking company for the load of a shipper and pays it the agreed rate |
| trucking | in "trucking company": a company that moves loads by truck |
| load | one shipment that one truck moves from its pickup to its delivery |
| pickup | the date and the place where the truck takes the load |
| confirmation | in "rate confirmation": the document that a broker sends a trucking company for one load, with its dates and its rate |
| fuel | in "fuel surcharge": the cost of fuel, as an amount for each mile |
| surcharge | an amount added to a rate, for example for fuel |
| insurance | the market where an insurer accepts a risk for a premium |
| insurer | the company that accepts a risk and pays for its losses |
| coverholder | a company that binds risks for an insurer, under a binding authority |
| binding | in "binding authority": the agreement that lets a coverholder bind risks for an insurer |
| authority | in "binding authority": the limits, rates, classes and territory inside which a coverholder can bind |
| bind | to accept a risk for an insurer, so that its cover starts |
| policy | the insurance contract; the policy system is where each policy is recorded |
| appetite | the classes and territories of risk that an insurer accepts |
| procurement | the work of a company that buys goods from its suppliers |
| purchaser | the company that buys goods from a supplier under a contract |
| supplier | the company that sells goods to a purchaser under a contract |
| contract | the written agreement between a purchaser and a supplier |
| contract price | the amount for each unit that a contract or its amendment sets; not the price of the service |
| amendment | a change to a contract that both parties accept, here by email |
| email | a message that one person sends to another on the network |
| requisition | the request inside a company to buy goods; the purchase order comes from it |
| purchase | in "purchase order": the document in which a purchaser orders goods from a supplier |
| invoice | the document in which a supplier asks for payment |
| three-way | in "three-way match": the check that an invoice, its purchase order and its goods receipt agree |
| match | in "three-way match": to agree, value for value |
| delivery | the date and the place where the goods arrive at the purchaser |
