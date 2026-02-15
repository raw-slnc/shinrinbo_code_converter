# -*- coding: utf-8 -*-
"""
DOCXコード表パーサー
R6森林簿コード表（詳細版）.docx を解析し、コード→テキストの辞書を構築する。

テーブルパターン:
- SIMPLE: CD|名称 (2列)
- COMPLEX: CD含む3-10列 (1.4市町村, 1.13樹種等)
- PAIRED-2x: CD|名称|CD|名称 (4列、最多パターン)
- PAIRED-3x/4x: 3-4ペア並列
- OAZA: 大字コード（付録、市町村別 CD|ｶﾅ|名称×2）
"""
import re
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# テーブルインデックス→コード表番号のマッピング
# DOCXの本文を順走査して見出しとテーブルを対応付ける
# 以下は事前解析結果に基づくフォールバック用ハードコード

TABLE_INDEX_TO_CODE_ID = {
    0: '1.1',    # 広域流域
    1: '1.2',    # 森林計画区
    2: '1.3',    # 農林事務所
    3: 'ref_hierarchy',  # 広域流域/計画区/農林事務所の階層参照表
    4: '1.4',    # 市町村
    5: '1.5',    # 準林班
    6: '1.7',    # 地番_代表
    7: '1.8',    # 所有形態
    8: '1.9',    # 在村・不在村
    9: '1.10',   # 森林区分
    10: '1.11',  # 施業優先度
    11: '1.12',  # 林種
    12: '1.13',  # 樹種
    13: '1.14',  # 制普別
    14: '1.15',  # 保安林
    15: '1.16',  # 自然公園
    16: '1.17',  # その他の制限林
    17: '1.18',  # 推進方向
    18: '1.19',  # ５機能区分
    19: '1.20',  # 新特定施業森林
    20: '1.21',  # 特定施業森林
    21: '1.22',  # ゾーニング機能
    22: '1.23',  # ゾーニング施業種
    23: '1.24',  # 森林認証
    24: '1.25',  # 針広別
    25: '1.26',  # 樹冠疎密度
    26: '1.27',  # 地位
    27: '1.28',  # 平均標高
    28: '1.29',  # 平均傾斜
    29: '1.30',  # 方位
    30: '1.31',  # 局所地形
    31: '1.32',  # 地質
    32: '1.33',  # 土性
    33: '1.34',  # 土壌
    34: '1.35',  # 地利
    35: '1.36',  # 都市計画
    36: '1.37',  # 保健機能森林
    37: '1.38',  # 森林施業計画（廃止）
    38: '1.39',  # 森林総合利用
    39: '1.40',  # 作業種
    40: '1.41',  # 崩壊土砂流出危険地区
    41: '1.42',  # 更新区分
    42: '1.43',  # 異動区分
    43: '1.44',  # 森林異動
    44: '1.45',  # 東海パルプ（廃止）
    45: '1.46',  # パンチデータ（廃止）
    46: '1.47',  # 松林区分
    47: '1.48_type',    # 施業履歴_事業種類
    48: '1.48_method',  # 施業履歴_施業方法
    # 49-54: 参考表 (2.x)
    49: '2.1',   # 市町村コード対照表
    50: '2.2',   # 市町村合併の経緯
    51: '2.3',   # 所有者名略号
    52: '2.4',   # 施業優先度適用表
    53: '2.5',   # 新特定施業森林
    54: '2.6',   # 林種と樹種の関係
    # 55: データ出力仕様 (3.1)
    55: '3.1',
    # 56-114: 大字コード（付録 4.1）
    # 115: 新旧林班対照表 (4.2)
}

# COMPLEXテーブルのCD列・名称列インデックス
COMPLEX_TABLE_CONFIG = {
    '1.4':  {'cd_col': 2, 'name_col': 3},    # [計画区, 農林, CD, 市町村名]
    '1.7':  {'cd_col': 0, 'name_col': 1},    # [CD, 名称, ...]
    '1.8':  {'cd_col': 1, 'name_col': 2},    # [区分, CD, 名称]
    '1.34': {'cd_col': 0, 'name_col': 2},    # [CD, 略称記号, 名称]
}


def parse_docx(docx_path: str, progress_callback=None) -> dict:
    """DOCXファイルをパースし、コード表辞書を返す。

    Returns:
        {
            'tables': {code_id: {cd_value: text_value, ...}, ...},
            'oaza': {municipality_cd: {oaza_cd: oaza_name, ...}, ...},
            'municipality_names': {cd: name, ...},  # 1.4から抽出
        }
    """
    from docx import Document

    if progress_callback:
        progress_callback(0, 'DOCXファイルを読み込み中...')

    doc = Document(docx_path)
    tables = doc.tables
    total_tables = len(tables)

    result_tables = {}
    oaza_tables = {}
    municipality_names = {}

    # 大字テーブル（インデックス56-114）の市町村CD抽出のため、
    # 本文の段落からテーブル前の見出しを取得
    oaza_municipality_map = _build_oaza_municipality_map(doc)

    for idx, table in enumerate(tables):
        if progress_callback:
            pct = int((idx / total_tables) * 100)
            progress_callback(pct, f'テーブル {idx+1}/{total_tables} を解析中...')

        code_id = TABLE_INDEX_TO_CODE_ID.get(idx)

        # 大字テーブル（56-114）
        if 56 <= idx <= 114:
            muni_cd = oaza_municipality_map.get(idx)
            if muni_cd:
                parsed = _parse_oaza_table(table)
                if parsed:
                    oaza_tables[str(muni_cd)] = parsed
            continue

        if code_id is None or code_id.startswith('ref') or code_id.startswith('2.') or code_id.startswith('3.'):
            continue

        # メインコード表のパース
        parsed = _parse_table(table, code_id)
        if parsed:
            result_tables[code_id] = parsed

        # 1.4の市町村名を別途保存
        if code_id == '1.4' and parsed:
            municipality_names = dict(parsed)

    if progress_callback:
        progress_callback(100, '解析完了')

    return {
        'tables': result_tables,
        'oaza': oaza_tables,
        'municipality_names': municipality_names,
    }


