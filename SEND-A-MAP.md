# Send the map, not the data

If you run a pipeline of agents, you can see where its claims lose their status without showing anyone your text. Draw the map on your own machine and send only the picture. We would be glad to see it.

## How
1. Put one file per step of your pipeline in a folder, named in order: `01-order.md`, `02-research.md`, `03-summary.md`, and so on.
2. In the first file, mark with `[!]` each sentence that must reach the end, just before its full stop.
3. From this repository, run:

   ```
   python3 ledger.py run path/to/your-folder --against-all --only hedged,not_found,sourced,dated,must --fail-on danger,dropped_must
   ```

4. Open `flow.svg` in that folder. It holds no claim text, but it does hold your folder's name as its title, your file names as stage names, and the time it was drawn. Rename the files (the stage names and the picture's tooltips come from them), rename the folder or add `--title "my pipeline"` to the command, and look at the picture before you send it.
5. Open an issue here with the "Send a map" template and attach the SVG. Tell us the shape of the pipeline (a hub with helpers, a line of stages, a mesh) and the engine, if there is one. An issue is public, so post only a map you are happy to show; to send one privately, write to hello@bizbuilderai.com.

## What happens to it
Each map is read for one thing: where, in pipelines shaped like yours, claims kept or lost their status, and whether the must-travel sentences arrived. Maps are compared only as counts, by pipeline shape. Ask, and a map you sent is taken out.

## Why the picture is enough
The question is where the status of a claim falls off, not what your pipeline says. The map answers it with counts per step, so your data stays where it is.
