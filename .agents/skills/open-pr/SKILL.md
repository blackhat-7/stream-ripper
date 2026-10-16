---
name: open-pr
description: Open a pull request for the current work. Use when asked to open, create, or submit a PR.
---

<!-- Managed by blackhat-7/maintainer. Do not edit. Rerun the installer to update. -->

1. Review your diff against `.maintainer/STANDARDS.md`. Fix every violation first.
2. Run the project's checks. Do not open the PR if they fail.
3. One concern per PR. Split it otherwise.
4. Title: imperative, specific, under 70 characters, e.g. `Fix crash when config is empty`.
5. Body: the sections of `.github/pull_request_template.md` (What, Why, Testing), short and concrete.
6. Open it as a draft: `gh pr create --draft --title "<title>" --body-file <file>`.
7. Run `gh pr ready` only when the work is done. Each ready PR push costs a review.
