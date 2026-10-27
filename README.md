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
```

Prima di collegarti chiudi l'app BWT e disattiva il Bluetooth sugli altri
telefoni: con una connessione attiva l'addolcitore non è più visibile.
Il segnale è debole (~ -80 dBm): tieni l'adattatore entro pochi metri.

## Stato

Decodifica verificata solo con dati sintetici; non ancora provata su hardware.
Non calcola ancora consumo di oggi/ieri, contatore cumulativo, autonomia sale.
