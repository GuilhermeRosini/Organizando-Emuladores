#!/bin/sh
# Recompila o executável "Abrir My Games" na raiz do hub.
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
gcc -O2 -static -s -o "$DIR/../../Abrir My Games" "$DIR/abrir.c" && echo "Pronto: Abrir My Games"
