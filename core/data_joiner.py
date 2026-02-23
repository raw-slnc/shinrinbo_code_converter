# -*- coding: utf-8 -*-
"""
データ結合モジュール
森林簿XLSXの変換済みデータとShapefileをKEY1+整理番号_枝番の複合キーで結合する。
"""
import os
import logging
from typing import Dict, Any, List, Callable, Optional

from .code_table_registry import CodeTableRegistry
from .code_converter import convert_row, get_name_columns, CD_COLUMN_TO_TABLE
from .xlsx_reader import get_cd_columns

logger = logging.getLogger(__name__)


class JoinResult:
    def __init__(self):
        self.xlsx_rows = 0
        self.shp_features = 0
        self.joined = 0
        self.unmatched_shp = 0
        self.unmatched_xlsx = 0
        self.unknown_cd_count = 0
        self.duplicate_key = 0
        self.output_fields = 0
        self.layer = None
        self.errors: List[str] = []

    def summary(self) -> str:
        lines = [
            '=== 変換結果 ===',
            f'XLSX行数: {self.xlsx_rows:,}',
            f'Shape特徴量: {self.shp_features:,}',
            f'結合成功: {self.joined:,}',
            f'Shape未結合: {self.unmatched_shp:,}',
            f'XLSX未結合: {self.unmatched_xlsx:,}',
            f'変換不明値: {self.unknown_cd_count:,}件',
            f'重複キー: {self.duplicate_key:,}行',
            f'出力フィールド数: {self.output_fields}',
        ]
        if self.errors:
            lines.append(f'エラー: {len(self.errors)}件')
            for e in self.errors[:5]:
                lines.append(f'  - {e}')
        return '\n'.join(lines)


def join_data(
    registry: CodeTableRegistry,
    xlsx_rows: list,
    shp_path: str,
    output_gpkg: Optional[str],
    layer_name: str,
    keep_codes: bool,
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
) -> JoinResult:
    """森林簿XLSXとShapefileをKEY1+整理番号_枝番の複合キーで結合し、QGISレイヤを構築する。

    ステップ:
    1. XLSX変換 → メモリ内辞書に複合キーで格納（xlsx_rowsは呼び出し元で事前読込済み）
    2. Shapefile読込 → 複合キーで結合
    3. 出力レイヤ構築
    """
    result = JoinResult()

    # ---- Step 1: XLSX変換 → メモリ内辞書 ----
    # xlsx_rowsはメインスレッドで事前読込済み（lxmlスレッド安全問題の回避）
    if progress_callback:
        progress_callback(0, 'コード変換中...')

    xlsx_data = {}  # composite_key -> converted row dict
    headers = None
    cd_columns = None
    name_columns = None
    row_count = 0

    for row in xlsx_rows:
        if cancel_check and cancel_check():
            return result

        if headers is None:
            headers = list(row.keys())
            cd_columns = get_cd_columns(headers)
            name_columns = get_name_columns(cd_columns)

        row_count += 1
        converted = convert_row(row, registry, cd_columns)

        # 不明値カウント
        for col in cd_columns:
            name_col = name_columns[col]
            val = converted.get(name_col, '')
            if isinstance(val, str) and val.startswith('[不明:'):
                result.unknown_cd_count += 1

        # 複合キー: KEY1 + 整理番号_親番 + 整理番号_枝番
        # 混交林では同一小班に親番=1(主林木)・親番=2(副林木)の複数行があるため
        # 親番を含めないと重複キーと誤判定される
        key1 = str(converted.get('KEY1', '') or '').strip()
        parent_raw = converted.get('整理番号_親番', None)
        parent = str(parent_raw).strip() if parent_raw is not None else ''
        branch_raw = converted.get('整理番号_枝番', None)
        branch = str(branch_raw).strip() if branch_raw is not None else ''
        composite_key = key1
        if parent:
            composite_key += f'_{parent}'
        if branch:
            composite_key += f'_{branch}'
        if composite_key:
            if composite_key in xlsx_data:
                result.duplicate_key += 1
            else:
                xlsx_data[composite_key] = converted

        if row_count % 5000 == 0 and progress_callback:
            total_xlsx = len(xlsx_rows)
            pct = int(row_count / total_xlsx * 40) if total_xlsx else 0
            progress_callback(min(40, pct), f'コード変換中... {row_count:,}行')

    result.xlsx_rows = row_count

    if progress_callback:
        progress_callback(40, f'コード変換完了: {row_count:,}行')

    # ---- Step 2: フィールド定義の構築 ----
    if not headers or not xlsx_data:
        result.errors.append('XLSXデータが空です')
        return result

    # サンプル行からフィールドを構築
    sample_row = next(iter(xlsx_data.values()))
    raw_field_names = list(sample_row.keys())

    # CD列の直後に名称列を挿入（隣接配置）
    name_col_set = set(name_columns.values())
    ordered_fields = []
    for fname in raw_field_names:
        if fname in name_col_set:
            continue  # 名称列はこの位置ではスキップ（後でCD列の直後に挿入）
        ordered_fields.append(fname)
        if fname in name_columns:  # このフィールドはCD列 → 直後に名称列を挿入
            ordered_fields.append(name_columns[fname])
    all_field_names = ordered_fields

    if not keep_codes:
        # コード列を除外（名称列のみ残す）
        remove_cols = set()
        for col in cd_columns:
            if col in CD_COLUMN_TO_TABLE or col.startswith('施業履歴_施業方法') or col.startswith('施業履歴_事業種類'):
                remove_cols.add(col)
        all_field_names = [f for f in all_field_names if f not in remove_cols]

    # ---- Step 3: Shapefile結合 + 出力レイヤ構築 ----
    if progress_callback:
        progress_callback(45, 'Shapefile読込中...')

    from .layer_builder import build_layer
    layer = build_layer(
        shp_path=shp_path,
        xlsx_data=xlsx_data,
        field_names=all_field_names,
        output_gpkg=output_gpkg,
        layer_name=layer_name,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
        result=result,
    )

    result.layer = layer
    result.output_fields = len(all_field_names)

    # 未結合XLSX行を計算
    result.unmatched_xlsx = result.xlsx_rows - result.joined - result.duplicate_key

    return result
