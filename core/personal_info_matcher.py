# -*- coding: utf-8 -*-
"""
個人情報データの列判定・検証モジュール

森林簿の個体識別キー（KEY1 + 整理番号_親番 + 整理番号_枝番）が
市町提供データに省略なく含まれていることを検証する。
ヘッダー名は完全一致を要求するが、DBF10バイト制限による既知の
切り詰め形（layer_builder.pyのSHP側実例と同じパターン）のみ許容する。

氏名・住所列はヘッダー名を信用せず、値の文字列傾向（漢字の有無・
数字の含有率）で多数決判定する。
"""
import re
from typing import Dict, List, Optional, Tuple

# KEY1・整理番号の許容ヘッダー名バリエーション
# 「整理番号_」「整理番号1」はSHP側DBFの10バイト制限による既知の切り詰め形
# （layer_builder.py参照: 整理番号_親番→整理番号_, 整理番号_枝番→整理番号1）
KEY1_VARIANTS = ['KEY1']
PARENT_VARIANTS = ['整理番号_親番', '整理番号_']
BRANCH_VARIANTS = ['整理番号_枝番', '整理番号1']

_KANJI_RE = re.compile(r'[一-鿿]')
_DIGIT_RE = re.compile(r'[0-9０-９]')


class HeaderValidationResult:
    def __init__(self):
        self.ok = False
        self.key1_col: Optional[str] = None
        self.parent_col: Optional[str] = None
        self.branch_col: Optional[str] = None
        self.missing: List[str] = []

    def error_message(self) -> str:
        labels = {
            'key1': 'KEY1',
            'parent': '整理番号_親番（または切り詰め形「整理番号_」）',
            'branch': '整理番号_枝番（または切り詰め形「整理番号1」）',
        }
        lines = ['以下の列がデータに見つかりませんでした:']
        for key in self.missing:
            lines.append(f'  - {labels[key]}')
        lines.append('')
        lines.append(
            '林小班+整理番号（親番+枝番）は森林簿データの個体識別キーです。'
            'これが省略・改変されたデータは個人情報の結合先を正しく特定できないため、'
            '処理を中止します。'
        )
        return '\n'.join(lines)


def _resolve_column(headers: List[str], variants: List[str]) -> Optional[str]:
    for v in variants:
        if v in headers:
            return v
    return None


def validate_headers(headers: List[str]) -> HeaderValidationResult:
    """個人情報データのヘッダーを検証し、KEY1・整理番号列を解決する。"""
    result = HeaderValidationResult()
    result.key1_col = _resolve_column(headers, KEY1_VARIANTS)
    result.parent_col = _resolve_column(headers, PARENT_VARIANTS)
    result.branch_col = _resolve_column(headers, BRANCH_VARIANTS)

    if result.key1_col is None:
        result.missing.append('key1')
    if result.parent_col is None:
        result.missing.append('parent')
    if result.branch_col is None:
        result.missing.append('branch')

    result.ok = not result.missing
    return result


def build_composite_key(key1, parent, branch) -> str:
    """KEY1+整理番号_親番+整理番号_枝番の複合キー文字列を組み立てる。
    data_joiner.py / layer_builder.py と同一のフォーマットに揃える。
    """
    key1_s = str(key1).strip() if key1 is not None else ''
    parent_s = str(parent).strip() if parent is not None else ''
    branch_s = str(branch).strip() if branch is not None else ''
    composite = key1_s
    if parent_s:
        composite += f'_{parent_s}'
    if branch_s:
        composite += f'_{branch_s}'
    return composite


# 標準フォーマット（県提供xlsx）でヘッダーが生きている場合はこちらを最優先で使う。
# 市町CSVで名前が変わっている/崩れている場合のみ、内容判定にフォールバックする。
KNOWN_NAME_HEADERS = ['登記所有者_漢字']
KNOWN_ADDRESS_HEADERS = ['登記所有者_住所']


def _has_kanji(value: str) -> bool:
    return bool(_KANJI_RE.search(value))


def _has_digit(value: str) -> bool:
    return bool(_DIGIT_RE.search(value))


def classify_name_address_columns(
    rows: List[Dict[str, str]], candidate_columns: List[str]
) -> Tuple[Optional[str], Optional[str]]:
    """候補列から氏名列・住所列を判定する。

    1. ヘッダー名に「登記所有者_漢字」「登記所有者_住所」がそのまま残っていれば
       それを最優先で採用する（内容判定より確実なため）。
    2. 見つからない列のみ、値の文字列傾向（多数決）でフォールバック判定する。

    Returns:
        (name_col, address_col) 判定できない場合はNone
    """
    name_col = next((c for c in candidate_columns if c in KNOWN_NAME_HEADERS), None)
    address_col = next((c for c in candidate_columns if c in KNOWN_ADDRESS_HEADERS), None)

    if name_col is None or address_col is None:
        remaining = [c for c in candidate_columns if c not in (name_col, address_col)]
        guessed_name, guessed_address = _classify_by_content(rows, remaining)
        if name_col is None:
            name_col = guessed_name
        if address_col is None:
            address_col = guessed_address

    return name_col, address_col


def _classify_by_content(
    rows: List[Dict[str, str]], candidate_columns: List[str]
) -> Tuple[Optional[str], Optional[str]]:
    """値の文字列傾向で氏名列・住所列を判定する（ヘッダー名が使えない場合のみ）。

    住所らしさ = 漢字と数字の両方を含む行の割合。
    氏名らしさ = 漢字を含み数字を含まない行の割合。
    数値のみのCDカラム等（漢字を一切含まない列）は候補から除外し、
    無関係な列が消去法で氏名/住所に選ばれることを防ぐ。
    """
    if not candidate_columns:
        return None, None

    address_scores: Dict[str, float] = {}
    name_scores: Dict[str, float] = {}
    for col in candidate_columns:
        values = [str(r.get(col, '') or '').strip() for r in rows]
        values = [v for v in values if v]
        if not values:
            continue
        kanji_count = sum(1 for v in values if _has_kanji(v))
        if kanji_count / len(values) < 0.5:
            # 漢字をほとんど含まない列（CDコード等）は氏名/住所の候補から除外
            continue
        address_scores[col] = sum(
            1 for v in values if _has_kanji(v) and _has_digit(v)
        ) / len(values)
        name_scores[col] = sum(
            1 for v in values if _has_kanji(v) and not _has_digit(v)
        ) / len(values)

    if not address_scores:
        return None, None

    address_col = max(address_scores, key=address_scores.get)

    name_candidates = {c: s for c, s in name_scores.items() if c != address_col}
    if not name_candidates:
        return None, address_col

    name_col = max(name_candidates, key=name_candidates.get)
    if name_scores[name_col] <= 0.0:
        return None, address_col

    return name_col, address_col
