# -*- coding: utf-8 -*-
"""DOCXコード表解析ワーカースレッド"""
from qgis.PyQt.QtCore import QObject, pyqtSignal
import traceback


class ParseWorker(QObject):
    progress = pyqtSignal(int, str)       # percent, message
    finished = pyqtSignal(int)            # table_count
    error = pyqtSignal(str)

    def __init__(self, docx_path: str, cache_path: str):
        super().__init__()
        self.docx_path = docx_path
        self.cache_path = cache_path

    def run(self):
        try:
            from ..core.code_table_parser import parse_docx
            from ..core.code_table_registry import CodeTableRegistry

            def on_progress(pct, msg):
                self.progress.emit(pct, msg)

            parsed = parse_docx(self.docx_path, progress_callback=on_progress)

            registry = CodeTableRegistry()
            registry.load_from_parsed(parsed)

            # キャッシュ保存
            import os
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            registry.save_to_json(self.cache_path)

            self.finished.emit(registry.get_table_count())

        except Exception as e:
            self.error.emit(f'{str(e)}\n{traceback.format_exc()}')
