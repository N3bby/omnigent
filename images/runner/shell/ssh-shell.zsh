# This runner's Tailscale name (omnigent-<8 hex>, as the composer shows it),
# or the Pod's hostname without Tailscale. Exported, so shells in tmux panes
# reuse it instead of asking tailscaled again.
if [ -z "$RUNNER_NAME" ]; then
  RUNNER_NAME=$(tailscale --socket=/run/tailscale/tailscaled.sock status --json --peers=false 2>/dev/null |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["Self"]["HostName"])' 2>/dev/null)
  export RUNNER_NAME=${RUNNER_NAME:-$(hostname -s)}
fi

PROMPT='%(?:%{$fg[green]%}%n@${RUNNER_NAME} ➜ :%{$fg[red]%}%n@${RUNNER_NAME} ➜ )%{$fg[cyan]%}%c%{$reset_color%} $(git_prompt_info)'

alias detach-without-exit="source ~/.config/tmux/tmux-detach-exit-ssh/flag-and-detach.sh"

# Start or attach to a tmux session named after this runner. Detaching also
# ends the shell (and the SSH session), unless you use detach-without-exit.
# Omnigent runs its own tmux on a private socket, so this is a separate server.
if [ -z "$DISABLE_TMUX" ] && [ -z "$TMUX" ]; then
  tmux new-session -A -s "$RUNNER_NAME"
  source ~/.config/tmux/tmux-detach-exit-ssh/on-tmux-detach.sh
fi
