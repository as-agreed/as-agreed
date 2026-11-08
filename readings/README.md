# Readings of public agent traces

Readings of public multi-agent traces, one case at a time: where, between the order and the final answer, what was asked and the status of what was found were kept or lost. Each reading looks at four places (design, source, hand-off and sink), counts what it sees with its denominator, and carries the tag [C]: one case, read by a model, no second rater. A public trace usually holds only the task as it was stated, not a record of what was agreed before it, so each reading uses the stated task as a stand-in for that record and says so; that gap is why we also ask for maps of real pipelines ([SEND-A-MAP.md](../SEND-A-MAP.md)).

The first readings, of traces from MAST-Data (the public corpus of annotated multi-agent failures; Cemri, Pan et al., arXiv 2503.13657), are in preparation. They land here when they are ready: one reading per trace, with its counts and its map, and a table of the counts across the traces read. When a dataset is released it gets its own home (code and readings on GitHub, the data on Hugging Face, a DOI for each release), and this folder links to it.

## Words we use
- A **trace** is the evidence: what one run of a pipeline wrote, step by step.
- A **case** is the record: our reading of one trace.
- A **failure mode** is the category: where and how a commitment or a status was lost.
- We say **incident** only when someone was harmed. A lost hedge in a public benchmark trace is a case, not an incident.

## How we write about other people's traces
- Blameless and mechanism-first: "in this trace, 'probably' arrived at step 3 as 'is'", never "this framework lies". The teams who publish traces make this work possible.
- Classes before names. A framework or a team is named only when the trace is public and the name matters to the finding; personal names never appear, and dates and other details that could identify someone are generalised.
- No ranking by counts. A framework with more cases here is not less safe; it may simply be more open.
- What is not confirmed is marked as such, with the source of the claim.

## Licences
Each artifact carries its own licence: our readings and annotations are under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); the code is under the repository's licence; a third-party corpus keeps its own. We link third-party traces by their identifier and copy them only where their licence and access terms allow, and we record the licence we saw and the date. Where a corpus states no licence, we ask its maintainers before copying anything.

## What this collection is not
Not a measure of how often any framework fails, not a comparison between teams, not a judgment of anyone's work. Each reading is one case.

## Removal and reply
If a trace or a reading concerns you or your work and you want it corrected, answered or removed, open an issue here or write to hello@bizbuilderai.com. Corrections are published with the reading. Material released under CC BY, and releases that carry a DOI, cannot be fully recalled; a removed reading leaves a short note saying it was removed.
