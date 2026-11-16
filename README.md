HUB DE EMULADORES

Este é um projeto pessoal com ideia de fazer um hub de emuladores portatil e plug and play. Estou desenvolvendo junto do Claude para reunir alguns emuladores todos em um lugar só.

## Como usar

1. Clique em **Code → Download ZIP** e extraia a pasta onde quiser (HD, pendrive, Documentos...).
2. Abra o arquivo do seu sistema:
   - **Windows:** `Abrir My Games (Windows).exe`
     (se aparecer "O Windows protegeu o computador", clique em **Mais informações → Executar assim mesmo**)
   - **Linux:** `Abrir My Games (Linux)`
3. Pronto. Os emuladores já vêm dentro da pasta e nada é instalado no computador.

As **Configurações** abrem pelo botão **Menu** (canto de cima da tela), pela tecla **M** ou pelo botão **☰** do controle.

## Seus jogos: duas opções

Na tela de cada console (PS2, N64, GameCube...):

- **Adicionar jogos**: abre a pasta `Emuladores/<Console>/Adicionar Jogos`. Copie os jogos para lá.
- **Usar pasta do PC**: escolha uma pasta do computador que já tem seus jogos (ex.: `D:\Jogos PS2`).
  O hub lê os jogos direto de lá, sem copiar nada. Para tirar uma pasta da lista, use as **Configurações**.

## BIOS

A BIOS é o "sistema" do videogame original. Só dois consoles do hub precisam dela:

| Console          | Precisa de BIOS? | Arquivo                                   | Onde colocar                     |
|------------------|------------------|-------------------------------------------|----------------------------------|
| PlayStation 2    | **Sim**          | a BIOS `.bin` (qualquer nome)             | `Emuladores/PlayStation 2/bios`  |
| Neo Geo          | **Sim**          | `neogeo.zip` (ou `aes.zip`)               | `Emuladores/Neo Geo/bios`        |
| Super Nintendo   | Não              | —                                         | —                                |
| Game Boy Advance | Não              | —                                         | —                                |
| GameCube         | Não              | —                                         | —                                |
| Nintendo 64      | Não              | —                                         | —                                |

### Como colocar

1. Abra as **Configurações** e, no cartão do console, clique no ícone de pasta ao lado do aviso da BIOS
   (**Abrir pasta da BIOS**). Ou abra a pasta `Emuladores/<Console>/bios` direto no explorador de arquivos.
2. Copie o arquivo da BIOS para dentro dessa pasta.
3. Pronto. O hub configura o emulador sozinho: não precisa abrir as configurações do PCSX2 nem do RetroArch.
   Se a BIOS estiver faltando, o hub avisa qual é quando você tenta abrir um jogo.

### Detalhes de cada console

- **PlayStation 2**: a BIOS é um arquivo `.bin` de uns 4 MB (ex.: `SCPH-70012_BIOS_V12_USA_200.BIN`).
  - Se ela veio com arquivos `.MEC` e `.NVM` de mesmo nome, copie junto (não é obrigatório).
  - Deixe só uma BIOS de PS2 na pasta. Se houver mais de uma, o hub escolhe sozinho.
- **Neo Geo**: a BIOS é um arquivo `.zip`. **Não descompacte**, e o nome precisa ser exatamente um destes:
  - `neogeo.zip` (modo fliperama): **recomendado**, funciona com todos os jogos.
  - `aes.zip` (modo console): funciona só com os jogos no formato `.neo`.

A BIOS precisa vir do seu próprio videogame, por isso ela não vem junto, igual aos jogos.
O site oficial do PCSX2 (pcsx2.net) explica como copiar a BIOS de um PlayStation 2.

## O que não vem junto

- **Jogos** e **BIOS**: têm direitos autorais, então cada pessoa usa os seus (veja [BIOS](#bios)).
- **Saves**: são de cada pessoa. Ficam em `Emuladores/<Console>/dados` (os emuladores de GBA e
  Super Nintendo salvam ao lado do próprio jogo). Para levar seu progresso para outro computador,
  copie essas pastas.

O emulador de Nintendo 64 do Linux tem mais de 100 MB (limite do GitHub), por isso fica guardado em
partes (`.parte1`, `.parte2`). O hub junta as partes sozinho na primeira vez que precisa.
