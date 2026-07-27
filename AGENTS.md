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