def _parse_table(table, code_id: str) -> Optional[Dict[str, str]]:
    """テーブル構造を自動検出してパースする。"""
    rows = table.rows
    if len(rows) < 2:
        return None

    ncols = len(rows[0].cells)
    header = [cell.text.strip() for cell in rows[0].cells]

    # COMPLEX テーブル（ハードコードされた設定がある場合）
    if code_id in COMPLEX_TABLE_CONFIG:
        return _parse_complex(table, COMPLEX_TABLE_CONFIG[code_id])

    # SPECIAL: 1.13 樹種 (CD|漢字|カナ|CD|漢字|カナ)
    if code_id == '1.13':
        return _parse_species(table)

    # PAIRED-4x: 1.5 準林班 (8列)
    if ncols == 8:
        return _parse_paired(table, pairs=4, group_size=2)

    # PAIRED-3x (6列でヘッダーが3ペア繰り返し)
    if ncols == 6:
        # 3x2 か 2x3 かを判定
        if len(header) >= 6 and header[0] == header[2] == header[4]:
            return _parse_paired(table, pairs=3, group_size=2)
        elif len(header) >= 6 and header[0] == header[3]:
            # 2x3 (大字形式とは別) - ヘッダーの繰り返しで判定
            return _parse_paired(table, pairs=2, group_size=3)

    # PAIRED-2x (4列)
    if ncols == 4:
        return _parse_paired(table, pairs=2, group_size=2)

    # SIMPLE (2列)
    if ncols == 2:
        return _parse_simple(table)

    # フォールバック: 最初の2列をCD|名称として扱う
    logger.warning(f'Unknown table structure for {code_id}: {ncols} cols, headers={header}')
    return _parse_simple(table)


def _parse_simple(table) -> Dict[str, str]:
    """SIMPLE: 2列 CD|名称"""
    result = {}
    for row in table.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        if len(cells) >= 2 and cells[0]:
            result[cells[0]] = cells[1]
    return result




def _parse_complex(table, config: dict) -> Dict[str, str]:
    """COMPLEX: 指定されたCD列と名称列からパース"""
    cd_col = config['cd_col']
    name_col = config['name_col']
    result = {}
    for row in table.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        if len(cells) > max(cd_col, name_col):
            cd = cells[cd_col]
            name = cells[name_col]
            if cd:
                result[cd] = name
    return result


def _parse_paired(table, pairs: int, group_size: int) -> Dict[str, str]:
    """PAIRED: pairs個のCD|名称ペアを左右に並べたテーブル"""
    result = {}
    for row in table.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        for p in range(pairs):
            start = p * group_size
            cd_idx = start
            name_idx = start + (group_size - 1)  # グループの最後が名称
            if name_idx < len(cells):
                cd = cells[cd_idx]
                name = cells[name_idx]
                if cd:
                    result[cd] = name
    return result


def _parse_species(table) -> Dict[str, str]:
    """SPECIAL: 1.13 樹種テーブル (CD|漢字|カナ|CD|漢字|カナ)"""
    result = {}
    for row in table.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        # 左ペア: idx 0=CD, 1=漢字, 2=カナ
        if len(cells) >= 3 and cells[0]:
            result[cells[0]] = cells[1]
        # 右ペア: idx 3=CD, 4=漢字, 5=カナ
        if len(cells) >= 6 and cells[3]:
            result[cells[3]] = cells[4]
    return result


def _parse_oaza_table(table) -> Dict[str, str]:
    """大字テーブル: CD|ｶﾅ|名称|CD|ｶﾅ|名称 (6列)"""
    result = {}
    for row in table.rows[1:]:
        cells = [c.text.strip() for c in row.cells]
        # 左ペア: idx 0=CD, 1=カナ, 2=名称
        if len(cells) >= 3 and cells[0]:
            result[cells[0]] = cells[2]
        # 右ペア: idx 3=CD, 4=カナ, 5=名称
        if len(cells) >= 6 and cells[3]:
            result[cells[3]] = cells[5]
    return result


def _build_oaza_municipality_map(doc) -> Dict[int, str]:
    """DOCXの本文要素を順走査し、大字テーブル（idx 56-114）と
    市町村CDの対応を構築する。

    大字セクションの見出しパターン: '・{CD}　{市町村名}' or '・{CD} {市町村名}'
    """
    from docx.oxml.ns import qn

    municipality_map = {}
    table_index = 0
    current_muni_cd = None

    for element in doc.element.body:
        if element.tag == qn('w:p'):
            text = element.text or ''
            # 段落内のすべてのrunからテキストを取得
            for run in element.iter(qn('w:r')):
                t = run.find(qn('w:t'))
                if t is not None and t.text:
                    text += t.text

            text = text.strip()
            # '・1　沼津市' or '・1 沼津市' のパターン
            m = re.match(r'・(\d+)\s+(.+)', text)
            if not m:
                m = re.match(r'・(\d+)　(.+)', text)
            if m:
                current_muni_cd = m.group(1)

        elif element.tag == qn('w:tbl'):
            if 56 <= table_index <= 114 and current_muni_cd:
                municipality_map[table_index] = current_muni_cd
            table_index += 1

    return municipality_map
