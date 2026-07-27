# Repository Conventions

## Language

Use English only for all repository content, including documentation, source-code comments, log messages, configuration descriptions, pull request text, and Git commit messages.

Non-English text is allowed only in explicitly designated internationalization (i18n) resources.

## Git Commits

All Git commits must include a `Signed-off-by` trailer. Use `git commit -s` when creating commits.

## Presentation Intermediate Files

Store all generated presentation inspection and build artifacts under
`.codex-tmp/presentations/<deck-stem>/` at the repository root. This includes
`*.inspect.ndjson`, rendered slides, layout JSON, montages, temporary source
files, and intermediate PPTX files. Do not place generated artifacts beside the
source presentation. Only the final presentation may be written under
`Docs/decks/`.

## Temporary Development Machines

Temporary development, image-build, or test machines must be stopped or
terminated immediately after use, including after failures and interrupted
workflows. Automation that creates a machine must include a cleanup path that
runs on success and failure. Before finishing work that used a development
machine, verify from the infrastructure provider that no task-created machine
is still running. Keep a machine only when the user explicitly requests it, and
report its identifier, purpose, and expected shutdown time.
