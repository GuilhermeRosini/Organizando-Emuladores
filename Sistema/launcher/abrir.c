/*
 * "Abrir My Games (Linux)" — o arquivo para clicar no Linux (fica na raiz do hub).
 * Descobre a própria pasta e executa Sistema/Iniciar My Games.sh.
 * Compilar: sh Sistema/launcher/compilar.sh
 */
#define _GNU_SOURCE
#include <limits.h>
#include <libgen.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    char exe[PATH_MAX];
    ssize_t size = readlink("/proc/self/exe", exe, sizeof(exe) - 1);
    if (size < 0) {
        perror("readlink");
        return 1;
    }
    exe[size] = '\0';

    char script[PATH_MAX];
    snprintf(script, sizeof(script), "%s/Sistema/Iniciar My Games.sh", dirname(exe));
    if (access(script, R_OK) != 0) {
        fprintf(stderr, "Não encontrei %s. A pasta Sistema precisa ficar ao lado deste arquivo.\n", script);
        return 1;
    }

    char *args[argc + 3];
    args[0] = "sh";
    args[1] = script;
    for (int i = 1; i < argc; i++)
        args[i + 1] = argv[i];
    args[argc + 1] = NULL;
    execv("/bin/sh", args);
    perror("execv");
    return 1;
}
