# shellcheck shell=sh
# Puts mise's shims first on PATH, so `node`, `python` and the rest follow a
# project's .mise.toml or .tool-versions, installing a missing version on first
# use. Outside such a project they fall through to the image's own tools.
# mise keeps everything in the session's home volume, so runtimes survive a
# Pod waking. Tailscale SSH shells get HOME=/root, which isn't kept, hence the
# fixed paths. The image installs this as zz-omnigent-mise.sh so it runs after
# omnigent-venv.sh, and a project's Python comes before the venv's.
export MISE_DATA_DIR=/home/omnigent/.local/share/mise
export MISE_CONFIG_DIR=/home/omnigent/.config/mise
export MISE_STATE_DIR=/home/omnigent/.local/state/mise
# Agents can't answer prompts, and any repository they clone into the home
# directory is one they already run code from.
export MISE_YES=1
export MISE_TRUSTED_CONFIG_PATHS=/home/omnigent
case ":${PATH}:" in
    *":${MISE_DATA_DIR}/shims:"*) ;;
    *) export PATH="${MISE_DATA_DIR}/shims:${PATH}" ;;
esac
