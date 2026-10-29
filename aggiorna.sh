#!/usr/bin/env bash
# Aggiorna il programma sul Raspberry in modo sicuro: scarica da GitHub (solo
# fast-forward), esegue i test e riavvia il servizio. Se il codice e' gia'
# aggiornato ma il servizio e' piu' vecchio del codice (si e' dimenticato il
# riavvio dopo un pull), lo riavvia comunque.
#
#   ./aggiorna.sh               aggiorna, prova e riavvia
#   ./aggiorna.sh --controlla   solo controlla: dice se serve aggiornare o riavviare
#   ./aggiorna.sh --solo-riavvia  riavvia il servizio senza scaricare nulla
#
# Variabili: SERVIZIO (predefinito bwt-perla), PORTA della pagina (predefinita 8765),
# PYTHON (predefinito .venv/bin/python se c'e', altrimenti python3).
#
# Non tocca mai le modifiche locali non salvate, non cambia ramo e non crea merge:
# in questi casi si ferma e dice cosa fare. Licenza GPLv3, come il resto del progetto.
set -euo pipefail

SERVIZIO="${SERVIZIO:-bwt-perla}"
PORTA="${PORTA:-8765}"
CONTROLLA=0
SOLO_RIAVVIA=0

usa() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }
errore() { printf 'ERRORE: %b\n' "$*" >&2; exit 1; }
info() { printf '• %s\n' "$*"; }

for argomento in "$@"; do
    case "$argomento" in
        --controlla) CONTROLLA=1 ;;
        --solo-riavvia) SOLO_RIAVVIA=1 ;;
        -h | --help) usa; exit 0 ;;
        *) printf 'Opzione sconosciuta: %s\n' "$argomento" >&2; usa >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")"
git rev-parse --is-inside-work-tree > /dev/null 2>&1 || errore "questa cartella non e' un repository git."

# ── prima di tutto: lo stato locale deve essere pulito e su main ──────────────
RAMO="$(git rev-parse --abbrev-ref HEAD)"
[ "$RAMO" = "main" ] || errore "sei sul ramo '$RAMO', non su 'main'.\n  Torna su main con: git checkout main"
MODIFICHE="$(git status --porcelain)"
[ -z "$MODIFICHE" ] || errore "ci sono modifiche locali non salvate; non le tocco:\n$MODIFICHE\n  Salvale o scartale e riprova."

git fetch -q origin main || errore "git fetch non riuscito (rete?)."
# a sinistra i commit solo su GitHub (siamo indietro), a destra quelli solo qui (siamo avanti)
read -r DIETRO AVANTI < <(git rev-list --left-right --count origin/main...HEAD)
[ "$AVANTI" -eq 0 ] || errore "qui ci sono $AVANTI commit che su GitHub non esistono:\n$(git log --oneline origin/main..HEAD)\n  Non faccio il pull per non creare un merge."

# ── servizio: esiste? e' piu' vecchio del codice? ─────────────────────────────
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo"
HA_SERVIZIO=0
systemctl cat "$SERVIZIO" > /dev/null 2>&1 && HA_SERVIZIO=1

secondi_codice() {      # il file di codice modificato piu' di recente
    git ls-files '*.py' 'web/*' | xargs -r stat -c %Y | sort -n | tail -1
}
secondi_servizio() {    # da quando il servizio e' avviato (0 se sconosciuto)
    local avvio
    avvio="$(systemctl show -p ActiveEnterTimestamp --value "$SERVIZIO" 2> /dev/null || true)"
    if [ -n "$avvio" ] && date -d "$avvio" +%s 2> /dev/null; then return 0; fi
    echo 0
}
servizio_obsoleto() {
    [ "$HA_SERVIZIO" -eq 1 ] && [ "$(secondi_servizio)" -lt "$(secondi_codice)" ]
}

info "commit locale $(git rev-parse --short HEAD), su GitHub $(git rev-parse --short origin/main)" \
     "(indietro di $DIETRO)"
if [ "$HA_SERVIZIO" -eq 0 ]; then
    info "il servizio '$SERVIZIO' non esiste su questa macchina: niente riavvio."
