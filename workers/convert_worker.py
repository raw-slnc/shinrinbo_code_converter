# -*- coding: utf-8 -*-
"""変換パイプラインワーカースレッド"""
from qgis.PyQt.QtCore import QObject, pyqtSignal
import traceback


class ConvertWorker(QObject):
    progress = pyqtSignal(int, str)       # percent, message
    finished = pyqtSignal(dict)           # result dict with 'layer' and 'summary'
    error = pyqtSignal(str)

    def __init__(self, cache_path, xlsx_path, shp_path,
                 output_gpkg, layer_name, keep_codes):
        super().__init__()
        self.cache_path = cache_path
        self.xlsx_path = xlsx_path
        self.shp_path = shp_path
        self.output_gpkg = output_gpkg
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
            join_result = join_data(
                registry=registry,
                xlsx_path=self.xlsx_path,
                shp_path=self.shp_path,
                output_gpkg=self.output_gpkg,
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
