# -*- coding: utf-8 -*-
"""
コード変換エンジン
森林簿の各行について、CDカラムの値をコード表に基づきテキストに変換する。
"""
import re
import logging
from typing import Dict, Any

from .code_table_registry import CodeTableRegistry

logger = logging.getLogger(__name__)

# XLSX CDカラム名 → コード表ID のマッピング
CD_COLUMN_TO_TABLE = {
    '広域流域CD':       '1.1',
    '計画区CD':        '1.2',
    '農林事務所CD':     '1.3',
    '市町村CD':        '1.4',
    '準林班CD':        '1.5',
    '大字CD':          'oaza',     # 特殊: 市町村CD依存
    '地番_代表CD':      '1.7',
    '林種CD':          '1.12',
    '樹種CD':          '1.13',
    '流域CD':          '1.1',      # 広域流域と同じテーブル
    '都市計画区分CD':    '1.36',
    '森林総合利用CD':    '1.39',
    '施業計画種CD':     '1.9',
    '所有者市町村CD':    '1.4',
    '地利CD':          '1.35',
    '地位CD':          '1.27',
    '制普別CD':        '1.14',
    '保安林種１CD':     '1.15',
    '保安林種２CD':     '1.15',
    '自然公園種CD':     '1.16',
    '制限林種１CD':     '1.17',
    '制限林種２CD':     '1.17',
    '制限林種３CD':     '1.17',
    '作業種CD':        '1.40',
    '針広別CD':        '1.25',
    '疎密度CD':        '1.26',
    '特定林分１CD':     '1.21',
    '特定林分２CD':     '1.21',
    '特定施業森林CD':    '1.21',
    '保健機能森林CD':    '1.37',
    '植伐動向CD':       '1.43',
    '森林区分CD':       '1.10',
    '施業優先度CD':     '1.11',
    '施業特定林分１CD':  '1.21',
    '施業特定林分２CD':  '1.21',
    '新特定施業森林CD':  '1.20',
    '更新区分CD':       '1.42',
    '森林認証':         '1.24',
    '松林区分':         '1.47',
    'ゾーニング施業種':   '1.23',
    # ゾーニング機能は5列 (木材生産/水源涵養/山地災害防止/快適環境/保健)
    'ゾーニング機能_木材生産':   '1.22',
    'ゾーニング機能_水源涵養':   '1.22',
    'ゾーニング機能_山地災害防止': '1.22',
    'ゾーニング機能_快適環境':   '1.22',
    'ゾーニング機能_保健':      '1.22',
}

# 施業履歴の繰り返しカラム名パターン
HISTORY_METHOD_PREFIX = '施業履歴_施業方法'   # → 1.48_method
HISTORY_TYPE_PREFIX = '施業履歴_事業種類'     # → 1.48_type


def _cd_col_to_name_col(cd_col: str) -> str:
    """CDカラム名から対応する名称カラム名を生成する。
    CDを除去し「名称」を付与。末尾の番号は名称の後ろに移動。
    例: '林種CD'     → '林種名称'
        '保安林種１CD' → '保安林種名称１'
        '制限林種３CD' → '制限林種名称３'
        '森林認証'    → '森林認証名称'
    """
    if cd_col.endswith('CD'):
        base = cd_col[:-2]  # CD除去
    else:
        base = cd_col

    # 末尾の番号（全角・半角）を分離
    m = re.match(r'^(.+?)([０-９0-9]+)$', base)
    if m:
        return m.group(1) + '名称' + m.group(2)
    return base + '名称'


def get_name_columns(cd_columns: list) -> Dict[str, str]:
    """CDカラムリストから、{CDカラム名: 名称カラム名} のマッピングを返す。"""
    return {col: _cd_col_to_name_col(col) for col in cd_columns}


def convert_row(row: Dict[str, Any], registry: CodeTableRegistry,
                cd_columns: list) -> Dict[str, Any]:
    """1行のCDカラムをテキストに変換し、名称カラムを追加した新しい行辞書を返す。

    Args:
        row: XLSXから読み込んだ1行の辞書
        registry: コード表レジストリ
        cd_columns: 変換対象CDカラム名リスト

    Returns:
        元の行に名称カラムを追加した辞書
    """
    result = dict(row)  # 元データを保持
    municipality_cd = row.get('市町村CD')

    for col in cd_columns:
        name_col = _cd_col_to_name_col(col)
        cd_value = row.get(col)

        if cd_value is None or str(cd_value).strip() == '':
            result[name_col] = ''
            continue

        # 施業履歴の繰り返しカラム
        if col.startswith(HISTORY_METHOD_PREFIX):
            text = registry.lookup('1.48_method', cd_value)
        elif col.startswith(HISTORY_TYPE_PREFIX):
            text = registry.lookup('1.48_type', cd_value)
        # 大字CD（市町村別）
        elif col == '大字CD':
            text = registry.lookup_oaza(municipality_cd, cd_value)
        # 通常のCDカラム
        else:
            table_id = CD_COLUMN_TO_TABLE.get(col)
            if table_id:
                text = registry.lookup(table_id, cd_value)
            else:
                text = None

        if text is not None:
            result[name_col] = text
        else:
            # 変換できない場合は元のコード値をそのまま記載
            result[name_col] = f'[不明:{cd_value}]'

    return result
