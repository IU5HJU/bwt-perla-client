#!/usr/bin/env bash
# Installa BWT Perla Client su un Raspberry (Raspberry Pi OS o Debian): ambiente
# Python, dipendenze e servizio che parte da solo all'accensione. Alla fine stampa
# l'indirizzo della pagina web.
#
#   ./installa.sh                     installa (chiede conferma)
#   ./installa.sh --si                installa senza chiedere conferma
#   ./installa.sh --porta 8080        porta della pagina web (predefinita 8765, o la prima libera)
#   ./installa.sh --senza-servizio    solo programma e dipendenze, niente servizio
#   ./installa.sh --disinstalla       toglie il servizio (dati e cartella restano)
#
# Si puo' rilanciare quando si vuole: aggiorna cio' che manca e riavvia il servizio.
# Usa il tuo utente normale (non root): chiede la password solo dove serve (sudo).
# Variabili: SERVIZIO (predefinito bwt-perla), REQUISITI (predefinito requirements.txt),
# CARTELLA_UNIT (predefinita /etc/systemd/system).
# Licenza GPLv3, come il resto del progetto.
set -euo pipefail

SERVIZIO="${SERVIZIO:-bwt-perla}"
REQUISITI="${REQUISITI:-requirements.txt}"
CARTELLA_UNIT="${CARTELLA_UNIT:-/etc/systemd/system}"
UNIT="$CARTELLA_UNIT/$SERVIZIO.service"
PORTA_PREDEFINITA=8765
PAUSA="${PAUSA:-1}"          # secondi tra un controllo e l'altro dopo l'avvio
PORTA_RICHIESTA=""
SI=0
SENZA_SERVIZIO=0
DISINSTALLA=0

usa() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }
errore() { printf 'ERRORE: %b\n' "$*" >&2; exit 1; }
info() { printf '• %s\n' "$*"; }
avviso() { printf '! %s\n' "$*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --si) SI=1 ;;
        --senza-servizio) SENZA_SERVIZIO=1 ;;
        --disinstalla) DISINSTALLA=1 ;;
        --porta)
            [ $# -ge 2 ] || errore "--porta vuole un numero (per esempio --porta 8080)."
            PORTA_RICHIESTA="$2"; shift ;;
        -h | --help) usa; exit 0 ;;
        *) printf 'Opzione sconosciuta: %s\n' "$1" >&2; usa >&2; exit 2 ;;
    esac
    shift
done
if [ -n "$PORTA_RICHIESTA" ]; then
    case "$PORTA_RICHIESTA" in *[!0-9]* | "") errore "la porta deve essere un numero, non '$PORTA_RICHIESTA'." ;; esac
    { [ "$PORTA_RICHIESTA" -ge 1024 ] && [ "$PORTA_RICHIESTA" -le 65535 ]; } \
        || errore "la porta deve stare tra 1024 e 65535."
fi

cd "$(dirname "$0")"
DIR="$(pwd)"
[ "$(id -u)" -ne 0 ] || errore "non lanciarlo come root: usa il tuo utente normale (usa sudo da solo dove serve)."
[ "$(uname -s)" = "Linux" ] || errore "serve un Raspberry (o un altro Linux con Bluetooth)."
UTENTE="$(id -un)"

# ── disinstallazione ──────────────────────────────────────────────────────────
if [ "$DISINSTALLA" -eq 1 ]; then
    command -v systemctl > /dev/null 2>&1 || errore "systemctl non trovato: niente servizio da togliere."
    sudo systemctl disable --now "$SERVIZIO" > /dev/null 2>&1 || true
    sudo rm -f "$UNIT"
    sudo systemctl daemon-reload
    info "servizio '$SERVIZIO' rimosso. La cartella $DIR e i dati in ~/bwt-data non sono stati toccati."
    exit 0
fi

# ── controlli ─────────────────────────────────────────────────────────────────
command -v python3 > /dev/null 2>&1 || errore "python3 non trovato. Installalo con: sudo apt install python3"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
    || errore "serve Python 3.9 o piu' recente (hai $(python3 --version 2>&1)). Aggiorna Raspberry Pi OS."
