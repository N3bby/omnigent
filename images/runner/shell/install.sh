#!/bin/sh
# Installs the shell setup for Tailscale SSH logins into /root while the image
# builds. Everything in this directory is personal taste; change it freely.
set -eu

src=$(dirname "$0")
home=/root

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends zsh
rm -rf /var/lib/apt/lists/*

fetch() {
    git init -q "$3"
    git -C "$3" fetch -q --depth 1 "https://github.com/$1.git" "$2"
    git -C "$3" checkout -q FETCH_HEAD
}

# GitHub repository, full commit hash (tests reject anything else), and where
# it goes under /root. tpm looks for plugins next to tmux.conf, in
# ~/.config/tmux/plugins. catppuccin/tmux is tag v0.3.0.
while read -r repo commit dest; do
    fetch "$repo" "$commit" "$home/$dest"
done <<'EOF'
ohmyzsh/ohmyzsh                 60c9a7a839b790cd905d0fd4419435124fd1bdc0  .oh-my-zsh
tmux-plugins/tpm                e261deb1b47614eed3400089ce7197dc68acc4eb  .tmux/plugins/tpm
tmux-plugins/tmux-sensible      25cb91f42d020f675bb0a2ce3fbd3a5d96119efa  .config/tmux/plugins/tmux-sensible
christoomey/vim-tmux-navigator  e41c431a0c7b7388ae7ba341f01a0d217eb3a432  .config/tmux/plugins/vim-tmux-navigator
tmux-plugins/tmux-yank          acfd36e4fcba99f8310a7dfb432111c242fe7392  .config/tmux/plugins/tmux-yank
catppuccin/tmux                 5fbfcc12f5144d4ba603a0e6e6ec61c44b6ccba4  .config/tmux/plugins/catppuccin/tmux
EOF
# tmux.conf lists tpm as a plugin too, so tpm would otherwise report it missing.
ln -s ../../../.tmux/plugins/tpm "$home/.config/tmux/plugins/tpm"

mkdir -p "$home/.config/zsh"
cp "$src/tmux.conf" "$home/.config/tmux/tmux.conf"
cp -r "$src/tmux-detach-exit-ssh" "$home/.config/tmux/"
cp "$src/ssh-shell.zsh" "$home/.config/zsh/ssh-shell.zsh"
cp "$src/bash_profile" "$home/.bash_profile"
cp "$src/zshenv" "$home/.zshenv"

# .zshrc is oh-my-zsh's template, as its installer writes it, without update
# checks because the version is pinned above. ssh-shell.zsh starts tmux, so it
# must come last.
sed "s/^# \(zstyle ':omz:update' mode disabled\)/\1/" \
    "$home/.oh-my-zsh/templates/zshrc.zsh-template" > "$home/.zshrc"
grep -q "^zstyle ':omz:update' mode disabled" "$home/.zshrc"
printf '\n%s\n' 'source ~/.config/zsh/ssh-shell.zsh' >> "$home/.zshrc"

# Fails the build if the shell setup has errors. The completion cache it
# writes is named after the build host, so it is removed again.
HOME=$home DISABLE_TMUX=1 zsh -lic 'whence -w omz' >/dev/null
rm -f "$home"/.zcompdump*
