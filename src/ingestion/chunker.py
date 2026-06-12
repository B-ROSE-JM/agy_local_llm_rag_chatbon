"""
시맨틱 청킹 모듈: 페이지 단위 문서를 검색에 최적화된 청크로 분할합니다.

핵심 원칙:
- 페이지 경계를 존중하되, 너무 긴 페이지는 단락 단위로 분할
- 각 청크에 출처 메타데이터(파일명, 페이지 번호) 반드시 보존
- 오버랩으로 문맥 연속성 보장
"""
import re
import hashlib
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from src.ingestion.document_loader import DocumentPage
from src.utils.logging_utils import get_logger

logger = get_logger("chunker")

# 한국어 기준 토큰-문자 비율 (한국어 1토큰 ≈ 1.5자)
KOR_CHARS_PER_TOKEN = 1.5


@dataclass
class DocumentChunk:
    """검색 가능한 문서 청크를 나타냅니다."""
    chunk_id: str                    # 고유 ID: "{파일명}_p{page}_c{chunk_idx}"
    text: str                        # 청크 텍스트
    source_file: str                 # 원본 파일명
    source_path: str                 # 원본 파일 절대 경로
    page_numbers: List[int]          # 해당 청크가 걸치는 페이지 번호들
    start_page: int                  # 시작 페이지
    end_page: int                    # 끝 페이지
    total_pages: int                 # 원본 문서 총 페이지 수
    section_title: str = ""          # 감지된 섹션 제목
    chunk_index: int = 0             # 해당 페이지 내 청크 순번
    char_count: int = 0              # 문자 수
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_metadata_dict(self) -> Dict[str, Any]:
        """ChromaDB 저장용 메타데이터 딕셔너리를 반환합니다."""
        return {
            "source_file": self.source_file,
            "source_path": self.source_path,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "page_numbers": ",".join(str(p) for p in self.page_numbers),
            "total_pages": self.total_pages,
            "section_title": self.section_title,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
        }


def chunk_pages(
    pages: List[DocumentPage],
    max_chunk_chars: int = 1500,
    min_chunk_chars: int = 100,
    overlap_chars: int = 150,
) -> List[DocumentChunk]:
    """
    페이지 목록을 검색에 최적화된 청크로 분할합니다.

    전략:
    1. 페이지 텍스트가 max_chunk_chars 이하면 1개 청크로 유지
    2. 초과하면 단락(\\n) 경계에서 분할
    3. 짧은 인접 페이지는 합쳐서 1개 청크로

    Args:
        pages: DocumentPage 목록
        max_chunk_chars: 한 청크 최대 문자 수 (기본 1500 ≈ 1000 토큰)
        min_chunk_chars: 너무 짧은 청크 방지 (기본 100자)
        overlap_chars: 청크 간 오버랩 문자 수 (기본 150자 ≈ 100 토큰)

    Returns:
        List[DocumentChunk]: 분할된 청크 목록
    """
    if not pages:
        return []

    chunks: List[DocumentChunk] = []

    # 파일별로 그룹핑
    file_groups: Dict[str, List[DocumentPage]] = {}
    for page in pages:
        key = page.file_path
        if key not in file_groups:
            file_groups[key] = []
        file_groups[key].append(page)

    for file_path, file_pages in file_groups.items():
        # 페이지 번호 순 정렬
        file_pages.sort(key=lambda p: p.page_number)
        file_chunks = _chunk_single_file(
            file_pages, max_chunk_chars, min_chunk_chars, overlap_chars
        )
        chunks.extend(file_chunks)

    logger.info(
        f"총 {len(pages)} 페이지 → {len(chunks)} 청크 생성 "
        f"({len(file_groups)}개 파일)"
    )
    return chunks


def _chunk_single_file(
    pages: List[DocumentPage],
    max_chunk_chars: int,
    min_chunk_chars: int,
    overlap_chars: int,
) -> List[DocumentChunk]:
    """단일 파일의 페이지들을 청크로 분할합니다."""
    chunks: List[DocumentChunk] = []
    file_name = pages[0].file_name
    file_path = pages[0].file_path
    total_pages = pages[0].total_pages

    # 현재 섹션 제목 추적
    current_section = ""

    for page in pages:
        page_text = page.text.strip()
        if not page_text:
            continue

        # 섹션 제목 감지 (한국어/영어 헤딩 패턴)
        section = _detect_section_title(page_text) or current_section
        if section:
            current_section = section

        # 페이지가 max_chunk_chars 이하면 단일 청크
        if len(page_text) <= max_chunk_chars:
            chunk_id = _make_chunk_id(file_name, page.page_number, 0)
            chunks.append(DocumentChunk(
                chunk_id=chunk_id,
                text=page_text,
                source_file=file_name,
                source_path=file_path,
                page_numbers=[page.page_number],
                start_page=page.page_number,
                end_page=page.page_number,
                total_pages=total_pages,
                section_title=current_section,
                chunk_index=0,
                char_count=len(page_text),
            ))
        else:
            # 긴 페이지: 단락 경계에서 분할
            sub_chunks = _split_long_text(
                page_text, max_chunk_chars, min_chunk_chars, overlap_chars
            )
            for idx, sub_text in enumerate(sub_chunks):
                chunk_id = _make_chunk_id(file_name, page.page_number, idx)
                chunks.append(DocumentChunk(
                    chunk_id=chunk_id,
                    text=sub_text,
                    source_file=file_name,
                    source_path=file_path,
                    page_numbers=[page.page_number],
                    start_page=page.page_number,
                    end_page=page.page_number,
                    total_pages=total_pages,
                    section_title=current_section,
                    chunk_index=idx,
                    char_count=len(sub_text),
                ))

    # 후처리: 너무 짧은 청크를 이전 청크에 병합
    chunks = _merge_short_chunks(chunks, min_chunk_chars, max_chunk_chars)

    return chunks


