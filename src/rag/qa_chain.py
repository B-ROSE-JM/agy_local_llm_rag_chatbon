"""
RAG Q&A 체인: 검색 → 컨텍스트 구성 → LLM 응답 → 출처 표기.

전체 파이프라인:
1. 사용자 질문 수신
2. Retriever로 관련 청크 검색 (top-K)
3. 프롬프트 템플릿에 컨텍스트 삽입
4. LLM에 질의 (스트리밍 지원)
5. 응답 + 출처 정보 반환
"""
import datetime
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Generator

from src.rag.retriever import Retriever
from src.llm.llama_client import LlamaClient
from src.llm.prompt_templates import (
    SYSTEM_PROMPT,
    build_qa_prompt,
    format_context,
    SUMMARY_PROMPT_TEMPLATE,
)
from src.utils.logging_utils import get_logger
from config import (
    LLAMA_SERVER_URL,
    LLAMA_MODEL_NAME,
    RAG_TOP_K,
    MAX_CHAT_HISTORY,
    LLM_TEMPERATURE,
)

logger = get_logger("qa_chain")

# Q&A 로그 저장 경로
QA_LOG_DIR = Path("data/qa_logs")


class QAChain:
    """연구노트 RAG Q&A 체인."""

    def __init__(
        self,
        retriever: Optional[Retriever] = None,
        llm_client: Optional[LlamaClient] = None,
    ):
        self.retriever = retriever
        self.llm_client = llm_client
        self._initialized = False

    def initialize(self) -> bool:
        """Q&A 체인을 초기화합니다."""
        if self._initialized:
            return True

        # Retriever 초기화
        if self.retriever is None:
            self.retriever = Retriever()

        if not self.retriever.initialize():
            logger.error("Retriever 초기화 실패")
            return False

        # LLM 클라이언트 초기화
        if self.llm_client is None:
            self.llm_client = LlamaClient(
                base_url=LLAMA_SERVER_URL,
                model=LLAMA_MODEL_NAME,
            )

        self._initialized = True
        logger.info("QAChain 초기화 완료")
        return True

    def ask(
        self,
        question: str,
        chat_history: List[Dict[str, str]] = None,
        top_k: int = None,
        file_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        질문에 대한 답변을 생성합니다 (비스트리밍).

        Args:
            question: 사용자 질문
            chat_history: 이전 대화 히스토리
            top_k: 검색할 청크 수
            file_filter: 특정 파일로 검색 범위 제한

        Returns:
            {
                "answer": "답변 텍스트",
                "sources": [출처 정보 목록],
                "retrieval_count": 검색된 청크 수,
            }
        """
        if not self._initialized:
            self.initialize()

        if top_k is None:
            top_k = RAG_TOP_K

        # 1. 관련 청크 검색
        chunks = self.retriever.retrieve(
            query=question,
            top_k=top_k,
            file_filter=file_filter,
        )

        # 2. 프롬프트 구성
        prompt = build_qa_prompt(question, chunks, chat_history)

        # 3. LLM 질의
        try:
            answer = self.llm_client.query(
                prompt=prompt,
                system_instruction=SYSTEM_PROMPT,
                temperature=LLM_TEMPERATURE,
                chat_history=None,  # 히스토리는 프롬프트에 직접 포함
            )
        except ConnectionError as e:
            answer = str(e)
        except Exception as e:
            logger.error(f"LLM 질의 오류: {e}")
            answer = f"⚠️ LLM 응답 오류가 발생했습니다: {str(e)}"

        # 4. 출처 정보 추출
        sources = []
        for chunk in chunks:
            sources.append({
                "file": chunk["source_file"],
                "pages": chunk["page_numbers"],
                "start_page": chunk["start_page"],
                "section": chunk["section_title"],
                "similarity": chunk["similarity"],
                "link": chunk["source_link"],
                "source_path": chunk["source_path"],
            })

        # 5. Q&A 로그 저장
        self._save_qa_log(question, answer, sources)

        return {
            "answer": answer,
            "sources": sources,
            "retrieval_count": len(chunks),
        }

    def ask_stream(
        self,
        question: str,
        chat_history: List[Dict[str, str]] = None,
        top_k: int = None,
        file_filter: Optional[str] = None,
    ) -> tuple:
        """
        질문에 대한 답변을 스트리밍으로 생성합니다.

        Returns:
            (token_generator, sources) 튜플
            - token_generator: str을 yield하는 Generator
            - sources: 출처 정보 리스트
        """
        if not self._initialized:
            self.initialize()

        if top_k is None:
            top_k = RAG_TOP_K

        # 1. 관련 청크 검색
        chunks = self.retriever.retrieve(
            query=question,
            top_k=top_k,
            file_filter=file_filter,
        )

        # 2. 프롬프트 구성
        prompt = build_qa_prompt(question, chunks, chat_history)

        # 3. 출처 정보 추출
        sources = []
        for chunk in chunks:
            sources.append({
                "file": chunk["source_file"],
                "pages": chunk["page_numbers"],
                "start_page": chunk["start_page"],
                "section": chunk["section_title"],
                "similarity": chunk["similarity"],
                "link": chunk["source_link"],
                "source_path": chunk["source_path"],
            })

        # 4. 스트리밍 생성기 반환
        def token_generator():
            full_answer = []
            try:
                for token in self.llm_client.query_stream(
                    prompt=prompt,
                    system_instruction=SYSTEM_PROMPT,
                    temperature=LLM_TEMPERATURE,
                ):
                    full_answer.append(token)
                    yield token
            except ConnectionError as e:
                yield str(e)
            except Exception as e:
                yield f"\n⚠️ LLM 응답 오류: {str(e)}"
            finally:
                # 로그 저장
                self._save_qa_log(question, "".join(full_answer), sources)

        return token_generator(), sources

    def summarize_document(self, file_name: str) -> str:
        """특정 문서를 요약합니다."""
        if not self._initialized:
            self.initialize()

        # 해당 파일의 모든 청크를 가져와 합침
        chunks = self.retriever.vector_store.get_documents_by_file(file_name)
        if not chunks:
            return f"'{file_name}' 파일을 찾을 수 없습니다."

        # 처음 5~10개 청크의 텍스트를 합쳐서 요약
        combined_text = "\n\n".join(
            c.get("document", "") for c in chunks[:8]
        )

        # 최대 3000자로 제한
        if len(combined_text) > 3000:
            combined_text = combined_text[:3000] + "..."

        prompt = SUMMARY_PROMPT_TEMPLATE.format(document_text=combined_text)

        try:
            summary = self.llm_client.query(
                prompt=prompt,
                system_instruction="당신은 연구노트 요약 전문가입니다. 핵심 내용을 정확하게 요약하세요.",
                temperature=0.3,
            )
            return summary
        except Exception as e:
            return f"요약 생성 오류: {str(e)}"

    def _save_qa_log(
        self,
        question: str,
        answer: str,
        sources: List[Dict],
    ):
        """Q&A 기록을 로그 파일에 저장합니다."""
        try:
            QA_LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_file = QA_LOG_DIR / "qa_history.jsonl"

            entry = {
                "timestamp": datetime.datetime.now().isoformat(),
                "question": question,
                "answer": answer[:2000],  # 로그 크기 제한
                "sources": [
                    {"file": s["file"], "pages": s["pages"], "similarity": s["similarity"]}
                    for s in sources
                ],
            }

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        except Exception as e:
            logger.debug(f"Q&A 로그 저장 실패 (무시): {e}")

    def get_qa_history(self, limit: int = 50) -> List[Dict]:
        """저장된 Q&A 히스토리를 반환합니다."""
        log_file = QA_LOG_DIR / "qa_history.jsonl"
        if not log_file.exists():
            return []

        entries = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except Exception as e:
            logger.error(f"Q&A 히스토리 로드 오류: {e}")

        return entries[-limit:]
