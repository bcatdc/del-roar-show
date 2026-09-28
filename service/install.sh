#!/bin/bash
# Installs the three services and the `delroar` command, for the user running it.
# Run on the NUC:  bash ~/delroar/service/install.sh
set -e
UNITS=~/.config/systemd/user
BIN=~/.local/bin
HERE="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$UNITS" "$BIN"
cp "$HERE"/delroar-*.service "$HERE"/delroar.target "$UNITS"/
install -m 755 "$HERE"/delroar "$BIN"/delroar

systemctl --user daemon-reload
loginctl enable-linger "$USER"          # lets the services run without you logged in
systemctl --user enable delroar.target delroar-renderer delroar-show delroar-stt

case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
     echo "added ~/.local/bin to PATH; open a new shell or run: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac

echo
echo "Installed. Next:"
echo "  sudo systemctl disable --now display-image.service   # frees the screen at boot"
echo "  delroar start"
echo "  delroar status"
