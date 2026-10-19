"""인사 시스템 API에서 원천 표를 받는다(2026-10-19 사용자 요청, 설계 .omc/plan/2026-10-19-hr-source.md).

사용자: "해당 서비스(기술 이력 화면 서비스, 피어 리뷰 화면 서비스 등 > 아마 인사 시스템)에서 api 따서 그 정보들을 뭐 배치성으로 주기적으로
인터페이스 받겠지 … 디폴트는 api고 그게 없다면 2번째 옵션으로 Csv로도 받을 수 있도록 해줘".

- 사람·인사 정보(HR_TABLES)는 인사 시스템에서, 배치 결정에 쓰는 사업·현재 배치(SERVICE_TABLES)와 manifest의 단계·동시 수행 허용 등은
  이 서비스의 작업 데이터 판에서 온다 -- 받을 때마다 지금 판의 것을 이어 붙여 낮에 등록한 신규 사업·확정이 사라지지 않게.
- 표마다 원천을 바꿀 수 있다(설정 JSON `tables`): 끝점을 주면 API, null이면 지금 데이터의 그 표(그 서비스가 없는 회사 -- CSV로 올린 것).
  사업 표에 끝점을 주면 사업도 API에서 받는다(PMS 연동).
- 받은 표는 CSV 묶음으로 바꿔 업로드와 **같은 검사**(load_bundle)를 거친다 -- 검증 경로는 하나다. 이 모듈은 받기·합치기만 한다.
- 계약(docs/hr-api-contract.md): GET {base}/{endpoint}?limit=N[&cursor=C], Authorization: Bearer 토큰 → {"items": [...], "next": 커서|null}.
  항목 칸 이름은 CSV 계약의 열 이름(설정 `fields`로 {계약 열: 원천 칸} 대응 가능).
"""
from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from core.ingest.contract import FILE_SPECS

HR_TABLES = ("people.csv", "rate_card.csv", "person_skills.csv", "work_history.csv", "work_skills.csv",
             "availability.csv", "reviews.csv", "review_items.csv", "project_outcomes.csv", "replacements.csv")
SERVICE_TABLES = ("projects.csv", "project_grade_requirements.csv", "project_skill_requirements.csv",
                  "current_assignments.csv")
DEFAULT_ENDPOINTS = {"people.csv": "people", "rate_card.csv": "rate-card", "person_skills.csv": "person-skills",
                     "work_history.csv": "work-history", "work_skills.csv": "work-skills",
                     "availability.csv": "availability", "reviews.csv": "reviews", "review_items.csv": "review-items",
                     "project_outcomes.csv": "project-outcomes", "replacements.csv": "replacements"}
# 사업 표 끝점 이름(설정에 적을 때의 기본값 -- 기본으로는 받지 않는다)
SERVICE_ENDPOINTS = {"projects.csv": "projects", "project_grade_requirements.csv": "project-grade-requirements",
                     "project_skill_requirements.csv": "project-skill-requirements",
                     "current_assignments.csv": "current-assignments"}

URL_ENV = "TEAMWEAVER_HR_API_URL"
TOKEN_ENV = "TEAMWEAVER_HR_API_TOKEN"
CONFIG_ENV = "TEAMWEAVER_HR_API_CONFIG"
PAGE_SIZE = 500
MAX_PAGES = 10_000           # 커서가 돌고 돌아도 끝나게
TIMEOUT_S = 30.0


class HrApiError(Exception):
    """받지 못했다(연결·인증·응답 모양). 메시지에 토큰을 넣지 않는다."""


@dataclass(frozen=True)
class TableSource:
    endpoint: str
    fields: dict[str, str] = field(default_factory=dict)     # 계약 열 → 원천 칸(없으면 같은 이름)


