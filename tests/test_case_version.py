"""D-23: отпечаток набора данных не должен зависеть от платформы.

На Windows контрольные CSV лежат с CRLF, на Linux с LF. До правки sha256 считался по сырым
байтам, и один и тот же набор давал разные отпечатки: f7d7e963292bd2c5 против 698addcac211e6ef.
Воспроизводимость между машинами так подтвердить нельзя.
"""
import shutil

import pytest

from kriokontur import paths
from kriokontur.caseinput import case_version, load_case

CANONICAL = "698addcac211e6ef"     # отпечаток набора cec6de1 организатора в канонической форме LF


def _copy_with_newlines(src_dir, dst_dir, newline: bytes):
    dst_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(src_dir.glob("*.csv")):
        body = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", newline)
        (dst_dir / path.name).write_bytes(body)


def test_case_version_matches_canonical_lf_fingerprint():
    assert load_case().version == CANONICAL


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_case_version_is_stable_across_line_endings(newline, tmp_path_factory):
    """Копия набора с другими переводами строк даёт тот же отпечаток."""
    target = paths.RESULTS / f"case-version-check-{'crlf' if newline == b'\r\n' else 'lf'}"
    try:
        _copy_with_newlines(paths.DATA, target, newline)
        assert case_version(target) == CANONICAL
    finally:
        shutil.rmtree(target, ignore_errors=True)


def test_case_version_changes_when_data_changes():
    """Отпечаток обязан реагировать на правку данных: иначе он бесполезен."""
    target = paths.RESULTS / "case-version-check-modified"
    try:
        _copy_with_newlines(paths.DATA, target, b"\n")
        demand = target / "demand.csv"
        demand.write_bytes(demand.read_bytes().replace(b"100", b"101"))
        assert case_version(target) != CANONICAL
    finally:
        shutil.rmtree(target, ignore_errors=True)
