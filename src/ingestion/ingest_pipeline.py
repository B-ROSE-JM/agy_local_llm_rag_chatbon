"""
인제스션 파이프라인: 연구노트 문서를 파싱 → 청킹 → 임베딩 → 벡터 DB 저장합니다.

파이프라인 단계:
1. 디렉토리/파일에서 문서 로드 (Word/PDF)
2. 페이지 단위 파싱
3. 시맨틱 청킹 + 메타데이터 보존
4. 임베딩 생성
5. ChromaDB에 저장
6. 인덱스 메타데이터 JSON 저장 (파일 해시, 인제스션 시각)
"""
import json
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from src.ingestion.document_loader import load_document, load_documents_from_directory
from src.ingestion.chunker import chunk_pages, compute_file_hash, DocumentChunk
from src.knowledge_base.embedding_engine import EmbeddingEngine
from src.knowledge_base.vector_store import VectorStore
from src.utils.logging_utils import get_logger
from config import (
    CHROMADB_PERSIST_DIR,
    CHROMADB_COLLECTION,
    DOCUMENT_INDEX_DIR,
    RESEARCH_NOTES_DIR,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    MAX_CHUNK_CHARS,
    MIN_CHUNK_CHARS,
)

logger = get_logger("ingest_pipeline")

INDEX_FILE = DOCUMENT_INDEX_DIR / "file_index.json"


def _load_index() -> Dict[str, Any]:
    """파일 인덱스를 로드합니다."""
    if INDEX_FILE.exists():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"files": {}}


