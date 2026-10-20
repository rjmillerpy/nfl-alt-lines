# Code quality rules

All code in this project must follow these rules. The numbering is the
original; rules 54-61 appeared twice in the source list and are listed once.

**In this project** the source package is `nfl_alt_lines/` (the rules' `app/`)
and tests mirror it under `tests/`: `nfl_alt_lines/odds/logger.py` is tested by
`tests/odds/test_logger.py`. Shared code (settings, the HTTP client, logging)
lives in `nfl_alt_lines/common/`. There is no web API, database or frontend
yet, so the rules written for them (10, 39, 46, 50-51 for endpoints, 53, 68-71,
the API-contract, stale-cache and frontend test rules) apply from the moment
that part is added; the [roadmap](ROADMAP.md) says which phase brings each in.
Here, rule 5's "external input" means The Odds API, nflverse, Open-Meteo, the
saved snapshot files and `.env`; each is validated where it is read.

## General design

1. Functions stay under 50 lines and do one thing.
2. No duplicated logic: extract shared patterns into utilities or hooks after the second occurrence.
3. Prefer clarity over cleverness: no dense one-liners that sacrifice readability.
4. Use descriptive naming that reveals intent: no `temp`, `data2`, `handleStuff`.
5. Validate all external input at the boundary: request bodies, query params, localStorage, third-party APIs.
6. Return early instead of nesting deeply: max 3 levels of indentation in logic.
7. Separate business logic from framework glue: services/hooks handle logic, views/components handle rendering.
8. No magic strings or numbers: extract named constants.
9. All external calls must have a timeout, error handling, and bounded retries.
10. Never expose internal errors, stack traces, or SQL to clients.
11. No unbounded operations: paginate queries, cap uploads, limit recursion, set timeouts.
12. Default to immutable state updates.
13. Use types everywhere: no `any` unless truly unavoidable.
14. No silent failures: every catch block must log meaningfully or degrade gracefully with a comment explaining why silence is intentional.
15. No file over 300 lines for components, 400 for backend modules: decompose into smaller units.
16. One source of truth per concept: one enum, one schema, one constant, one API client.
17. Secrets never live in code: use environment variables.
18. Log events and context, never sensitive data (passwords, tokens, PII).
19. No helper/util dumping grounds: group by domain, not by "misc".
20. Prefer deletion over extension: before adding code, ask if you can remove or merge existing code instead.

## Loop design

21. No expensive work inside loops: batch DB queries and API calls instead of N+1 patterns.
22. Move invariant computations outside loops.
23. Prefer built-in methods (map, filter, list comprehensions) over manual accumulation loops.
24. Max 2 nested loops: 3+ means rethink with pre-indexed maps or separate steps.
25. Use sets for O(1) lookups inside loops instead of lists.
26. Never mutate a collection while iterating it: build a new one.
27. Use generators/streaming for large datasets to avoid memory blowups.
28. Break early when possible: use `any()`, `next()`, or early return.
29. No mixed side effects and heavy logic in loops: transform first, side-effect later.
30. Prefer batching over per-item operations (bulk insert, `Promise.all`).
31. Explicitly bound loops that depend on external data: max iterations, timeouts, fail-safe exits.

## Error handling

32. Never use bare `except:`; always catch a specific exception type.
33. Catch only what you can handle: don't catch `Exception` when you mean `ValueError`.
34. Keep try blocks small: wrap only the operation that can fail.
35. Never silently swallow errors: log with context or degrade gracefully.
36. Convert low-level errors to domain-level errors at boundaries (DB errors -> "User creation failed").
37. Always add context when re-raising (`raise DomainError("msg") from e`).
38. Use `finally` only for cleanup; prefer context managers (`with`).
39. Define clear error boundaries: controllers convert to HTTP responses, services raise domain errors, repositories raise data errors.
40. All external calls must be wrapped with timeout, retry-or-not decision, and fallback behavior.
41. Errors must be observable: logged with request ID, user context, and traceable info.

## Control flow

42. Max 2-3 levels of nesting: use early returns, guard clauses, helper functions.
43. Extract complex boolean conditions into named variables (`is_valid_user = ...`).
44. Replace long if/elif chains with mappings/dicts when possible.
45. Never use exceptions as normal control flow: check `if key in dict` instead of catching `KeyError`.

## Schemas, types and tests

46. Use Pydantic/dataclass schemas for all API request and response shapes: never accept raw `dict = Body(...)` in endpoints.
47. Every function must have full type annotations: parameters and return types.
48. Test files must mirror the source filesystem: `tests/auth/test_views.py` tests `app/auth/views.py`, `__tests__/app/agora/test_page.test.tsx` tests `src/app/agora/page.tsx`.
49. Use `@pytest.mark.parametrize` (backend) and `test.each` (frontend) for test cases that vary only by input: validation boundaries, toggle endpoints, error codes.

## Project layout

50. All schema files use the singular name `schema.py`, not `schemas.py`.
51. Each domain gets its own directory under `app/` with `models.py`, `schema.py`, `service.py`, `views.py`.
52. Every source file in `app/` must have a corresponding `test_*.py` in `tests/` mirroring the same directory structure.
53. Frontend test files must use the `test_` prefix and mirror the `src/` directory: `__tests__/app/agora/test_page.test.tsx` for `src/app/agora/page.tsx`, `__tests__/components/test_PostCard.test.tsx` for `src/components/PostCard.tsx`, `__tests__/hooks/test_useAuth.test.tsx` for `src/hooks/useAuth.ts`. Every source file must have a corresponding test file.

## Classes and modules

54. Only create a class when it owns state, enforces invariants, or represents a domain concept: prefer plain functions for stateless transforms.
55. Each module has one reason to change: if a file mixes CRUD, toggles, search, and LLM generation, split it.
56. Modules stay under 400 lines, classes under 200 lines, methods under 30 lines.
57. Prefer composition over inheritance: max 1 level of inheritance in app code.
58. Inject dependencies, don't create them inside classes/functions.
59. Value objects (Pydantic schemas, dataclasses) should be immutable: expose behavior through services, not by adding methods to data containers.
60. No god objects: if a module has 20+ functions touching different concerns, it must be split into focused modules.
61. Keep public interfaces minimal: expose meaningful actions, not raw field mutation.

## Performance

62. Classify the bottleneck before optimizing: CPU-bound, I/O-bound, or mixed.
63. Remove redundant work first: no repeated DB/API calls for same data, no recreating expensive clients per request.
64. Batch before parallelizing: 1 bulk query beats 100 small ones.
65. Use better data structures before concurrency: sets for lookups, indexes for queries.
66. Reuse connections and clients: one shared DB pool, one shared HTTP/API client per app scope.
67. Every external call needs an explicit timeout.
68. `React.memo` for list item components, `useCallback` for handlers passed as props.
69. Lazy-load heavy components (dynamic import) that aren't needed on initial render.
70. Fetch only what the user is looking at: don't prefetch inactive tabs or off-screen data.
71. Clean up subscriptions (EventSource, WebSocket, intervals) on unmount.

## After implementing: tests

Write or update tests to cover the changes:

- unit tests for new or modified functions;
- integration tests for multi-step flows;
- API contract tests for any endpoint changes (status codes, response schemas, error formats);
- security tests if auth, input or access control is involved;
- performance tests if the change affects query patterns or rendering of lists;
- regression tests documenting each bug fixed, with a description of what broke and how to verify it stays fixed;
- smoke tests verifying all pages render and critical flows still work.

Run all tests and confirm they pass with 90%+ coverage on changed files.

**UNITTEST-RULES:** public black-box tests only; real DB rows; minimize mocks
(mock only external boundaries like S3, email and the network; never mock
internal functions or components); include user-visible endpoint regression
tests for stale-cache lockout behavior.

**TEST-STRUCTURE:** test files mirror the source filesystem
(`tests/auth/test_views.py` for `app/auth/views.py`). Every `app/` source file
has a `tests/` file named `test_{filename}.py` in the matching subdirectory.
Use `@pytest.mark.parametrize` with descriptive ids for all boundary,
validation, toggle, error-code and auth cases. All request/response shapes use
Pydantic schema objects, not raw dicts. Schema validation tests may instantiate
Pydantic models directly.

**FRONTEND-TEST-STRUCTURE:** test files mirror `src/` using `test_` prefix
naming: `__tests__/app/{route}/test_page.test.tsx` for pages,
`__tests__/components/test_{Component}.test.tsx` for components,
`__tests__/hooks/test_{hook}.test.tsx` for hooks,
`__tests__/lib/test_{module}.test.ts` for utilities. API route tests go in
`__tests__/app/api/{route}/test_route.test.ts`. Cross-cutting tests
(integration, performance, regressions, security, smoke) use the `test_`
prefix in their own directories. Use `test.each` wherever inputs vary across
the same assertion pattern.

## How the rules are enforced here

| Check | Rules it covers | Command |
|---|---|---|
| ruff (`ANN`, `BLE`, `E722`, `TRY`, `PLR2004`, `C901`, `PLR0912`, `PLR0915`, `PLR1702`, `RET`, `PERF`, `S`, `B`, `SIM`, `C4`) | 1, 6, 8, 23, 32-34, 37, 42, 47, plus Bandit-style security | `uv run ruff check .` |
| mypy `strict` | 13, 47 | `uv run mypy` |
| Bandit | security | `uv run bandit -c pyproject.toml -r nfl_alt_lines` |
| `tests/test_structure.py` | 1, 6, 15, 24, 42, 48, 52, 56: function, method, class and module size, nesting, nested loops, mirrored test files | `uv run pytest` |
| pytest-cov, `fail_under = 90` | the 90% coverage requirement | `uv run pytest --cov` |
| CI (`.github/workflows/ci.yml`) | all of the above on every push and pull request | |

Everything else is checked in review.
