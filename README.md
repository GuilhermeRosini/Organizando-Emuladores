HUB DE EMULADORES

Este é um projeto pessoal com ideia de fazer um hub de emuladores portatil e plug and play. Estou desenvolvendo junto do Claude para reunir alguns emuladores todos em um lugar só.

## Como usar

1. Clique em **Code → Download ZIP** e extraia a pasta onde quiser (HD, pendrive, Documentos...).
2. Abra o arquivo do seu sistema:
   - **Windows:** `Abrir My Games (Windows).exe`
     (se aparecer "O Windows protegeu o computador", clique em **Mais informações → Executar assim mesmo**)
   - **Linux:** `Abrir My Games (Linux)`
3. Pronto. Os emuladores já vêm dentro da pasta e nada é instalado no computador.

## Seus jogos: duas opções

Na tela de cada console (PS2, N64, GameCube...):

- **Adicionar jogos**: abre a pasta `Emuladores/<Console>/Adicionar Jogos`. Copie os jogos para lá.
- **Usar pasta do PC**: escolha uma pasta do computador que já tem seus jogos (ex.: `D:\Jogos PS2`).
  O hub lê os jogos direto de lá, sem copiar nada. Para tirar uma pasta da lista: **Menu → Configurações**.

## O que não vem junto

- **Jogos** e **BIOS**: têm direitos autorais, então cada pessoa usa os seus.
  As BIOS (PlayStation 2 e Neo Geo) ficam em `Emuladores/<Console>/bios` (ou **Menu → Configurações → pasta da BIOS**).
- **Saves**: são de cada pessoa. Ficam em `Emuladores/<Console>/dados` (os emuladores de GBA e
  Super Nintendo salvam ao lado do próprio jogo). Para levar seu progresso para outro computador,
  copie essas pastas.

O emulador de Nintendo 64 do Linux tem mais de 100 MB (limite do GitHub), por isso fica guardado em
partes (`.parte1`, `.parte2`). O hub junta as partes sozinho na primeira vez que precisa.
