# Shinrinbo Code Converter / 森林簿コード変換

静岡県がオープンデータとして公開している森林簿（XLSX）のコード値を、森林簿コード表（DOCX）に基づき人間が読めるテキストに変換し、森林計画図（Shapefile）と結合してQGISレイヤとして出力するプラグインです。

## 主な機能

- 森林簿コード表（DOCX）の解析・JSONキャッシュ
- 森林簿（XLSX）の約50種類のCDカラムを自動テキスト変換
- 森林計画図（Shapefile）との複合キー（KEY1 + 整理番号_枝番）による結合
- メモリレイヤまたはGeoPackageとして出力
- Shapefileの自動検出（同一ディレクトリ内の計画図を自動選択）

## 使い方

1. コード表DOCXを選択し「コード表解析」を実行（初回のみ、結果はキャッシュされる）
2. 森林簿XLSXを選択（計画図Shapefileが同一ディレクトリにあれば自動検出）
3. 出力形式（メモリレイヤ / GeoPackage）とオプションを選択
4. 「変換実行」でQGISレイヤとして出力

## 同一小班に複数樹種がある場合の注意

森林簿では同一小班（KEY1）に複数の林種・樹種が存在する場合があります（例: スギ 1.3ha とヒノキ 0.7ha）。この場合、整理番号_枝番で区別された複数レコードが同一ジオメトリのフィーチャとして出力されます。

これはデータとして正確ですが、QGIS上では同一形状のポリゴンが重なるため、シンポロジ（色分け）では上のフィーチャしか見えません。

対処方法:
- **ルールベースレンダラ**: `"整理番号_枝番_簿" = '0'` でフィルタし主レコードのみ表示
- **パターン塗り分け**: 枝番0を塗りつぶし、枝番1以降をハッチングで重ね表示
- **地物情報パネル**: ポリゴンクリックで全枝番のレコードを確認可能

## 必要なデータ

- R6森林簿コード表（詳細版）.docx（静岡県配布）
- 森林簿XLSX（静岡県オープンデータ）
- 森林計画図Shapefile（静岡県オープンデータ）

## 動作要件

- QGIS 3.0 以上
- Python パッケージ: openpyxl, python-docx
  - QGIS同梱のPythonには含まれないため、事前に以下でインストールしてください:
    ```
    pip install openpyxl python-docx
    ```
  - 必要なパッケージが不足している場合、プラグイン起動時に画面下部の結果欄にコピー可能なインストールコマンドが表示されます

## インストール

1. このリポジトリをクローンまたはダウンロード
2. `shinrinbo_code_converter` フォルダを QGIS のプラグインディレクトリにコピー
   - Linux: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - macOS: `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/`
3. QGISを起動し、プラグインマネージャから有効化

## サポート

開発を応援していただけると嬉しいです。
https://paypal.me/rawslnc

## ライセンス

GNU General Public License v2 以降

## 作者

Copyright (C) 2026 Hideharu Masai
