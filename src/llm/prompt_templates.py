"""
연구노트 Q&A 전용 프롬프트 템플릿.

한국어 위주 연구노트에 최적화되어 있으며,
출처(파일명, 페이지)를 반드시 명시하도록 지시합니다.
"""

# ── 시스템 프롬프트 ──
SYSTEM_PROMPT = """당신은 연구노트 기반 Q&A 어시스턴트입니다.
다음 규칙을 반드시 지키세요:

1. 제공된 연구노트 컨텍스트만을 근거로 정확하게 답변하세요.
2. 답변 시 반드시 출처를 [파일명, 페이지 N] 형식으로 명시하세요.
3. 여러 연구노트에서 정보를 종합하는 경우, 각 정보의 출처를 모두 표기하세요.
4. 컨텍스트에 없는 내용은 추측하지 말고 "해당 정보를 제공된 연구노트에서 찾을 수 없습니다"라고 답하세요.
5. 답변은 한국어로 작성하세요.
6. 실험 데이터, 수치, 조건 등은 정확하게 인용하세요."""

# ── Q&A 사용자 프롬프트 ──
QA_PROMPT_TEMPLATE = """## 참고 연구노트 컨텍스트:
{context}

## 이전 대화:
{chat_history}

## 질문:
{question}

위 연구노트 내용을 기반으로 답변하고, 출처를 [파일명, 페이지 N] 형식으로 표기하세요."""


# ── 문서 요약 프롬프트 ──
SUMMARY_PROMPT_TEMPLATE = """다음 연구노트의 내용을 3~5문장으로 요약하세요.
주요 연구 목적, 실험 방법, 핵심 결과를 포함하세요.

## 연구노트 내용:
{document_text}

## 요약:"""


# ── 관련 문서 추천 프롬프트 ──
RELATED_DOCS_PROMPT = """다음 질문과 관련된 추가 검색 키워드를 3~5개 제안하세요.
한국어와 영어 키워드를 모두 포함하세요.
JSON 형식으로 반환하세요: {{"keywords": ["키워드1", "키워드2", ...]}}

질문: {question}"""


def format_context(chunks: list) -> str:
    """
    검색된 청크 목록을 LLM 컨텍스트 문자열로 포맷팅합니다.
    
    Args:
        chunks: 검색된 청크 딕셔너리 목록
        
    Returns:
        포맷된 컨텍스트 문자열
    """
    if not chunks:
        return "(관련 연구노트 컨텍스트 없음)"

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        source_file = meta.get("source_file", "알 수 없는 파일")
        page_info = meta.get("page_numbers", meta.get("start_page", "?"))
        section = meta.get("section_title", "")
        similarity = chunk.get("similarity", 0)

        header = f"--- 참고자료 {i} [출처: {source_file}, 페이지 {page_info}]"
        if section:
            header += f" (섹션: {section})"
        header += f" (유사도: {similarity:.2f}) ---"

        context_parts.append(f"{header}\n{chunk.get('document', '')}")

    return "\n\n".join(context_parts)


def format_chat_history(history: list, max_turns: int = 6) -> str:
    """
    대화 히스토리를 프롬프트용 문자열로 포맷팅합니다.

    Args:
        history: [{"role": "user/assistant", "content": "..."}] 형식
        max_turns: 포함할 최대 턴 수

    Returns:
        포맷된 대화 히스토리 문자열
    """
    if not history:
        return "(이전 대화 없음)"

    # 최근 N턴만 유지
    recent = history[-max_turns * 2:]  # user + assistant = 2 messages per turn

    parts = []
    for msg in recent:
        role = "사용자" if msg["role"] == "user" else "어시스턴트"
        parts.append(f"{role}: {msg['content']}")

    return "\n".join(parts)


def build_qa_prompt(question: str, chunks: list, chat_history: list = None) -> str:
    """
    완성된 Q&A 프롬프트를 생성합니다.

    Args:
        question: 사용자 질문
        chunks: 검색된 청크 목록
        chat_history: 이전 대화 히스토리

    Returns:
        LLM에 전달할 완성된 프롬프트
    """
    context = format_context(chunks)
    history = format_chat_history(chat_history or [])

    return QA_PROMPT_TEMPLATE.format(
        context=context,
        chat_history=history,
        question=question,
    )
