import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

# روی Railway اگر Volume به /data وصل باشد از آن استفاده می‌کند
_data_dir = Path("/data") if Path("/data").exists() else Path(__file__).parent
DB_PATH = _data_dir / "reports.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # کاربران
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('manager', 'supervisor')),
        projects TEXT DEFAULT '[]',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # گزارش‌ها
    c.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supervisor_id INTEGER NOT NULL,
        supervisor_name TEXT,
        project TEXT NOT NULL,
        report_date TEXT NOT NULL,
        day_name TEXT,
        workers TEXT,              -- JSON list of {name, entry, exit, hours}
        work_report TEXT,
        materials_in TEXT,
        materials_out TEXT,
        food_count INTEGER DEFAULT 0,
        petty_cash REAL DEFAULT 0,
        petty_cash_reason TEXT,
        issues TEXT,
        miscellaneous TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT,
        FOREIGN KEY (supervisor_id) REFERENCES users(user_id)
    )
    """)

    # رسانه
    c.execute("""
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        media_type TEXT NOT NULL,   -- photo / video
        FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
    )
    """)

    conn.commit()
    conn.close()


# ---------- Users ----------

def upsert_user(user_id: int, username: Optional[str], name: str, role: str, projects: List[str]):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    INSERT INTO users (user_id, username, name, role, projects)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username = excluded.username,
        name = excluded.name,
        role = excluded.role,
        projects = excluded.projects
    """, (user_id, username, name, role, json.dumps(projects, ensure_ascii=False)))
    conn.commit()
    conn.close()


def get_user(user_id: int) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["projects"] = json.loads(d["projects"] or "[]")
    return d


def get_user_by_username(username: str) -> Optional[Dict]:
    if not username:
        return None
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.lstrip("@"),))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["projects"] = json.loads(d["projects"] or "[]")
    return d


def get_all_users() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY role, name")
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["projects"] = json.loads(d["projects"] or "[]")
        result.append(d)
    return result


def get_manager_ids() -> List[int]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE role = 'manager'")
    ids = [r[0] for r in c.fetchall()]
    conn.close()
    return ids


# ---------- Reports ----------

def save_report(data: Dict[str, Any], media_list: List[Dict]) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    INSERT INTO reports (
        supervisor_id, supervisor_name, project, report_date, day_name,
        workers, work_report, materials_in, materials_out,
        food_count, petty_cash, petty_cash_reason, issues, miscellaneous
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["supervisor_id"],
        data["supervisor_name"],
        data["project"],
        data["report_date"],
        data["day_name"],
        json.dumps(data.get("workers", []), ensure_ascii=False),
        data.get("work_report", ""),
        data.get("materials_in", ""),
        data.get("materials_out", ""),
        data.get("food_count", 0),
        data.get("petty_cash", 0),
        data.get("petty_cash_reason", ""),
        data.get("issues", ""),
        data.get("miscellaneous", ""),
    ))
    report_id = c.lastrowid

    for m in media_list:
        c.execute(
            "INSERT INTO media (report_id, file_id, media_type) VALUES (?, ?, ?)",
            (report_id, m["file_id"], m["type"])
        )

    conn.commit()
    conn.close()
    return report_id


def update_report(report_id: int, data: Dict[str, Any], media_list: Optional[List[Dict]] = None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    UPDATE reports SET
        project = ?, report_date = ?, day_name = ?,
        workers = ?, work_report = ?, materials_in = ?, materials_out = ?,
        food_count = ?, petty_cash = ?, petty_cash_reason = ?,
        issues = ?, miscellaneous = ?, updated_at = ?
    WHERE id = ?
    """, (
        data["project"],
        data["report_date"],
        data["day_name"],
        json.dumps(data.get("workers", []), ensure_ascii=False),
        data.get("work_report", ""),
        data.get("materials_in", ""),
        data.get("materials_out", ""),
        data.get("food_count", 0),
        data.get("petty_cash", 0),
        data.get("petty_cash_reason", ""),
        data.get("issues", ""),
        data.get("miscellaneous", ""),
        datetime.now().isoformat(timespec="seconds"),
        report_id,
    ))

    if media_list is not None:
        c.execute("DELETE FROM media WHERE report_id = ?", (report_id,))
        for m in media_list:
            c.execute(
                "INSERT INTO media (report_id, file_id, media_type) VALUES (?, ?, ?)",
                (report_id, m["file_id"], m["type"])
            )

    conn.commit()
    conn.close()


def delete_report(report_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM media WHERE report_id = ?", (report_id,))
    c.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    conn.commit()
    conn.close()


def get_report(report_id: int) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["workers"] = json.loads(d["workers"] or "[]")
    return d


def get_report_media(report_id: int) -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM media WHERE report_id = ?", (report_id,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_reports(
    supervisor_id: Optional[int] = None,
    project: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 30,
) -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    query = "SELECT * FROM reports WHERE 1=1"
    params: list = []

    if supervisor_id is not None:
        query += " AND supervisor_id = ?"
        params.append(supervisor_id)
    if project:
        query += " AND project = ?"
        params.append(project)
    if date_from:
        query += " AND report_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND report_date <= ?"
        params.append(date_to)

    query += " ORDER BY report_date DESC, id DESC LIMIT ?"
    params.append(limit)

    c.execute(query, params)
    rows = []
    for r in c.fetchall():
        d = dict(r)
        d["workers"] = json.loads(d["workers"] or "[]")
        rows.append(d)
    conn.close()
    return rows
