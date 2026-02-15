# -*- coding: utf-8 -*-
"""
コード表レジストリ
パース済みコード表の統合管理、JSONキャッシュ入出力、コード値の検索を行う。
"""
import json
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class CodeTableRegistry:
    """コード表の統合管理クラス"""

    def __init__(self):
        self.tables: Dict[str, Dict[str, str]] = {}
        self.oaza: Dict[str, Dict[str, str]] = {}
        self.municipality_names: Dict[str, str] = {}

    def load_from_parsed(self, parsed_data: dict):
        """code_table_parser.parse_docx() の結果から読み込む"""
        self.tables = parsed_data.get('tables', {})
        self.oaza = parsed_data.get('oaza', {})
        self.municipality_names = parsed_data.get('municipality_names', {})

    def save_to_json(self, path: str):
        """JSONファイルにキャッシュ保存"""
        data = {
            'tables': self.tables,
            'oaza': self.oaza,
            'municipality_names': self.municipality_names,
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_from_json(self, path: str):
        """JSONキャッシュから読み込む"""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        self.tables = data.get('tables', {})
        self.oaza = data.get('oaza', {})
        self.municipality_names = data.get('municipality_names', {})

    def lookup(self, table_id: str, cd_value) -> Optional[str]:
        """コード表からテキスト値を検索する。

        Args:
            table_id: コード表ID (例: '1.12')
            cd_value: コード値 (例: 1, '01', '5')

        Returns:
            対応するテキスト値。見つからない場合はNone。
        """
        table = self.tables.get(table_id)
        if table is None:
            return None

        # コード値を文字列に変換して検索
        cd_str = str(cd_value).strip() if cd_value is not None else ''
        if not cd_str or cd_str == '0' or cd_str == 'None':
            # 0は多くのテーブルで「なし」を意味する
            result = table.get(cd_str)
            if result:
                return result
            result = table.get('0')
            if result:
                return result
            result = table.get('00')
            if result:
                return result
            return None

        # 直接一致
        if cd_str in table:
            return table[cd_str]

        # ゼロパディングを試す (1 → 01, 01 → 1)
        if cd_str.isdigit():
            padded = cd_str.zfill(2)
            if padded in table:
                return table[padded]
            unpadded = cd_str.lstrip('0') or '0'
            if unpadded in table:
                return table[unpadded]

        return None

    def lookup_oaza(self, municipality_cd, oaza_cd) -> Optional[str]:
        """大字コードを市町村別に検索する。

        Args:
            municipality_cd: 市町村CD
            oaza_cd: 大字CD

        Returns:
            大字名。見つからない場合はNone。
        """
        muni_str = str(municipality_cd).strip() if municipality_cd is not None else ''
        oaza_str = str(oaza_cd).strip() if oaza_cd is not None else ''

        if not muni_str or not oaza_str:
            return None

        muni_table = self.oaza.get(muni_str)
        if muni_table is None:
            # ゼロパディングを試す
            if muni_str.isdigit():
                for alt in [muni_str.zfill(2), muni_str.lstrip('0') or '0']:
                    muni_table = self.oaza.get(alt)
                    if muni_table:
                        break
        if muni_table is None:
            return None

        if oaza_str in muni_table:
            return muni_table[oaza_str]
        if oaza_str.isdigit():
            for alt in [oaza_str.zfill(2), oaza_str.lstrip('0') or '0']:
                if alt in muni_table:
                    return muni_table[alt]

        return None

    def get_table_count(self) -> int:
        """登録されたコード表の総数"""
        return len(self.tables) + len(self.oaza)

