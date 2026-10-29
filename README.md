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
(18% del serbatoio, ricavato da un rabbocco ogni ~40 giorni e da ~0,24 rigenerazioni al giorno): i banner lo dicono ("stima provvisoria"). Cicli con troppo
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

**Uso previsto.** La frequenza delle rigenerazioni e i litri al giorno usati nelle
previsioni sono la media degli **ultimi 12 mesi** dello storico dell'apparecchio
(al massimo fino alla messa in servizio, vedi `messa_in_servizio` in `config.json`),
riletta al piu' una volta al giorno: una media su 30 giorni oscilla di circa sei
giorni per una sola rigenerazione in piu' o in meno. Se la lettura lunga fallisce si
ripiega sui 30 giorni. La pagina indica su quanti giorni e' calcolata.

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

**Riletture senza Bluetooth: `analisi-storico`.** I CSV si rileggono quando si vuole,
senza fermare il servizio e senza toccare l'apparecchio:

```
python bwt_perla.py analisi-storico --da 2023-03-22
```
`--da` e' la data di **messa in servizio**: i giorni precedenti sono a zero perche'
l'apparecchio non era installato (non c'era nulla da registrare), e vanno esclusi
dalle medie. La data si salva in `config.json` e le volte dopo non serve piu'
indicarla (vale anche per `storico`). Il comando stampa media e mediana sui giorni
in servizio e sui soli giorni con consumo, i giorni a zero (assenze), le
rigenerazioni (una ogni N giorni), la media per giorno della settimana, i totali
per anno e per mese dell'anno, la media per giorno della settimana **anno per anno** (serve a controllare la datazione: il picco del fine settimana deve stare su sab/dom in tutti gli anni), i giorni con due o piu' rigenerazioni e con interruzioni di corrente (interventi, prove di avvio) e il **profilo orario** (litri medi per ora, feriali
contro fine settimana, sugli ultimi 30 giorni a quarti d'ora).

**Consumi e calendario: `analisi-calendario`.** Confronta i totali giornalieri con un
calendario italiano calcolato in locale (nessun accesso a internet): le festivita'
nazionali fisse, la Pasquetta (dalla formula della Pasqua) e, a richiesta, il
patrono della citta' (`--patrono MM-GG`, per esempio `12-07`):

```
python bwt_perla.py analisi-calendario --da 2023-03-22 --patrono 12-07
```
I giorni sono divisi in **feriale, ponte probabile** (feriale tra due giorni non
lavorativi), **festivo infrasettimanale, sabato, domenica**. Per ognuna stampa
giorni, giorni a zero, media, mediana e deviazione standard. Poi misura quanto i
totali separano lavorativi e non lavorativi: **AUC** (0,50 = nessuna separazione,
1,00 = perfetta) e l'accuratezza della migliore soglia sui litri; i giorni a zero
(assenze) sono esclusi dal confronto perche' non dicono se il giorno era
lavorativo. Elenca anche i feriali con consumo da festivo e i non lavorativi con
consumo da feriale (ferie, smart working, fuori casa). Con le sole medie di un
nucleo familiare tipico ci si aspetta una correlazione visibile ma non netta
(AUC intorno a 0,7): per distinguere meglio servono i profili orari.

**Limiti.** Gli orari dei quarti sono approssimati come nella registrazione
normale; i giorni sono a multipli di 10 L; piu' si va indietro piu' la datazione si
fida dell'ipotesi di slot da 24 ore senza interruzioni; non so quanti giorni
validi contenga davvero un apparecchio vecchio. Non e' stato ancora provato su
un apparecchio vero.

**Indizi di perdita: `analisi-perdite` (sperimentale).** Una perdita e' un filo
d'acqua che non si ferma mai, quindi si cerca dove la casa dovrebbe essere ferma.
Il comando rilegge i CSV senza Bluetooth e mette insieme tre indizi:

```
python bwt_perla.py analisi-perdite                  # rapporto
python bwt_perla.py analisi-perdite --giorno oggi    # i quarti d'ora di un giorno (oggi, ieri, AAAA-MM-GG)
```
- **Notte**: quanti dei 16 quarti d'ora tra le 01:00 e le 05:00 hanno consumo.
  Sospetta da 8, continua da 12. Le notti con una rigenerazione o un'interruzione
  di corrente si scartano (la rigenerazione usa acqua di notte).
- **Silenzio**: la striscia piu' lunga di quarti d'ora a zero nella giornata. Con
  una perdita si accorcia.
- **Pavimento**: il consumo minimo su 14 giorni, sui soli giorni senza
  rigenerazione, confrontato con tutta la storia (ultimo giorno escluso, puo' essere
  parziale). Un pavimento che sale e non scende piu' e' l'indizio piu' lento ma piu'
  affidabile, e usa i 5 anni dell'apparecchio.

Avvisa solo se **3 notti valide di fila** sono anomale, oppure se il pavimento e'
il piu' alto mai registrato. Gli stessi indizi compaiono nella **pagina web**: card «Perdite
(sperimentale)», una riga di quadratini con le ultime notti (verde / arancione /
rosso, a tratteggio le notti scartate) e un banner. Il banner e' **rosso** con 3 notti
anomale di fila («possibile perdita»), **arancione** con l'ultima notte anomala o con il
pavimento al massimo storico; il simbolo 🔴/🟠 compare anche nel titolo della scheda.
Il dato e' in `/api/riepilogo` alla chiave `perdite`; un errore nel calcolo non toglie la
pagina, e la card lo mostra. Gli avvisi fuori dalla pagina non ci sono ancora. E' un indizio, non una diagnosi: una perdita piccola
(sotto circa 1 L/h) si perde nel rumore dei quarti d'ora in litri interi, e un
uso notturno che non ricordi sembra una perdita. Il controllo vero e' il
contatore di casa con tutto fermo. `--giorno` serve a vedere la «firma» di un
elettrodomestico: per esempio una lavastoviglie ha un lavaggio con picchi
di consumo seguito da un'asciugatura a zero, che il comando elenca tra le strisce
di almeno un'ora senza consumo.

## Pagina web (rete locale)

```
python web.py                 # solo pagina, senza ricerca Bluetooth
```
Mostra sale stimato, autonomia, consumo di oggi, rigenerazioni e i grafici per
giorno (ultimi 30 giorni), la scheda **Rigenerazioni** (quante nell'ultimo mese, quando e' stata l'ultima,
con che intervallo tipico, quando e' attesa la prossima e i litri degli ultimi 7 giorni), per ora (a scelta: ultime 24 ore oppure ultimi 7
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

**Storico nei grafici.** Se in `~/bwt-data/` ci sono i file di `storico` (gli ultimi
per nome), i grafici li usano per il periodo precedente alla registrazione: i
quarti d'ora si agganciano a quelli registrati dal vivo per **indice del registro**
(lo stesso quarto ha lo stesso indice nei due file, quindi niente duplicati ne'
scarti d'orario) e dove c'e' il dato dal vivo vince quello. Un giorno con tutti i
96 quarti si somma dai quarti; un giorno a meta' prende il valore dello storico
giornaliero, che da' anche le rigenerazioni del giorno. Per aggiornarlo basta
rieseguire `storico`; il grafico per ora resta su 7 giorni e quello per giorno su
30. Se la registrazione parte molto dopo l'importazione, nel mezzo manca il dato.

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

**Descrizione dei valori.** Passando il mouse su una barra dei grafici (o sulle
notti della card «Perdite») compare una finestrella (*tooltip*) con data e litri; da
telefono basta **toccare** la barra (si chiude con un secondo tocco, toccando altrove
o dopo 4 secondi). L'area sensibile e' a tutta altezza, cosi' si centra anche una barra
bassa o sottile.

**Orari dei quarti d'ora.** L'ora di ogni quarto si assegna alla lettura (ultimo
multiplo di 15 minuti), ma il contatore dell'apparecchio cambia quarto con un piccolo
scarto rispetto al Raspberry: capitava un quarto d'ora «saltato» nella tabella (o due con
la stessa ora, e allora uno spariva). Ora gli orari seguono l'indice assoluto del
registro, che e' consecutivo: si corregge solo chi si discosta di un quarto dallo scarto
abituale; scarti piu' grandi (registrazione ferma a lungo) restano come sono.
`quarti.csv` non viene modificato, la correzione si applica alla lettura.

## Aggiornare il Raspberry (`aggiorna.sh`)

Un `git pull` non ricarica il servizio che sta girando: resta in memoria il
codice vecchio finche' non lo riavvii. `aggiorna.sh` fa tutto in sicurezza:

```
cd ~/bwt-perla-client
./aggiorna.sh --controlla      # dice se serve aggiornare o riavviare (uscita 3) o e' tutto a posto (0)
./aggiorna.sh                  # scarica, prova, riavvia
./aggiorna.sh --solo-riavvia   # riavvia soltanto, senza scaricare
```

- scarica solo in *fast-forward* da `origin/main`: niente merge automatici;
- si ferma, senza toccare nulla, se non sei su `main`, se hai modifiche locali
  non salvate o commit che su GitHub non esistono;
- esegue i test **prima** del riavvio: se falliscono il servizio resta com'e' e
  ti indica come tornare indietro;
- riavvia anche se il codice e' gia' aggiornato ma il servizio e' piu' vecchio
  dei file (pull fatto e riavvio dimenticato);
- dopo il riavvio verifica che il servizio sia attivo e che la pagina risponda.

Variabili: `SERVIZIO` (predefinito `bwt-perla`), `PORTA` (`8765`), `PYTHON`.

## Stato

Lettura e decodifica verificate su hardware (Raspberry Pi 3B + addolcitore
AQA Perla, firmware A22X V1.18). Non calcola ancora consumo di oggi/ieri con
datazione esatta.
