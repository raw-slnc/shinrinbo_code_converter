# -*- coding: utf-8 -*-
import importlib.util
import os
import platform
import re
import sys
from qgis.PyQt import uic
from qgis.PyQt.QtCore import QThread
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from .workers.parse_worker import ParseWorker
from .workers.convert_worker import ConvertWorker

REQUIRED_PACKAGES = {
    'openpyxl': 'openpyxl',
    'docx': 'python-docx',
}


def _missing_packages():
    return [pip_name for mod_name, pip_name in REQUIRED_PACKAGES.items()
            if importlib.util.find_spec(mod_name) is None]


def _python_executable():
    # QGIS本体内では sys.executable が qgis-bin.exe/qgis-ltr-bin.exe を指す。
    # 同じbinフォルダのpython.exeを直接指定してもPYTHONHOME等の環境変数が
    # 無く初期化に失敗するため、それらを設定してから起動する
    # python-qgis(-ltr).bat を探す。
    if platform.system() == 'Windows':
        bin_dir = os.path.dirname(sys.executable)
        for name in ('python-qgis.bat', 'python-qgis-ltr.bat'):
            candidate = os.path.join(bin_dir, name)
            if os.path.exists(candidate):
                return candidate
    return sys.executable or 'python3'


def _install_guidance(missing):
    pkgs = ' '.join(missing)
    python_exe = _python_executable()
    command = f'"{python_exe}" -m pip install --user {pkgs}'
    if platform.system() == 'Windows':
        note = 'コマンドプロンプトまたはOSGeo4Wシェルに上記コマンドを貼り付けて実行してください。'
    else:
        note = 'ターミナルに上記コマンドを貼り付けて実行してください。'
    return command, note


FORM_CLASS, _ = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), 'shinrinbo_dialog_base.ui')
)


