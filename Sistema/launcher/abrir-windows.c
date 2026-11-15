/*
 * "Abrir My Games (Windows).exe" — o arquivo para clicar no Windows (fica na raiz do hub).
 *
 * Descobre a própria pasta e abre o hub com o Python que vem dentro dela
 * (Sistema\windows\runtime\bin\pythonw.exe), sem janela de terminal.
 * Nada é instalado no Windows: tudo roda de dentro da pasta do hub.
 *
 * O ícone do controle fica embutido no .exe (veja abrir-windows.rc).
 * Compilar no Linux: sh Sistema/launcher/compilar.sh  (precisa do pacote gcc-mingw-w64-x86-64)
 */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>

#define LONG_PATH 32768

/* Mostra um erro em português e encerra. */
static int fail(const wchar_t *message)
{
    MessageBoxW(NULL, message, L"My Games", MB_OK | MB_ICONERROR);
    return 1;
}

/* O arquivo existe? */
static int exists(const wchar_t *path)
{
    return GetFileAttributesW(path) != INVALID_FILE_ATTRIBUTES;
}

/* Pula o nome do programa na linha de comando, para repassar só os argumentos. */
static const wchar_t *arguments_after_program(const wchar_t *line)
{
    int quoted = 0;
    while (*line && (quoted || (*line != L' ' && *line != L'\t'))) {
        if (*line == L'"')
            quoted = !quoted;
        line++;
    }
    while (*line == L' ' || *line == L'\t')
        line++;
    return line;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show)
{
    (void)instance; (void)previous; (void)command_line; (void)show;
    static wchar_t root[LONG_PATH], python[LONG_PATH], script[LONG_PATH], bin[LONG_PATH];
    static wchar_t path[LONG_PATH], line[LONG_PATH];

    /* Pasta do hub = pasta onde este .exe está. */
    DWORD size = GetModuleFileNameW(NULL, root, LONG_PATH);
    if (size == 0 || size >= LONG_PATH)
        return fail(L"Não consegui descobrir a pasta do My Games.");
    wchar_t *slash = wcsrchr(root, L'\\');
    if (slash)
        *slash = L'\0';

    _snwprintf(bin, LONG_PATH, L"%ls\\Sistema\\windows\\runtime\\bin", root);
    _snwprintf(python, LONG_PATH, L"%ls\\pythonw.exe", bin);
    _snwprintf(script, LONG_PATH, L"%ls\\Sistema\\app\\main.py", root);
    if (!exists(script))
        return fail(L"Não encontrei Sistema\\app\\main.py.\nA pasta Sistema precisa ficar ao lado deste arquivo.");
    if (!exists(python))
        return fail(L"Não encontrei o runtime do Windows (Sistema\\windows\\runtime).\n"
                    L"No Linux, rode: python3 Sistema/windows/montar-runtime.py");

    /* As DLLs do GTK ficam na pasta bin do runtime: ela vai na frente do PATH. */
    DWORD used = GetEnvironmentVariableW(L"PATH", path, LONG_PATH);
    if (used == 0 || used >= LONG_PATH)
        path[0] = L'\0';
    static wchar_t new_path[LONG_PATH];
    _snwprintf(new_path, LONG_PATH, L"%ls;%ls", bin, path);
    SetEnvironmentVariableW(L"PATH", new_path);
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");        /* acentos em caminhos e arquivos */
    SetEnvironmentVariableW(L"PYTHONNOUSERSITE", L"1");  /* ignora pacotes Python do usuário */
    SetEnvironmentVariableW(L"PYTHONHOME", NULL);
    SetEnvironmentVariableW(L"PYTHONPATH", NULL);

    _snwprintf(line, LONG_PATH, L"\"%ls\" \"%ls\" %ls", python, script, arguments_after_program(GetCommandLineW()));

    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    if (!CreateProcessW(python, line, NULL, NULL, FALSE, 0, NULL, root, &startup, &process))
        return fail(L"Não consegui abrir o My Games (falha ao iniciar o Python do runtime).");
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
