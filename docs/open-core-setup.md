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

## Lessons from doing it

From splitting a real app (nfl-alt-lines), in the order they bit:

- **Measure the history before promising filtered history.** Run the leak check over every commit's public
  paths. If the private code ever lived in public paths, or old versions of public files held private constants
  or findings, filtering cannot hide it: start the public repo fresh at the split, and write a dated build log
  of the engineering milestones (no results) so the progression still shows.
- **Prove "nothing changed" with the real job, back to back.** Live inputs (weather forecasts, odds) drift
  between runs, so run the old and new code one after the other on the same frozen data, and install the
  private package in both: in a workspace, a plain install leaves it out and the app quietly runs the baseline.
- **Pin production to its strategy.** An environment variable in the production image (STRATEGY=production)
  turns a missing private package into a failed, alarmed run instead of a quiet fall-back to the baseline.
- **Public tests must run on public rules.** Pin public tests to the baseline (module-scoped, so module-scoped
  fixtures see it), and where a mechanism needs rules (stakes, which types are recommended), use a neutral test
  rule set that differs from production on purpose. Copy the production-value versions of those tests to the
  private side. Otherwise expected strings in public tests reveal production's numbers.
- **Findings hide in prose and fixtures.** The first export caught a report footer stating what the backtest
  found, sample report text in a page test, a real model-version hash in a fixture, and docstrings naming
  private constants. Let the export find them; do not trust a read-through.
- **Derive the forbidden names from the private code.** Every identifier the private package exposes that the
  public package does not define: matched as whole words, skipping single plain words ("compare") and
  leading-underscore helpers. A hand-kept list drifts; this one cannot.
- **Formatters can split a marked line.** A long line with a trailing marker may be wrapped so the marker only
  covers its last part; prefer a short line, or the comment-line form of the marker, and keep a test that runs
  the real export on the current tree.
- **List the CI files one by one.** The mirror's publishing workflow and the dependency bot's config are not for
  the mirror (a read-only mirror should not get dependency pull requests).
- **Move what trusts the repo name.** Renaming the private repo frees its old name for the public one: update
  anything that trusts the name (an OIDC deploy role's subject, deploy keys) before creating the public repo.
- **Publish as private first,** review, then flip. A private repo may have no CI minutes; public repos run CI
  for free, which is also the public signal that the code is real.
- **Commit as the owner's no-reply address,** so mirror commits count as theirs without exposing an email.

## Prompt to give another instance

> Split this repository into a public portfolio mirror and a private repo, following the open-core setup below
> (or docs/open-core-setup.md if it is here). Production must not change at any step.
>
> 1. Inventory every file (code, tests, docs, CI, infra) and propose which parts are private: anything that
>    would let someone reproduce the edge (models, features, thresholds and constants that encode findings,
>    selection and sizing rules, research scripts, results). Wait for my approval of the split.
> 2. Add a seam: one interface the app calls for everything private, found through a plugin entry point, plus
>    a simple, honest public baseline behind it, so the public repo runs end to end. Contract tests for both.
> 3. Before and after every step that moves code, run the real production job on a frozen copy of real data,
>    old and new back to back, with the private code installed in both, and diff the output: it must be
>    byte-identical.
> 4. Move the private code into its own package (a workspace member registering the entry point), its tests
>    into a private test folder. Pin public tests to the baseline, and use neutral test rules (deliberately not
>    production's values) wherever a public test needs rules. Pin production's image to the private strategy so
>    a missing package fails loudly.
> 5. Write an export tool: an allowlist of public paths (CI files listed one by one), lines marked
>    "open-core: private-only" dropped, and a forbidden-terms check derived from the private package's own
>    identifiers plus a short phrase list. It must refuse to export anything private, and verify the exported
>    tree on its own (lock, install, lint, type-check, tests, container build, secret scan).
> 6. Run the leak check over the whole history. If old commits held private code in public paths, start the
>    public repo fresh at the split with a dated build log of engineering milestones, not filtered history.
> 7. Rename the private repo first and move anything that trusts the old name; create the public repo as
>    private, publish, let me review it, then make it public. Automate publishing on every push to main with a
>    deploy key scoped to the public repo. Never edit the public repo by hand.
