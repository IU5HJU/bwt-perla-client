"""Configurazione salvata in `config.json` (cartella dei dati).

Per ora contiene solo l'indirizzo Bluetooth dell'addolcitore, scelto dalla
pagina web (Impostazioni) invece che scritto nei file di sistema.

Licenza GPLv3, come il resto del progetto.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CONFIG_JSON = "config.json"
MAC_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")


def valida_mac(mac) -> str:
    """Indirizzo Bluetooth in maiuscolo; ValueError se non e' nel formato
    AA:BB:CC:DD:EE:FF (su Linux/Raspberry gli indirizzi sono MAC)."""
    m = str(mac or "").strip().upper()
    if not MAC_RE.match(m):
        raise ValueError("indirizzo Bluetooth non valido (atteso AA:BB:CC:DD:EE:FF)")
    return m


def carica_config(data_dir) -> dict:
    try:
        d = json.loads((Path(data_dir).expanduser() / CONFIG_JSON).read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def salva_config(data_dir, **valori) -> dict:
    d = Path(data_dir).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    cfg = carica_config(d)
    if "mac" in valori:
        valori["mac"] = valida_mac(valori["mac"])
    cfg.update(valori)
    tmp = d / (CONFIG_JSON + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(d / CONFIG_JSON)
    return cfg
