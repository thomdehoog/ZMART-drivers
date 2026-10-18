# Working in this repo

## Audience: write for biologists, not software engineers

ZMART is used mostly by **microscopists and biologists who are learning**, not by
professional software engineers. Every docstring, comment, notebook-markdown
cell, and README must be written for that reader. This is a **general rule** for
all code and docs in this repository.

Concretely:

- **Convey the information the reader needs, and give context.** Say *why*
  something is done and what it means for their experiment — not just *what* the
  code does. A line that only restates the code in English adds nothing; a line
  that explains the reason earns its place.
- **Be gentle and welcoming.** Assume curiosity, not expertise. The tone should
  help someone learn, never make them feel they should already know.
- **Avoid unexplained software-engineering jargon.** Terms like *dihedral
  group*, *Jacobian*, *atomic replace*, *idempotent*, *dataclass*, *closure* are
  fine only if you also explain them in plain language (or replace them with a
  plainer phrase). Prefer "a 90° turn" over "a D4 element" in operator-facing
  text; keep the precise term for internal code comments if it genuinely helps a
  maintainer, but still gloss it.
- **Operator-facing surfaces get the most care**: each driver's setup
  notebooks (for the Leica driver: `limits`, `orientation`, `calibration`),
  the methods each driver's `ZmartDriver` class offers to the ZMART Controller,
  and the READMEs. These are the front door.
- **Docstrings state contracts plainly**: what goes in, what comes back, what
  can go wrong — in a sentence or two a non-programmer can follow.
- **Write in easy, complete sentences.** Read it back and make sure it flows.
  Avoid clipped "telegram style" — the terse, article-dropping shorthand that
  saves keystrokes but makes the reader work ("Fail-closed guard; abandoned-leg
  drain sizing" reads as noise). Full sentences cost a few more words and are
  far kinder to read.
- **Keep a calm, neutral voice.** Not chatty, not hype, and not the dense,
  sloppy shorthand of throwaway code comments. Steady and clear, the way you
  would explain something to a colleague at the microscope.

Good docs are not decoration here; they are how a biologist learns to drive
their microscope. Treat them with the same care as the code.

## How code is written

The full guide is `docs/code-style.md`, with the reason for each rule. The short
version, for every file you write or change:

- Code that belongs together sits together. Remove dead code, and its tests.
- Fewer lines are better, until they hurt readability. Then stop cutting.
- Put blank lines between the steps of a function. Where a step needs a label,
  put a short `#` comment above it.
- Start each file with a readable docstring that is not bloated. Under it, give
  the author, where they work and their email:
  Thom de Hoog, Center for Microscopy and Image Analysis (ZMB), University of
  Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
- If anything in the code is inspired by someone else's work, or by Thom de
  Hoog's earlier work, acknowledge it in the docstring.
- Name things after their job, not after jargon. Keep a file to one job.
- Comments say why, or what was seen, with the date. Numbers live in one place
  and carry their unit in the name.
- One idea per commit, with a message in plain words.

