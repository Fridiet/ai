"""Explicit, paid live check using a user-selected container photo."""
import argparse
import mimetypes
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description="실제 용기 사진으로 잔량 추정 API 검증 (OpenAI 요금 발생)")
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    token = os.getenv("AI_SERVICE_TOKEN", "")
    if not os.getenv("OPENAI_API_KEY", "").strip() or len(token) < 32:
        parser.error("AI/.env의 OPENAI_API_KEY와 32자 이상 AI_SERVICE_TOKEN 설정이 필요합니다.")
    if not args.image.is_file():
        parser.error("이미지 파일을 찾을 수 없습니다.")
    mime = mimetypes.guess_type(args.image.name)[0]
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        parser.error("JPEG/PNG/WebP 사진을 지정해주세요.")
    if args.image.stat().st_size > 10 * 1024 * 1024:
        parser.error("사진은 10MB 이하여야 합니다.")
    with args.image.open("rb") as image:
        try:
            response = httpx.post("http://127.0.0.1:8000/api/containers/estimate",
                headers={"X-AI-Service-Token": token},
                files={"file": (args.image.name, image, mime)}, timeout=60)
        except httpx.RequestError:
            parser.exit(1, "AI 서버 연결 실패 또는 시간 초과입니다. 서버 상태를 확인해주세요.\n")
    print("HTTP", response.status_code)
    # The local API deliberately returns only sanitized errors, never upstream bodies.
    print(response.text)
    if response.is_error:
        parser.exit(1)


if __name__ == "__main__":
    main()
