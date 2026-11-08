#!/bin/sh
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# Remove variáveis do VS Code Snap para o app usar as bibliotecas GTK do Linux.
unset GTK_PATH GTK_EXE_PREFIX GTK_MODULES GSETTINGS_SCHEMA_DIR
unset GDK_PIXBUF_MODULEDIR GDK_PIXBUF_MODULE_FILE GTK_IM_MODULE_FILE LOCPATH

exec /usr/bin/python3 "$APP_DIR/jogos_organizados.py" "$@"
