"""
원문 바로가기 링크 생성 및 파일 열기 유틸리티.

- PDF: 브라우저에서 해당 페이지로 직접 이동 (file:///path#page=N)
- Word: Windows 기본 앱으로 열기 (os.startfile)
"""
import os
import sys
import subprocess
from pathlib import Path
from typing import Optional
from src.utils.logging_utils import get_logger

logger = get_logger("file_viewer")


def generate_file_link(source_path: str, page: int = 1) -> str:
    """
    원문 바로가기 링크를 생성합니다.

    Args:
        source_path: 파일 절대 경로
        page: 페이지 번호 (PDF용)

    Returns:
        file:// URI 문자열
    """
    if not source_path:
        return ""

    path_normalized = source_path.replace("\\", "/")
    if not path_normalized.startswith("/"):
        path_normalized = "/" + path_normalized

    file_uri = f"file://{path_normalized}"

    if source_path.lower().endswith(".pdf"):
        file_uri += f"#page={page}"

    return file_uri


def open_file_at_page(source_path: str, page: int = 1) -> bool:
    """
    파일을 OS 기본 앱으로 열어줍니다.

    Args:
        source_path: 파일 경로
        page: 페이지 번호 (현재 PDF 뷰어에 따라 지원 여부가 다름)

    Returns:
        성공 여부
    """
    path = Path(source_path)
    if not path.exists():
        logger.error(f"파일이 존재하지 않습니다: {source_path}")
        return False

    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)])
        else:
            subprocess.run(["xdg-open", str(path)])

        logger.info(f"파일 열기: {path.name} (페이지 {page})")
        return True
    except Exception as e:
        logger.error(f"파일 열기 실패: {e}")
        return False


def format_source_badge(source_file: str, page_numbers: str, similarity: float) -> str:
    """
    Streamlit 표시용 출처 뱃지 HTML을 생성합니다.

    Args:
        source_file: 파일명
        page_numbers: 페이지 번호 문자열 (쉼표 구분)
        similarity: 유사도 점수

    Returns:
        HTML 문자열
    """
    # 유사도에 따른 색상
    if similarity >= 0.8:
        color = "#10B981"  # green
        label = "매우 높음"
    elif similarity >= 0.6:
        color = "#3B82F6"  # blue
        label = "높음"
    elif similarity >= 0.4:
        color = "#F59E0B"  # amber
        label = "보통"
    else:
        color = "#6B7280"  # gray
        label = "낮음"

    return (
        f'<div style="display:inline-flex; align-items:center; gap:8px; '
        f'padding:6px 12px; border-radius:8px; '
        f'background:rgba(99,102,241,0.1); border:1px solid rgba(99,102,241,0.2); '
        f'margin:4px 2px;">'
        f'<span style="font-weight:600;">📄 {source_file}</span>'
        f'<span style="color:#94A3B8;">p.{page_numbers}</span>'
        f'<span style="background:{color}; color:white; padding:2px 8px; '
        f'border-radius:12px; font-size:0.75em;">{similarity:.0%} {label}</span>'
        f'</div>'
    )
