"""SerpApi(Google Lens / Google 검색) 클라이언트.

인스타그램은 사진으로 게시물을 찾는 공식 API 를 제공하지 않으므로,
Google Lens 역이미지 검색 결과 중 instagram.com 링크를 골라내는 방식을 쓴다.

- 이미지 업로드: POST https://serpapi.com/image  (JPG/PNG/WebP, 500KB 이하, 10분 후 만료)
- Lens 검색:    GET  https://serpapi.com/search.json?engine=google_lens&image_id=...
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://serpapi.com"

# 결과가 없을 때 SerpApi 가 error 필드로 돌려주는 문구들
_NO_RESULTS_MARKERS = ("hasn't returned any results", "no results")


class SerpApiError(RuntimeError):
    """SerpApi 호출 실패 (키 오류, 크레딧 부족, 네트워크 오류 등)."""


class SerpApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        language: str | None = "en",
        country: str | None = None,
        timeout: float = 90.0,
        http: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("SERPAPI_API_KEY 가 설정되지 않았습니다.")
        self.api_key = api_key
        self.language = language or None
        self.country = country or None
        self._http = http or httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> SerpApiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ 내부
    def _check(self, response: httpx.Response) -> dict[str, Any]:
        # 주의: 요청 URL 에 API 키가 들어 있으므로 raise_for_status() 의 메시지를 그대로 쓰지 않는다.
        try:
            data = response.json()
        except ValueError:
            raise SerpApiError(
                f"SerpApi 응답을 해석할 수 없습니다 (HTTP {response.status_code})."
            ) from None
        if not isinstance(data, dict):
            raise SerpApiError("SerpApi 응답 형식이 올바르지 않습니다.")
        error = data.get("error")
        if error:
            if any(marker in str(error).lower() for marker in _NO_RESULTS_MARKERS):
                return {}
            raise SerpApiError(str(error))
        if response.status_code >= 400:
            raise SerpApiError(f"SerpApi 요청 실패 (HTTP {response.status_code}).")
        return data

    def _search(self, params: dict[str, Any]) -> dict[str, Any]:
        params = {k: v for k, v in params.items() if v is not None}
        params["api_key"] = self.api_key
        try:
            response = self._http.get(f"{BASE_URL}/search.json", params=params)
        except httpx.HTTPError as exc:
            raise SerpApiError(f"SerpApi 에 연결할 수 없습니다 ({type(exc).__name__}).") from None
        return self._check(response)

    # ------------------------------------------------------------------ 공개 API
    def upload_image(self, data: bytes, filename: str = "image.jpg") -> str:
        """이미지를 업로드하고 Lens 검색에 쓸 image_id 를 돌려준다."""
        try:
            response = self._http.post(
                f"{BASE_URL}/image",
                data={"api_key": self.api_key},
                files={"image": (filename, data, "image/jpeg")},
            )
        except httpx.HTTPError as exc:
            raise SerpApiError(f"SerpApi 에 연결할 수 없습니다 ({type(exc).__name__}).") from None
        result = self._check(response)
        image_id = result.get("image_id")
        if not image_id:
            raise SerpApiError("이미지 업로드 응답에 image_id 가 없습니다.")
        return image_id

    def lens(
        self,
        *,
        image_id: str | None = None,
        url: str | None = None,
        search_type: str = "exact_matches",
        query: str | None = None,
    ) -> dict[str, Any]:
        """Google Lens 검색. image_id 또는 공개 이미지 url 중 하나가 필요하다."""
        if not image_id and not url:
            raise ValueError("image_id 또는 url 이 필요합니다.")
        return self._search(
            {
                "engine": "google_lens",
                "type": search_type,
                "image_id": image_id,
                "url": None if image_id else url,
                "q": query,
                "hl": self.language,
                "country": self.country,
            }
        )

    def google(self, query: str, num: int = 10) -> dict[str, Any]:
        """일반 Google 검색 (게시물의 게시자 이름을 보완할 때 사용)."""
        return self._search(
            {"engine": "google", "q": query, "num": num, "hl": self.language, "gl": self.country}
        )
