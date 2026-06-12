"""
ChromaDB 벡터 스토어: 연구노트 청크의 벡터 저장 및 유사도 검색을 담당합니다.

patent_analysis_local의 VectorStore를 기반으로,
연구노트 Q&A에 필요한 메타데이터 필터링을 강화했습니다.
"""
import json
from pathlib import Path
from typing import List, Optional, Dict, Any
import numpy as np
from src.utils.logging_utils import get_logger

logger = get_logger("vector_store")

DEFAULT_PERSIST_DIR = Path("data/chromadb")
DEFAULT_COLLECTION = "research_notes"


class VectorStore:
    """ChromaDB 기반 벡터 스토어."""

    def __init__(
        self,
        persist_dir: Path = DEFAULT_PERSIST_DIR,
        collection_name: str = DEFAULT_COLLECTION,
    ):
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self._client = None
        self._collection = None

    def _ensure_client(self):
        """ChromaDB 클라이언트를 lazy 초기화합니다."""
        if self._client is not None:
            return

        import chromadb
        from chromadb.config import Settings

        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            f"ChromaDB 초기화 완료. 컬렉션 '{self.collection_name}': "
            f"{self._collection.count()}개 문서"
        )

    @property
    def collection(self):
        """컬렉션 참조를 반환합니다. stale 참조 시 재획득."""
        self._ensure_client()
        try:
            if self._collection is not None:
                self._collection.count()
            else:
                self._collection = self._client.get_collection(self.collection_name)
        except Exception:
            try:
                self._collection = self._client.get_collection(self.collection_name)
            except Exception:
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
        return self._collection

    @property
    def count(self) -> int:
        return self.collection.count()

    def add_documents(
        self,
        ids: List[str],
        embeddings: np.ndarray,
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        batch_size: int = 500,
    ) -> int:
        """청크 문서와 임베딩을 벡터 스토어에 추가합니다."""
        self._ensure_client()
        if len(ids) == 0:
            return 0

        total_added = 0
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i: i + batch_size]
            batch_embeddings = embeddings[i: i + batch_size].tolist()
            batch_documents = documents[i: i + batch_size]
            batch_metadatas = metadatas[i: i + batch_size] if metadatas else None

            try:
                self.collection.upsert(
                    ids=batch_ids,
                    embeddings=batch_embeddings,
                    documents=batch_documents,
                    metadatas=batch_metadatas,
                )
                total_added += len(batch_ids)
                logger.info(f"Upsert 배치 {i // batch_size + 1}: {total_added}/{len(ids)}")
            except Exception as e:
                logger.error(f"Upsert 오류 (배치 {i}): {e}")

        return total_added

    def search_similar(
        self,
        query_embedding: np.ndarray,
        n_results: int = 10,
        where: Optional[Dict] = None,
        min_similarity: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """벡터 유사도 검색을 수행합니다."""
        self._ensure_client()
        coll = self.collection

        if coll.count() == 0:
            logger.warning("벡터 스토어가 비어 있습니다.")
            return []

        query_params = {
            "query_embeddings": [query_embedding.tolist()],
            "n_results": min(n_results, coll.count()),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            query_params["where"] = where

        try:
            results = coll.query(**query_params)
        except Exception as e:
            logger.error(f"벡터 검색 오류: {e}")
            return []

        output = []
        if results and results["ids"] and len(results["ids"]) > 0:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                similarity = 1 - distance

                if similarity < min_similarity:
                    continue

                output.append({
                    "id": doc_id,
                    "document": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "similarity": round(similarity, 4),
                })

        return output

    def search_by_text(
        self,
        query_text: str,
        embedding_engine,
        n_results: int = 10,
        min_similarity: float = 0.0,
        where: Optional[Dict] = None,
    ) -> List[Dict[str, Any]]:
        """텍스트 질의를 임베딩한 후 유사도 검색을 수행합니다."""
        query_emb = embedding_engine.embed_single(query_text)
        if len(query_emb) == 0:
            logger.error("질의 임베딩 생성 실패")
            return []
        return self.search_similar(query_emb, n_results, where=where, min_similarity=min_similarity)

    def get_all_ids(self) -> List[str]:
        """벡터 스토어의 모든 문서 ID를 반환합니다."""
        coll = self.collection
        if coll.count() == 0:
            return []
        result = coll.get(include=[])
        return result["ids"] if result else []

    def get_documents_by_file(self, source_file: str) -> List[Dict[str, Any]]:
        """특정 파일에 속하는 모든 청크를 반환합니다."""
        self._ensure_client()
        coll = self.collection
        if coll.count() == 0:
            return []
        try:
            results = coll.get(
                where={"source_file": source_file},
                include=["documents", "metadatas"],
            )
            output = []
            if results and results["ids"]:
                for i, doc_id in enumerate(results["ids"]):
                    output.append({
                        "id": doc_id,
                        "document": results["documents"][i] if results["documents"] else "",
                        "metadata": results["metadatas"][i] if results["metadatas"] else {},
                    })
            return output
        except Exception as e:
            logger.error(f"파일별 검색 오류: {e}")
            return []

    def delete_by_file(self, source_file: str) -> int:
        """특정 파일의 모든 청크를 삭제합니다."""
        docs = self.get_documents_by_file(source_file)
        if not docs:
            return 0
        ids_to_delete = [d["id"] for d in docs]
        try:
            self.collection.delete(ids=ids_to_delete)
            logger.info(f"'{source_file}' 파일의 {len(ids_to_delete)}개 청크 삭제 완료")
            return len(ids_to_delete)
        except Exception as e:
            logger.error(f"삭제 오류: {e}")
            return 0

    def delete_all(self):
        """모든 문서를 삭제합니다."""
        self._ensure_client()
        self._client.delete_collection(self.collection_name)
        self._collection = None
        _ = self.collection
        logger.info("벡터 스토어 전체 삭제 완료")

    def get_indexed_files(self) -> List[str]:
        """인덱싱된 모든 파일명 목록을 반환합니다."""
        self._ensure_client()
        coll = self.collection
        if coll.count() == 0:
            return []
        try:
            results = coll.get(include=["metadatas"])
            files = set()
            if results and results["metadatas"]:
                for meta in results["metadatas"]:
                    if meta and "source_file" in meta:
                        files.add(meta["source_file"])
            return sorted(files)
        except Exception as e:
            logger.error(f"파일 목록 조회 오류: {e}")
            return []