class ShinrinboDialog(QDialog, FORM_CLASS):

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.setWindowIcon(QIcon(os.path.join(self.plugin_dir, 'icon.png')))
        self._parse_thread = None
        self._parse_worker = None
        self._convert_thread = None
        self._convert_worker = None
        self._pending_output_gpkg = None
        self._pending_layer_name = None
        self._pending_append_mode = False
        self._mode = 'initial'  # 'initial' | 'append'
        self._locked_height = None

        self._connect_signals()
        self._check_cache()
        self._check_dependencies()
        self._update_mode_ui()

    def showEvent(self, event):
        super().showEvent(event)
        # 初回表示時の実サイズでダイアログ高さを固定する。QTextEdit等のsizeHintは
        # 実際に画面へ表示されるまでスタイル・フォント計測が確定しないため、
        # ここで確定した高さをロックし、以降のモード切替で変動しないようにする。
        if self._locked_height is None:
            self.layout().activate()
            self._locked_height = self.height()
            self.setFixedHeight(self._locked_height)

    def _connect_signals(self):
        # モード切替ボタンがEnterキーで誤って反応しないようにする
        # （最初にフォーカスを持つボタンがautoDefaultによりEnter反応の
        # デフォルトボタン扱いになってしまうため）
        self.btnModeToggle.setAutoDefault(False)
        self.btnModeToggle.setDefault(False)

        self.btnBrowseDocx.clicked.connect(self._browse_docx)
        self.btnBrowseXlsx.clicked.connect(self._browse_xlsx)
        self.btnBrowseShp.clicked.connect(self._browse_shp)
        self.btnBrowseGpkg.clicked.connect(self._browse_gpkg)
        self.btnParseDocx.clicked.connect(self._parse_docx)
        self.btnExecute.clicked.connect(self._execute)
        self.btnCancel.clicked.connect(self._cancel)
        self.btnClose.clicked.connect(self.close)
        self.radioGeoPackage.toggled.connect(self._on_output_format_changed)
        self.btnModeToggle.clicked.connect(self._toggle_mode)
        self.btnManual.setAutoDefault(False)
        self.btnManual.setDefault(False)
        self.btnManual.clicked.connect(self._open_manual)
        self.btnBrowsePersonalInfo.clicked.connect(self._browse_personal_info)
        self.btnBrowseExistingGpkg.clicked.connect(self._browse_existing_gpkg)
        self.checkAppendToExistingGpkg.toggled.connect(self._on_append_mode_changed)

    # ---- モード切替 ----

    def _toggle_mode(self):
        self._mode = 'append' if self._mode == 'initial' else 'initial'
        self._update_mode_ui()

    def _update_mode_ui(self):
        is_initial = self._mode == 'initial'
        self.groupCodeTable.setVisible(is_initial)
        self.groupData.setVisible(is_initial)
        self.groupOutput.setVisible(is_initial)
        self.groupExistingGpkg.setVisible(not is_initial)
        self.btnModeToggle.setText('初期モード' if is_initial else '追加モード')
        self.groupPersonalInfo.setTitle(
            'Step 3: 所有者情報データ選択（任意）' if is_initial else 'Step 2: 所有者情報データ選択'
        )
        self.groupExecute.setTitle('Step 5: 実行' if is_initial else 'Step 3: 実行')
        self.textResult.clear()
        self.progressBar.setValue(0)
        self.labelStatus.setText('待機中')
        # ダイアログの高さはモード間で変えない。非表示グループ分の余白は
        # textResult（結果サマリ）が伸縮して吸収する（.ui側でExpanding設定）。

    def _open_manual(self):
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtGui import QDesktopServices
        path = os.path.join(self.plugin_dir, 'manual.html')
        if not os.path.exists(path):
            QMessageBox.information(self, 'マニュアル', 'マニュアルファイルが見つかりません。')
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_output_format_changed(self, checked):
        self.editGpkgPath.setEnabled(checked)
        self.btnBrowseGpkg.setEnabled(checked)
        self.checkAppendToExistingGpkg.setEnabled(checked)
        if not checked:
            self.checkAppendToExistingGpkg.setChecked(False)

    def _on_append_mode_changed(self, checked):
        self.editGpkgPath.clear()
        if checked:
            self.labelGpkgPath.setText('既存GPKG:')
            self.editGpkgPath.setPlaceholderText('追加先の既存GeoPackageを選択...')
        else:
            self.labelGpkgPath.setText('GPKG保存先:')
            self.editGpkgPath.setPlaceholderText('GeoPackage保存先を選択...')

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

    def _check_dependencies(self):
        missing = _missing_packages()
        if not missing:
            return
        command, note = _install_guidance(missing)
        self.textResult.setPlainText(
            f'必要なPythonパッケージが不足しています: {", ".join(missing)}\n\n'
            f'以下のコマンドをコピーして実行してください:\n{command}\n\n'
            f'{note}'
        )

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
        """県提供データは「{地域}_森林簿{日付8桁}.xlsx」「{地域}_計画図{日付8桁}.shp」
        のように、地域名と日付の両方が一致する組で提供される。日付が抽出できない、
        または一致するshpが見つからない場合は自動選択せず、手動選択に委ねる
        （違う年度のshpを誤って自動選択しないための安全側の判断）。
        """
        directory = os.path.dirname(xlsx_path)
        basename = os.path.basename(xlsx_path)
        if '_' not in basename:
            return
        region = basename.split('_')[0]
        date_match = re.search(r'(\d{8})\.xlsx$', basename)
        if not date_match:
            return
        date = date_match.group(1)
        shp_pattern = f'{region}_計画図{date}.shp'
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
        if self.checkAppendToExistingGpkg.isChecked():
            path, _ = QFileDialog.getOpenFileName(
                self, '追加先の既存GeoPackageを選択',
                os.path.expanduser('~'),
                'GeoPackage (*.gpkg)'
            )
            if path:
                self.editGpkgPath.setText(path)
            return

        path, _ = QFileDialog.getSaveFileName(
            self, 'GeoPackage保存先を選択',
            os.path.expanduser('~'),
            'GeoPackage (*.gpkg)'
        )
        if path:
            if not path.endswith('.gpkg'):
                path += '.gpkg'
            self.editGpkgPath.setText(path)

    def _browse_personal_info(self):
        if self.editPersonalInfoPath.text():
            self.editPersonalInfoPath.clear()
            self.labelPersonalInfoDetail.setText('未選択')
            self.btnBrowsePersonalInfo.setText('参照...')
            return

        path, _ = QFileDialog.getOpenFileName(
            self, '所有者情報データを選択',
            os.path.expanduser('~'),
            'CSV/Excel Files (*.csv *.xlsx)'
        )
        if path:
            self.editPersonalInfoPath.setText(path)
            self.labelPersonalInfoDetail.setText(os.path.basename(path))
            self.btnBrowsePersonalInfo.setText('クリア')

    def _browse_existing_gpkg(self):
        if self.editExistingGpkgPath.text():
            self.editExistingGpkgPath.clear()
            self.comboExistingLayer.clear()
            self.comboExistingLayer.setEnabled(False)
            self.btnBrowseExistingGpkg.setText('参照...')
            return

        path, _ = QFileDialog.getOpenFileName(
            self, '森林簿コンバーターで作成したGeoPackageを選択',
            os.path.expanduser('~'),
            'GeoPackage (*.gpkg)'
        )
        if not path:
            return
        self.editExistingGpkgPath.setText(path)
        self.btnBrowseExistingGpkg.setText('クリア')
        self.comboExistingLayer.clear()
        try:
            from osgeo import ogr
            ds = ogr.Open(path)
            if ds is None:
                raise RuntimeError('GeoPackageを開けませんでした')
            layer_names = [ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())]
            ds = None
        except Exception as e:
            QMessageBox.warning(self, '警告', f'レイヤ一覧の取得に失敗しました:\n{e}')
            self.comboExistingLayer.setEnabled(False)
            return
        if layer_names:
            self.comboExistingLayer.addItems(layer_names)
            self.comboExistingLayer.setEnabled(True)
        else:
            QMessageBox.warning(self, '警告', 'GeoPackage内にレイヤが見つかりませんでした。')
            self.comboExistingLayer.setEnabled(False)

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
        if self._mode == 'initial':
            self._execute_initial()
        else:
            self._execute_append()

    def _execute_initial(self):
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

        personal_info_path = self.editPersonalInfoPath.text()
        if personal_info_path and not self.radioGeoPackage.isChecked():
            QMessageBox.warning(
                self, '警告',
                '所有者情報データを結合するには出力形式をGeoPackageにしてください'
                '（メモリレイヤには結合できません）。'
            )
            return

        output_gpkg = None
        append_mode = False
        if self.radioGeoPackage.isChecked():
            output_gpkg = self.editGpkgPath.text()
            if not output_gpkg:
                QMessageBox.warning(self, '警告', 'GeoPackage保存先を選択してください。')
                return
            append_mode = self.checkAppendToExistingGpkg.isChecked()
            if append_mode and not os.path.exists(output_gpkg):
                QMessageBox.warning(self, '警告', '追加先の既存GeoPackageを選択してください。')
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
        self._pending_append_mode = append_mode

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
        append_mode = self._pending_append_mode

        # GeoPackage書き出しをメインスレッドで実行
        # ワーカースレッドでQgsVectorFileWriterを使うとWindowsでGDAL/OGRが
        # アクセス違反を起こすため、メインスレッドで書き出す
        if output_gpkg and layer and layer.isValid():
            if append_mode:
                from osgeo import ogr
                ds = ogr.Open(output_gpkg)
                existing_layers = (
                    [ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())]
                    if ds else []
                )
                ds = None
                if layer_name in existing_layers:
                    reply = QMessageBox.question(
                        self, '確認',
                        f'GeoPackage内に既に同名のレイヤ「{layer_name}」が存在します。\n'
                        '上書きしてよろしいですか？（既存レイヤの内容は失われます）',
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        self.btnExecute.setEnabled(True)
                        self.btnCancel.setEnabled(False)
                        self.labelStatus.setText('中止')
                        return

            self.labelStatus.setText('GeoPackage書き出し中...')
            from qgis.core import (
                QgsVectorFileWriter, QgsCoordinateTransformContext, QgsVectorLayer
            )
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = layer_name
            options.fileEncoding = 'UTF-8'
            if append_mode:
                options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
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

                personal_info_path = self.editPersonalInfoPath.text()
                if personal_info_path and os.path.exists(personal_info_path):
                    self.labelStatus.setText('所有者情報データ読込中...')
                    QApplication.processEvents()
                    try:
                        from .core.personal_info_reader import read_personal_info_file
                        source_rows = read_personal_info_file(personal_info_path)
                    except Exception as e:
                        QMessageBox.critical(
                            self, 'エラー', f'所有者情報データの読込に失敗しました:\n{e}'
                        )
                        source_rows = None
                    if source_rows is not None:
                        pi_result = self._run_personal_info_join(
                            output_gpkg, layer_name, source_rows
                        )
                        if pi_result and not pi_result.errors:
                            refreshed = QgsVectorLayer(
                                f'{output_gpkg}|layername={layer_name}', layer_name, 'ogr'
                            )
                            if refreshed.isValid():
                                layer = refreshed
                            result['summary'] = (
                                result.get('summary', '') + '\n\n' + pi_result.summary()
                            )
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

    # ---- 追加モード: 既存GeoPackageへの個人情報結合 ----

    def _execute_append(self):
        gpkg_path = self.editExistingGpkgPath.text()
        if not gpkg_path or not os.path.exists(gpkg_path):
            QMessageBox.warning(self, '警告', '既存のGeoPackageを選択してください。')
            return
        layer_name = self.comboExistingLayer.currentText()
        if not layer_name:
            QMessageBox.warning(self, '警告', 'レイヤを選択してください。')
            return
        personal_info_path = self.editPersonalInfoPath.text()
        if not personal_info_path or not os.path.exists(personal_info_path):
            QMessageBox.warning(self, '警告', '所有者情報データファイルを選択してください。')
            return

        self.btnExecute.setEnabled(False)
        self.progressBar.setValue(0)
        self.textResult.clear()
        self.labelStatus.setText('所有者情報データ読込中...')
        QApplication.processEvents()

        try:
            from .core.personal_info_reader import read_personal_info_file
            source_rows = read_personal_info_file(personal_info_path)
        except Exception as e:
            QMessageBox.critical(self, 'エラー', f'所有者情報データの読込に失敗しました:\n{e}')
            self.btnExecute.setEnabled(True)
            self.labelStatus.setText('エラー')
            return

        result = self._run_personal_info_join(gpkg_path, layer_name, source_rows)
        self.btnExecute.setEnabled(True)
        if result is None:
            self.labelStatus.setText('エラー')
            return

        self.progressBar.setValue(100)
        self.textResult.setPlainText(result.summary())
        self.labelStatus.setText('完了' if not result.errors else 'エラー')

    def _run_personal_info_join(self, gpkg_path, layer_name, source_rows):
        """個人情報結合をメインスレッドで同期実行する。

        GeoPackageへのOGR書き込みはWindowsでQThread併用時にアクセス違反を
        起こすリスクがあるため（ConvertWorkerのGeoPackage書き出しと同じ理由）、
        ワーカースレッドは使わずprocessEvents()で進捗更新しながら実行する。
        個人情報データは申請範囲に限られ森林簿本体より小規模なため許容する。
        """
        from .core.personal_info_joiner import join_personal_info
        overwrite = self.checkOverwriteExisting.isChecked()

        def _progress(pct, msg):
            self.progressBar.setValue(pct)
            self.labelStatus.setText(msg)
            QApplication.processEvents()

        try:
            result = join_personal_info(
                gpkg_path=gpkg_path,
                layer_name=layer_name,
                source_rows=source_rows,
                overwrite_existing=overwrite,
                progress_callback=_progress,
            )
        except Exception as e:
            QMessageBox.critical(self, 'エラー', f'所有者情報の結合に失敗しました:\n{e}')
            return None

        if result.errors:
            QMessageBox.critical(self, 'エラー', '\n\n'.join(result.errors))
        return result
