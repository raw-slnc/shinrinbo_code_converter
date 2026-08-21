# -*- coding: utf-8 -*-
"""
QGISレイヤ構築モジュール
Shapefile + 変換済みXLSXデータからメモリレイヤまたはGeoPackageを構築する。
"""
import logging
from typing import Dict, Any, List, Callable, Optional

logger = logging.getLogger(__name__)


def build_layer(
    shp_path: str,
    xlsx_data: Dict[str, Dict[str, Any]],
    field_names: List[str],
    output_gpkg: Optional[str],
    layer_name: str,
    progress_callback: Optional[Callable],
    cancel_check: Optional[Callable],
    result,  # JoinResult
):
    """ShapefileとXLSXデータをKEY1で結合してQGISレイヤを構築する。"""
    from qgis.core import (
        QgsVectorLayer, QgsField, QgsFeature, QgsFields,
    )
    from qgis.PyQt.QtCore import QMetaType

    # Shapefile読込（日本語ShapefileはCP932エンコーディング）
    shp_layer = QgsVectorLayer(shp_path, 'temp_shp', 'ogr')
    if not shp_layer.isValid():
        result.errors.append(f'Shapefile読込失敗: {shp_path}')
        return None
    shp_layer.setProviderEncoding('CP932')

    crs = shp_layer.crs()
    result.shp_features = shp_layer.featureCount()

    # KEY1フィールドの存在確認
    shp_field_names = [f.name() for f in shp_layer.fields()]
    if 'KEY1' not in shp_field_names:
        result.errors.append('ShapefileにKEY1フィールドがありません')
        return None

    # 出力フィールド定義
    fields = QgsFields()
    # Shapefileのフィールド（KEY1以外は残す）
    for f in shp_layer.fields():
        if f.name() != 'KEY1':
            fields.append(QgsField(f.name(), f.type(), f.typeName(), f.length(), f.precision()))

    # XLSX + 変換フィールド
    # KEY1はShapefileから取得するので重複を避ける
    # ただしXLSXのKEY1も一応含めるとShapefileのKEY1と参照可能
    for fname in field_names:
        # Shapefileと同名のフィールドは「_簿」サフィックスを付ける
        actual_name = fname
        if fname in shp_field_names and fname != 'KEY1':
            actual_name = fname + '_森林簿'
        # フィールド名は10文字制限があるDBFだがメモリレイヤ/GeoPackageでは制限なし
        fields.append(QgsField(actual_name, QMetaType.Type.QString))

    # 出力先の決定
    if output_gpkg:
        return _build_geopackage(
            shp_layer, xlsx_data, fields, field_names, shp_field_names,
            crs, output_gpkg, layer_name,
            progress_callback, cancel_check, result
        )
    else:
        return _build_memory_layer(
            shp_layer, xlsx_data, fields, field_names, shp_field_names,
            crs, layer_name,
            progress_callback, cancel_check, result
        )


def _build_memory_layer(
    shp_layer, xlsx_data, fields, field_names, shp_field_names,
    crs, layer_name,
    progress_callback, cancel_check, result
):
    """メモリレイヤとして構築"""
    from qgis.core import QgsVectorLayer, QgsFeature

    # メモリレイヤ作成
    mem_layer = QgsVectorLayer(
        f'Polygon?crs={crs.authid()}',
        layer_name,
        'memory'
    )
    provider = mem_layer.dataProvider()
    provider.addAttributes(fields.toList())
    mem_layer.updateFields()

    features = []
    total = shp_layer.featureCount()
    count = 0
    joined = 0

    for shp_feat in shp_layer.getFeatures():
        if cancel_check and cancel_check():
            break

        count += 1
        key1 = str(shp_feat['KEY1']).strip() if shp_feat['KEY1'] else ''

        # 複合キー: KEY1 + 整理番号_親番 + 整理番号_枝番
        # SHP側DBFフィールド名: 整理番号_親番→「整理番号_」, 整理番号_枝番→「整理番号1」に截断
        parent_val = shp_feat['整理番号_'] if '整理番号_' in shp_field_names else None
        parent = str(parent_val).strip() if parent_val is not None else ''
        branch_val = shp_feat['整理番号1'] if '整理番号1' in shp_field_names else None
        branch = str(branch_val).strip() if branch_val is not None else ''
        composite_key = key1
        if parent:
            composite_key += f'_{parent}'
        if branch:
            composite_key += f'_{branch}'

        new_feat = QgsFeature(mem_layer.fields())
        new_feat.setGeometry(shp_feat.geometry())

        # Shapefileフィールドをコピー（KEY1以外）
        idx = 0
        for f in shp_layer.fields():
            if f.name() != 'KEY1':
                new_feat.setAttribute(idx, shp_feat[f.name()])
                idx += 1

        # XLSXデータの結合（複合キーでルックアップ）
        xlsx_row = xlsx_data.get(composite_key)
        if xlsx_row:
            for fname in field_names:
                actual_name = fname
                if fname in shp_field_names and fname != 'KEY1':
                    actual_name = fname + '_森林簿'
                val = xlsx_row.get(fname)
                if val is not None:
                    new_feat.setAttribute(actual_name, str(val))
            joined += 1
        else:
            result.unmatched_shp += 1

        features.append(new_feat)

        if count % 5000 == 0 and progress_callback:
            pct = 45 + int((count / total) * 50)
            progress_callback(min(95, pct), f'レイヤ構築中... {count:,}/{total:,}')

    provider.addFeatures(features)
    mem_layer.updateExtents()
    result.joined = joined

    if progress_callback:
        progress_callback(98, f'メモリレイヤ構築完了: {joined:,}件結合')

    return mem_layer


def _build_geopackage(
    shp_layer, xlsx_data, fields, field_names, shp_field_names,
    crs, output_gpkg, layer_name,
    progress_callback, cancel_check, result
):
    """GeoPackageファイルとして構築"""
    from qgis.core import (
        QgsVectorLayer, QgsVectorFileWriter, QgsFeature,
        QgsCoordinateTransformContext,
    )

    # 一時メモリレイヤを作成して書き出し
    mem_layer = _build_memory_layer(
        shp_layer, xlsx_data, fields, field_names, shp_field_names,
        crs, layer_name, progress_callback, cancel_check, result
    )

    if mem_layer is None or not mem_layer.isValid():
        return None

    if progress_callback:
        progress_callback(96, 'GeoPackage書き出し中...')

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = 'GPKG'
    options.layerName = layer_name
    options.fileEncoding = 'UTF-8'

    context = QgsCoordinateTransformContext()
    write_result = QgsVectorFileWriter.writeAsVectorFormatV3(
        mem_layer, output_gpkg, context, options
    )
    # writeAsVectorFormatV3 の戻り値はQGISバージョンにより異なる
    if isinstance(write_result, tuple):
        error_code = write_result[0]
        error_msg = write_result[1] if len(write_result) > 1 else ''
    else:
        error_code = write_result
        error_msg = ''

    if error_code != QgsVectorFileWriter.WriterError.NoError:
        result.errors.append(f'GeoPackage書き出しエラー: {error_msg}')
        return mem_layer  # フォールバックとしてメモリレイヤを返す

    # GeoPackageからレイヤを読み込んで返す
    gpkg_layer = QgsVectorLayer(
        f'{output_gpkg}|layername={layer_name}',
        layer_name,
        'ogr'
    )
    if gpkg_layer.isValid():
        if progress_callback:
            progress_callback(98, 'GeoPackage書き出し完了')
        return gpkg_layer

    return mem_layer