[ -f "$REQUISITI" ] || errore "non trovo $REQUISITI: lancia lo script dalla cartella del progetto."
[ -f deploy/bwt-perla.service ] || errore "non trovo deploy/bwt-perla.service: la cartella e' incompleta."
if [ "$SENZA_SERVIZIO" -eq 0 ]; then
    command -v systemctl > /dev/null 2>&1 \
        || errore "systemctl non trovato: questo sistema non usa systemd. Usa --senza-servizio."
    command -v sudo > /dev/null 2>&1 || errore "sudo non trovato: serve per installare il servizio."
fi

porta_occupata() { (exec 3<> "/dev/tcp/127.0.0.1/$1") 2> /dev/null; }
porta_del_servizio() {
    [ -f "$UNIT" ] && grep -o -- '--port [0-9]*' "$UNIT" | head -1 | awk '{print $2}' || true
}

PORTA="$PORTA_PREDEFINITA"
if [ "$SENZA_SERVIZIO" -eq 0 ]; then
    ATTUALE="$(porta_del_servizio)"
    if [ -n "$PORTA_RICHIESTA" ]; then
        PORTA="$PORTA_RICHIESTA"
        if porta_occupata "$PORTA" && [ "$PORTA" != "$ATTUALE" ]; then
            errore "la porta $PORTA e' gia' usata da un altro programma: scegline un'altra con --porta."
        fi
    elif [ -n "$ATTUALE" ]; then
        PORTA="$ATTUALE"                     # reinstallazione: si tiene la porta di prima
    else
        PORTA=""
        for candidata in $(seq "$PORTA_PREDEFINITA" $((PORTA_PREDEFINITA + 20))); do
            if ! porta_occupata "$candidata"; then PORTA="$candidata"; break; fi
        done
        [ -n "$PORTA" ] || errore "nessuna porta libera tra $PORTA_PREDEFINITA e $((PORTA_PREDEFINITA + 20)): usa --porta."
    fi
fi

MANCANTI=()
if command -v dpkg > /dev/null 2>&1; then
    for pacchetto in python3-venv bluez rfkill curl; do
        dpkg -s "$pacchetto" > /dev/null 2>&1 || MANCANTI+=("$pacchetto")
    done
fi

