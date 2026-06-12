"""
연구노트 Q&A 챗봇 — 프로젝트 설정
"""
from pathlib import Path

# ── 프로젝트 루트 ──
PROJECT_ROOT = Path(__file__).parent

# ── llama.cpp 서버 설정 ──
LLAMA_SERVER_URL = "http://127.0.0.1:8080/v1"
LLAMA_MODEL_NAME = "local-model"

# ── 임베딩 설정 ──
EMBEDDING_FALLBACK_MODEL = "intfloat/multilingual-e5-large"

# ── ChromaDB 설정 ──
CHROMADB_PERSIST_DIR = PROJECT_ROOT / "data" / "chromadb"
CHROMADB_COLLECTION = "research_notes"

# ── 문서 인제스션 설정 ──
RESEARCH_NOTES_DIR = PROJECT_ROOT / "data" / "research_notes"
DOCUMENT_INDEX_DIR = PROJECT_ROOT / "data" / "index"
SUPPORTED_EXTENSIONS = {".docx", ".pdf"}

# ── 청킹 설정 ──
CHUNK_SIZE = 500          # 토큰 기준 (한국어 약 750자)
CHUNK_OVERLAP = 50        # 오버랩 토큰 수
MAX_CHUNK_CHARS = 1500    # 한 청크 최대 문자 수
MIN_CHUNK_CHARS = 100     # 너무 작은 청크 방지

# ── RAG 설정 ──
RAG_TOP_K = 5             # 검색 시 상위 K개 청크
RAG_MIN_SIMILARITY = 0.15 # 최소 유사도 임계치

# ── LLM Q&A 설정 ──
MAX_CONTEXT_TOKENS = 3000  # 컨텍스트에 투입할 최대 토큰
MAX_CHAT_HISTORY = 6       # 유지할 대화 히스토리 턴 수
LLM_TEMPERATURE = 0.2      # 답변 생성 온도

# ── 로깅 ──
LOG_DIR = PROJECT_ROOT / "data" / "logs"
