#!/bin/sh
# Recompila os arquivos para clicar, na raiz do hub:
#   "Abrir My Games"      (Linux)   — precisa do gcc
#   "Abrir My Games.exe"  (Windows) — precisa do gcc-mingw-w64-x86-64 (sudo apt install gcc-mingw-w64-x86-64)
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT="$DIR/../.."

gcc -O2 -static -s -o "$ROOT/Abrir My Games" "$DIR/abrir.c" && echo "Pronto: Abrir My Games (Linux)"

if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
  python3 "$DIR/gerar-icone.py" >/dev/null
  # O ícone e as informações do arquivo entram no .exe pelo arquivo de recursos (.rc).
  (cd "$DIR" && x86_64-w64-mingw32-windres abrir-windows.rc -O coff -o abrir-windows.res) &&
  x86_64-w64-mingw32-gcc -O2 -s -municode -mwindows -static \
    -o "$ROOT/Abrir My Games.exe" "$DIR/abrir-windows.c" "$DIR/abrir-windows.res" &&
  rm -f "$DIR/abrir-windows.res" &&
  echo "Pronto: Abrir My Games.exe (Windows)"
else
  echo "Para gerar o .exe do Windows: sudo apt install gcc-mingw-w64-x86-64 e rode este script de novo."
fi
