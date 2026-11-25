"""CSV / Excel / PDF exports, including the edge cases that broke before."""
import io

import numpy as np
import pandas as pd
import pytest

from exporters import to_csv_bytes, to_excel_bytes, to_pdf_bytes

DF = pd.DataFrame({"state": ["TEXAS", "A & B <x>"], "n": [2, 1]})


def test_csv_roundtrip():
    out = pd.read_csv(io.BytesIO(to_csv_bytes(DF)))
    assert out.equals(DF)


def test_excel_has_results_and_query_sheet():
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(to_excel_bytes(DF, question="Which states?")))
    assert "Results" in wb.sheetnames and "Query info" in wb.sheetnames
    assert wb["Results"]["A1"].value == "state"


def test_pdf_is_valid():
    assert to_pdf_bytes(DF, question="q", answer="a").startswith(b"%PDF")


def test_pdf_with_nan_and_empty():
    nan_df = pd.DataFrame({"damage": [np.nan]})
    assert to_pdf_bytes(nan_df, question="q", answer="a").startswith(b"%PDF")   # SUM over no rows
    assert to_pdf_bytes(pd.DataFrame({"x": []})).startswith(b"%PDF")


def test_pdf_caps_long_tables():
    big = pd.DataFrame({"n": range(1000), "t": ["x" * 200] * 1000})
    assert to_pdf_bytes(big, max_rows=100).startswith(b"%PDF")
