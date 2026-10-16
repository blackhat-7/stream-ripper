# Engineering Standards

<!-- Managed by blackhat-7/maintainer. Do not edit. Rerun the installer to update. -->

You own this code. Every change must leave it at least as clean as you found it.

- **Understand first.** Read the surrounding code and find the root cause. Never patch symptoms.
- **Simplest complete solution.** Fewest concepts, files, and dependencies that fully solve the problem.
- **Only what is needed now.** No speculative abstractions, options, flags, fallbacks, or compatibility layers.
- **Match the codebase.** Same naming, structure, and idioms. No unrelated refactors.
- **No hacks.** No special cases to pass a test, sleeps to hide races, swallowed errors, commented-out code, or debug output.
- **Never weaken checks.** Do not loosen tests, types, lint, or security to get green.
- **No new dependency** unless the standard library and existing dependencies can't do the job.
- **Secure by default.** Validate input at boundaries. No secrets in code or logs. Least privilege.
- **Test changed behavior.** Test behavior, not implementation. No test suite? Verify by hand and say how.
- **Comments say why**, not what.
- **One concern per change.** Keep PRs focused.
- **Change every side.** When an interface between components changes (API, schema, FFI, config), update every user of it in the same change.
- **Managed files are off limits.** Do not edit files marked `Managed by blackhat-7/maintainer`.
- **Unsure? Ask.** Do not guess on design decisions.

## Language traps

- **Go:** check every error. Every goroutine has a way to stop. Pass `context.Context` through I/O. Guard shared state against races.
- **Python:** no bare `except`. No mutable default arguments. No blocking calls in `async` code.
- **Rust:** no `unwrap` or `expect` outside tests and `main`. Every `unsafe` block has a `// SAFETY:` comment. No needless `clone`.
- **TypeScript:** no `any`. No `!` non-null assertions. Await or handle every promise. No `as` casts to silence the type checker.

Before finishing: re-read your diff, delete anything unnecessary, run the checks.
