"""
임베딩 엔진: 텍스트를 벡터로 변환합니다.
llama.cpp /v1/embeddings → sentence-transformers → TF-IDF 순으로 폴백합니다.

patent_analysis_local의 EmbeddingEngine을 기반으로 연구노트 Q&A에 맞게 조정했습니다.
"""
import hashlib
import httpx
import numpy as np
from pathlib import Path
from typing import List, Optional
from src.utils.logging_utils import get_logger
from config import (
    LLAMA_SERVER_URL,
    EMBEDDING_SERVER_URL,
    USE_EMBEDDING_SERVER,
    EMBEDDING_FALLBACK_MODEL,
)

logger = get_logger("embedding_engine")

BATCH_SIZE = 1


class EmbeddingEngine:
    """로컬 임베딩 엔진. llama.cpp → sentence-transformers → TF-IDF 폴백."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: str = "local-model",
        fallback_model_name: Optional[str] = None,
    ):
        # 임베딩 전용 서버 설정이 켜져 있으면 EMBEDDING_SERVER_URL 사용, 아니면 일반 LLAMA_SERVER_URL 사용
        if base_url is None:
            if USE_EMBEDDING_SERVER:
                self.base_url = EMBEDDING_SERVER_URL
                logger.info(f"임베딩 전용 서버 사용 예정: {self.base_url}")
            else:
                self.base_url = LLAMA_SERVER_URL
        else:
            self.base_url = base_url

        self.model = model
        self.fallback_model_name = fallback_model_name or EMBEDDING_FALLBACK_MODEL
        self._fallback_model = None
        self._use_fallback = False
        self._use_tfidf = False
        self._tfidf_vectorizer = None
        self._embedding_dim: Optional[int] = None

    def _check_llama_embedding_support(self) -> bool:
        """llama.cpp 서버의 임베딩 엔드포인트 지원 여부를 확인합니다."""
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": "test"},
                timeout=10.0,
            )
            if response.status_code == 200:
                data = response.json()
                if "data" in data and len(data["data"]) > 0:
                    emb = data["data"][0].get("embedding", [])
                    if len(emb) > 0:
                        self._embedding_dim = len(emb)
                        logger.info(f"llama.cpp 임베딩 지원 확인. 차원: {self._embedding_dim}")
                        return True
            logger.warning(f"llama.cpp /v1/embeddings 비정상 응답: {response.status_code}")
            return False
        except Exception as e:
            logger.warning(f"llama.cpp 임베딩 엔드포인트 사용 불가: {e}")
            return False

    def _load_fallback_model(self):
        """sentence-transformers 폴백 모델을 로드합니다."""
        if self._fallback_model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            import os

            logger.info(f"폴백 임베딩 모델 로딩: {self.fallback_model_name}")
            
            # 로컬 디렉토리 경로가 존재하면 직접 로드
            model_path = Path(self.fallback_model_name)
            if model_path.exists() and model_path.is_dir():
                logger.info(f"로컬 디렉토리 경로 감지: {model_path.resolve()}")
                self._fallback_model = SentenceTransformer(str(model_path.resolve()))
                logger.info("로컬 디렉토리에서 모델 로드 성공")
            else:
                is_offline = os.environ.get("HF_HUB_OFFLINE") == "1"
                try:
                    self._fallback_model = SentenceTransformer(
                        self.fallback_model_name, local_files_only=True
                    )
                    logger.info("로컬 캐시에서 sentence-transformers 모델 로드 성공")
                except Exception:
                    if is_offline:
                        raise OSError("오프라인 모드. 로컬 모델 없음.")
                    import socket
                    logger.info("로컬 캐시 없음. huggingface.co 연결 확인 중...")
                    try:
                        socket.create_connection(("huggingface.co", 443), timeout=3.0).close()
                        logger.info("인터넷 연결 확인. 모델 다운로드 중...")
                        self._fallback_model = SentenceTransformer(
                            self.fallback_model_name, local_files_only=False
                        )
                    except Exception as conn_err:
                        raise OSError(f"huggingface.co 연결 불가: {conn_err}")

            test_emb = self._fallback_model.encode(["test"])
            self._embedding_dim = test_emb.shape[1]
            logger.info(f"폴백 모델 로드 완료. 차원: {self._embedding_dim}")

        except ImportError:
            raise ImportError(
                "sentence-transformers가 필요합니다. "
                "설치: pip install sentence-transformers"
            )

    def _load_tfidf_vectorizer(self):
        """TF-IDF 벡터라이저를 로드하거나 생성합니다."""
        import pickle
        from sklearn.feature_extraction.text import TfidfVectorizer

        tfidf_path = Path("data/tfidf_vectorizer.pkl")
        if tfidf_path.exists():
            try:
                with open(tfidf_path, "rb") as f:
                    self._tfidf_vectorizer = pickle.load(f)
                self._embedding_dim = len(self._tfidf_vectorizer.vocabulary_)
                logger.info(f"기존 TF-IDF 벡터라이저 로드. 차원: {self._embedding_dim}")
            except Exception as e:
                logger.error(f"TF-IDF 벡터라이저 로드 오류: {e}")

        if self._tfidf_vectorizer is None:
            self._embedding_dim = 1024
            self._tfidf_vectorizer = TfidfVectorizer(
                max_features=self._embedding_dim,
                ngram_range=(1, 2),
            )
            logger.info("새 TF-IDF 벡터라이저 생성 (인제스션 시 학습 예정)")

    def initialize(self) -> bool:
        """
        임베딩 엔진을 초기화합니다.
        llama.cpp → sentence-transformers → TF-IDF 순으로 시도합니다.
        """
        if self._check_llama_embedding_support():
            self._use_fallback = False
            self._use_tfidf = False
            logger.info("llama.cpp 임베딩 사용")
            return True
        else:
            logger.info("llama.cpp 사용 불가. sentence-transformers 시도...")
            try:
                self._load_fallback_model()
                self._use_fallback = True
                self._use_tfidf = False
                logger.info("sentence-transformers 임베딩 사용")
                return True
            except Exception as e:
                logger.warning(f"sentence-transformers 실패: {e}. TF-IDF 폴백...")
                self._use_fallback = False
                self._use_tfidf = True
                self._load_tfidf_vectorizer()
                return True

    @property
    def embedding_dimension(self) -> Optional[int]:
        return self._embedding_dim

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """텍스트 목록을 벡터로 변환합니다."""
        if not texts:
            return np.array([])

        if self._use_tfidf:
            return self._embed_with_tfidf(texts)
        elif self._use_fallback:
            return self._embed_with_sentence_transformers(texts)
        else:
            return self._embed_with_llama(texts)

    def embed_single(self, text: str) -> np.ndarray:
        """단일 텍스트를 벡터로 변환합니다."""
        result = self.embed_texts([text])
        return result[0] if len(result) > 0 else np.array([])

    @staticmethod
    def _sanitize_for_llama(text: str) -> str:
        """
        llama.cpp 서버로 보내기 전에 특수 토큰으로 오인될 수 있는 패턴을 이스케이프합니다.
        
        [CONFIDENTIAL], [INST], [SYSTEM] 등 대괄호 태그가 llama.cpp의
        채팅 템플릿 파서와 충돌하여 500 에러를 유발하는 문제를 방지합니다.
        """
        import re
        if not text:
            return ""
        # 대괄호로 감싼 영문/숫자 태그 패턴 (대소문자 무관, 공백 허용) -> 대괄호 제거 후 공백 제거
        # 예: [CONFIDENTIAL] -> CONFIDENTIAL, [Confidential] -> Confidential, [INST] -> INST
        text = re.sub(r'\[\s*([a-zA-Z][a-zA-Z0-9_/\s-]{0,30})\s*\]', lambda m: m.group(1).strip(), text)
        # 한글 대괄호 태그도 처리 (대외비, 기밀, 비밀, 사내한, 극비 등)
        text = re.sub(r'\[\s*(대외비|기밀|비밀|사내한|극비)\s*\]', lambda m: m.group(1).strip(), text)
        return text

    def _embed_with_llama(self, texts: List[str]) -> np.ndarray:
        """llama.cpp /v1/embeddings로 임베딩을 생성합니다."""
        import time
        all_embeddings = []
        fail_report = []  # 실패 청크 진단 리포트

        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i: i + BATCH_SIZE]
            # llama 서버 전송용: 특수 토큰 태그 이스케이프
            sanitized_batch = [self._sanitize_for_llama(t) for t in batch]
            chunk_idx = i // BATCH_SIZE  # 청크 순번
            
            orig_text = batch[0] if batch else ""
            sanit_text = sanitized_batch[0] if sanitized_batch else ""
            text_len = len(orig_text)
            
            text_preview_orig = orig_text[:80].replace('\n', ' ')
            text_preview_sanit = sanit_text[:80].replace('\n', ' ')
            
            # 로그 미리보기 문구: 변경 사항이 있으면 원본->순화 표기
            if text_preview_orig != text_preview_sanit:
                preview_log_str = f"'{text_preview_orig}' -> '{text_preview_sanit}' (순화됨)"
            else:
                preview_log_str = f"'{text_preview_orig}'"

            # 연속 요청 간의 짧은 딜레이 추가 (부하 분산)
            if i > 0:
                time.sleep(0.1)

            # 재시도 루프 (최대 3회)
            max_retries = 3
            success = False
            last_status = None
            last_body = None

            for attempt in range(max_retries):
                try:
                    response = httpx.post(
                        f"{self.base_url}/embeddings",
                        json={"model": self.model, "input": sanitized_batch},
                        timeout=30.0,
                    )

                    if response.status_code == 200:
                        data = response.json()
                        for item in data.get("data", []):
                            all_embeddings.append(item.get("embedding", []))
                        success = True
                        logger.info(
                            f"[청크 #{chunk_idx}] 임베딩 성공 | 텍스트길이={text_len}자 | 미리보기: {preview_log_str}"
                        )
                        break
                    else:
                        last_status = response.status_code
                        last_body = response.text[:500]  # 서버 응답 본문 (최대 500자)
                        hint = ""
                        if response.status_code == 500:
                            hint = " (도움말: llama-server의 컨텍스트 크기(-c) 제한 초과, 모델 파일 손상, 혹은 빈 입력 오류일 수 있습니다.)"
                        logger.warning(
                            f"[청크 #{chunk_idx}] 임베딩 API 응답 오류 "
                            f"(코드 {response.status_code}, 시도 {attempt + 1}/{max_retries}){hint} "
                            f"| 텍스트길이={text_len}자 | 미리보기: {preview_log_str} "
                            f"| 서버 응답: {last_body}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[청크 #{chunk_idx}] 임베딩 API 연결 오류 "
                        f"({e}, 시도 {attempt + 1}/{max_retries}) "
                        f"| 텍스트길이={text_len}자 | 미리보기: {preview_log_str}"
                    )

                # 실패 시 대기 후 재시도 (Exponential Backoff: 0.5초, 1.0초, 2.0초)
                time.sleep(0.5 * (2 ** attempt))

            if not success:
                fail_info = {
                    "chunk_idx": chunk_idx,
                    "text_len": text_len,
                    "preview": text_preview_orig,
                    "status": last_status,
                    "server_response": last_body,
                }
                fail_report.append(fail_info)
                logger.warning(
                    f"[청크 #{chunk_idx}] llama 서버 최종 실패. "
                    f"sentence-transformers 폴백 시도... "
                    f"| 텍스트길이={text_len}자 | 미리보기: {preview_log_str}"
                )
                try:
                    fallback_embs = self._fallback_embed_single_batch(batch)
                    all_embeddings.extend(fallback_embs)
                    logger.info(f"[청크 #{chunk_idx}] sentence-transformers 폴백 성공 | 미리보기: {preview_log_str}")
                except Exception as fb_err:
                    logger.error(f"[청크 #{chunk_idx}] 폴백도 실패: {fb_err}. 0-벡터로 채웁니다.")
                    for _ in batch:
                        all_embeddings.append([0.0] * (self._embedding_dim or 768))

        # 실패 요약 리포트
        if fail_report:
            logger.error(
                f"=== 임베딩 실패 요약 === "
                f"총 {len(texts)}개 중 {len(fail_report)}개 실패 | "
                f"실패 청크: {[f['chunk_idx'] for f in fail_report]} | "
                f"텍스트 길이 범위: {min(f['text_len'] for f in fail_report)}"
                f"~{max(f['text_len'] for f in fail_report)}자 | "
                f"HTTP 상태코드: {set(f['status'] for f in fail_report)}"
            )

        return np.array(all_embeddings, dtype=np.float32)

    def _fallback_embed_single_batch(self, texts: List[str]) -> List[List[float]]:
        """llama 서버 실패 시 sentence-transformers로 개별 배치를 폴백 임베딩합니다."""
        if self._fallback_model is None:
            self._load_fallback_model()
        embeddings = self._fallback_model.encode(
            texts, normalize_embeddings=True
        )
        return embeddings.tolist()

    def _embed_with_sentence_transformers(self, texts: List[str]) -> np.ndarray:
        """sentence-transformers로 임베딩을 생성합니다."""
        if self._fallback_model is None:
            self._load_fallback_model()
        logger.info(f"sentence-transformers로 {len(texts)}개 텍스트 임베딩 중...")
        embeddings = self._fallback_model.encode(
            texts, batch_size=32, show_progress_bar=len(texts) > 50,
            normalize_embeddings=True,
        )
        return np.array(embeddings, dtype=np.float32)

    def _embed_with_tfidf(self, texts: List[str]) -> np.ndarray:
        """TF-IDF로 벡터를 생성합니다."""
        import pickle
        if self._tfidf_vectorizer is None:
            self._load_tfidf_vectorizer()

        is_fitted = hasattr(self._tfidf_vectorizer, "vocabulary_")
        if not is_fitted:
            logger.info("TF-IDF 벡터라이저 학습 중...")
            vectors = self._tfidf_vectorizer.fit_transform(texts)
            self._embedding_dim = len(self._tfidf_vectorizer.vocabulary_)
            tfidf_path = Path("data/tfidf_vectorizer.pkl")
            tfidf_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tfidf_path, "wb") as f:
                pickle.dump(self._tfidf_vectorizer, f)
        else:
            vectors = self._tfidf_vectorizer.transform(texts)

        return vectors.toarray().astype(np.float32)

    @staticmethod
    def text_hash(text: str) -> str:
        """텍스트의 MD5 해시를 생성합니다."""
        return hashlib.md5(text.encode("utf-8")).hexdigest()
