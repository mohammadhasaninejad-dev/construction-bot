import sqlite3
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

_data_dir = Path("/data") if Path("/data").exists() else Path(__file__).parent
DB_PATH = _data_dir / "reports.db"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

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

    c.execute("""
    CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        supervisor_id INTEGER NOT NULL,
        supervisor_name TEXT,
        project TEXT NOT NULL,
        report_date TEXT NOT NULL,
        day_name TEXT,
        workers TEXT,
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

    c.execute("""
    CREATE TABLE IF NOT EXISTS media (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        report_id INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        media_type TEXT NOT NULL,
        local_path TEXT,
        FOREIGN KEY (report_id) REFERENCES reports(id) ON DELETE CASCADE
    )
    """)

    try:
        c.execute("ALTER TABLE media ADD COLUMN local_path TEXT")
    except sqlite3.OperationalError:
        pass

    c.execute("""
    CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id INTEGER,
        actor_name TEXT,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id INTEGER,
        details TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS workers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        active INTEGER DEFAULT 1,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
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


def add_pending_user(username: str, name: str, role: str, projects: List[str]) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username = ? COLLATE NOCASE", (username.lstrip("@"),))
    if c.fetchone():
        conn.close()
        return False
    temp_id = -int(datetime.now().timestamp())
    c.execute("""
    INSERT INTO users (user_id, username, name, role, projects)
    VALUES (?, ?, ?, ?, ?)
    """, (temp_id, username.lstrip("@"), name, role, json.dumps(projects, ensure_ascii=False)))
    conn.commit()
    conn.close()
    return True


def promote_pending_user(real_user_id: int, username: str) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.lstrip("@"),))
    row = c.fetchone()
    if not row:
        conn.close()
        return None
    d = dict(row)
    if d["user_id"] > 0:
        conn.close()
        d["projects"] = json.loads(d["projects"] or "[]")
        return d
    old_id = d["user_id"]
    projects = d["projects"]
    c.execute("DELETE FROM users WHERE user_id = ?", (old_id,))
    c.execute("""
    INSERT INTO users (user_id, username, name, role, projects, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
        username = excluded.username,
        name = excluded.name,
        role = excluded.role,
        projects = excluded.projects
    """, (real_user_id, d["username"], d["name"], d["role"], projects, d.get("created_at")))
    conn.commit()
    conn.close()
    d["user_id"] = real_user_id
    d["projects"] = json.loads(projects or "[]")
    return d


def update_user(user_id: int, name: Optional[str] = None, role: Optional[str] = None,
                projects: Optional[List[str]] = None, username: Optional[str] = None):
    current = get_user(user_id)
    if not current:
        return False
    conn = get_connection()
    c = conn.cursor()
    name = name if name is not None else current["name"]
    role = role if role is not None else current["role"]
    projects = projects if projects is not None else current["projects"]
    username = username if username is not None else current.get("username")
    c.execute("""
    UPDATE users SET name = ?, role = ?, projects = ?, username = ? WHERE user_id = ?
    """, (name, role, json.dumps(projects, ensure_ascii=False), username, user_id))
    conn.commit()
    conn.close()
    return True


