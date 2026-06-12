"""
LLM 클라이언트: llama.cpp 로컬 서버와 통신합니다.
OpenAI-compatible API를 사용하며, 텍스트 및 스트리밍 응답을 지원합니다.
"""
import json
import httpx
from openai import OpenAI
from typing import Dict, Any, Optional, Generator
from src.utils.logging_utils import get_logger

logger = get_logger("llama_client")

DEFAULT_ENDPOINT = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "local-model"


class LlamaClient:
    """llama.cpp 로컬 LLM 클라이언트."""

    def __init__(self, base_url: str = DEFAULT_ENDPOINT, model: str = DEFAULT_MODEL):
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(
            base_url=self.base_url,
            api_key="sk-no-key-required",
        )

    def is_server_running(self) -> bool:
        """llama.cpp 서버가 실행 중인지 확인합니다."""
        try:
            response = httpx.get(f"{self.base_url}/models", timeout=2.0)
            return response.status_code == 200
        except Exception:
            return False

    def get_server_start_instruction(self) -> str:
        """서버 시작 안내 메시지를 반환합니다."""
        return (
            "⚠️ llama.cpp 서버가 실행 중이 아닙니다.\n"
            "터미널에서 다음 명령어로 서버를 시작하세요:\n\n"
            "C:\\llama\\main\\llama-server.exe "
            "-m C:\\llama\\models\\gemma-4-E2B-it-Q8_0.gguf "
            "--port 8080 -c 8192\n\n"
            "※ 연구노트 Q&A를 위해 -c 8192 이상 권장합니다."
        )

    def query(
        self,
        prompt: str,
        system_instruction: str = "You are a helpful research assistant.",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        chat_history: list = None,
    ) -> str:
        """
        LLM에 질의하고 텍스트 응답을 반환합니다.

        Args:
            prompt: 사용자 질문
            system_instruction: 시스템 프롬프트
            temperature: 생성 온도
            max_tokens: 최대 생성 토큰
            chat_history: 이전 대화 히스토리 [{"role": "user/assistant", "content": "..."}]

        Returns:
            LLM 응답 텍스트
        """
        if not self.is_server_running():
            raise ConnectionError(self.get_server_start_instruction())

        messages = [{"role": "system", "content": system_instruction}]

        # 대화 히스토리 추가
        if chat_history:
            messages.extend(chat_history)

        messages.append({"role": "user", "content": prompt})

        try:
            completion = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            response_text = completion.choices[0].message.content.strip()
            return response_text

        except Exception as e:
            logger.error(f"LLM 질의 오류: {e}")
            raise

    def query_stream(
        self,
        prompt: str,
        system_instruction: str = "You are a helpful research assistant.",
        temperature: float = 0.2,
        max_tokens: int = 2048,
        chat_history: list = None,
    ) -> Generator[str, None, None]:
        """
        LLM에 스트리밍 질의를 수행합니다.
        토큰 단위로 yield합니다.

        Args:
            prompt: 사용자 질문
            system_instruction: 시스템 프롬프트
            temperature: 생성 온도
            max_tokens: 최대 생성 토큰
            chat_history: 이전 대화 히스토리

        Yields:
            생성된 텍스트 토큰
        """
        if not self.is_server_running():
            raise ConnectionError(self.get_server_start_instruction())

        messages = [{"role": "system", "content": system_instruction}]

        if chat_history:
            messages.extend(chat_history)

        messages.append({"role": "user", "content": prompt})

        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
            )

            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"LLM 스트리밍 오류: {e}")
            raise

    def query_json(
        self,
        prompt: str,
        system_instruction: str = "Return valid JSON only.",
        temperature: float = 0.1,
        max_retries: int = 3,
    ) -> Optional[Dict[str, Any]]:
        """LLM에 JSON 형식 응답을 요청합니다."""
        if not self.is_server_running():
            raise ConnectionError(self.get_server_start_instruction())

        current_prompt = prompt

        for attempt in range(max_retries):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_instruction},
                        {"role": "user", "content": current_prompt},
                    ],
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )

                response_text = completion.choices[0].message.content.strip()

                # 마크다운 코드 블록 제거
                if response_text.startswith("```json"):
                    response_text = response_text[7:]
                if response_text.startswith("```"):
                    response_text = response_text[3:]
                if response_text.endswith("```"):
                    response_text = response_text[:-3]
                response_text = response_text.strip()

                data = json.loads(response_text)
                return data

            except json.JSONDecodeError as je:
                logger.warning(f"JSON 파싱 실패 (시도 {attempt + 1}): {je}")
                current_prompt = (
                    f"{prompt}\n\n"
                    f"CRITICAL: 이전 응답이 유효하지 않은 JSON이었습니다. "
                    f"올바른 JSON만 출력하세요."
                )
            except Exception as e:
                logger.error(f"LLM 질의 오류: {e}")
                if attempt == max_retries - 1:
                    raise

        return None