def _split_long_text(
    text: str,
    max_chars: int,
    min_chars: int,
    overlap_chars: int,
) -> List[str]:
    """
    긴 텍스트를 단락(\\n) 경계에서 분할합니다.
    오버랩을 적용하여 문맥 연속성을 보장합니다.
    """
    paragraphs = text.split("\n")
    chunks: List[str] = []
    current_parts: List[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_len = len(para)

        # 단일 단락이 max_chars를 초과하는 경우 강제 분할
        if para_len > max_chars:
            # 현재 버퍼 먼저 저장
            if current_parts:
                chunks.append("\n".join(current_parts))
                current_parts = []
                current_len = 0

            # 문장 단위로 분할 시도
            sentences = _split_into_sentences(para)
            for sent in sentences:
                if current_len + len(sent) > max_chars and current_parts:
                    chunks.append("\n".join(current_parts))
                    # 오버랩: 마지막 부분 유지
                    overlap_text = current_parts[-1] if current_parts else ""
                    current_parts = [overlap_text] if len(overlap_text) <= overlap_chars else []
                    current_len = sum(len(p) for p in current_parts)
                current_parts.append(sent)
                current_len += len(sent)
            continue

        if current_len + para_len > max_chars and current_parts:
            chunks.append("\n".join(current_parts))
            # 오버랩: 마지막 단락 유지
            overlap_part = current_parts[-1] if current_parts else ""
            if len(overlap_part) <= overlap_chars:
                current_parts = [overlap_part]
                current_len = len(overlap_part)
            else:
                current_parts = []
                current_len = 0
        
        current_parts.append(para)
        current_len += para_len

    # 남은 버퍼 저장
    if current_parts:
        chunks.append("\n".join(current_parts))

    return chunks


def _split_into_sentences(text: str) -> List[str]:
    """텍스트를 문장 단위로 분할합니다 (한국어/영어 혼용 지원)."""
    # 한국어 문장 종결 패턴 + 영어 마침표/물음표/느낌표
    pattern = r'(?<=[.!?。]\s)|(?<=다\.\s)|(?<=요\.\s)|(?<=음\.\s)'
    sentences = re.split(pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def _detect_section_title(text: str) -> Optional[str]:
    """
    텍스트에서 섹션 제목을 감지합니다.
    연구노트에서 흔한 패턴: "1. 서론", "제2장 실험방법", "[제목 1]" 등
    """
    lines = text.split("\n")[:5]  # 첫 5줄만 확인
    for line in lines:
        line = line.strip()
        # [제목 N] 패턴 (docx 파서에서 삽입)
        match = re.match(r'^\[제목\s*\d*\]\s*(.+)', line)
        if match:
            return match.group(1).strip()
        # 번호 + 제목 패턴: "1. 서론", "2.1 실험 방법"
        match = re.match(r'^(\d+\.?\d*\.?)\s+(.{2,50})$', line)
        if match:
            return match.group(2).strip()
        # 로마/한자 장절 패턴: "제1장", "Ⅲ."
        match = re.match(r'^(제?\d+[장절편]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.?)\s*(.{2,50})$', line)
        if match:
            return match.group(2).strip()
    return None


def _merge_short_chunks(
    chunks: List[DocumentChunk],
    min_chars: int,
    max_chars: int,
) -> List[DocumentChunk]:
    """너무 짧은 청크를 이전 청크에 병합합니다."""
    if len(chunks) <= 1:
        return chunks

    merged: List[DocumentChunk] = [chunks[0]]

    for chunk in chunks[1:]:
        prev = merged[-1]
        # 같은 파일이고, 현재 청크가 너무 짧고, 병합해도 max 이하이면 병합
        if (
            chunk.source_file == prev.source_file
            and chunk.char_count < min_chars
            and prev.char_count + chunk.char_count <= max_chars
        ):
            # 병합
            prev.text = prev.text + "\n" + chunk.text
            prev.char_count = len(prev.text)
            prev.end_page = max(prev.end_page, chunk.end_page)
            if chunk.end_page not in prev.page_numbers:
                prev.page_numbers.append(chunk.end_page)
            if chunk.section_title and not prev.section_title:
                prev.section_title = chunk.section_title
        else:
            merged.append(chunk)

    return merged


def _make_chunk_id(file_name: str, page_number: int, chunk_index: int) -> str:
    """고유한 청크 ID를 생성합니다."""
    # 파일명에서 확장자 제거하고 안전한 문자만 유지
    safe_name = re.sub(r'[^\w가-힣-]', '_', file_name.rsplit('.', 1)[0])
    return f"{safe_name}_p{page_number}_c{chunk_index}"


def compute_file_hash(file_path: str) -> str:
    """파일의 MD5 해시를 계산합니다 (중복 인제스션 방지용)."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        for block in iter(lambda: f.read(8192), b""):
            hasher.update(block)
    return hasher.hexdigest()
