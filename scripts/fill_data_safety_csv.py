"""Fill out Play Console Data Safety CSV for 韭菜健檢.

Run:
    python scripts/fill_data_safety_csv.py

Input:  C:/Users/USER/Downloads/data_safety_export.csv
Output: C:/Users/USER/Downloads/data_safety_filled.csv  (upload back to Play)

我們收集的資料:
- EMAIL (Supabase auth)
- USER_ACCOUNT (Supabase user_id)
- USER_INTERACTION (觀察清單動作、按鈕點擊 — 改善體驗用)

每項都:
- 收集 = true, 分享 = false
- 非暫存 (PSL_DATA_USAGE_EPHEMERAL = false)
- 使用者可控 (OPTIONAL,因為有訪客模式)
- 收集目的 = APP_FUNCTIONALITY + ACCOUNT_MANAGEMENT
- 不分享 → 分享目的全空

加密 = true,部分刪除 = 是,刪除 URL 已設,帳戶刪除 URL 已設
"""
from __future__ import annotations
import csv
from pathlib import Path

SRC = Path("C:/Users/USER/Downloads/data_safety_export.csv")
DST = Path("C:/Users/USER/Downloads/data_safety_filled.csv")

# 我們收集的 3 種資料(其他都不收)
COLLECTED = {"PSL_EMAIL", "PSL_USER_ACCOUNT", "PSL_USER_INTERACTION"}

# 每個收集資料的「收集目的」(勾這幾個)
COLLECTION_PURPOSES = {
    "PSL_EMAIL": {"PSL_APP_FUNCTIONALITY", "PSL_ACCOUNT_MANAGEMENT"},
    "PSL_USER_ACCOUNT": {"PSL_APP_FUNCTIONALITY", "PSL_ACCOUNT_MANAGEMENT"},
    "PSL_USER_INTERACTION": {"PSL_APP_FUNCTIONALITY"},
}

# Top-level 答案(從 Q1 開始)
TOP_LEVEL = {
    "PSL_DATA_COLLECTION_COLLECTS_PERSONAL_DATA": "true",
    "PSL_DATA_COLLECTION_ENCRYPTED_IN_TRANSIT": "true",
    "PSL_ACCOUNT_DELETION_URL": "https://aaowobbowocc-ai.github.io/leek-check/delete-account.html",
    "PSL_DATA_DELETION_URL": "https://aaowobbowocc-ai.github.io/leek-check/delete-account.html",
}

# 帳戶建立方式 — 只勾使用者名稱+密碼
ACCT_CREATION = {
    "PSL_ACM_USER_ID_PASSWORD": "true",
}

# 部分資料刪除 = 是
DATA_DELETION = "DATA_DELETION_YES"

# Top-level 資料類型勾選 (PSL_DATA_TYPES_*)
DATA_TYPES_TICKED = {
    "PSL_DATA_TYPES_PERSONAL": {"PSL_EMAIL", "PSL_USER_ACCOUNT"},
    "PSL_DATA_TYPES_APP_ACTIVITY": {"PSL_USER_INTERACTION"},
}


def fill_row(row: list[str]) -> list[str]:
    """每 row 依規則填 col[2] (Response value)."""
    qid = row[0]
    resp_id = row[1]
    # col[2] 是 response value 我們要寫的

    # 1. Top-level (collection / encrypted / URLs)
    if qid in TOP_LEVEL:
        row[2] = TOP_LEVEL[qid]
        return row

    # 2. 帳戶建立方式
    if qid == "PSL_SUPPORTED_ACCOUNT_CREATION_METHODS":
        row[2] = "true" if resp_id in ACCT_CREATION else ""
        return row

    # 3. 部分資料刪除 yes/no
    if qid == "PSL_SUPPORT_DATA_DELETION_BY_USER":
        row[2] = "true" if resp_id == DATA_DELETION else ""
        return row

    # 4. 資料類型勾選 (PSL_DATA_TYPES_PERSONAL, PSL_DATA_TYPES_APP_ACTIVITY...)
    if qid.startswith("PSL_DATA_TYPES_"):
        ticked = DATA_TYPES_TICKED.get(qid, set())
        row[2] = "true" if resp_id in ticked else ""
        return row

    # 5. 每項資料的使用方式 (PSL_DATA_USAGE_RESPONSES:PSL_XXX:...)
    if qid.startswith("PSL_DATA_USAGE_RESPONSES:"):
        parts = qid.split(":")
        data_key = parts[1]  # PSL_EMAIL / PSL_USER_ACCOUNT / PSL_USER_INTERACTION
        sub_q = parts[2]     # PSL_DATA_USAGE_COLLECTION_AND_SHARING / EPHEMERAL / USER_CONTROL / COLLECTION_PURPOSE / SHARING_PURPOSE

        # 沒收集的資料 → 全部留空
        if data_key not in COLLECTED:
            row[2] = ""
            return row

        # 收集 + 不分享
        if sub_q == "PSL_DATA_USAGE_COLLECTION_AND_SHARING":
            if resp_id == "PSL_DATA_USAGE_ONLY_COLLECTED":
                row[2] = "true"
            else:
                row[2] = ""
            return row

        # 暫存性 — false (我們是長期儲存到 Supabase,不是暫存)
        if sub_q == "PSL_DATA_USAGE_EPHEMERAL":
            row[2] = "false"
            return row

        # 使用者可控 — OPTIONAL (因為有訪客模式)
        if sub_q == "DATA_USAGE_USER_CONTROL":
            if resp_id == "PSL_DATA_USAGE_USER_CONTROL_OPTIONAL":
                row[2] = "true"
            else:
                row[2] = ""
            return row

        # 收集目的 — 按 COLLECTION_PURPOSES 字典
        if sub_q == "DATA_USAGE_COLLECTION_PURPOSE":
            purposes = COLLECTION_PURPOSES.get(data_key, set())
            row[2] = "true" if resp_id in purposes else ""
            return row

        # 分享目的 — 全空 (我們不分享)
        if sub_q == "DATA_USAGE_SHARING_PURPOSE":
            row[2] = ""
            return row

    # 其他都留空
    return row


def main():
    if not SRC.exists():
        raise SystemExit(f"找不到 source CSV: {SRC}")

    with SRC.open("r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    filled = [fill_row(row) for row in rows]

    with DST.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(filled)

    # Print summary
    true_count = sum(1 for r in filled if r[2] == "true")
    url_count = sum(1 for r in filled if r[2].startswith("http"))
    print(f"✅ 填好的 CSV: {DST}")
    print(f"   勾選 true:{true_count} 項")
    print(f"   填 URL:{url_count} 項")
    print(f"   總行數:{len(filled)}")

    # Sanity check — print each "true" row
    print("\n── 勾選的項目 ──")
    for r in filled:
        if r[2] == "true":
            print(f"  ✓ {r[4]}")


if __name__ == "__main__":
    main()
