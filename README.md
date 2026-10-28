# bwt-perla-client

Client BLE standalone (Python + [bleak](https://github.com/hbldh/bleak)) per
addolcitori **BWT AQA Perla**, senza Home Assistant.

Il protocollo è ricostruito dal progetto
[Micka41/bwt-aqa-perla-ble](https://github.com/Micka41/bwt-aqa-perla-ble)
(GPL-3.0); questo repository ne eredita la licenza.

## Uso

```
pip install -r requirements.txt
python bwt_perla.py scan                  # trova l'addolcitore (servizio D973F2E0-...)
python bwt_perla.py read 03:12:00:2E:00:59  # stato sale + ultimi consumi
python bwt_perla.py dump <MAC>            # trame grezze per debug
python bwt_perla.py selftest
python bwt_perla.py log 03:12:00:2E:00:59       # lettura ogni 15 min -> ~/bwt-data/
python bwt_perla.py salt-refill 14              # registra un rabbocco di 14 kg
python bwt_perla.py status                      # ultima riga registrata
```

Prima di collegarti chiudi l'app BWT e disattiva il Bluetooth sugli altri
telefoni: con una connessione attiva l'addolcitore non è più visibile.
Il segnale è debole (~ -80 dBm): tieni l'adattatore entro pochi metri.

## Registrazione e stima del sale

Il modello in uso riporta sempre 0 g di sale (anche la vecchia app BWT mostrava
0), quindi il residuo e' **stimato**:
`kg messi - rigenerazioni dal rabbocco x g per rigenerazione`.
Dopo ogni rabbocco lancia `salt-refill KG`; se il rabbocco e' gia' avvenuto da un
po', `salt-refill KG --used-regens N` tiene conto delle N rigenerazioni passate.
Il parametro globale `--data-dir` (prima del comando) cambia la cartella dei dati.

In `~/bwt-data/`:
- `cicli.csv`: una riga per lettura (litri, rigenerazioni, sale stimato,
  autonomia, BROADCAST grezzo per studiare il campo del sale);
- `quarti.csv`: ogni quarto d'ora letto (orario *approssimato*, l'orologio
  dell'apparecchio non e' noto);
- `state.json`: contatori e rabbocco.

Il primo avvio non conta il passato: parte da "adesso". Per l'avvio automatico
al boot vedi `deploy/bwt-perla-log.service`.

Test: `python -m unittest discover -s tests`.

## Pagina web (rete locale)

```
python web.py                 # poi apri http://IP-DEL-RASPBERRY:8080
```
Mostra sale stimato, autonomia, consumo di oggi, rigenerazioni e i grafici per
giorno, per ora e del sale nel tempo. Legge i CSV di `~/bwt-data` (opzione
`--data-dir`) e si aggiorna da sola ogni minuto; avvisa se la registrazione e'
ferma da piu' di 45 minuti. Solo libreria standard, nessun accesso a internet.

**Sicurezza:** sola lettura e senza password. Usala solo in rete locale, non
esporla a internet. Per l'avvio al boot: `deploy/bwt-perla-web.service`.
L'API JSON e' su `/api/riepilogo`.

## Stato

Lettura e decodifica verificate su hardware (Raspberry Pi 3B + addolcitore
AQA Perla, firmware A22X V1.18). Non calcola ancora consumo di oggi/ieri con
datazione esatta.
