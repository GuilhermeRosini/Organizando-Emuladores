#!/bin/sh
# Abre o My Games a partir da pasta onde este arquivo está (pendrive ou disco).
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Remove variáveis do VS Code Snap para o app usar as bibliotecas GTK do sistema.
unset GTK_PATH GTK_EXE_PREFIX GTK_MODULES GSETTINGS_SCHEMA_DIR GIO_MODULE_DIR
unset GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE GTK_IM_MODULE_FILE LOCPATH

PYTHON=$(command -v python3 || echo /usr/bin/python3)
if ! "$PYTHON" -c 'import gi; gi.require_version("Gtk", "4.0")' 2>/dev/null; then
  MSG="O My Games precisa do GTK 4 para Python. Instale com: sudo apt install python3-gi gir1.2-gtk-4.0"
  command -v zenity >/dev/null && zenity --error --text="$MSG" 2>/dev/null || echo "$MSG" >&2
  exit 1
fi
exec "$PYTHON" "$APP_DIR/app/main.py" "$@"
