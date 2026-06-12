#!/usr/bin/env python
"""
CLI 배치 인제스션 스크립트.
디렉토리 내 모든 연구노트를 일괄 인제스션합니다.

사용법:
    python scripts/batch_ingest.py                          # 기본 디렉토리
    python scripts/batch_ingest.py --dir "C:/my/notes"      # 지정 디렉토리
    python scripts/batch_ingest.py --force                  # 강제 재인제스션
"""
import sys
import argparse
from pathlib import Path

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import RESEARCH_NOTES_DIR
from src.ingestion.ingest_pipeline import ingest_directory


def main():
    parser = argparse.ArgumentParser(
        description="연구노트 배치 인제스션 스크립트"
    )
    parser.add_argument(
        "--dir", type=str,
        default=str(RESEARCH_NOTES_DIR),
        help=f"연구노트 디렉토리 경로 (기본: {RESEARCH_NOTES_DIR})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="이미 인제스션된 파일도 강제 재처리",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("연구노트 배치 인제스션")
    print("=" * 60)
    print(f"대상 디렉토리: {args.dir}")
    print(f"강제 재처리: {'예' if args.force else '아니오'}")
    print("-" * 60)

    def progress_callback(p):
        bar_len = 40
        filled = int(bar_len * p)
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"\r진행률: [{bar}] {p:.0%}", end="", flush=True)

    result = ingest_directory(
        directory=args.dir,
        force=args.force,
        progress_callback=progress_callback,
    )

    print()
    print("=" * 60)
    print("인제스션 결과:")
    print(f"  총 파일: {result['total_files']}")
    print(f"  성공: {result['success_files']}")
    print(f"  스킵: {result['skipped_files']}")
    print(f"  오류: {result['error_files']}")
    print(f"  총 청크: {result['total_chunks']}")
    print(f"  총 페이지: {result['total_pages']}")
    print("=" * 60)

    # 개별 결과
    for r in result.get("results", []):
        status_icon = (
            "[SUCCESS]" if r["status"] == "success"
            else "[SKIP]" if r["status"] == "skipped"
            else "[ERROR]"
        )
        extra = ""
        if r["status"] == "success":
            extra = f" ({r.get('pages', 0)}p → {r.get('chunks', 0)} 청크)"
        elif r["status"] == "error":
            extra = f" ({r.get('error', '')})"
        elif r["status"] == "skipped":
            extra = f" ({r.get('reason', '')})"
        print(f"  {status_icon} {r['file_name']}{extra}")


if __name__ == "__main__":
    main()
