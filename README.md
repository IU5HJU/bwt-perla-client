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
python bwt_perla.py serve                       # registrazione + pagina web insieme
```

Prima di collegarti chiudi l'app BWT e disattiva il Bluetooth sugli altri
telefoni: con una connessione attiva l'addolcitore non è più visibile.
Il segnale è debole (~ -80 dBm): tieni l'adattatore entro pochi metri. La radio
dell'apparecchio ha poca portata di suo: `log` riprova una lettura fallita 3 volte,
a 30 secondi l'una dall'altra, prima di rimandarla al ciclo successivo.

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

Tra due rabbocchi si conoscono i litri addolciti, le rigenerazioni avvenute e i kg
rimessi: se ne ricavano il **consumo reale di sale** e la **soglia di riserva**,
cioe' il livello a cui rabbocchi davvero.

Il consumo si misura in **grammi per litro**: il manuale spiega che la
rigenerazione e' proporzionale alla resina esaurita (rigenera solo la parte
consumata, al massimo ogni 7 giorni), quindi il sale usato segue i litri e non il
numero di rigenerazioni. Finche' non c'e' un ciclo misurato si usa il valore
dichiarato dall'apparecchio per rigenerazione (1200 g) e una riserva provvisoria
(10% del serbatoio, ricavato da un rabbocco ogni ~40 giorni): i banner lo dicono ("stima provvisoria"). Cicli con troppo
pochi litri o rigenerazioni, o con un consumo incoerente (per esempio un kg
digitato male), vengono scartati e segnalati. Lo stato del sale e' `ok`, `avviso`
(riserva entro 7 giorni) o `riserva`: l'esaurimento non e' mai lo scenario previsto.

### Dal manuale (AQA perla 5 C - 30 C)

Valori del manuale svizzero-tedesco/francese. La targhetta di questo apparecchio
dice **AQAPERLA 5 BIO PR IT** (codice B00449961IT, 2021, costruito in Francia):
una variante vicina ma non identica, quindi i numeri sotto sono indicativi.

- Modello piu' piccolo (**5 C**): capacita' 32 °f x m3, serbatoio del sale max
  13 kg, 20 g di sale per °f x m3, 1,8 l di acqua di risciacquo per °f x m3.
  Via Bluetooth l'apparecchio dichiara 14 kg: i due valori non coincidono.
- La rigenerazione e' proporzionale alla resina esaurita (al massimo ogni 7 giorni).
- Il fondo del serbatoio deve restare sempre coperto di sale non sciolto.
- Per gli addolcitori con controllo del sale via app, dopo ogni rabbocco *la
  quantita' di sale va inserita nell'app*: e' per questo che il livello letto via
  Bluetooth resta a 0 se non lo scrive nessuno.

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

## Un solo comando: `serve`

```
python bwt_perla.py serve        # poi apri http://IP-DEL-RASPBERRY:8765
```
Fa insieme la registrazione e la pagina web, senza altri strumenti. Al primo
avvio la pagina chiede di scegliere l'addolcitore: **Impostazioni > Cerca
addolcitore** mostra i dispositivi Bluetooth vicini (gli addolcitori BWT in
alto), **Verifica** si collega in sola lettura e mostra firmware, serbatoio e
sale per rigenerazione, **Usa questo addolcitore** salva la scelta in
`config.json`. La registrazione la rilegge a ogni ciclo, quindi non servono
riavvii. Un solo processo evita che scansione e lettura usino l'antenna insieme.

L'addolcitore si riconosce dal dato del costruttore dell'annuncio Bluetooth
(iBeacon con la scritta `BestWaterTechno`), verificato sull'apparecchio in uso.
Provato solo su **AQAPERLA 5 BIO PR IT, firmware A22X V1.18**: altri modelli o
firmware potrebbero non funzionare; la verifica avvisa se il firmware non e'
tra quelli provati.

Per l'avvio al boot: `deploy/bwt-perla.service` (un solo servizio). Chi usa
gia' i due servizi separati puo' passare cosi':

```
sudo systemctl disable --now bwt-perla-log bwt-perla-web
sudo cp deploy/bwt-perla.service /etc/systemd/system/   # poi sostituisci UTENTE
sudo systemctl daemon-reload
sudo systemctl enable --now bwt-perla
```
I dati in `~/bwt-data/` restano quelli. Se non scegli l'addolcitore dalla
pagina, puoi indicarlo nel servizio: `bwt_perla.py serve 03:12:00:2E:00:59`.

## Storico dell'apparecchio (`storico`)

L'apparecchio conserva i litri in due registri circolari: **30 giorni a quarti
d'ora** (al litro) e fino a **~5 anni a giorni interi** (a multipli di 10 litri,
con le rigenerazioni del giorno). Il comando li legge una volta, in sola lettura:

```
sudo systemctl stop bwt-perla        # stessa antenna della registrazione
python bwt_perla.py storico          # qualche minuto; --giorni N per limitarlo
sudo systemctl start bwt-perla
```

Scrive in `~/bwt-data/` `storico_giorni_AAAAMMGG.csv` (indice, inizio dello slot,
data, litri, rigenerazioni, blackout) e `storico_quarti_AAAAMMGG.csv`, e stampa
media e mediana, i giorni a zero e la **media per giorno della settimana**.

**Come si datano i giorni.** Il giorno dell'apparecchio non comincia a mezzanotte
ma a un'ora sua, e l'apparecchio non ha un orologio affidabile. Negli ultimi 30
giorni i due registri contengono gli stessi litri: si cerca l'ora di cambio giorno
per cui i giorni interi coincidono meglio con la somma dei quarti d'ora, e da li'
si etichetta tutto lo storico (ogni giorno dura 24 ore; la data e' quella
dell'inizio dello slot, dove cade la sera). Il comando dice se la calibrazione e'
affidabile o incerta. Se l'apparecchio cambia giorno in piena notte, quando non si
usa acqua, l'ora esatta non si ricava ma l'intervallo si, e le date non cambiano.

**Limiti.** Gli orari dei quarti sono approssimati come nella registrazione
normale; i giorni sono a multipli di 10 L; piu' si va indietro piu' la datazione si
fida dell'ipotesi di slot da 24 ore senza interruzioni; non so quanti giorni
validi contenga davvero un apparecchio vecchio. Non e' stato ancora provato su
un apparecchio vero.

## Pagina web (rete locale)

```
python web.py                 # solo pagina, senza ricerca Bluetooth
```
Mostra sale stimato, autonomia, consumo di oggi, rigenerazioni e i grafici per
giorno (ultimi 30 giorni), per ora (a scelta: ultime 24 ore oppure ultimi 7
giorni, con il totale del periodo) e del sale nel tempo. Legge i CSV di `~/bwt-data` (opzione
`--data-dir`) e si aggiorna da sola ogni minuto; avvisa se la registrazione e'
ferma da piu' di 45 minuti. Solo libreria standard (la ricerca Bluetooth di
`serve` usa `bleak`), nessun accesso a internet.

**Icona sulla schermata Home.** Dal telefono, aprendo la pagina:
- Android (Chrome): menu ⋮ > **Aggiungi a schermata Home** (o «Installa app»);
- iPhone/iPad (Safari): Condividi > **Aggiungi alla schermata Home**.

Appare l'icona dell'addolcitore (una goccia con dei cristalli di sale, disegnata
per questo progetto; non e' il logo BWT, che e' un marchio loro). Se avevi gia'
aggiunto un collegamento prima, l'icona vecchia resta: toglilo e aggiungilo di
nuovo. La pagina e' in `http://`, quindi il telefono la tratta come un
collegamento, non come un'app installabile. Le icone stanno in `web/icons/`
(sorgenti `.svg` e `.png`) e sono servite da un elenco chiuso di file.

La porta predefinita e' 8765 (`--port` per cambiarla): controlla prima che sia libera
con `sudo ss -tlnp`.

**Sicurezza:** la pagina legge, e le azioni che scrivono sono solo la
registrazione e l'annullamento di un rabbocco, la scelta dell'addolcitore e
la ricerca/verifica Bluetooth. Non ha password: usala solo in rete locale, non
esporla a internet. Con `--pin 1234` tutte queste azioni chiedono il PIN (i
dati restano visibili a tutti). Le richieste di scrittura accettano solo JSON
dallo stesso sito; la ricerca mostra anche dispositivi altrui, ma ci si
collega solo a quello scelto da te. Per l'avvio al boot della sola pagina:
`deploy/bwt-perla-web.service`. L'API JSON e' su `/api/riepilogo`.

## Stato

Lettura e decodifica verificate su hardware (Raspberry Pi 3B + addolcitore
AQA Perla, firmware A22X V1.18). Non calcola ancora consumo di oggi/ieri con
datazione esatta.
