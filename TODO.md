<!-- NOTE: TODO.md is intentionally tracked in version control as the primary task-tracking mechanism for this project. This is by design and will not be migrated to an external issue tracker. -->

## Do Now

- Have the agent automatically triage and fix the most obvious of problems (?)
- Support some kind of "incremental/diff" mode for reviewing commits/PR's/changes.  Compare against some `main` commit.

## Skipping for now, but might do later:
- For single-letter inputs, can we accept the input without requiring an <enter>?
  - This would require using some other UI library. Can consider for later.
- triage agent?
  - An agent that checks to see which review items are applicable to the given file.
- If we have a long list of review items for a file during resolution, what happens when we run out of context window?
- Manage hierarchical review process?
- Manage specific unit test coverage inspections?
- Support cached content?  Something to send at the start of every new context window?
  - (Some way of loading REPLIT.md into context)
- Support some way of doing a *whole project* review from scratch?
  - Currently you have to go in and manually clear out the file_status.json file.
- Git commit: Currently, at the end of a resolution phase, it will do a git add/commit with the -u option, meaning that it will ignore new files that were added during the resolution. This has the advantage of not reflexively adding files into the repo that maybe you really want to gitignore, but it has the disadvantage of leaving the work tree dirty in those cases.  It would be nice to have a better solution here.  Have the agent decide how to handle it?
