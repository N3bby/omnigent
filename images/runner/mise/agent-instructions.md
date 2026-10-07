# Runtimes

This machine has [mise](https://mise.jdx.dev). When a project has a
`.mise.toml`, `mise.toml` or `.tool-versions`, run `mise install` in it before
building or testing, so `node`, `python` and the other tools it pins resolve to
those versions. Use `mise exec -- <command>` when you also need the project's
`[env]`, and `mise run <task>` for its tasks. To add a runtime a project
doesn't pin, use `mise use <tool>@<version>` rather than `apt-get`, since
runtimes mise installs survive the Pod stopping.