def delete_user(user_id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


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
    c.execute("SELECT user_id FROM users WHERE role = 'manager' AND user_id > 0")
    ids = [r[0] for r in c.fetchall()]
    conn.close()
    return ids


def get_supervisors() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE role = 'supervisor' AND user_id > 0")
    rows = c.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["projects"] = json.loads(d["projects"] or "[]")
        result.append(d)
    return result


# ---------- Workers ----------

def add_worker(name: str) -> Optional[int]:
    name = name.strip()
    if not name:
        return None
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO workers (name, active) VALUES (?, 1)", (name,))
        wid = c.lastrowid
        conn.commit()
        conn.close()
        return wid
    except sqlite3.IntegrityError:
        # اگر قبلاً بوده، فعالش کن
        c.execute("UPDATE workers SET active = 1 WHERE name = ?", (name,))
        c.execute("SELECT id FROM workers WHERE name = ?", (name,))
        row = c.fetchone()
        conn.commit()
        conn.close()
        return row[0] if row else None


def deactivate_worker(worker_id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE workers SET active = 0 WHERE id = ?", (worker_id,))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def delete_worker(worker_id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM workers WHERE id = ?", (worker_id,))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def rename_worker(worker_id: int, new_name: str) -> bool:
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("UPDATE workers SET name = ? WHERE id = ?", (new_name.strip(), worker_id))
        ok = c.rowcount > 0
        conn.commit()
        conn.close()
        return ok
    except sqlite3.IntegrityError:
        conn.close()
        return False


def get_active_workers() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM workers WHERE active = 1 ORDER BY name")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_all_workers() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM workers ORDER BY active DESC, name")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_worker(worker_id: int) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM workers WHERE id = ?", (worker_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


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
            "INSERT INTO media (report_id, file_id, media_type, local_path) VALUES (?, ?, ?, ?)",
            (report_id, m["file_id"], m["type"], m.get("local_path"))
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
        issues = ?, miscellaneous = ?, updated_at = ?,
        supervisor_name = COALESCE(?, supervisor_name)
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
        data.get("supervisor_name"),
        report_id,
    ))

    if media_list is not None:
        c.execute("DELETE FROM media WHERE report_id = ?", (report_id,))
        for m in media_list:
            c.execute(
                "INSERT INTO media (report_id, file_id, media_type, local_path) VALUES (?, ?, ?, ?)",
                (report_id, m["file_id"], m["type"], m.get("local_path"))
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


def get_last_report_for_day_project(supervisor_id: int, project: str, report_date: str) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    SELECT * FROM reports
    WHERE supervisor_id = ? AND project = ? AND report_date = ?
    ORDER BY id DESC LIMIT 1
    """, (supervisor_id, project, report_date))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["workers"] = json.loads(d["workers"] or "[]")
    return d


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


def get_reported_projects_on_date(report_date: str) -> List[str]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT DISTINCT project FROM reports WHERE report_date = ?", (report_date,))
    projects = [r[0] for r in c.fetchall()]
    conn.close()
    return projects


def get_stats(date_from: str, date_to: str) -> Dict[str, Any]:
    reports = get_reports(date_from=date_from, date_to=date_to, limit=5000)
    total_hours = 0.0
    total_food = 0
    total_petty = 0.0
    by_project: Dict[str, int] = {}
    supervisors = set()

    for r in reports:
        workers = r.get("workers") or []
        for w in workers:
            try:
                total_hours += float(w.get("hours") or 0)
            except (TypeError, ValueError):
                pass
        total_food += int(r.get("food_count") or 0)
        try:
            total_petty += float(r.get("petty_cash") or 0)
        except (TypeError, ValueError):
            pass
        p = r.get("project") or "—"
        by_project[p] = by_project.get(p, 0) + 1
        if r.get("supervisor_name"):
            supervisors.add(r["supervisor_name"])

    return {
        "count": len(reports),
        "total_hours": round(total_hours, 1),
        "total_food": total_food,
        "total_petty_cash": total_petty,
        "by_project": by_project,
        "supervisors_count": len(supervisors),
        "reports": reports,
    }


def log_activity(actor_id: int, actor_name: str, action: str,
                 target_type: str = None, target_id: int = None, details: str = None):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
    INSERT INTO activity_log (actor_id, actor_name, action, target_type, target_id, details)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (actor_id, actor_name, action, target_type, target_id, details))
    conn.commit()
    conn.close()


def get_activity_log(limit: int = 30) -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows

# ---------- Projects ----------

def seed_projects_if_empty(default_names: list):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM projects")
    if c.fetchone()[0] == 0:
        for name in default_names:
            try:
                c.execute("INSERT INTO projects (name, active) VALUES (?, 1)", (name,))
            except sqlite3.IntegrityError:
                pass
        conn.commit()
    conn.close()


def get_active_projects() -> List[str]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT name FROM projects WHERE active = 1 ORDER BY name")
    names = [r[0] for r in c.fetchall()]
    conn.close()
    return names


def get_all_projects() -> List[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM projects ORDER BY active DESC, name")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def add_project(name: str) -> Optional[int]:
    name = name.strip()
    if not name:
        return None
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO projects (name, active) VALUES (?, 1)", (name,))
        pid = c.lastrowid
        conn.commit()
        conn.close()
        return pid
    except sqlite3.IntegrityError:
        c.execute("UPDATE projects SET active = 1 WHERE name = ?", (name,))
        c.execute("SELECT id FROM projects WHERE name = ?", (name,))
        row = c.fetchone()
        conn.commit()
        conn.close()
        return row[0] if row else None


def deactivate_project(project_id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE projects SET active = 0 WHERE id = ?", (project_id,))
    ok = c.rowcount > 0
    conn.commit()
    conn.close()
    return ok


def get_project_by_name(name: str) -> Optional[Dict]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM projects WHERE name = ?", (name.strip(),))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None
