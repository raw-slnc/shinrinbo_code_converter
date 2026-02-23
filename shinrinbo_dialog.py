# -*- coding: utf-8 -*-
import os
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QThread
from qgis.PyQt.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from .workers.parse_worker import ParseWorker
from .workers.convert_worker import ConvertWorker

FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), 'shinrinbo_dialog_base.ui')
)


class ShinrinboDialog(QDialog, FORM_CLASS):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self._parse_thread = None
        self._parse_worker = None
        self._convert_thread = None
        self._convert_worker = None
        self._pending_output_gpkg = None
        self._pending_layer_name = None

        self._connect_signals()
        self._check_cache()

    def _connect_signals(self):
        self.btnBrowseDocx.clicked.connect(self._browse_docx)
        self.btnBrowseXlsx.clicked.connect(self._browse_xlsx)
        self.btnBrowseShp.clicked.connect(self._browse_shp)
        self.btnBrowseGpkg.clicked.connect(self._browse_gpkg)
        self.btnParseDocx.clicked.connect(self._parse_docx)
        self.btnExecute.clicked.connect(self._execute)
        self.btnCancel.clicked.connect(self._cancel)
        self.btnClose.clicked.connect(self.close)
        self.radioGeoPackage.toggled.connect(self._on_output_format_changed)

    def _on_output_format_changed(self, checked):
        self.editGpkgPath.setEnabled(checked)
        self.btnBrowseGpkg.setEnabled(checked)

    def _cache_path(self):
        return os.path.join(self.plugin_dir, 'cache', 'code_tables.json')

    def _check_cache(self):
        cache = self._cache_path()
        if os.path.exists(cache):
            import datetime
            mtime = os.path.getmtime(cache)
            dt = datetime.datetime.fromtimestamp(mtime)
            self.labelCacheInfo.setText(f'キャッシュ済み ({dt:%Y-%m-%d %H:%M})')
        else:
            self.labelCacheInfo.setText('未解析')

    # ---- File Browse ----

    def _browse_docx(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'コード表DOCXを選択',
            os.path.expanduser('~'),
            'Word Documents (*.docx)'
        )
        if path:
            self.editDocxPath.setText(path)

    def _browse_xlsx(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '森林簿XLSXを選択',
            os.path.expanduser('~'),
            'Excel Files (*.xlsx)'
        )
        if path:
            self.editXlsxPath.setText(path)
            basename = os.path.basename(path)
            self.labelXlsxDetail.setText(f'{basename}')
            # Auto-detect layer name from filename
            region = basename.split('_')[0] if '_' in basename else '変換済'
            self.editLayerName.setText(f'森林簿_{region}')
            # Try to auto-detect shapefile in same directory
            self._auto_detect_shapefile(path)

    def _auto_detect_shapefile(self, xlsx_path):
        directory = os.path.dirname(xlsx_path)
        basename = os.path.basename(xlsx_path)
        region = basename.split('_')[0] if '_' in basename else None
        if region:
            shp_pattern = f'{region}_計画図.shp'
            shp_path = os.path.join(directory, shp_pattern)
            if os.path.exists(shp_path):
                self.editShpPath.setText(shp_path)
                self.labelShpDetail.setText(f'{shp_pattern} (自動検出)')

    def _browse_shp(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '森林計画図Shapefileを選択',
            os.path.expanduser('~'),
            'Shapefiles (*.shp)'
        )
        if path:
            self.editShpPath.setText(path)
            self.labelShpDetail.setText(os.path.basename(path))

    def _browse_gpkg(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'GeoPackage保存先を選択',
            os.path.expanduser('~'),
            'GeoPackage (*.gpkg)'
        )
        if path:
            if not path.endswith('.gpkg'):
                path += '.gpkg'
            self.editGpkgPath.setText(path)

    # ---- Parse DOCX ----

    def _parse_docx(self):
        docx_path = self.editDocxPath.text()
        if not docx_path or not os.path.exists(docx_path):
            QMessageBox.warning(self, '警告', 'コード表DOCXファイルを選択してください。')
            return

        self.btnParseDocx.setEnabled(False)
        self.labelCacheInfo.setText('解析中...')

        self._parse_thread = QThread()
        self._parse_worker = ParseWorker(docx_path, self._cache_path())
        self._parse_worker.moveToThread(self._parse_thread)

        self._parse_thread.started.connect(self._parse_worker.run)
        self._parse_worker.progress.connect(self._on_parse_progress)
        self._parse_worker.finished.connect(self._on_parse_finished)
        self._parse_worker.error.connect(self._on_parse_error)
        self._parse_worker.finished.connect(self._parse_thread.quit)
        self._parse_worker.error.connect(self._parse_thread.quit)

        self._parse_thread.start()

    def _on_parse_progress(self, percent, message):
        self.labelCacheInfo.setText(f'解析中... {message}')

    def _on_parse_finished(self, table_count):
        self.btnParseDocx.setEnabled(True)
        self._check_cache()
        self.textResult.append(f'コード表解析完了: {table_count}個のコード表をキャッシュしました。')

    def _on_parse_error(self, error_msg):
        self.btnParseDocx.setEnabled(True)
        self.labelCacheInfo.setText('解析エラー')
        QMessageBox.critical(self, 'エラー', f'コード表の解析に失敗しました:\n{error_msg}')

    # ---- Execute Conversion ----

    def _execute(self):
        # Validate inputs
        cache = self._cache_path()
        if not os.path.exists(cache):
            QMessageBox.warning(self, '警告', 'まずコード表を解析してください。')
            return
        xlsx_path = self.editXlsxPath.text()
        if not xlsx_path or not os.path.exists(xlsx_path):
            QMessageBox.warning(self, '警告', '森林簿XLSXファイルを選択してください。')
            return
        shp_path = self.editShpPath.text()
        if not shp_path or not os.path.exists(shp_path):
            QMessageBox.warning(self, '警告', '森林計画図Shapefileを選択してください。')
            return

        output_gpkg = None
        if self.radioGeoPackage.isChecked():
            output_gpkg = self.editGpkgPath.text()
            if not output_gpkg:
                QMessageBox.warning(self, '警告', 'GeoPackage保存先を選択してください。')
                return

        layer_name = self.editLayerName.text() or '森林簿_変換済'
        keep_codes = self.checkKeepCodes.isChecked()

        self.btnExecute.setEnabled(False)
        self.btnCancel.setEnabled(True)
        self.progressBar.setValue(0)
        self.textResult.clear()

        # XLSXをメインスレッドで読込
        # ワーカースレッドでopenpyxlを使うとlxml/libxml2のスレッド非安全により
        # Windowsでアクセス違反が発生するため、事前読込してdictリストとして渡す

        # load_workbook 中はメインスレッドがブロックされるため空バーで待機表示
        # （マーキーはprocessEvents()が止まると固まって見えるため使わない）
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)
        self.labelStatus.setText('XLSXファイル読込中...')
        QApplication.processEvents()

        def _on_xlsx_progress(current, total):
            # load_workbook 完了後の最初の呼び出し（current==0）で上限を確定する
            if current == 0 and total > 0:
                self.progressBar.setRange(0, total)
            self.progressBar.setValue(current)
            self.labelStatus.setText(f'XLSX読込中... {current:,} / {total:,} 行')
            QApplication.processEvents()

        try:
            from .core.xlsx_reader import read_xlsx
            xlsx_rows = list(read_xlsx(xlsx_path, progress_callback=_on_xlsx_progress))
        except Exception as e:
            self.progressBar.setRange(0, 100)
            self.progressBar.setValue(0)
            QMessageBox.critical(self, 'エラー', f'XLSXの読み込みに失敗しました:\n{e}')
            self.btnExecute.setEnabled(True)
            self.btnCancel.setEnabled(False)
            self.labelStatus.setText('エラー')
            return

        # ConvertWorker は 0-100% の range で進捗を送出するので元に戻す
        self.progressBar.setRange(0, 100)
        self.progressBar.setValue(0)

        self._pending_output_gpkg = output_gpkg
        self._pending_layer_name = layer_name

        self._convert_thread = QThread()
        self._convert_worker = ConvertWorker(
            cache_path=cache,
            xlsx_rows=xlsx_rows,
            shp_path=shp_path,
            layer_name=layer_name,
            keep_codes=keep_codes,
        )
        self._convert_worker.moveToThread(self._convert_thread)

        self._convert_thread.started.connect(self._convert_worker.run)
        self._convert_worker.progress.connect(self._on_convert_progress)
        self._convert_worker.finished.connect(self._on_convert_finished)
        self._convert_worker.error.connect(self._on_convert_error)
        self._convert_worker.finished.connect(self._convert_thread.quit)
        self._convert_worker.error.connect(self._convert_thread.quit)
        self._convert_thread.finished.connect(self._cleanup_convert_thread)

        self.labelStatus.setText('開始中...')
        self._convert_thread.start()

    def _on_convert_progress(self, percent, message):
        self.progressBar.setValue(percent)
        self.labelStatus.setText(message)

    def _on_convert_finished(self, result):
        self.btnExecute.setEnabled(True)
        self.btnCancel.setEnabled(False)
        self.progressBar.setValue(100)

        layer = result.get('layer')
        output_gpkg = self._pending_output_gpkg
        layer_name = self._pending_layer_name or '森林簿_変換済'

        # GeoPackage書き出しをメインスレッドで実行
        # ワーカースレッドでQgsVectorFileWriterを使うとWindowsでGDAL/OGRが
        # アクセス違反を起こすため、メインスレッドで書き出す
        if output_gpkg and layer and layer.isValid():
            self.labelStatus.setText('GeoPackage書き出し中...')
            from qgis.core import (
                QgsVectorFileWriter, QgsCoordinateTransformContext, QgsVectorLayer
            )
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = layer_name
            options.fileEncoding = 'UTF-8'
            context = QgsCoordinateTransformContext()
            write_result = QgsVectorFileWriter.writeAsVectorFormatV3(
                layer, output_gpkg, context, options
            )
            if isinstance(write_result, tuple):
                error_code = write_result[0]
                error_msg = write_result[1] if len(write_result) > 1 else ''
            else:
                error_code = write_result
                error_msg = ''

            if error_code == QgsVectorFileWriter.NoError:
                gpkg_layer = QgsVectorLayer(
                    f'{output_gpkg}|layername={layer_name}', layer_name, 'ogr'
                )
                if gpkg_layer.isValid():
                    layer = gpkg_layer
            else:
                QMessageBox.warning(
                    self, '警告',
                    f'GeoPackage書き出しエラー: {error_msg}\nメモリレイヤとして追加します。'
                )

        self.labelStatus.setText('完了')

        if layer and layer.isValid():
            from qgis.core import QgsProject
            QgsProject.instance().addMapLayer(layer)

        summary = result.get('summary', '')
        self.textResult.setPlainText(summary)

    def _cleanup_convert_thread(self):
        if self._convert_worker:
            self._convert_worker.deleteLater()
            self._convert_worker = None
        if self._convert_thread:
            self._convert_thread.deleteLater()
            self._convert_thread = None

    def _on_convert_error(self, error_msg):
        self.btnExecute.setEnabled(True)
        self.btnCancel.setEnabled(False)
        self.labelStatus.setText('エラー')
        QMessageBox.critical(self, 'エラー', f'変換に失敗しました:\n{error_msg}')

    def _cancel(self):
        if self._convert_worker:
            self._convert_worker.cancel()
        self.labelStatus.setText('キャンセル中...')