# ── cosa sta per succedere ────────────────────────────────────────────────────
echo "Installazione di BWT Perla Client in $DIR (utente: $UTENTE)"
if [ ${#MANCANTI[@]} -gt 0 ]; then
    echo "  1. installa i pacchetti mancanti: ${MANCANTI[*]}"
else
    echo "  1. pacchetti di sistema: gia' presenti"
fi
echo "  2. crea l'ambiente Python (.venv) e installa le dipendenze"
if [ "$SENZA_SERVIZIO" -eq 0 ]; then
    echo "  3. installa il servizio '$SERVIZIO' (parte da solo all'accensione) sulla porta $PORTA"
else
    echo "  3. (servizio non installato: --senza-servizio)"
fi
if [ "$SI" -eq 0 ] && [ -t 0 ]; then
    read -r -p "Procedo? [S/n] " risposta
    case "$risposta" in [nN]*) echo "Annullato."; exit 0 ;; esac
fi

# ── 1. pacchetti ──────────────────────────────────────────────────────────────
if [ ${#MANCANTI[@]} -gt 0 ]; then
    if command -v apt-get > /dev/null 2>&1; then
        info "installo ${MANCANTI[*]} (chiede la password)..."
        sudo apt-get update -qq
        sudo apt-get install -y -qq "${MANCANTI[@]}"
    else
        avviso "apt-get non trovato: installa a mano ${MANCANTI[*]}."
    fi
fi

# ── 2. ambiente Python ────────────────────────────────────────────────────────
if [ ! -x .venv/bin/python ]; then
    info "creo l'ambiente Python (.venv)..."
    python3 -m venv .venv || errore "creazione di .venv non riuscita (manca python3-venv?)."
fi
info "installo le dipendenze (puo' richiedere qualche minuto sul Raspberry)..."
.venv/bin/python -m pip install -q --disable-pip-version-check -r "$REQUISITI" \
    || errore "installazione delle dipendenze non riuscita (rete?)."
.venv/bin/python bwt_perla.py selftest > /dev/null || errore "la prova interna non e' riuscita: il programma non funziona su questo sistema."
info "programma installato e prova interna superata."

if [ "$SENZA_SERVIZIO" -eq 1 ]; then
    echo
    echo "Fatto. Per avviarlo a mano:"
    echo "  cd $DIR && .venv/bin/python bwt_perla.py serve --port ${PORTA_RICHIESTA:-$PORTA_PREDEFINITA}"
    exit 0
fi

# ── 3. Bluetooth e servizio ───────────────────────────────────────────────────
if ! ls /sys/class/bluetooth 2> /dev/null | grep -q '^hci'; then
    avviso "non vedo nessun adattatore Bluetooth. Raspberry Pi 3/4/5 e Zero W ce l'hanno integrato; altrimenti serve una chiavetta USB."
fi
if ! systemctl is-active --quiet bluetooth 2> /dev/null; then
    info "accendo il servizio Bluetooth del sistema..."
    sudo systemctl enable --now bluetooth || avviso "non sono riuscito ad avviare il servizio bluetooth."
fi
if getent group bluetooth > /dev/null 2>&1 && ! id -nG "$UTENTE" | tr ' ' '\n' | grep -qx bluetooth; then
    info "aggiungo $UTENTE al gruppo bluetooth..."
    sudo usermod -aG bluetooth "$UTENTE"
fi

# i due servizi separati delle prime versioni userebbero la stessa antenna e la stessa porta
for vecchio in bwt-perla-log bwt-perla-web; do
    if systemctl cat "$vecchio" > /dev/null 2>&1; then
        info "fermo il vecchio servizio $vecchio (ora c'e' un solo servizio)."
        sudo systemctl disable --now "$vecchio" > /dev/null 2>&1 || true
    fi
done

escapa() { printf '%s' "$1" | sed 's/[&|\\]/\\&/g'; }
FILE_UNIT="$(mktemp)"
trap 'rm -f "$FILE_UNIT"' EXIT
sed -e "s|/home/UTENTE/bwt-perla-client|$(escapa "$DIR")|g" \
    -e "s|^User=UTENTE|User=$(escapa "$UTENTE")|" \
    -e "s|--port [0-9]*|--port $PORTA|" deploy/bwt-perla.service > "$FILE_UNIT"
info "installo il servizio '$SERVIZIO'..."
sudo install -m 644 "$FILE_UNIT" "$UNIT"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVIZIO" > /dev/null 2>&1
sudo systemctl restart "$SERVIZIO"

for _ in $(seq 1 15); do
    systemctl is-active --quiet "$SERVIZIO" && break
    sleep "$PAUSA"
done
systemctl is-active --quiet "$SERVIZIO" \
    || errore "il servizio non e' ripartito.\n  Guarda: journalctl -u $SERVIZIO -n 30 --no-pager"
PAGINA_OK=0
if command -v curl > /dev/null 2>&1; then
    for _ in $(seq 1 20); do
        if curl -fsS "http://localhost:$PORTA/healthz" 2> /dev/null | grep -q '^ok'; then PAGINA_OK=1; break; fi
        sleep "$PAUSA"
    done
    [ "$PAGINA_OK" -eq 1 ] \
        || errore "il servizio e' attivo ma la pagina non risponde su localhost:$PORTA.\n  Guarda: journalctl -u $SERVIZIO -n 30 --no-pager"
fi

# ── fine ──────────────────────────────────────────────────────────────────────
IP="$(hostname -I 2> /dev/null | awk '{print $1}')"
NOME="$(hostname 2> /dev/null || true)"
echo
echo "Fatto: il servizio e' attivo e parte da solo a ogni accensione."
echo
echo "Ora apri la pagina dal telefono o dal computer (sulla stessa rete Wi-Fi):"
[ -n "$IP" ] && echo "    http://$IP:$PORTA/"
[ -n "$NOME" ] && echo "    http://$NOME.local:$PORTA/"
echo
echo "Poi, una volta sola:"
echo "  1. Chiudi l'app BWT sul telefono (con l'app aperta l'addolcitore non e' visibile)."
echo "  2. In alto a destra scegli «Esperto», apri «Impostazioni» e premi «Cerca addolcitore»."
echo "  3. Premi «Verifica» sul tuo apparecchio e poi «Usa questo addolcitore»."
echo "I dati si salvano in ~/bwt-data. Per aggiornare in futuro: ./aggiorna.sh"