elif servizio_obsoleto; then
    info "il servizio e' piu' vecchio del codice: sta usando una versione precedente."
else
    info "il servizio e' in linea con il codice."
fi

if [ "$CONTROLLA" -eq 1 ]; then
    if [ "$DIETRO" -gt 0 ] || servizio_obsoleto; then
        info "serve aggiornare o riavviare: lancia ./aggiorna.sh"
        exit 3
    fi
    info "tutto in ordine."
    exit 0
fi

# ── aggiornamento ─────────────────────────────────────────────────────────────
VECCHIO="$(git rev-parse HEAD)"
if [ "$SOLO_RIAVVIA" -eq 0 ] && [ "$DIETRO" -gt 0 ]; then
    git pull -q --ff-only origin main || errore "git pull non riuscito."
    info "aggiornato da $(git rev-parse --short "$VECCHIO") a $(git rev-parse --short HEAD):"
    git log --oneline --no-decorate "$VECCHIO..HEAD" | sed 's/^/    /'
fi
NUOVO="$(git rev-parse HEAD)"
CAMBIATO=0
[ "$VECCHIO" = "$NUOVO" ] || CAMBIATO=1

if [ -n "${PYTHON:-}" ]; then PY="$PYTHON"
elif [ -x .venv/bin/python ]; then PY=".venv/bin/python"
else PY="python3"; fi

DEVE_RIAVVIARE=0
if [ "$SOLO_RIAVVIA" -eq 1 ] || [ "$CAMBIATO" -eq 1 ] || servizio_obsoleto; then DEVE_RIAVVIARE=1; fi

if [ "$DEVE_RIAVVIARE" -eq 0 ]; then
    info "gia' tutto aggiornato e servizio in linea: nulla da fare."
    exit 0
fi

if [ "$CAMBIATO" -eq 1 ] && git diff --name-only "$VECCHIO" "$NUOVO" | grep -qx 'requirements.txt'; then
    info "le dipendenze sono cambiate: le installo."
    "$PY" -m pip install -q -r requirements.txt || errore "installazione delle dipendenze non riuscita."
fi

# i test girano prima del riavvio: se falliscono il servizio resta com'e' (vecchio ma funzionante)
if [ "$SOLO_RIAVVIA" -eq 0 ]; then
    info "eseguo i test..."
    REGISTRO="$(mktemp)"
    if ! PYTHONDONTWRITEBYTECODE=1 "$PY" -m unittest discover -s tests > "$REGISTRO" 2>&1; then
        tail -n 25 "$REGISTRO" >&2
        rm -f "$REGISTRO"
        errore "i test sono falliti: NON riavvio il servizio (resta la versione che girava).\n  Per tornare al codice precedente: git reset --hard $VECCHIO"
    fi
    info "test superati: $(tail -n 3 "$REGISTRO" | grep -E '^(Ran|OK)' | tr '\n' ' ')"
    rm -f "$REGISTRO"
fi

if [ "$HA_SERVIZIO" -eq 0 ]; then
    info "fatto (nessun servizio da riavviare)."
    exit 0
fi

info "riavvio il servizio $SERVIZIO..."
$SUDO systemctl restart "$SERVIZIO"
for _ in $(seq 1 15); do
    systemctl is-active --quiet "$SERVIZIO" && break
    sleep 1
done
systemctl is-active --quiet "$SERVIZIO" \
    || errore "il servizio non e' ripartito.\n  Guarda: journalctl -u $SERVIZIO -n 30 --no-pager"

if command -v curl > /dev/null 2>&1; then
    PAGINA_OK=0
    for _ in $(seq 1 15); do
        if curl -fsS "http://localhost:$PORTA/healthz" 2> /dev/null | grep -q '^ok'; then PAGINA_OK=1; break; fi
        sleep 1
    done
    [ "$PAGINA_OK" -eq 1 ] \
        || errore "il servizio e' attivo ma la pagina non risponde su localhost:$PORTA.\n  Guarda: journalctl -u $SERVIZIO -n 30 --no-pager"
fi
info "fatto: $(git log -1 --oneline --no-decorate), servizio attivo e pagina raggiungibile."
