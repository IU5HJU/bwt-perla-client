import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bwt_perla import ciclo_con_tentativi  # noqa: E402


class RetryTest(unittest.TestCase):
    def prova(self, falliscono, tentativi=3):
        chiamate = []

        async def fn(mac, tr):
            chiamate.append(mac)
            if len(chiamate) <= falliscono:
                raise TimeoutError("radio debole")
            return {"ok": True}

        res = asyncio.run(ciclo_con_tentativi("MAC", None, tentativi, 0, fn))
        return res, chiamate

    def test_riprova_e_riesce(self):
        res, chiamate = self.prova(falliscono=2)
        self.assertEqual(res, {"ok": True})
        self.assertEqual(len(chiamate), 3)

    def test_riesce_subito(self):
        self.assertEqual(len(self.prova(0)[1]), 1)

    def test_dopo_i_tentativi_solleva(self):
        with self.assertRaises(TimeoutError):
            self.prova(falliscono=5)


if __name__ == "__main__":
    unittest.main()