def _save_index(index: Dict[str, Any]):
    """파일 인덱스를 저장합니다."""
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def ingest_file(
    file_path: str,
    embedding_engine: Optional[EmbeddingEngine] = None,
    vector_store: Optional[VectorStore] = None,
    force: bool = False,
    progress_callback=None,
) -> Dict[str, Any]:
    """
    단일 파일을 인제스션합니다.

    Args:
        file_path: 문서 파일 경로
        embedding_engine: 초기화된 EmbeddingEngine (None이면 자동 생성)
        vector_store: 초기화된 VectorStore (None이면 자동 생성)
        force: True이면 이미 인제스션된 파일도 재처리
        progress_callback: 진행률 콜백 (0.0~1.0)

    Returns:
        인제스션 통계 딕셔너리
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

    file_hash = compute_file_hash(file_path)
    index = _load_index()

    # 중복 체크
    if not force and path.name in index.get("files", {}):
        existing = index["files"][path.name]
        if existing.get("file_hash") == file_hash:
            logger.info(f"'{path.name}'은 이미 인제스션된 파일입니다 (스킵)")
            return {
                "file_name": path.name,
                "status": "skipped",
                "reason": "이미 인제스션됨 (동일 해시)",
                "chunks": existing.get("chunk_count", 0),
            }

    # 엔진 초기화
    if embedding_engine is None:
        embedding_engine = EmbeddingEngine()
        embedding_engine.initialize()

    if vector_store is None:
        vector_store = VectorStore(
            persist_dir=CHROMADB_PERSIST_DIR,
            collection_name=CHROMADB_COLLECTION,
        )

    if progress_callback:
        progress_callback(0.1)

    # 1. 문서 파싱
    logger.info(f"파싱 중: {path.name}")
    pages = load_document(str(path))
    logger.info(f"  → {len(pages)} 페이지 파싱 완료")

    if progress_callback:
        progress_callback(0.3)

    # 2. 청킹
    logger.info(f"청킹 중: {path.name}")
    chunks = chunk_pages(
        pages,
        max_chunk_chars=MAX_CHUNK_CHARS,
        min_chunk_chars=MIN_CHUNK_CHARS,
        overlap_chars=int(CHUNK_OVERLAP * 1.5),  # 토큰 → 한국어 문자 변환
    )
    logger.info(f"  → {len(chunks)} 청크 생성")

    if progress_callback:
        progress_callback(0.5)

    # 3. 기존 동일 파일 청크 삭제 (재인제스션 시)
    if path.name in index.get("files", {}):
        vector_store.delete_by_file(path.name)
        logger.info(f"  → 기존 청크 삭제 완료")

    # 4. 임베딩 생성
    logger.info(f"임베딩 생성 중: {len(chunks)}개 청크")
    texts = [c.text for c in chunks]
    embeddings = embedding_engine.embed_texts(texts)

    if progress_callback:
        progress_callback(0.8)

    # 5. ChromaDB 저장
    chunk_ids = [c.chunk_id for c in chunks]
    documents = texts
    metadatas = [c.to_metadata_dict() for c in chunks]

    stored_count = vector_store.add_documents(
        ids=chunk_ids,
        embeddings=embeddings,
        documents=documents,
        metadatas=metadatas,
    )

    if progress_callback:
        progress_callback(0.95)

    # 6. 인덱스 업데이트
    index["files"][path.name] = {
        "file_path": str(path.resolve()),
        "file_hash": file_hash,
        "page_count": len(pages),
        "chunk_count": len(chunks),
        "stored_count": stored_count,
        "ingested_at": datetime.datetime.now().isoformat(),
        "file_size_bytes": path.stat().st_size,
    }
    _save_index(index)

    if progress_callback:
        progress_callback(1.0)

    stats = {
        "file_name": path.name,
        "status": "success",
        "pages": len(pages),
        "chunks": len(chunks),
        "stored": stored_count,
    }
    logger.info(f"인제스션 완료: {path.name} ({stats['pages']}p → {stats['chunks']}청크)")
    return stats


def ingest_directory(
    directory: str = None,
    embedding_engine: Optional[EmbeddingEngine] = None,
    vector_store: Optional[VectorStore] = None,
    force: bool = False,
    progress_callback=None,
) -> Dict[str, Any]:
    """
    디렉토리 내 모든 연구노트를 인제스션합니다.

    Args:
        directory: 연구노트 디렉토리 (None이면 기본 경로)
        embedding_engine: 초기화된 EmbeddingEngine
        vector_store: 초기화된 VectorStore
        force: 이미 인제스션된 파일도 재처리
        progress_callback: 전체 진행률 콜백 (0.0~1.0)

    Returns:
        전체 인제스션 통계
    """
    if directory is None:
        directory = str(RESEARCH_NOTES_DIR)

    dir_path = Path(directory)
    if not dir_path.exists():
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.warning(f"디렉토리가 생성되었습니다: {directory}")
        return {"total_files": 0, "total_chunks": 0, "results": []}

    # 엔진 초기화 (공유)
    if embedding_engine is None:
        embedding_engine = EmbeddingEngine()
        embedding_engine.initialize()

    if vector_store is None:
        vector_store = VectorStore(
            persist_dir=CHROMADB_PERSIST_DIR,
            collection_name=CHROMADB_COLLECTION,
        )

    # 지원 파일 목록
    files = sorted(
        f for f in dir_path.iterdir()
        if f.is_file() and f.suffix.lower() in {".docx", ".pdf"}
    )

    logger.info(f"인제스션 대상: {len(files)}개 파일 ({directory})")

    results = []
    for idx, file_path in enumerate(files):
        try:
            def file_progress(p):
                if progress_callback:
                    overall = (idx + p) / len(files)
                    progress_callback(overall)

            stats = ingest_file(
                str(file_path),
                embedding_engine=embedding_engine,
                vector_store=vector_store,
                force=force,
                progress_callback=file_progress,
            )
            results.append(stats)
        except Exception as e:
            logger.error(f"인제스션 실패 ({file_path.name}): {e}")
            results.append({
                "file_name": file_path.name,
                "status": "error",
                "error": str(e),
            })

    if progress_callback:
        progress_callback(1.0)

    total_stats = {
        "total_files": len(files),
        "success_files": sum(1 for r in results if r.get("status") == "success"),
        "skipped_files": sum(1 for r in results if r.get("status") == "skipped"),
        "error_files": sum(1 for r in results if r.get("status") == "error"),
        "total_chunks": sum(r.get("chunks", 0) for r in results),
        "total_pages": sum(r.get("pages", 0) for r in results),
        "results": results,
    }

    logger.info(
        f"디렉토리 인제스션 완료: "
        f"{total_stats['success_files']}/{total_stats['total_files']}건 성공, "
        f"{total_stats['total_chunks']}개 청크 생성"
    )
    return total_stats


def get_ingest_status() -> Dict[str, Any]:
    """현재 인제스션 상태를 반환합니다."""
    index = _load_index()
    files = index.get("files", {})

    vs = VectorStore(persist_dir=CHROMADB_PERSIST_DIR, collection_name=CHROMADB_COLLECTION)

    return {
        "indexed_files": len(files),
        "total_chunks": vs.count,
        "total_pages": sum(f.get("page_count", 0) for f in files.values()),
        "files": [
            {
                "name": name,
                "pages": info.get("page_count", 0),
                "chunks": info.get("chunk_count", 0),
                "ingested_at": info.get("ingested_at", ""),
                "size_mb": round(info.get("file_size_bytes", 0) / 1024 / 1024, 2),
            }
            for name, info in sorted(files.items())
        ],
    }


def remove_file_from_index(file_name: str) -> bool:
    """인덱스에서 특정 파일을 제거하고 벡터도 삭제합니다."""
    index = _load_index()
    if file_name not in index.get("files", {}):
        return False

    vs = VectorStore(persist_dir=CHROMADB_PERSIST_DIR, collection_name=CHROMADB_COLLECTION)
    vs.delete_by_file(file_name)

    del index["files"][file_name]
    _save_index(index)
    logger.info(f"'{file_name}' 인덱스 및 벡터 삭제 완료")
    return True