@dataclass(frozen=True)
class HrApiConfig:
    base_url: str
    token: str | None
    tables: dict[str, TableSource]                           # API에서 받는 표만(나머지는 지금 데이터의 것)
    page_size: int = PAGE_SIZE
    timeout_s: float = TIMEOUT_S

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc or self.base_url

    @classmethod
    def from_env(cls, env=None) -> "HrApiConfig | None":
        """URL이 없으면 None(CSV 모드). 설정 JSON이 깨졌으면 HrApiError -- 조용히 기본값으로 받지 않는다."""
        env = os.environ if env is None else env
        url = (env.get(URL_ENV) or "").strip()
        if not url:
            return None
        raw: dict = {}
        path = (env.get(CONFIG_ENV) or "").strip()
        if path:
            try:
                raw = json.loads(open(path, encoding="utf-8-sig").read())
            except (OSError, ValueError) as exc:
                raise HrApiError(f"인사 시스템 연결 설정({CONFIG_ENV})을 읽지 못했다: {exc}") from None
            if not isinstance(raw, dict):
                raise HrApiError(f"인사 시스템 연결 설정({CONFIG_ENV})은 JSON 객체여야 한다")
        return cls(base_url=url.rstrip("/"), token=(env.get(TOKEN_ENV) or "").strip() or None,
                   tables=_tables_of(raw.get("tables")), page_size=_positive(raw, "page_size", PAGE_SIZE, int),
                   timeout_s=_positive(raw, "timeout_s", TIMEOUT_S, float))


def _positive(raw: dict, key: str, default, kind):
    v = raw.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise HrApiError(f"인사 시스템 연결 설정 {key}는 양수여야 한다")
    return kind(v)


def _tables_of(raw) -> dict[str, TableSource]:
    """기본 = 인사 표 전부 API. 설정 `tables`: {"reviews.csv": null}(그 표는 지금 데이터의 것), {"projects.csv": {"endpoint": ...}} 등."""
    out = {name: TableSource(ep) for name, ep in DEFAULT_ENDPOINTS.items()}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        raise HrApiError('인사 시스템 연결 설정 tables는 {"표.csv": {"endpoint": ..., "fields": {...}} | null} 모양이어야 한다')
    for name, spec in raw.items():
        if name not in FILE_SPECS:
            raise HrApiError(f"인사 시스템 연결 설정에 모르는 표 '{name}'이 있다")
        if spec is None:
            out.pop(name, None)
            continue
        if not isinstance(spec, dict):
            raise HrApiError(f"인사 시스템 연결 설정 {name}은 객체이거나 null이어야 한다")
        ep = spec.get("endpoint", DEFAULT_ENDPOINTS.get(name) or SERVICE_ENDPOINTS.get(name))
        fields = spec.get("fields") or {}
        cols = {c.name for c in FILE_SPECS[name].columns}
        if not isinstance(ep, str) or not ep.strip() or not isinstance(fields, dict) \
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
            raise HrApiError(f"인사 시스템 연결 설정 {name}: endpoint는 문자열, fields는 {{계약 열: 원천 칸}}이어야 한다")
        unknown = sorted(set(fields) - cols)
        if unknown:
            raise HrApiError(f"인사 시스템 연결 설정 {name}: 계약에 없는 열 {unknown}")
        out[name] = TableSource(ep.strip().strip("/"), dict(fields))
    return out


# --- 받기 ---------------------------------------------------------------------------------------------------------

def fetch(cfg: HrApiConfig, client=None) -> dict[str, list[dict]]:
    """설정한 표를 모두 받는다(표 이름 → 원천 항목 목록). 하나라도 못 받으면 HrApiError -- 일부만 바꿔 켜지 않는다.
    client: httpx.Client(시험은 MockTransport). 없으면 만든다."""
    import httpx
    own = client is None
    if own:
        client = httpx.Client(timeout=cfg.timeout_s)
    headers = {"Accept": "application/json"}
    if cfg.token:
        headers["Authorization"] = f"Bearer {cfg.token}"
    try:
        out = {}
        for name, src in cfg.tables.items():
            out[name] = _fetch_table(client, f"{cfg.base_url}/{src.endpoint}", headers, cfg.page_size, name)
        return out
    finally:
        if own:
            client.close()


