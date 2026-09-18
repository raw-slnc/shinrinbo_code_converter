# -*- coding: utf-8 -*-
"""
個人情報データリーダー
市町提供のCSV/XLSXを、値の自動型変換を避けてすべて文字列として読み込む。
"""
import csv
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

_CSV_ENCODINGS = ('utf-8-sig', 'cp932')


def read_personal_info_file(path: str) -> List[Dict[str, str]]:
    """個人情報CSV/XLSXを読み込み、行辞書のリストを返す。

    すべての値は文字列として扱う（Excelのセル型自動判定による
    ゼロ落ち・日付化を避けるため、CSVはcsvモジュールで直接読む）。
    """
    lower = path.lower()
    if lower.endswith('.csv'):
        return _read_csv(path)
    elif lower.endswith('.xlsx'):
        return _read_xlsx_as_text(path)
    else:
        raise ValueError(f'対応していないファイル形式です: {path}')


def _read_csv(path: str) -> List[Dict[str, str]]:
    last_error = None
    for enc in _CSV_ENCODINGS:
        try:
            with open(path, 'r', encoding=enc, newline='') as f:
                reader = csv.DictReader(f)
                rows = [dict(row) for row in reader]
            return rows
        except UnicodeDecodeError as e:
            last_error = e
            continue
    raise ValueError(f'CSVの文字コードを判定できませんでした（試行: {", ".join(_CSV_ENCODINGS)}）: {last_error}')


def _read_xlsx_as_text(path: str) -> List[Dict[str, str]]:
    """XLSXをすべてのセルを文字列化して読み込む。
    ユーザーが既にExcelで開いた結果ゼロ落ち・日付化している場合でも、
    それ以上は復元しない（照合時に不一致として検出される想定）。

    read_only=False（既定）で全量ロードする。read_only=Trueはlxml.etree.iterparse
    のGC残留によりWindowsでアクセス違反を起こす既知の問題があるため使わない
    （xlsx_reader.pyの同種の対処を参照）。
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    row_iter = ws.iter_rows(values_only=True)

    try:
        header = [str(h).strip() if h is not None else f'col_{i}'
                  for i, h in enumerate(next(row_iter))]
    except StopIteration:
        wb.close()
        return []

    rows = []
    for row in row_iter:
        row_dict = {}
        for i, val in enumerate(row):
            if i >= len(header):
                break
            row_dict[header[i]] = '' if val is None else str(val)
        rows.append(row_dict)

    wb.close()
    return rows
