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

## Rabbocco del sale e stima

Il modello in uso riporta sempre 0 g di sale (anche la vecchia app BWT
mostrava 0), quindi il livello e' **stimato** partendo dai rabbocchi:
`kg dopo il rabbocco - rigenerazioni dal rabbocco x consumo per rigenerazione`.

Ogni rabbocco e' una misura reale. Si registra dal pulsante **Rabbocco sale**
della pagina web, oppure da riga di comando:

```
python bwt_perla.py salt-refill 14 --aggiunti 9.5 --livello riserva
```
- `14`: kg nel serbatoio *dopo* il rabbocco (nella pagina: "riempito al massimo");
- `--aggiunti`: kg versati, serve alla calibrazione;
- `--livello`: com'era il sale prima: `riserva` (acqua di salamoia visibile sul
  fondo, il momento in cui di solito si rabbocca), `abbondante`, `sconosciuto`;
- `--used-regens N`: rigenerazioni gia' avvenute, se il rabbocco e' stato fatto
  prima di registrarlo.

Tra due rabbocchi si conoscono le rigenerazioni avvenute e i kg rimessi: se ne
ricavano il **consumo reale per rigenerazione** (all'inizio vale quello
dichiarato dall'apparecchio, 1200 g) e la **soglia di riserva**, cioe' il livello
a cui rabbocchi davvero (all'inizio provvisoria, 25% del serbatoio). Cicli con
meno di 3 rigenerazioni o con un consumo incoerente (fuori da 0,3-3 volte il
valore dichiarato, per esempio un kg digitato male) vengono scartati e
segnalati. Lo stato del sale e' `ok`, `avviso` (riserva entro 7 giorni) o
`riserva`: l'esaurimento non e' mai lo scenario previsto.

**Notifica:** quando mancano 7 giorni alla riserva la pagina mostra un banner
arancione con la data prevista, e uno rosso, con il giorno d'inizio, quando il
sale e' in riserva; il titolo della scheda del browser prende 🟠 o 🔴. Il
giorno d'inizio lo memorizza il servizio di registrazione (`state.json`). Non
ci sono notifiche del browser a pagina chiusa: richiedono HTTPS. Il 100% e' il
livello dell'ultimo rabbocco ("kg dopo"), non la capacita' dichiarata
dall'apparecchio.

I rabbocchi stanno in `rabbocchi.json`, a parte, cosi' non vanno persi se li
registri mentre il servizio di registrazione e' in funzione. Un rabbocco sbagliato
si annulla dalla pagina (Storico rabbocchi) o a mano modificando il file.

## Registrazione

In `~/bwt-data/`:
- `cicli.csv`: una riga per lettura (litri, rigenerazioni, sale stimato,
  autonomia, BROADCAST grezzo per studiare il campo del sale);
- `quarti.csv`: ogni quarto d'ora letto (orario *approssimato*, l'orologio
  dell'apparecchio non e' noto);
- `state.json`: contatori;
- `rabbocchi.json`: i rabbocchi di sale.

Il primo avvio non conta il passato: parte da "adesso". Per l'avvio automatico
al boot vedi `deploy/bwt-perla-log.service`.

Test: `python -m unittest discover -s tests`.

## Pagina web (rete locale)

```
python web.py                 # poi apri http://IP-DEL-RASPBERRY:8765
```
Mostra sale stimato, autonomia, consumo di oggi, rigenerazioni e i grafici per
giorno, per ora e del sale nel tempo. Legge i CSV di `~/bwt-data` (opzione
`--data-dir`) e si aggiorna da sola ogni minuto; avvisa se la registrazione e'
ferma da piu' di 45 minuti. Solo libreria standard, nessun accesso a internet.

La porta predefinita e' 8765 (`--port` per cambiarla): controlla prima che sia libera
con `sudo ss -tlnp`.

**Sicurezza:** la pagina legge soltanto, salvo registrare o annullare un
rabbocco. Non ha password: usala solo in rete locale, non esporla a internet.
Con `--pin 1234` registrare e annullare chiedono il PIN (i dati restano visibili a
tutti). Le richieste di scrittura accettano solo JSON dallo stesso sito. Per l'avvio al boot: `deploy/bwt-perla-web.service`.
L'API JSON e' su `/api/riepilogo`.

## Stato

Lettura e decodifica verificate su hardware (Raspberry Pi 3B + addolcitore
AQA Perla, firmware A22X V1.18). Non calcola ancora consumo di oggi/ieri con
datazione esatta.
