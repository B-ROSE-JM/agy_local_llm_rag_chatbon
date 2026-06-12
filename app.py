"""
연구노트 Q&A 챗봇 — Streamlit 메인 앱

3개 탭:
1. 📥 문서 관리: 연구노트 업로드/인제스션, 인덱스 현황 대시보드
2. 💬 Q&A 챗봇: 멀티턴 대화형 인터페이스, 출처 표시, 원문 바로가기
3. 📊 분석 대시보드: 문서 통계, Q&A 히스토리
"""
import os
os.environ["PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION"] = "python"

import warnings
warnings.filterwarnings("ignore")

import sys
import json
import shutil
import streamlit as st
from pathlib import Path

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    RESEARCH_NOTES_DIR,
    RAG_TOP_K,
    MAX_CHAT_HISTORY,
    CHROMADB_PERSIST_DIR,
    CHROMADB_COLLECTION,
)
from src.ingestion.ingest_pipeline import (
    ingest_file,
    ingest_directory,
    get_ingest_status,
    remove_file_from_index,
)
from src.rag.qa_chain import QAChain
from src.utils.file_viewer import format_source_badge, generate_file_link


# ── 페이지 설정 ──
st.set_page_config(
    page_title="🔬 연구노트 Q&A 챗봇",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 프리미엄 CSS 스타일 ──
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

    /* 전역 폰트 및 기본 스타일 */
    .stApp {
        font-family: 'Inter', 'Noto Sans KR', sans-serif;
    }

    /* 메인 타이틀 그라디언트 */
    .main-title {
        font-size: 36px;
        font-weight: 800;
        background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 50%, #EC4899 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 8px;
        letter-spacing: -0.5px;
    }
    .main-subtitle {
        font-size: 15px;
        color: #94A3B8;
        margin-bottom: 24px;
    }

    /* 메트릭 카드 */
    .metric-card {
        background: linear-gradient(135deg, rgba(99,102,241,0.08) 0%, rgba(139,92,246,0.05) 100%);
        padding: 20px;
        border-radius: 16px;
        border: 1px solid rgba(99,102,241,0.15);
        text-align: center;
        transition: transform 0.2s, box-shadow 0.2s;
    }
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(99,102,241,0.15);
    }
    .metric-value {
        font-size: 32px;
        font-weight: 700;
        background: linear-gradient(135deg, #6366F1, #8B5CF6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .metric-label {
        font-size: 13px;
        color: #94A3B8;
        margin-top: 4px;
    }

    /* 소스 카드 */
    .source-card {
        background: rgba(99,102,241,0.06);
        border: 1px solid rgba(99,102,241,0.15);
        border-radius: 12px;
        padding: 12px 16px;
        margin: 6px 0;
        transition: all 0.2s;
    }
    .source-card:hover {
        border-color: rgba(99,102,241,0.4);
        background: rgba(99,102,241,0.1);
    }
    .source-file {
        font-weight: 600;
        color: #6366F1;
        font-size: 14px;
    }
    .source-meta {
        color: #94A3B8;
        font-size: 12px;
        margin-top: 4px;
    }
    .source-sim {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 11px;
        font-weight: 600;
        color: white;
    }
    .sim-high { background: #10B981; }
    .sim-mid { background: #3B82F6; }
    .sim-low { background: #F59E0B; }

    /* 채팅 메시지 커스터마이징 */
    .stChatMessage {
        border-radius: 16px !important;
    }

    /* 파일 목록 테이블 */
    .file-table {
        width: 100%;
        border-collapse: separate;
        border-spacing: 0 8px;
    }
    .file-table th {
        background: rgba(99,102,241,0.1);
        padding: 10px 16px;
        border-radius: 8px;
        font-size: 13px;
        color: #6366F1;
        font-weight: 600;
    }
    .file-table td {
        padding: 10px 16px;
        background: rgba(255,255,255,0.02);
        border-bottom: 1px solid rgba(99,102,241,0.08);
    }

    /* 사이드바 */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0F172A 0%, #1E293B 100%);
    }

    /* 탭 스타일 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 20px;
    }

    /* 스피너 */
    .stSpinner > div {
        border-color: #6366F1 !important;
    }
</style>
""", unsafe_allow_html=True)


# ── 세션 상태 초기화 ──
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "messages" not in st.session_state:
    st.session_state.messages = []
if "qa_chain" not in st.session_state:
    st.session_state.qa_chain = None


def get_qa_chain() -> QAChain:
    """QAChain 싱글톤을 반환합니다."""
    if st.session_state.qa_chain is None:
        chain = QAChain()
        chain.initialize()
        st.session_state.qa_chain = chain
    return st.session_state.qa_chain


# ── 헤더 ──
st.markdown('<div class="main-title">🔬 연구노트 Q&A 챗봇</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="main-subtitle">'
    '로컬 LLM 기반 RAG 시스템 — 연구노트에서 답을 찾아드립니다'
    '</div>',
    unsafe_allow_html=True,
)

# ── 사이드바 ──
with st.sidebar:
    st.markdown("### ⚙️ 설정")

    # LLM 서버 상태
    from src.llm.llama_client import LlamaClient
    llm = LlamaClient()
    server_ok = llm.is_server_running()

    if server_ok:
        st.success("✅ LLM 서버 연결됨")
    else:
        st.error("❌ LLM 서버 미연결")
        st.code(llm.get_server_start_instruction(), language="bash")

    st.divider()

    # 검색 설정
    st.markdown("### 🔍 검색 옵션")
    search_top_k = st.slider("검색 결과 수 (Top-K)", 3, 15, RAG_TOP_K)

    # 파일 필터
    status = get_ingest_status()
    file_names = [f["name"] for f in status.get("files", [])]
    file_filter_options = ["전체 검색"] + file_names
    selected_filter = st.selectbox("검색 범위", file_filter_options)
    file_filter = None if selected_filter == "전체 검색" else selected_filter

    st.divider()

    # 인덱스 요약
    st.markdown("### 📊 인덱스 현황")
    st.metric("인덱싱된 문서", f"{status['indexed_files']}건")
    st.metric("총 청크 수", f"{status['total_chunks']}개")
    st.metric("총 페이지 수", f"{status['total_pages']}p")

    st.divider()

    # 대화 초기화
    if st.button("🗑️ 대화 초기화", use_container_width=True):
        st.session_state.chat_history = []
        st.session_state.messages = []
        st.rerun()


# ── 메인 콘텐츠: 탭 ──
tab_chat, tab_docs, tab_dashboard = st.tabs([
    "💬 Q&A 챗봇",
    "📥 문서 관리",
    "📊 분석 대시보드",
])


# ═══════════════════════════════════════════
# 탭 1: Q&A 챗봇
# ═══════════════════════════════════════════
with tab_chat:
    if status["indexed_files"] == 0:
        st.info(
            "📥 먼저 '문서 관리' 탭에서 연구노트를 업로드하고 인제스션하세요."
        )
    else:
        # 채팅 히스토리 표시
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

                # 어시스턴트 메시지에 출처 표시
                if msg["role"] == "assistant" and "sources" in msg:
                    sources = msg["sources"]
                    if sources:
                        st.markdown("---")
                        st.markdown("**📑 출처:**")
                        for src in sources:
                            sim = src["similarity"]
                            sim_class = (
                                "sim-high" if sim >= 0.6
                                else "sim-mid" if sim >= 0.4
                                else "sim-low"
                            )
                            sim_pct = f"{sim:.0%}"

                            col_info, col_link = st.columns([4, 1])
                            with col_info:
                                st.markdown(
                                    f'<div class="source-card">'
                                    f'<span class="source-file">📄 {src["file"]}</span>'
                                    f'&nbsp;&nbsp;'
                                    f'<span class="source-sim {sim_class}">{sim_pct}</span>'
                                    f'<div class="source-meta">'
                                    f'페이지 {src["pages"]}'
                                    f'{" · " + src["section"] if src.get("section") else ""}'
                                    f'</div></div>',
                                    unsafe_allow_html=True,
                                )
                            with col_link:
                                if src.get("link"):
                                    st.markdown(
                                        f'<a href="{src["link"]}" target="_blank" '
                                        f'style="text-decoration:none; color:#6366F1; '
                                        f'font-weight:600; font-size:13px;">'
                                        f'📂 원문 열기</a>',
                                        unsafe_allow_html=True,
                                    )

        # 채팅 입력
        if prompt := st.chat_input("연구노트에 대해 질문하세요..."):
            # 사용자 메시지 표시
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # 어시스턴트 응답 생성
            with st.chat_message("assistant"):
                with st.spinner("연구노트에서 관련 내용을 검색하고 답변을 생성 중..."):
                    chain = get_qa_chain()

                    # 대화 히스토리 구성 (최근 N턴)
                    history_for_llm = []
                    recent_msgs = st.session_state.messages[-(MAX_CHAT_HISTORY * 2):]
                    for m in recent_msgs:
                        if m["role"] in ("user", "assistant"):
                            history_for_llm.append({
                                "role": m["role"],
                                "content": m["content"][:500],
                            })

                    # 스트리밍 응답
                    try:
                        token_gen, sources = chain.ask_stream(
                            question=prompt,
                            chat_history=history_for_llm,
                            top_k=search_top_k,
                            file_filter=file_filter,
                        )

                        full_response = st.write_stream(token_gen)

                    except Exception as e:
                        full_response = f"⚠️ 오류가 발생했습니다: {str(e)}"
                        sources = []
                        st.error(full_response)

                # 출처 표시
                if sources:
                    st.markdown("---")
                    st.markdown("**📑 출처:**")
                    for src in sources:
                        sim = src["similarity"]
                        sim_class = (
                            "sim-high" if sim >= 0.6
                            else "sim-mid" if sim >= 0.4
                            else "sim-low"
                        )
                        sim_pct = f"{sim:.0%}"

                        col_info, col_link = st.columns([4, 1])
                        with col_info:
                            st.markdown(
                                f'<div class="source-card">'
                                f'<span class="source-file">📄 {src["file"]}</span>'
                                f'&nbsp;&nbsp;'
                                f'<span class="source-sim {sim_class}">{sim_pct}</span>'
                                f'<div class="source-meta">'
                                f'페이지 {src["pages"]}'
                                f'{" · " + src["section"] if src.get("section") else ""}'
                                f'</div></div>',
                                unsafe_allow_html=True,
                            )
                        with col_link:
                            if src.get("link"):
                                st.markdown(
                                    f'<a href="{src["link"]}" target="_blank" '
                                    f'style="text-decoration:none; color:#6366F1; '
                                    f'font-weight:600; font-size:13px;">'
                                    f'📂 원문 열기</a>',
                                    unsafe_allow_html=True,
                                )

            # 어시스턴트 메시지 저장
            st.session_state.messages.append({
                "role": "assistant",
                "content": full_response,
                "sources": sources,
            })


# ═══════════════════════════════════════════
# 탭 2: 문서 관리
# ═══════════════════════════════════════════
with tab_docs:
    st.markdown("### 📥 연구노트 업로드 & 인제스션")

    doc_col1, doc_col2 = st.columns([2, 1])

    with doc_col1:
        uploaded_files = st.file_uploader(
            "연구노트 파일 업로드 (Word / PDF)",
            type=["docx", "pdf"],
            accept_multiple_files=True,
            help="최대 40페이지 × 100건의 연구노트를 지원합니다.",
        )

        if uploaded_files:
            st.info(f"📎 {len(uploaded_files)}개 파일 선택됨")

            if st.button("🚀 인제스션 실행", type="primary", use_container_width=True):
                # 파일 저장
                RESEARCH_NOTES_DIR.mkdir(parents=True, exist_ok=True)

                progress = st.progress(0.0)
                status_text = st.empty()

                total = len(uploaded_files)
                all_results = []

                for idx, file in enumerate(uploaded_files):
                    status_text.text(
                        f"처리 중: {file.name} ({idx + 1}/{total})"
                    )

                    # 파일 저장
                    save_path = RESEARCH_NOTES_DIR / file.name
                    with open(save_path, "wb") as f:
                        f.write(file.getbuffer())

                    # 인제스션
                    try:
                        def update_progress(p):
                            overall = (idx + p) / total
                            progress.progress(overall)

                        result = ingest_file(
                            str(save_path),
                            force=True,
                            progress_callback=update_progress,
                        )
                        all_results.append(result)
                    except Exception as e:
                        all_results.append({
                            "file_name": file.name,
                            "status": "error",
                            "error": str(e),
                        })

                progress.progress(1.0)
                status_text.empty()

                # 결과 표시
                success = sum(1 for r in all_results if r.get("status") == "success")
                errors = sum(1 for r in all_results if r.get("status") == "error")

                if success > 0:
                    st.success(
                        f"✅ {success}개 파일 인제스션 완료! "
                        f"(총 {sum(r.get('chunks', 0) for r in all_results)}개 청크 생성)"
                    )
                if errors > 0:
                    st.error(f"❌ {errors}개 파일 오류 발생")
                    for r in all_results:
                        if r.get("status") == "error":
                            st.warning(f"  {r['file_name']}: {r.get('error', '')}")

                # QAChain 캐시 무효화
                st.session_state.qa_chain = None
                st.rerun()

    with doc_col2:
        st.markdown("#### 📂 폴더 일괄 인제스션")
        dir_path = st.text_input(
            "연구노트 디렉토리 경로",
            value=str(RESEARCH_NOTES_DIR),
            help="해당 디렉토리 내 모든 .docx, .pdf 파일을 인제스션합니다.",
        )

        if st.button("📁 폴더 일괄 처리", use_container_width=True):
            if Path(dir_path).exists():
                with st.spinner("폴더 내 모든 문서를 인제스션 중..."):
                    result = ingest_directory(dir_path, force=False)
                    st.success(
                        f"✅ {result['success_files']}/{result['total_files']}건 처리 완료 "
                        f"({result['total_chunks']}개 청크)"
                    )
                    if result["error_files"] > 0:
                        st.warning(f"⚠️ {result['error_files']}건 오류")
                st.session_state.qa_chain = None
                st.rerun()
            else:
                st.error("디렉토리가 존재하지 않습니다.")

    # ── 인덱스 현황 ──
    st.divider()
    st.markdown("### 📋 인덱싱된 문서 목록")

    status = get_ingest_status()
    files = status.get("files", [])

    if not files:
        st.info("인덱싱된 문서가 없습니다. 위에서 연구노트를 업로드하세요.")
    else:
        # 메트릭 카드
        mc1, mc2, mc3, mc4 = st.columns(4)
        with mc1:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{status["indexed_files"]}</div>'
                f'<div class="metric-label">인덱싱된 문서</div></div>',
                unsafe_allow_html=True,
            )
        with mc2:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{status["total_chunks"]}</div>'
                f'<div class="metric-label">총 청크 수</div></div>',
                unsafe_allow_html=True,
            )
        with mc3:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{status["total_pages"]}</div>'
                f'<div class="metric-label">총 페이지 수</div></div>',
                unsafe_allow_html=True,
            )
        with mc4:
            total_size = sum(f.get("size_mb", 0) for f in files)
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{total_size:.1f}</div>'
                f'<div class="metric-label">총 용량 (MB)</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("")

        # 파일 목록 테이블
        for f in files:
            with st.expander(
                f"📄 **{f['name']}** — {f['pages']}p · {f['chunks']}청크 · {f['size_mb']}MB"
            ):
                fc1, fc2, fc3 = st.columns([2, 1, 1])
                with fc1:
                    st.write(f"**인제스션 시각**: {f.get('ingested_at', '알 수 없음')}")
                with fc2:
                    if st.button("🔄 재인제스션", key=f"reingest_{f['name']}"):
                        file_path = RESEARCH_NOTES_DIR / f["name"]
                        if file_path.exists():
                            with st.spinner(f"'{f['name']}' 재인제스션 중..."):
                                ingest_file(str(file_path), force=True)
                            st.session_state.qa_chain = None
                            st.rerun()
                with fc3:
                    if st.button("🗑️ 삭제", key=f"delete_{f['name']}"):
                        remove_file_from_index(f["name"])
                        st.session_state.qa_chain = None
                        st.rerun()


# ═══════════════════════════════════════════
# 탭 3: 분석 대시보드
# ═══════════════════════════════════════════
with tab_dashboard:
    st.markdown("### 📊 연구노트 분석 대시보드")

    status = get_ingest_status()

    if status["indexed_files"] == 0:
        st.info("📥 먼저 문서를 인제스션하세요.")
    else:
        # 요약 통계
        d1, d2, d3, d4 = st.columns(4)
        with d1:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{status["indexed_files"]}</div>'
                f'<div class="metric-label">연구노트 수</div></div>',
                unsafe_allow_html=True,
            )
        with d2:
            avg_pages = (
                status["total_pages"] / status["indexed_files"]
                if status["indexed_files"] > 0
                else 0
            )
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{avg_pages:.1f}</div>'
                f'<div class="metric-label">평균 페이지 수</div></div>',
                unsafe_allow_html=True,
            )
        with d3:
            avg_chunks = (
                status["total_chunks"] / status["indexed_files"]
                if status["indexed_files"] > 0
                else 0
            )
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{avg_chunks:.1f}</div>'
                f'<div class="metric-label">평균 청크 수</div></div>',
                unsafe_allow_html=True,
            )
        with d4:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{status["total_chunks"]}</div>'
                f'<div class="metric-label">검색 가능 청크</div></div>',
                unsafe_allow_html=True,
            )

        st.divider()

        # 문서 크기 분포 차트
        st.markdown("#### 📄 문서별 청크 분포")
        files = status.get("files", [])
        if files:
            import plotly.express as px
            import pandas as pd

            df_files = pd.DataFrame(files)
            if not df_files.empty:
                fig = px.bar(
                    df_files.sort_values("chunks", ascending=True),
                    x="chunks",
                    y="name",
                    orientation="h",
                    title="문서별 생성된 청크 수",
                    labels={"chunks": "청크 수", "name": "파일명"},
                    color="chunks",
                    color_continuous_scale="Viridis",
                )
                fig.update_layout(
                    height=max(300, len(files) * 30),
                    showlegend=False,
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(family="Inter"),
                )
                st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # Q&A 히스토리
        st.markdown("#### 💬 최근 Q&A 히스토리")
        try:
            chain = get_qa_chain()
            qa_history = chain.get_qa_history(limit=20)
            if qa_history:
                for i, entry in enumerate(reversed(qa_history[-10:])):
                    with st.expander(
                        f"❓ {entry['question'][:80]}... "
                        f"({entry.get('timestamp', '')[:16]})"
                    ):
                        st.write(f"**답변**: {entry.get('answer', '')[:500]}...")
                        if entry.get("sources"):
                            st.write("**출처**:")
                            for s in entry["sources"]:
                                st.write(
                                    f"  - {s['file']} (p.{s['pages']}, "
                                    f"유사도 {s['similarity']:.2f})"
                                )
            else:
                st.info("아직 Q&A 기록이 없습니다.")
        except Exception:
            st.info("Q&A 히스토리를 불러올 수 없습니다.")

        st.divider()

        # 문서 요약 기능
        st.markdown("#### 📝 문서 요약 생성")
        summary_file = st.selectbox(
            "요약할 문서 선택",
            [f["name"] for f in files],
            key="summary_select",
        )
        if st.button("📝 요약 생성", key="generate_summary"):
            with st.spinner(f"'{summary_file}' 요약 생성 중..."):
                try:
                    chain = get_qa_chain()
                    summary = chain.summarize_document(summary_file)
                    st.markdown(f"**📋 {summary_file} 요약:**")
                    st.write(summary)
                except Exception as e:
                    st.error(f"요약 생성 오류: {e}")
