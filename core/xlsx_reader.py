# -*- coding: utf-8 -*-
"""
XLSX森林簿リーダー
openpyxlのread_onlyモードで大容量xlsxをストリーミング読込する。
"""
import logging
from typing import Iterator, Dict, Any, List

logger = logging.getLogger(__name__)


def read_xlsx(path: str, sheet_name: str = 'データ') -> Iterator[Dict[str, Any]]:
    """XLSXファイルをストリーミングで読み込み、行辞書を返す。

    Args:
        path: XLSXファイルパス
        sheet_name: シート名（デフォルト: 'データ'）

    Yields:
        {カラム名: 値, ...} の辞書
    """
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # シート名で検索、なければ最初のシート
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb[wb.sheetnames[0]]
        logger.warning(f'シート "{sheet_name}" が見つかりません。"{wb.sheetnames[0]}" を使用します。')

    headers = None
    for row in ws.iter_rows(values_only=True):
        if headers is None:
            headers = [str(h).strip() if h is not None else f'col_{i}'
                       for i, h in enumerate(row)]
            continue

        row_dict = {}
        for i, val in enumerate(row):
            if i < len(headers):
                row_dict[headers[i]] = val
        yield row_dict

    wb.close()



def get_cd_columns(headers: List[str]) -> List[str]:
    """ヘッダーリストからCD列名を抽出する。"""
    cd_cols = [h for h in headers if h.endswith('CD')]
    # 森林認証は CDで終わらないが変換対象
    if '森林認証' in headers:
        cd_cols.append('森林認証')
    # ゾーニング施業種
    if 'ゾーニング施業種' in headers:
        cd_cols.append('ゾーニング施業種')
    # 施業履歴関連
    for h in headers:
        if h.startswith('施業履歴_施業方法') or h.startswith('施業履歴_事業種類'):
            if h not in cd_cols:
                cd_cols.append(h)
    return cd_cols
