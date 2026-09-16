# -*- coding: utf-8 -*-
"""
個人情報結合モジュール
森林簿コンバーターが作成済みのGeoPackageに、登記所有者_漢字/登記所有者_住所を
KEY1+整理番号_親番+整理番号_枝番の複合キーで後乗せする。
"""
import logging
from typing import Callable, Dict, List, Optional

from .personal_info_matcher import (
    build_composite_key, classify_name_address_columns, validate_headers,
)

logger = logging.getLogger(__name__)

NAME_FIELD = '登記所有者_漢字'
ADDRESS_FIELD = '登記所有者_住所'


def _feat_value(feat, idx):
    """QgsFeatureの属性値をPython標準の値に正規化する。
    PyQGISのNULLセンチネル（`qgis.core.NULL`）はPythonの`None`とは別物で、
    素通しするとstr(NULL)が'NULL'という文字列になってしまうため、ここでNoneに変換する。
    """
    from qgis.core import NULL
    val = feat[idx]
    return None if val == NULL else val


class PersonalInfoJoinResult:
    def __init__(self):
        self.source_rows = 0
        self.gpkg_features = 0
        self.matched = 0
        self.unmatched_gpkg = 0
        self.unmatched_source = 0
        self.newly_filled = 0     # 空欄だった項目に新規記入した件数（氏名/住所を項目ごとに集計）
        self.overwritten = 0      # 既存値があり、上書き設定により書き換えた件数
        self.skipped_existing = 0  # 既存値があり、上書きしない設定のため保持した件数
        self.key1_zero_padded = 0  # KEY1の桁不足を0埋め補正した個人情報データ行数
        self.name_column: Optional[str] = None
        self.address_column: Optional[str] = None
        self.errors: List[str] = []

    def summary(self) -> str:
        lines = [
            '=== 個人情報結合結果 ===',
            f'個人情報データ行数: {self.source_rows:,}',
            f'GeoPackage地物数: {self.gpkg_features:,}',
            f'結合成功: {self.matched:,}',
            f'GeoPackage未結合: {self.unmatched_gpkg:,}',
            f'個人情報データ未結合: {self.unmatched_source:,}',
            f'新規記入（項目単位）: {self.newly_filled:,}',
            f'上書き（項目単位）: {self.overwritten:,}',
            f'既存値を保持（項目単位）: {self.skipped_existing:,}',
            f'KEY1の0埋め補正: {self.key1_zero_padded:,}件',
            f'氏名列と判定: {self.name_column or "(判定不能)"}',
            f'住所列と判定: {self.address_column or "(判定不能)"}',
        ]
        if self.errors:
            lines.append(f'エラー: {len(self.errors)}件')
            for e in self.errors[:5]:
                lines.append(f'  - {e}')
        return '\n'.join(lines)


def _normalize_key1(value, target_width: int, result: PersonalInfoJoinResult) -> str:
    """KEY1の桁不足（数値変換等によるゼロ落ち）を0埋め補正する。

    KEY1はCD値を連結した固定長の数値文字列。code_table_registry.lookup()の
    CD値ゼロ埋め補正と同じ考え方で、gpkg側の実際の桁数に満たない場合のみ
    左側を0埋めする（非数値や規定桁数以上の値はそのまま扱う＝不一致として検出させる）。
    """
    s = str(value).strip() if value is not None else ''
    if s and s.isdigit() and len(s) < target_width:
        result.key1_zero_padded += 1
        return s.zfill(target_width)
    return s


def _apply_field(layer, feat, field_idx, new_value, overwrite_existing, result):
    """1項目（氏名 or 住所）を、既存値・新しい値の有無とoverwrite_existingに応じて更新する。

    新しい値が空欄の場合は、上書き設定に関わらず既存値を保持する
    （空欄の新データで既存の正しいデータを潰さないため）。
    """
    existing = _feat_value(feat, field_idx)
    existing_str = str(existing).strip() if existing is not None else ''
    new_str = str(new_value).strip() if new_value is not None else ''

    if not new_str:
        if existing_str:
            result.skipped_existing += 1
        return

    if existing_str:
        if overwrite_existing:
            layer.changeAttributeValue(feat.id(), field_idx, new_value)
            result.overwritten += 1
        else:
            result.skipped_existing += 1
    else:
        layer.changeAttributeValue(feat.id(), field_idx, new_value)
        result.newly_filled += 1


