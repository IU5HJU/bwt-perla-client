"""Prova di aggiorna.sh con un repository «origine» finto, un clone che fa da
Raspberry e comandi systemctl/sudo/curl finti nel PATH."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "aggiorna.sh"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null"}

STUB_SYSTEMCTL = """#!/bin/sh
# systemctl finto: stato in $STUB_DIR
case "$1" in
  cat) [ -f "$STUB_DIR/senza_servizio" ] && exit 1; exit 0 ;;
  show) cat "$STUB_DIR/attivo_da" ;;
  is-active) [ -f "$STUB_DIR/non_parte" ] && exit 3; exit 0 ;;
  restart) echo "riavviato" >> "$STUB_DIR/azioni"; date -u "+%a %Y-%m-%d %H:%M:%S UTC" > "$STUB_DIR/attivo_da" ;;
esac
"""
STUB_CURL = """#!/bin/sh
[ -f "$STUB_DIR/pagina_giu" ] && exit 22
echo ok
"""


def esegui(cmd, cwd, env=None, **kw):
    e = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **GIT_ENV, **(env or {})}
    return subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, **kw)


def git(cwd, *args):
    r = esegui(["git", *args], cwd)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


class AggiornaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.origine, self.pi, self.dev, self.stub = t / "origine.git", t / "pi", t / "dev", t / "stub"
        git(t, "init", "-q", "--bare", "-b", "main", str(self.origine))
        git(t, "clone", "-q", str(self.origine), str(self.dev))
        (self.dev / "tests").mkdir()
        (self.dev / "tests" / "test_ok.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        (self.dev / "web.py").write_text("# versione 1\n")
        (self.dev / "requirements.txt").write_text("bleak\n")
        shutil.copy(SCRIPT, self.dev / "aggiorna.sh")
        git(self.dev, "add", "-A")
        git(self.dev, "commit", "-q", "-m", "v1")
        git(self.dev, "push", "-q", "origin", "main")
        git(t, "clone", "-q", str(self.origine), str(self.pi))
        self.stub.mkdir()
        for nome, testo in (("systemctl", STUB_SYSTEMCTL), ("curl", STUB_CURL),
                            ("sudo", '#!/bin/sh\nexec "$@"\n')):
            p = self.stub / nome
            p.write_text(testo)
            p.chmod(0o755)
        self.imposta_servizio_avviato(futuro=True)

    def tearDown(self):
        self.tmp.cleanup()

    def imposta_servizio_avviato(self, futuro):
        (self.stub / "attivo_da").write_text(
            "Fri 2099-01-01 00:00:00 UTC\n" if futuro else "Wed 2020-01-01 00:00:00 UTC\n")

    def pubblica(self, file="web.py", testo="# versione 2\n", messaggio="v2"):
        (self.dev / file).write_text(testo)
        git(self.dev, "add", "-A")
        git(self.dev, "commit", "-q", "-m", messaggio)
        git(self.dev, "push", "-q", "origin", "main")

    def lancia(self, *args):
        env = {"PATH": f"{self.stub}:{os.environ['PATH']}", "STUB_DIR": str(self.stub),
               "PYTHON": sys.executable}
        return esegui(["bash", str(self.pi / "aggiorna.sh"), *args], self.pi, env)

    def riavvii(self):
        f = self.stub / "azioni"
        return f.read_text().count("riavviato") if f.exists() else 0

    # ── casi ────────────────────────────────────────────────────────────────

    def test_tutto_in_ordine_non_fa_nulla(self):
        r = self.lancia()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nulla da fare", r.stdout)
        self.assertEqual(self.riavvii(), 0)

    def test_aggiorna_prova_e_riavvia(self):
        self.pubblica()
        vecchio = git(self.pi, "rev-parse", "HEAD")
        r = self.lancia()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotEqual(git(self.pi, "rev-parse", "HEAD"), vecchio)
        self.assertEqual((self.pi / "web.py").read_text(), "# versione 2\n")
        for atteso in ("aggiornato da", "v2", "test superati", "riavvio il servizio", "fatto"):
            self.assertIn(atteso, r.stdout)
        self.assertEqual(self.riavvii(), 1)

    def test_servizio_vecchio_con_codice_gia_aggiornato_viene_riavviato(self):
        self.imposta_servizio_avviato(futuro=False)             # avviato nel 2020, il codice e' piu' nuovo
        r = self.lancia()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("piu' vecchio del codice", r.stdout)
        self.assertEqual(self.riavvii(), 1)
        # dopo il riavvio il servizio e' in linea: un secondo giro non fa nulla
        r2 = self.lancia()
        self.assertIn("nulla da fare", r2.stdout)
        self.assertEqual(self.riavvii(), 1)

    def test_controlla_non_modifica_nulla(self):
        self.pubblica()
        vecchio = git(self.pi, "rev-parse", "HEAD")
        r = self.lancia("--controlla")
        self.assertEqual(r.returncode, 3)                       # serve aggiornare
        self.assertIn("indietro di 1", r.stdout)
        self.assertEqual(git(self.pi, "rev-parse", "HEAD"), vecchio)
        self.assertEqual(self.riavvii(), 0)
        self.lancia()                                           # aggiorna davvero
        r2 = self.lancia("--controlla")
        self.assertEqual((r2.returncode, "tutto in ordine" in r2.stdout), (0, True), r2.stdout + r2.stderr)

    def test_controlla_segnala_il_servizio_vecchio(self):
        self.imposta_servizio_avviato(futuro=False)
        r = self.lancia("--controlla")
        self.assertEqual(r.returncode, 3)
        self.assertIn("serve aggiornare o riavviare", r.stdout)

    def test_test_falliti_non_riavviano_e_indicano_come_tornare_indietro(self):
        vecchio = git(self.pi, "rev-parse", "HEAD")
        self.pubblica("tests/test_ok.py",
                      "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.fail('rotto')\n", "rompe")
        r = self.lancia()
        self.assertEqual(r.returncode, 1)
        self.assertIn("test sono falliti", r.stderr)
        self.assertIn(f"git reset --hard {vecchio}", r.stderr)
        self.assertEqual(self.riavvii(), 0)                     # il servizio non si tocca

    def test_modifiche_locali_fermano_tutto(self):
        self.pubblica()
        (self.pi / "web.py").write_text("# modificato a mano\n")
        r = self.lancia()
        self.assertEqual(r.returncode, 1)
        self.assertIn("modifiche locali", r.stderr)
        self.assertIn("web.py", r.stderr)
        self.assertEqual((self.pi / "web.py").read_text(), "# modificato a mano\n")   # intatte
        self.assertEqual(self.riavvii(), 0)

    def test_ramo_diverso_da_main(self):
        git(self.pi, "checkout", "-q", "-b", "prova")
        r = self.lancia()
        self.assertEqual(r.returncode, 1)
        self.assertIn("non su 'main'", r.stderr)
        self.assertIn("git checkout main", r.stderr)

    def test_commit_locali_non_su_github(self):
        (self.pi / "extra.txt").write_text("x\n")
        git(self.pi, "add", "-A")
        git(self.pi, "commit", "-q", "-m", "locale")
        self.pubblica()
        r = self.lancia()
        self.assertEqual(r.returncode, 1)
        self.assertIn("commit che su GitHub non esistono", r.stderr)
        self.assertIn("locale", r.stderr)
        self.assertEqual(self.riavvii(), 0)

    def test_solo_riavvia(self):
        self.pubblica()
        vecchio = git(self.pi, "rev-parse", "HEAD")
        r = self.lancia("--solo-riavvia")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(git(self.pi, "rev-parse", "HEAD"), vecchio)    # niente pull
        self.assertEqual(self.riavvii(), 1)
        self.assertNotIn("eseguo i test", r.stdout)

    def test_servizio_che_non_riparte_e_pagina_che_non_risponde(self):
        self.pubblica()
        (self.stub / "non_parte").write_text("")
        r = self.lancia()
        self.assertEqual(r.returncode, 1)
        self.assertIn("non e' ripartito", r.stderr)
        self.assertIn("journalctl -u bwt-perla", r.stderr)

    def test_senza_servizio_aggiorna_e_prova_senza_riavviare(self):
        (self.stub / "senza_servizio").write_text("")
        self.pubblica()
        r = self.lancia()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("non esiste su questa macchina", r.stdout)
        self.assertIn("test superati", r.stdout)
        self.assertEqual(self.riavvii(), 0)

    def test_dipendenze_cambiate(self):
        pip_finto = self.stub / "py"
        pip_finto.write_text(f'#!/bin/sh\nif [ "$1 $2" = "-m pip" ]; then echo "pip $*" >> "$STUB_DIR/azioni"; exit 0; fi\nexec {sys.executable} "$@"\n')
        pip_finto.chmod(0o755)
        self.pubblica("requirements.txt", "bleak\nnuova-libreria\n", "dipendenza")
        env = {"PATH": f"{self.stub}:{os.environ['PATH']}", "STUB_DIR": str(self.stub), "PYTHON": str(pip_finto)}
        r = esegui(["bash", str(self.pi / "aggiorna.sh")], self.pi, env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("dipendenze sono cambiate", r.stdout)
        self.assertIn("pip -m pip install -q -r requirements.txt", (self.stub / "azioni").read_text())

    def test_opzione_sconosciuta(self):
        r = self.lancia("--boh")
        self.assertEqual(r.returncode, 2)
        self.assertIn("Opzione sconosciuta", r.stderr)


if __name__ == "__main__":
    unittest.main()
