# How we write code in ZMART

This guide says how the code in this repository should look. It exists so that
a microscopist who opens a file for the first time can follow it, and so that
code written on different days still reads as one piece of work. It follows the
same principles as the interface and the viewer: clear, small, and kind to
someone who is learning.

Every rule comes with its reason. When a rule and its reason ever disagree,
follow the reason.

## Keep code together, clean and short

1. **Code that belongs together sits together.** Put the lines of one step next
   to each other, and keep a function near the functions it works with. A
   reader should not have to jump around the file to follow one idea.
2. **Remove dead code.** Delete code that nothing calls, and delete its tests
   too. Also delete every mention of it in READMEs, docs and comments: a file
   tree that still lists a deleted file is as misleading as the dead code.
   Git remembers it, so nothing is lost.
3. **Fewer lines are better, until they hurt readability.** Cut what repeats
   or says nothing. Stop cutting when the result is harder to read than the
   longer version. Clever and short is not the aim; easy to follow is.
4. **A file does one job.** When a file passes about 500 lines, ask whether it
   does two jobs. If it does, split it. This is a prompt to look, not a hard
   limit.

## Make it easy to read

5. **Use blank lines between the steps of a function.** A function that is
   one solid block of text is hard to scan. A blank line tells the reader that
   one step has ended and another begins.
6. **Put a short `#` comment above a block that needs a label.** Where a blank
   line separates a step and the step is not obvious, give it a line saying
   what it is for, so the reader can find their way.
7. **Name things after their job, not after jargon.** A reader should be able to
   guess what a file or a function does from its name. We chose
   `primitive_handling` over `profiles`, and `for_zmart_controller` over
   `adapter`, for this reason.
8. **Comments say why, and what was seen.** A comment that only repeats the
   code adds nothing. A comment that gives the reason, or a measurement with
   its date and place ("measured on the LAS X simulator, 2026-10-12"), earns
   its place.
9. **Numbers live in one place and carry their unit.** Waiting times, retry
   counts and tolerances belong in a tuning file, and names say their unit
   (`_um`, `_s`). A number in the middle of a function is easy to miss and
   hard to change. A constant that only one test uses may stay in that test.
10. **Let a formatter decide the layout.** The formatter is `ruff format`. It
    should be a required check, so that nobody argues about line breaks. It is
    not required yet: it would reformat 128 of the 178 files in the Leica
    driver today, so the whole driver must be reformatted once, in a commit of
    its own that changes no behaviour. This guide is for what a formatter cannot check.

## The top of every file

11. **Begin each file with a docstring that is readable and not bloated.** Say
    what the file is for, in a few plain sentences. Write for a biologist who
    is learning, as `CLAUDE.md` asks.
12. **Under the docstring, give the author and where they work.** For example:

    ```
    Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
            University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
    ```

13. **Acknowledge what inspired the code.** If an idea, a design or a piece of
    code comes from someone else's work, or from Thom de Hoog's earlier work,
    say so in the docstring. Credit costs one line and means a great deal.

## Behaviour and structure

14. **Each kind of function keeps to one shape.** A command returns a result
    with `success`, `confirmed` and `message`. An acquisition raises an error
    when it fails, and the controller turns that into a failed answer. Within
    one kind, do not mix returning and raising. The controller relies on this
    contract.
15. **Dependencies point one way.** Each part of a driver uses only the parts
    below it (see `docs/driver-anatomy.md`). A test enforces this, so that a
    shortcut cannot slip in unnoticed.
16. **Error messages say what went wrong and what to do next.** Write them for
    the person at the microscope. A message may name a setting, such as "the
    key `password` is missing", but never prints a secret value.
17. **Tests read as sentences and check real values.** A test name says the
    behaviour it proves. A test that only checks that something has the right
    shape proves little, so assert the actual numbers.

## Commits

18. **One idea per commit, with a message in plain words.** Say what changed
    and why. Someone should be able to read the history and understand how the
    code got here.

## A note on how to use this guide

Rules 3, 6, 7 and 11 call for judgment, and no tool can check them. Where two
rules pull in different directions, such as removing comments (rule 3) and
labelling blocks (rule 6), keep the comment that tells the reader something
the code does not, and remove the one that repeats it. Docstrings state what
goes in, what comes back and what can go wrong, in plain sentences, and
explain any technical word the first time it is used, as `CLAUDE.md` asks.
