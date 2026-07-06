"""
물건 카탈로그 SQLite 저장소 (표준 라이브러리만 사용).

카탈로그 필드는 컬럼으로, 권리/임차인은 JSON으로 직렬화해 보관한다.
검색(필터)·상세조회를 지원한다.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date
from typing import Optional

from .models import Right, RightType, Tenant
from .listing import Listing, ResidentialType
from .distribution import Region


def _row_to_listing(row: sqlite3.Row) -> Listing:
    rights = [_dict_to_right(d) for d in json.loads(row["rights_json"] or "[]")]
    tenants = [_dict_to_tenant(d) for d in json.loads(row["tenants_json"] or "[]")]
    return Listing(
        case_no=row["case_no"], court=row["court"], raw_type=row["raw_type"],
        residential_type=ResidentialType(row["residential_type"]) if row["residential_type"] else None,
        address=row["address"], region=Region(row["region"]),
        appraisal_value=row["appraisal_value"], min_bid_price=row["min_bid_price"],
        failed_count=row["failed_count"],
        sale_date=date.fromisoformat(row["sale_date"]) if row["sale_date"] else None,
        status=row["status"],
        auction_type=row["auction_type"], building_area=row["building_area"],
        land_area=row["land_area"], view_count=row["view_count"],
        lat=row["lat"], lng=row["lng"], rights=rights, tenants=tenants,
    )


def _right_to_dict(r: Right) -> dict:
    d = asdict(r)
    d["type"] = r.type.value
    d["reg_date"] = r.reg_date.isoformat()
    return d


def _dict_to_right(d: dict) -> Right:
    return Right(
        type=RightType(d["type"]), reg_date=date.fromisoformat(d["reg_date"]),
        holder=d.get("holder", ""), amount=d.get("amount", 0), note=d.get("note", ""),
        jeonse_demanded_distribution=d.get("jeonse_demanded_distribution", False),
        jeonse_covers_whole=d.get("jeonse_covers_whole", True),
    )


def _tenant_to_dict(t: Tenant) -> dict:
    return {
        "name": t.name,
        "move_in_date": t.move_in_date.isoformat() if t.move_in_date else None,
        "fixed_date": t.fixed_date.isoformat() if t.fixed_date else None,
        "deposit": t.deposit, "demanded_distribution": t.demanded_distribution,
        "occupying": t.occupying,
    }


def _dict_to_tenant(d: dict) -> Tenant:
    return Tenant(
        name=d.get("name", ""),
        move_in_date=date.fromisoformat(d["move_in_date"]) if d.get("move_in_date") else None,
        fixed_date=date.fromisoformat(d["fixed_date"]) if d.get("fixed_date") else None,
        deposit=d.get("deposit", 0), demanded_distribution=d.get("demanded_distribution", False),
        occupying=d.get("occupying", True),
    )


class ListingStore:
    def __init__(self, db_path: str = "auction.db"):
        # check_same_thread=False: FastAPI 워커 스레드 간 단일 연결 공유 허용.
        # (SQLite 기본 serialized 모드 → 저동시성 개발/데모에 적정. 고동시성은 풀 도입.)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS listings (
                case_no TEXT PRIMARY KEY,
                court TEXT, raw_type TEXT, residential_type TEXT,
                address TEXT, region TEXT,
                appraisal_value INTEGER, min_bid_price INTEGER,
                failed_count INTEGER, sale_date TEXT, status TEXT,
                auction_type TEXT, building_area REAL, land_area REAL,
                view_count INTEGER,
                lat REAL, lng REAL,
                rights_json TEXT, tenants_json TEXT
            )
        """)
        self.conn.commit()

    def upsert(self, listing: Listing) -> None:
        self.conn.execute("""
            INSERT INTO listings VALUES (
                :case_no,:court,:raw_type,:residential_type,:address,:region,
                :appraisal_value,:min_bid_price,:failed_count,:sale_date,:status,
                :auction_type,:building_area,:land_area,:view_count,
                :lat,:lng,:rights_json,:tenants_json)
            ON CONFLICT(case_no) DO UPDATE SET
                court=excluded.court, raw_type=excluded.raw_type,
                residential_type=excluded.residential_type, address=excluded.address,
                region=excluded.region, appraisal_value=excluded.appraisal_value,
                min_bid_price=excluded.min_bid_price, failed_count=excluded.failed_count,
                sale_date=excluded.sale_date, status=excluded.status,
                auction_type=excluded.auction_type, building_area=excluded.building_area,
                land_area=excluded.land_area,
                lat=excluded.lat, lng=excluded.lng,
                rights_json=excluded.rights_json, tenants_json=excluded.tenants_json
        """, {
            "case_no": listing.case_no, "court": listing.court, "raw_type": listing.raw_type,
            "residential_type": listing.residential_type.value if listing.residential_type else None,
            "address": listing.address, "region": listing.region.value,
            "appraisal_value": listing.appraisal_value, "min_bid_price": listing.min_bid_price,
            "failed_count": listing.failed_count,
            "sale_date": listing.sale_date.isoformat() if listing.sale_date else None,
            "status": listing.status, "auction_type": listing.auction_type,
            "building_area": listing.building_area, "land_area": listing.land_area,
            "view_count": listing.view_count,
            "lat": listing.lat, "lng": listing.lng,
            "rights_json": json.dumps([_right_to_dict(r) for r in listing.rights], ensure_ascii=False),
            "tenants_json": json.dumps([_tenant_to_dict(t) for t in listing.tenants], ensure_ascii=False),
        })
        self.conn.commit()

    def get(self, case_no: str) -> Optional[Listing]:
        row = self.conn.execute(
            "SELECT * FROM listings WHERE case_no=?", (case_no,)).fetchone()
        return _row_to_listing(row) if row else None

    _SORTS = {
        "매각기일": "sale_date ASC",
        "감정가높은": "appraisal_value DESC",
        "감정가낮은": "appraisal_value ASC",
        "최저가높은": "min_bid_price DESC",
        "최저가낮은": "min_bid_price ASC",
        "유찰많은": "failed_count DESC",
    }

    def search(
        self,
        types: Optional[list[ResidentialType]] = None,
        region: Optional[Region] = None,
        court: Optional[str] = None,
        keyword: Optional[str] = None,           # 소재지/명칭 검색
        auction_type: Optional[str] = None,      # 임의경매/강제경매
        min_price: Optional[int] = None,         # 최저가 하한
        max_price: Optional[int] = None,         # 최저가 상한
        appraisal_min: Optional[int] = None,
        appraisal_max: Optional[int] = None,
        failed_min: Optional[int] = None,
        failed_max: Optional[int] = None,
        building_area_min: Optional[float] = None,
        building_area_max: Optional[float] = None,
        sale_from: Optional[str] = None,         # ISO 날짜
        sale_to: Optional[str] = None,
        sort: str = "매각기일",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Listing]:
        sql = "SELECT * FROM listings WHERE 1=1"
        params: list = []
        if types:
            sql += f" AND residential_type IN ({','.join('?' * len(types))})"
            params += [t.value for t in types]
        if region:
            sql += " AND region=?"; params.append(region.value)
        if court:
            sql += " AND court LIKE ?"; params.append(f"%{court}%")
        if keyword:
            sql += " AND address LIKE ?"; params.append(f"%{keyword}%")
        if auction_type:
            sql += " AND auction_type=?"; params.append(auction_type)
        if min_price is not None:
            sql += " AND min_bid_price>=?"; params.append(min_price)
        if max_price is not None:
            sql += " AND min_bid_price<=?"; params.append(max_price)
        if appraisal_min is not None:
            sql += " AND appraisal_value>=?"; params.append(appraisal_min)
        if appraisal_max is not None:
            sql += " AND appraisal_value<=?"; params.append(appraisal_max)
        if failed_min is not None:
            sql += " AND failed_count>=?"; params.append(failed_min)
        if failed_max is not None:
            sql += " AND failed_count<=?"; params.append(failed_max)
        if building_area_min is not None:
            sql += " AND building_area>=?"; params.append(building_area_min)
        if building_area_max is not None:
            sql += " AND building_area<=?"; params.append(building_area_max)
        if sale_from:
            sql += " AND sale_date>=?"; params.append(sale_from)
        if sale_to:
            sql += " AND sale_date<=?"; params.append(sale_to)
        sql += f" ORDER BY {self._SORTS.get(sort, 'sale_date ASC')} LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_listing(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]

    def close(self) -> None:
        self.conn.close()
