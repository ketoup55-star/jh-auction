"""지역(시도/시군구/읍면동) 이름·코드 조회 서비스 — 바이트코드 역분석으로 복원.

원본: service.pyc. 데이터 출처는 lawd_data.LAWD_CD_LIST (법정동코드 데이터셋).
LAWD_CD_LIST 구조(추정):
    [
      { "name": "서울특별시", "code": ..., "sigungu": [
          { "name": "종로구", "code": ..., "eupmyeondong": [
              { "name": "청운동", "lawd_code": "1111010100", ... }, ...
          ]}, ...
      ]}, ...
    ]
"""
from __future__ import annotations

from typing import Any

try:
    from kmong.kb2.lawd_data import LAWD_CD_LIST
except ModuleNotFoundError:
    from lawd_data import LAWD_CD_LIST


class Service:
    def __init__(self):
        self.lawd_cd_list = LAWD_CD_LIST

    def get_sido_names(self) -> list[str]:
        return [sido["name"] for sido in self.lawd_cd_list]

    def get_sigungu_names(self, sido_name: str) -> list[str]:
        sido = self.get_sido(sido_name)
        if not sido:
            return []
        return sorted(sigungu["name"] for sigungu in sido.get("sigungu", []))

    def get_eupmyeondong_names(self, sido_name: str, sigungu_name: str) -> list[str]:
        sigungu = self.get_sigungu(sido_name, sigungu_name)
        if not sigungu:
            return []
        return sorted(
            eupmyeondong["name"] for eupmyeondong in sigungu.get("eupmyeondong", [])
        )

    def get_sido(self, sido_name: str) -> dict[str, Any] | None:
        return next(
            (sido for sido in self.lawd_cd_list if sido.get("name") == sido_name),
            None,
        )

    def get_sigungu(self, sido_name: str, sigungu_name: str) -> dict[str, Any] | None:
        sido = self.get_sido(sido_name)
        if not sido:
            return None
        return next(
            (
                sigungu
                for sigungu in sido.get("sigungu", [])
                if sigungu.get("name") == sigungu_name
            ),
            None,
        )

    def get_eupmyeondong(
        self, sido_name: str, sigungu_name: str, eupmyeondong_name: str
    ) -> dict[str, Any] | None:
        sigungu = self.get_sigungu(sido_name, sigungu_name)
        if not sigungu:
            return None
        return next(
            (
                eupmyeondong
                for eupmyeondong in sigungu.get("eupmyeondong", [])
                if eupmyeondong.get("name") == eupmyeondong_name
            ),
            None,
        )
