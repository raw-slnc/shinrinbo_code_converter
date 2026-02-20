# -*- coding: utf-8 -*-
"""変換パイプラインワーカースレッド

Windows対応:
- XLSX読込(openpyxl/lxml)はメインスレッドで実施済みのxlsx_rowsを受け取る
- GeoPackage書き出し(QgsVectorFileWriter)はメインスレッドで実施
- このワーカーは純Pythonのコード変換とQgsVectorLayer(メモリ)構築のみ行う
"""
from qgis.PyQt.QtCore import QObject, pyqtSignal
import traceback


class ConvertWorker(QObject):
    progress = pyqtSignal(int, str)       # percent, message
    finished = pyqtSignal(dict)           # result dict with 'layer' and 'summary'
    error = pyqtSignal(str)

    def __init__(self, cache_path, xlsx_rows, shp_path, layer_name, keep_codes):
        super().__init__()
        self.cache_path = cache_path
        self.xlsx_rows = xlsx_rows  # メインスレッドで事前読込済みのXLSXデータ
        self.shp_path = shp_path
        self.layer_name = layer_name
        self.keep_codes = keep_codes
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from ..core.code_table_registry import CodeTableRegistry
            from ..core.data_joiner import join_data

            # コード表レジストリ読込
            self.progress.emit(0, 'コード表キャッシュ読込中...')
            registry = CodeTableRegistry()
            registry.load_from_json(self.cache_path)

            # 結合実行
            # output_gpkg=None: GeoPackage書き出しはメインスレッドで行うため常にメモリレイヤを返す
            join_result = join_data(
                registry=registry,
                xlsx_rows=self.xlsx_rows,
                shp_path=self.shp_path,
                output_gpkg=None,
                layer_name=self.layer_name,
                keep_codes=self.keep_codes,
                progress_callback=lambda pct, msg: self.progress.emit(pct, msg),
                cancel_check=lambda: self._cancelled,
            )

            self.finished.emit({
                'layer': join_result.layer,
                'summary': join_result.summary(),
            })

        except Exception as e:
            self.error.emit(f'{str(e)}\n{traceback.format_exc()}')