def _fetch_table(client, url: str, headers: dict, limit: int, name: str) -> list[dict]:
    import httpx
    items: list[dict] = []
    cursor, seen = None, set()
    for _ in range(MAX_PAGES):
        params = {"limit": limit} | ({"cursor": cursor} if cursor is not None else {})
        try:
            r = client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise HrApiError(f"{name}: 인사 시스템에 연결하지 못했다({type(exc).__name__})") from None
        if r.status_code in (401, 403):
            raise HrApiError(f"{name}: 인사 시스템이 인증을 거절했다(HTTP {r.status_code}) -- {TOKEN_ENV}를 확인한다")
        if r.status_code != 200:
            raise HrApiError(f"{name}: 인사 시스템 응답 HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError:
            raise HrApiError(f"{name}: 인사 시스템 응답이 JSON이 아니다") from None
        page = body.get("items") if isinstance(body, dict) else None
        if not isinstance(page, list) or not all(isinstance(x, dict) for x in page):
            raise HrApiError(f'{name}: 응답에 항목 목록이 없다({{"items": [...], "next": ...}} 모양이어야 한다)')
        items += page
        cursor = body.get("next")
        if cursor is None:
            return items
        if not isinstance(cursor, (str, int)) or str(cursor) in seen:
            raise HrApiError(f"{name}: 다음 쪽 커서가 잘못됐거나 되풀이된다")
        seen.add(str(cursor))
    raise HrApiError(f"{name}: {MAX_PAGES}쪽을 넘었다")


# --- CSV 묶음으로 ---------------------------------------------------------------------------------------------------

def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Y" if v else "N"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def to_csv(name: str, items: list[dict], fields: dict[str, str] | None = None) -> str:
    """계약 열 순서의 CSV 원문. 원천에 없는 칸은 빈 값(검사가 필수 열이면 오류로 알린다), 계약에 없는 칸은 버린다."""
    fields = fields or {}
    cols = [c.name for c in FILE_SPECS[name].columns]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(cols)
    for it in items:
        w.writerow([_cell(it.get(fields.get(c, c))) for c in cols])
    return out.getvalue()


def merge(current: dict[str, str], fetched: dict[str, list[dict]], cfg: HrApiConfig, *, as_of: str) -> dict[str, str]:
    """지금 판(파일 이름 → 원문)에 받은 표를 갈아 끼운 새 묶음. manifest는 지금 것을 이어 쓴다.
    데이터 기준월(as_of)은 기술 경력 표를 받았을 때만 받은 달로 바꾼다 -- 기술 경력을 지금 데이터(CSV)로 둔 채 다른 표만 받았는데 바꾸면
    마지막 사용 달이 없는 경력이 받은 달까지 센 것으로 읽혀 늘어날 달이 줄어든다(Codex 리뷰 2026-10-19).
    지금 판의 파일 해시 목록(manifest files)은 뺀다 -- 받은 표를 이 서비스가 만들었으니 원천 무결성 검사 대상이 아니다."""
    if "manifest.json" not in current:
        raise HrApiError("지금 데이터에 manifest.json이 없어 합칠 수 없다")
    files = dict(current)
    for name, items in fetched.items():
        files[name] = to_csv(name, items, cfg.tables[name].fields)
    m = json.loads(files["manifest.json"])
    m.pop("files", None)
    if "person_skills.csv" in fetched:
        m["as_of"] = as_of
    files["manifest.json"] = json.dumps(m, ensure_ascii=False, indent=1) + "\n"
    return files


def changed_tables(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """받기 전후에 원문이 바뀐 표(manifest 제외)."""
    names = sorted((set(before) | set(after)) - {"manifest.json"})
    return [n for n in names if before.get(n) != after.get(n)]