def join_personal_info(
    gpkg_path: str,
    layer_name: str,
    source_rows: List[Dict[str, str]],
    overwrite_existing: bool = False,
    progress_callback: Optional[Callable] = None,
    cancel_check: Optional[Callable] = None,
) -> PersonalInfoJoinResult:
    """個人情報データを既存GeoPackageに結合する。

    Args:
        gpkg_path: 森林簿コンバーターが作成したGeoPackageのパス
        layer_name: 対象レイヤ名
        source_rows: personal_info_reader.read_personal_info_file() の結果
        overwrite_existing: Trueなら既存の登記所有者_漢字/住所を新しい値で
            上書きする。Falseなら既存値がある項目は保持し、空欄の項目のみ記入する
            （氏名・住所は項目ごとに独立して判定する）。
    """
    from qgis.core import QgsField, QgsVectorLayer
    from qgis.PyQt.QtCore import QVariant

    result = PersonalInfoJoinResult()
    result.source_rows = len(source_rows)

    if not source_rows:
        result.errors.append('個人情報データが空です')
        return result

    headers = list(source_rows[0].keys())
    header_check = validate_headers(headers)
    if not header_check.ok:
        result.errors.append(header_check.error_message())
        return result

    if progress_callback:
        progress_callback(0, 'GeoPackageを開いています...')

    layer = QgsVectorLayer(f'{gpkg_path}|layername={layer_name}', layer_name, 'ogr')
    if not layer.isValid():
        result.errors.append(f'GeoPackageレイヤの読込に失敗しました: {gpkg_path} ({layer_name})')
        return result

    gpkg_field_names = [f.name() for f in layer.fields()]
    for required in ('KEY1', '整理番号_親番', '整理番号_枝番'):
        if required not in gpkg_field_names:
            result.errors.append(
                f'GeoPackageに"{required}"フィールドがありません。'
                'この森林簿コンバーターで作成したGeoPackageを指定してください。'
            )
            return result

    # gpkg側のKEY1桁数を検出する（先頭1件で判定。同一データセット内は固定長のはず）
    key1_field_idx = layer.fields().indexOf('KEY1')
    key1_width = 0
    for f in layer.getFeatures():
        v = _feat_value(f, key1_field_idx)
        if v is not None and str(v).strip():
            key1_width = len(str(v).strip())
        break

    # 氏名/住所候補列（KEY1・整理番号以外の全列）を文字列傾向で判定
    key_cols = {header_check.key1_col, header_check.parent_col, header_check.branch_col}
    candidate_columns = [h for h in headers if h not in key_cols]
    name_col, address_col = classify_name_address_columns(source_rows, candidate_columns)
    result.name_column = name_col
    result.address_column = address_col

    if name_col is None and address_col is None:
        result.errors.append(
            '氏名列・住所列を判定できませんでした。データの列内容をご確認ください。'
        )
        return result

    # 複合キー -> (氏名, 住所) の辞書を構築
    source_map: Dict[str, Dict[str, str]] = {}
    for row in source_rows:
        key1_value = row.get(header_check.key1_col)
        if key1_width:
            key1_value = _normalize_key1(key1_value, key1_width, result)
        composite_key = build_composite_key(
            key1_value,
            row.get(header_check.parent_col),
            row.get(header_check.branch_col),
        )
        if not composite_key:
            continue
        entry = {}
        if name_col:
            entry['name'] = str(row.get(name_col, '') or '').strip()
        if address_col:
            entry['address'] = str(row.get(address_col, '') or '').strip()
        source_map[composite_key] = entry

    if progress_callback:
        progress_callback(10, f'{len(source_map):,}件の個人情報を読込みました')

    # 出力フィールドの追加（未存在の場合のみ）
    if not layer.isEditable():
        layer.startEditing()

    new_fields = []
    if NAME_FIELD not in gpkg_field_names:
        new_fields.append(QgsField(NAME_FIELD, QVariant.String))
    if ADDRESS_FIELD not in gpkg_field_names:
        new_fields.append(QgsField(ADDRESS_FIELD, QVariant.String))
    if new_fields:
        layer.dataProvider().addAttributes(new_fields)
        layer.updateFields()

    name_idx = layer.fields().indexOf(NAME_FIELD)
    addr_idx = layer.fields().indexOf(ADDRESS_FIELD)
    key1_idx = layer.fields().indexOf('KEY1')
    parent_idx = layer.fields().indexOf('整理番号_親番')
    branch_idx = layer.fields().indexOf('整理番号_枝番')

    total = layer.featureCount()
    result.gpkg_features = total
    matched_keys = set()
    count = 0

    for feat in layer.getFeatures():
        if cancel_check and cancel_check():
            layer.rollBack()
            return result

        count += 1
        composite_key = build_composite_key(
            _feat_value(feat, key1_idx), _feat_value(feat, parent_idx), _feat_value(feat, branch_idx)
        )
        entry = source_map.get(composite_key)
        if entry:
            if name_col:
                _apply_field(layer, feat, name_idx, entry.get('name', ''),
                             overwrite_existing, result)
            if address_col:
                _apply_field(layer, feat, addr_idx, entry.get('address', ''),
                             overwrite_existing, result)
            matched_keys.add(composite_key)
            result.matched += 1
        else:
            result.unmatched_gpkg += 1

        if count % 5000 == 0 and progress_callback:
            pct = 10 + int((count / total) * 80) if total else 10
            progress_callback(min(90, pct), f'結合中... {count:,}/{total:,}')

    if not layer.commitChanges():
        result.errors.append('GeoPackageへの書き込みに失敗しました: ' + '; '.join(
            e.what() if hasattr(e, 'what') else str(e) for e in layer.commitErrors()
        ))
        return result

    result.unmatched_source = len(source_map) - len(matched_keys)

    if progress_callback:
        progress_callback(100, '完了')

    return result
