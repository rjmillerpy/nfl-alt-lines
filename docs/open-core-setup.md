# Open-core setup: a public portfolio repo fed from a private one

How to publish an app's engineering (infrastructure, pipeline, tests, observability) in a public repo while
the part that is the actual edge (a model, scoring rules, pricing logic, research results) stays private,
and production keeps running from the private repo. Written to be handed to another Claude instance: it
describes the pattern and the order of work; every app fills in its own names.

## The shape

```
private repo (source of truth, deploys production)
├── app/                    the engineering: jobs, storage, database, infra, CI, emails, logging, tests
│   └── model interface     a small Protocol the app calls; nothing else knows how the model works
├── baseline model          a simple, honest implementation of that interface (public)
├── private model package   the real implementation (never leaves this repo)
├── export manifest         an ALLOWLIST of paths that may be public
└── export workflow         on every push to main: copy allowlisted paths, check them, push to public

public repo (read-only mirror)
└── everything allowlisted, running end to end with the baseline model, with its own CI
```

- **One place to work.** All development, PRs and deploys stay in the private repo; nothing about
  production changes. The public repo is generated from it and never edited by hand.
- **A seam, not a fork.** The app depends only on the model interface. Production installs the private
  package; the public repo falls back to the baseline. Discovery through a Python entry point (group such
  as `<app>.models`) keeps the app free of any import of the private code.
- **Allowlist, never a denylist.** A new file is private until someone adds it to the manifest, so new
  proprietary code cannot leak by accident.

## Steps, in order

1. **Inventory.** List every module, doc and data file; mark each public or private, with the owner.
   Private means anything that would let someone reproduce the edge: features, model code, thresholds and
   constants that encode findings, selection and staking rules, backtests and their result numbers, and
   prose that states findings (roadmaps, READMEs, commit messages that quote results).
2. **Define the seam.** Write the interface (a `typing.Protocol`) at the narrowest point where the app
   needs model output: often "given this week's inputs, return a table of candidates with probabilities
   and ranks". Move constants that encode findings behind it. Shared pure helpers (odds maths, schemas)
   stay public.
3. **Write the baseline.** A real, simple implementation of the interface built from public knowledge:
   e.g. the market's own consensus, a base rate, or a published heuristic. The public repo must run
   end to end and its tests must pass with it.
4. **Contract tests.** One test suite that any implementation must pass (shapes, ranges, determinism,
   no lookahead), run against the baseline in public and against both in private.
5. **Prove production did not change.** Before and after the refactor, run the full production job on a
   frozen copy of real data at a fixed time and diff the output: it must be identical, byte for byte.
   Do this for every step that moves model code.
6. **Move the private code** into its own package (or folder) that the manifest never lists, with its
   tests next to it. Register it through the entry point; production installs it.
7. **Write the manifest and the checks.** The export keeps only allowlisted paths, then fails the run if:
   a secret scanner (e.g. TruffleHog) finds anything; any file contains a term from a forbidden-terms
   list (names of private modules, functions and constants); or the public tree does not pass lint and
   tests on its own. Keep the forbidden-terms list in the private repo, outside the manifest.
   Some public files must still name private paths (a workspace member, a type-checker path, a Docker step).
   Mark those lines and have the export drop them: a line ending in `# open-core: private-only`, or a comment
   line starting with it, which drops the line after it as well (for files that allow no trailing comments).
   Text that merely mentions the marker is kept. Derive part of the forbidden-terms list from the private
   package itself (every identifier it exposes that the public code does not define), so it never drifts.
   Pin production to its strategy (an environment variable in its image), so a missing private package fails
   the run loudly instead of quietly falling back to the baseline.
8. **History.** The existing history contains the private code in every commit. Either start the public
   repo fresh, or filter a copy of the history with `git filter-repo --paths-from-file <manifest>` (plus
   `--replace-text` for private strings in kept files and commit messages), then read the filtered log and
   the final tree before publishing. Filtered history shows real progress; review it like a release.
9. **Publish privately first.** Create the public repo as private, push the export, review it as an
   outsider would (README, docs, commit messages, Actions logs), then switch it to public.
10. **Automate.** A workflow in the private repo runs the export on every push to main and pushes to the
    public repo with a deploy key or fine-grained token scoped to that one repo (stored as a private-repo
    secret). Each public commit carries the private commit's message (filtered) and a reference.
11. **Say what it is.** The public README states plainly that the production model is private and that
    the repo runs a baseline behind the same interface. Pick a license deliberately. Turn off issues and
    pull requests, or say that the repo is a mirror.

## Pitfalls

- Findings leak through prose more often than through code: roadmaps, changelogs, docstrings, commit
  messages, comments with backtest numbers, test names and fixtures copied from real research.
- Constants in "shared" modules (thresholds, feature lists, weights) are model code.
- The forbidden-terms list and the manifest's private complement must never be exported themselves.
- Secrets, account IDs, email addresses and local config (`.env`, `*.tfvars`) stay out by construction:
  they are not on the allowlist.
- Public repos get free GitHub Actions minutes and branch protection; the mirror's CI is a useful public
  signal that the code is real.

## Prompt to give another instance

> Set up an open-core split for this repo following docs/open-core-setup.md (or the description below).
> First inventory every file and propose the public/private split for my approval. Then, in this order:
> define the model interface and a public baseline with contract tests; prove production output is
> byte-identical before and after each step on a frozen copy of real data; move the private code into
> its own package registered through an entry point; write the export allowlist, the forbidden-terms
> check and the secret scan; filter the history with git filter-repo and show me the result; publish to a
> new repo as private for my review before it goes public; then automate the export on every push to
> main. Never use a denylist, never edit the public repo by hand, and never change what production does.
