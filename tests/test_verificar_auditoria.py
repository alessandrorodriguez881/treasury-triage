"""Checking the audit trail away from the server (13-nov): the zip the Audit trail tab offers passes
`verificar_auditoria.py` on another computer, and an event or a ledger entry changed by hand makes it fail."""
import io
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verificar_auditoria
from triage_core.ledger import Ledger


def libro_con_un_pago(carpeta: Path) -> Ledger:
    carpeta.mkdir(parents=True, exist_ok=True)
    libro = Ledger(carpeta / "ledger.json")
    pago = libro.begin("pay:r20261113-100000:SHIP-1", "payout", 12_000.0, "USD", "FreightCo")
    libro.update(pago, "PAID", "po_demo_1")
    return libro


def bajar_y_descomprimir(libro: Ledger, carpeta: Path) -> Path:
    with zipfile.ZipFile(io.BytesIO(verificar_auditoria.empaquetar(libro))) as z:
        assert sorted(z.namelist()) == ["ledger.audit.jsonl", "ledger.json"]
        z.extractall(carpeta)
    return carpeta


def test_the_downloaded_files_pass_the_check_on_another_computer(tmp_path, capsys):
    otra = bajar_y_descomprimir(libro_con_un_pago(tmp_path / "servidor"), tmp_path / "mi_equipo")
    assert verificar_auditoria.main([str(otra)]) == 0
    assert "OK: 2 events" in capsys.readouterr().out


def test_an_event_changed_by_hand_fails_the_check(tmp_path, capsys):
    otra = bajar_y_descomprimir(libro_con_un_pago(tmp_path / "servidor"), tmp_path / "mi_equipo")
    registro = otra / "ledger.audit.jsonl"
    eventos = [json.loads(linea) for linea in registro.read_text(encoding="utf-8").splitlines()]
    eventos[0]["amount"] = 1_200.0  # alguien rebaja el importe del primer apunte
    registro.write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in eventos), encoding="utf-8")
    assert verificar_auditoria.main([str(otra)]) == 1
    assert "event 1 was changed after it was written" in capsys.readouterr().out


def test_a_ledger_entry_changed_by_hand_fails_the_check(tmp_path, capsys):
    otra = bajar_y_descomprimir(libro_con_un_pago(tmp_path / "servidor"), tmp_path / "mi_equipo")
    libro = json.loads((otra / "ledger.json").read_text(encoding="utf-8"))
    libro[0]["status"] = "FAILED"  # un pago hecho que se hace pasar por fallido, para poder pagarlo otra vez
    (otra / "ledger.json").write_text(json.dumps(libro), encoding="utf-8")
    assert verificar_auditoria.main([str(otra)]) == 1
    assert "differs from its audit trail" in capsys.readouterr().out


def test_nothing_to_download_before_anything_moves(tmp_path):
    with zipfile.ZipFile(io.BytesIO(verificar_auditoria.empaquetar(Ledger(tmp_path / "ledger.json")))) as z:
        assert z.namelist() == []
