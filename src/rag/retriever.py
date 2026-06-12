"""
벡터 검색 + 출처 추적 모듈.
ChromaDB에서 관련 청크를 검색하고, 출처 정보(파일명, 페이지, 바로가기 링크)를 생성합니다.
"""
from typing import List, Dict, Any, Optional
from src.knowledge_base.vector_store import VectorStore
from src.knowledge_base.embedding_engine import EmbeddingEngine
from src.utils.logging_utils import get_logger
from config import RAG_TOP_K, RAG_MIN_SIMILARITY, CHROMADB_PERSIST_DIR, CHROMADB_COLLECTION

logger = get_logger("retriever")


class Retriever:
    """연구노트 벡터 검색 및 출처 추적 엔진."""

    def __init__(
        self,
        embedding_engine: Optional[EmbeddingEngine] = None,
        vector_store: Optional[VectorStore] = None,
    ):
        self.embedding_engine = embedding_engine
        self.vector_store = vector_store
        self._initialized = False

    def initialize(self) -> bool:
        """검색 엔진을 초기화합니다."""
        if self._initialized:
            return True

        if self.embedding_engine is None:
            self.embedding_engine = EmbeddingEngine()

        if not self.embedding_engine.initialize():
            logger.error("임베딩 엔진 초기화 실패")
            return False

        if self.vector_store is None:
            self.vector_store = VectorStore(
                persist_dir=CHROMADB_PERSIST_DIR,
                collection_name=CHROMADB_COLLECTION,
            )

        self._initialized = True
        logger.info(
            f"Retriever 초기화 완료. "
            f"벡터 스토어: {self.vector_store.count}개 청크"
        )
        return True

    def retrieve(
        self,
        query: str,
        top_k: int = None,
        min_similarity: float = None,
        file_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        질문에 대해 관련 청크를 검색하고 출처 정보를 포함하여 반환합니다.

        Args:
            query: 검색 질의 (사용자 질문)
            top_k: 검색 결과 수
            min_similarity: 최소 유사도
            file_filter: 특정 파일명으로 검색 범위 제한

        Returns:
            출처 정보가 포함된 검색 결과 목록:
            [
                {
                    "id": "chunk_id",
                    "document": "청크 텍스트",
                    "similarity": 0.85,
                    "source_file": "연구노트_실험A.pdf",
                    "source_path": "C:/path/to/file.pdf",
                    "page_numbers": "3,4",
                    "start_page": 3,
                    "end_page": 4,
                    "section_title": "3.2 실험 결과",
                    "source_link": "file:///C:/path/to/file.pdf#page=3",
                    "metadata": {...}
                }
            ]
        """
        if not self._initialized:
            self.initialize()

        if top_k is None:
            top_k = RAG_TOP_K
        if min_similarity is None:
            min_similarity = RAG_MIN_SIMILARITY

        # 파일 필터 적용
        where = None
        if file_filter:
            where = {"source_file": file_filter}

        # 벡터 검색
        results = self.vector_store.search_by_text(
            query_text=query,
            embedding_engine=self.embedding_engine,
            n_results=top_k,
            min_similarity=min_similarity,
            where=where,
        )

        # 출처 정보 강화
        enriched_results = []
        for r in results:
            meta = r.get("metadata", {})
            source_file = meta.get("source_file", "알 수 없는 파일")
            source_path = meta.get("source_path", "")
            start_page = meta.get("start_page", 1)
            end_page = meta.get("end_page", start_page)
            page_numbers = meta.get("page_numbers", str(start_page))

            # 원문 바로가기 링크 생성
            source_link = _generate_source_link(source_path, start_page)

            enriched = {
                "id": r["id"],
                "document": r["document"],
                "similarity": r["similarity"],
                "source_file": source_file,
                "source_path": source_path,
                "page_numbers": page_numbers,
                "start_page": start_page,
                "end_page": end_page,
                "section_title": meta.get("section_title", ""),
                "source_link": source_link,
                "metadata": meta,
            }
            enriched_results.append(enriched)

        logger.info(
            f"검색 완료: '{query[:50]}...' → {len(enriched_results)}개 결과 "
            f"(top_k={top_k}, min_sim={min_similarity})"
        )
        return enriched_results

    def get_indexed_files(self) -> List[str]:
        """인덱싱된 파일 목록을 반환합니다."""
        if not self._initialized:
            self.initialize()
        return self.vector_store.get_indexed_files()


def _generate_source_link(source_path: str, page: int) -> str:
    """
    원문 바로가기 링크를 생성합니다.
    - PDF: file:///path/to/file.pdf#page=N
    - Word: file:///path/to/file.docx
    """
    if not source_path:
        return ""

    # Windows 경로를 file URI로 변환
    path_normalized = source_path.replace("\\", "/")
    if not path_normalized.startswith("/"):
        path_normalized = "/" + path_normalized

    file_uri = f"file://{path_normalized}"

    # PDF의 경우 페이지 앵커 추가
    if source_path.lower().endswith(".pdf"):
        file_uri += f"#page={page}"

    return file_uri
